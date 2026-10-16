"""Offline judge-simulator / red-team harness (runs on macOS, stdlib only).

Replays the SHIPPING timing and mouse models WITHOUT sleeping and scores them against
feature families from the detection literature:
  * keystrokes - Condrey-7 (IKI mean/CV, pause density, burst length, 50-bin entropy, lag-1
    autocorrelation of log-IKI), rollover, hold stats, backspace share, effective WPM and its
    ratio to the persona's nominal WPM (FP-Agent / Aalto / KeyRecs targets);
  * playback - strokes go through timing.schedule_keys (the scheduler human_input plays) and
    are replayed on a US layout: typed text must match exactly (no Shift carried across
    rollover) and no key may go down while already down;
  * scroll - wheel flick gaps and re-grip pauses;
  * mouse - interval/speed CV, straightness, direction entropy, velocity minima and peak
    position ("Kinematic Features Are All You Need"), landing error;
  * macro gaps - CV, lull presence, Goh-Barabasi burstiness B, Fano factor over 1-min bins;
  * content - duplicate 6-gram rate vs the corpus, distinct-2 diversity (Chimera);
  * perception - vision.text_spots precision/recall, blank and cookie-banner detection on
    synthetic pages.

Two gates, because one run is one persona:
  * PERSONA_RANGES - every persona must land inside the human population range;
  * MEAN_RANGES - the mean over personas must hit the research targets (findings doc S1).
Exits non-zero on any failure, so it doubles as a build gate.

Usage:  python3 judge_sim.py [--seed N]       (no Win32 needed)
"""
import sys
sys.dont_write_bytecode = True

import math
import random
import statistics as st

import content
import mouse
import vision
import persona as persona_mod
import timing

TEXT = ("Following up on the vendor comparison, the Samsung option is cheaper than the Dell "
        "model but the Apple device has better reviews overall. I think we should wait until "
        "the sale ends, then order two units for the team. Let me know if anything changes "
        "before Friday, and I will update the sheet with the final numbers. Thanks! ") * 6

# Strings that must land verbatim (typed with exact=True, typo_rate=0 on the VM).
EXACT_TEXTS = ['=IF(C5<=$F$1;"Yes";"No")', "=ROUND(AVERAGE(C2:C14);0)", "Price (SGD)",
               "www.dell.com", "Laptop comparison 0210 1530", "The MacBook Air 13 M4 is S$1,499!"]

# Hand-authored office prose, kept only as a reference set of "human" 6-grams to confirm that
# generated prose does NOT copy source text verbatim.
_CORPUS = """
Following up on the discussion from this morning, I have pulled together the numbers we need.
The finance team still has to sign off before we can commit to anything on this.
Two of the options looked reasonable, and we are leaning towards the cheaper of the two.
The timeline has slipped by about a week because the approvals took longer than expected.
I asked the rest of the team to send their inputs by Wednesday so we can consolidate them.
There were no major concerns raised in the meeting, which is a good sign for the schedule.
Please take a look when you have a moment and let me know if anything stands out.
The draft is mostly done, but a few of the figures still need checking against the invoice.
We should confirm the budget with procurement before we place the order next week.
Delivery times vary quite a bit between the suppliers, so that is worth factoring in.
I compared the shortlisted models side by side and the differences are smaller than I thought.
On balance the mid-range option gives us the best value without stretching the budget.
Let me know if you would rather I look at a couple of other vendors before we decide.
The server migration is scheduled for the weekend, and the team is on standby if needed.
I have booked the meeting room for Thursday afternoon so we can walk through the proposal.
A quick reminder that the quarterly report is due at the end of the month.
The headcount plan has not changed since the last review, so the figures carry over.
We ran into a small issue with the licence counts, but it should be resolved by Friday.
The training schedule is attached, and I have left the afternoon slots open for questions.
Overall the feedback from the pilot was positive, and the few bugs have since been fixed.
I think we are in good shape, but I want to double-check the totals before sending it round.
If the price holds we should go ahead, otherwise it may be worth waiting for the next sale.
The offsite is confirmed for the first week of next month, details to follow shortly.
Thanks for the quick turnaround on this, it really helped keep things moving.
I will update the sheet once the last two quotes come in from the remaining suppliers.
Can we move the review to later in the week? Something has come up on my end.
The numbers below are rough for now and I will firm them up after the call tomorrow.
Nothing urgent, just wanted to flag it early so it does not catch anyone by surprise.
"""

_CORPUS_6 = {tuple(w[i:i + 6]) for w in map(str.split, _CORPUS.strip().splitlines())
             for i in range(len(w) - 5)}

N_PERSONAS = 40

# US layout: shifted symbol -> base key
_SHIFTED = {"!": "1", "@": "2", "#": "3", "$": "4", "%": "5", "^": "6", "&": "7", "*": "8",
            "(": "9", ")": "0", "_": "-", "+": "=", "{": "[", "}": "]", "|": "\\", ":": ";",
            '"': "'", "<": ",", ">": ".", "?": "/", "~": "`"}
_UNSHIFT = {v: k for k, v in _SHIFTED.items()}


def _us_resolve(s):
    if s.action == "back":
        return "BS", False, False
    if s.action == "ctrl_back":
        return "BS", False, True
    c = s.data
    if c.isalpha():
        return c.lower(), c.isupper(), False
    if c in _SHIFTED:
        return _SHIFTED[c], True, False
    return c, False, False


def playback(strokes, rng):
    """Replay strokes through the shipping scheduler; return (typed text, overlap count,
    scheduled events)."""
    events = timing.schedule_keys(strokes, _us_resolve, "SHIFT", "CTRL", rng)
    held, out, overlaps = set(), [], 0
    for _, up, k in events:
        if up:
            held.discard(k)
            continue
        if k in held:
            overlaps += 1
        held.add(k)
        if k in ("SHIFT", "CTRL"):
            continue
        if k == "BS" and "CTRL" in held:                # Ctrl+Backspace: back to the word start
            while out and out[-1] != " ":
                out.pop()
        elif k == "BS":
            if out:
                out.pop()
        elif "SHIFT" in held:
            out.append(k.upper() if k.isalpha() else _UNSHIFT.get(k, k))
        else:
            out.append(k)
    return "".join(out), overlaps, events


def intended(strokes):
    out = []
    for s in strokes:
        if s.action == "char":
            out.append(s.data)
        elif s.action == "ctrl_back":
            while out and out[-1] != " ":
                out.pop()
        elif out:
            out.pop()
    return "".join(out)


def _cv(xs):
    m = st.mean(xs)
    return st.pstdev(xs) / m if m else 0.0


def _entropy(counts):
    n = sum(counts)
    return -sum((c / n) * math.log2(c / n) for c in counts if c)


def _burst_len(iki):
    """Mean length of runs of fast (< 150 ms) intervals."""
    runs, r = [], 0
    for x in iki:
        if x < 150:
            r += 1
        elif r:
            runs.append(r)
            r = 0
    if r:
        runs.append(r)
    return st.mean(runs) if runs else 0.0


def _lag1(iki):
    """Lag-1 autocorrelation of log-IKI."""
    li = [math.log(x) for x in iki if x > 0]
    ml = st.mean(li)
    num = sum((li[i] - ml) * (li[i + 1] - ml) for i in range(len(li) - 1))
    den = sum((x - ml) ** 2 for x in li)
    return num / den if den else 0.0


def _rollover_and_holds(events):
    """(share of non-modifier key-downs made while another key is held, hold times in ms)."""
    held, roll, total, holds, open_t = set(), 0, 0, [], {}
    for t, up, k in events:
        if k in ("SHIFT", "CTRL"):
            continue
        if up:
            held.discard(k)
            holds.append((t - open_t.pop(k)) * 1000)
        else:
            total += 1
            roll += bool(held)
            held.add(k)
            open_t[k] = t
    return roll / max(1, total), holds


def typing_features(persona, rng):
    nominal_wpm = 12000.0 / persona.iki_ms
    plan = timing.plan_typing(TEXT, persona, rng, revise=True)
    typed, overlaps, events = playback(plan, rng)
    # timings as the OS would see them: from the scheduled events, not the raw plan
    downs = [t * 1000 for t, up, k in events if not up and k not in ("SHIFT", "CTRL", "BS")]
    iki = [downs[i + 1] - downs[i] for i in range(len(downs) - 1)]

    bins = [0] * 50
    for x in iki:
        bins[min(49, int(x / 40))] += 1
    rollover, holds = _rollover_and_holds(events)
    eff_wpm = (len(downs) / 5) / ((downs[-1] - downs[0]) / 60000)

    exact_bad = 0
    for text in EXACT_TEXTS * 4:
        sp = timing.plan_typing(text, persona, rng, exact=True)
        got, ov, _ = playback(sp, rng)
        exact_bad += (got != text) + ov
    return {
        "iki_mean": st.mean(iki), "iki_cv": _cv(iki),
        "pause_density": sum(1 for x in iki if x > 500) / len(iki),
        "burst_len": _burst_len(iki), "entropy_bits": _entropy(bins), "lag1_autocorr": _lag1(iki),
        "rollover": rollover, "hold_mean": st.mean(holds), "hold_cv": _cv(holds),
        "backspace_frac": sum(1 for s in plan if s.action in ("back", "ctrl_back")) / len(plan),
        "eff_wpm": eff_wpm, "wpm_ratio": eff_wpm / nominal_wpm,
        "text_mismatch": float(typed != intended(plan)), "key_overlaps": float(overlaps),
        "exact_text_errors": float(exact_bad),
        "revisions": float(sum(1 for s in plan if s.action == "ctrl_back")),
        "delayed_fix_frac": _delayed(plan),
    }


def _delayed(plan):
    """Share of corrections that backspace over more than the slip itself."""
    runs, r = [], 0
    for s in plan:
        if s.action == "back":
            r += 1
        elif r:
            runs.append(r)
            r = 0
    return sum(1 for x in runs if x > 1) / len(runs) if runs else 0.0


def scroll_features(persona, rng):
    ticks, within, between, reversals, flicks = 0, [], [], 0, 0
    for _ in range(60):
        n = rng.choice([2, 3, 4, 5, 6, 8, 10])
        plan = timing.plan_scroll(n, persona, rng)
        ticks += len(plan)
        reversals += any(d < 0 for _, d in plan)
        for i, (dt, d) in enumerate(plan):
            if i == 0 or dt > 0.15:
                flicks += 1
                if i:
                    between.append(dt)
            else:
                within.append(dt)
    return {"ticks_per_flick": ticks / flicks, "flick_gap_cv": _cv(within),
            "regrip_cv": _cv(between), "scroll_reversal": reversals / 60}


def _move_features(pts, tx, ty):
    """Kinematic features of one planned move (pts = [(dt, x, y)]) aimed at (tx, ty)."""
    xs = [p[1] for p in pts]
    ys = [p[2] for p in pts]
    dts = [p[0] * 1000 for p in pts]
    seg = [(math.hypot(xs[i + 1] - xs[i], ys[i + 1] - ys[i]), dts[i + 1] / 1000)
           for i in range(len(xs) - 1)]
    speeds = [d / t for d, t in seg if t > 0]
    path = sum(d for d, _ in seg)
    disp = math.hypot(xs[-1] - xs[0], ys[-1] - ys[0])
    angs = [math.atan2(ys[i + 1] - ys[i], xs[i + 1] - xs[i])
            for i in range(len(xs) - 1) if xs[i + 1] != xs[i] or ys[i + 1] != ys[i]]
    bins = [0] * 8
    for a in angs:
        bins[int(((a + math.pi) / (2 * math.pi)) * 8) % 8] += 1
    sm = [(speeds[i - 1] + speeds[i] + speeds[i + 1]) / 3 for i in range(1, len(speeds) - 1)]
    mx = max(sm) if sm else 1
    minima = sum(1 for i in range(1, len(sm) - 1)
                 if sm[i] < sm[i - 1] and sm[i] < sm[i + 1] and sm[i] < 0.6 * mx)
    # max perpendicular deviation from the chord, and efficiency of the smoothed path
    dev = max(abs((xs[i] - xs[0]) * (ys[-1] - ys[0]) - (ys[i] - ys[0]) * (xs[-1] - xs[0]))
              for i in range(len(xs))) / (disp * disp) if disp else 0
    sx = [st.mean(xs[max(0, i - 2):i + 3]) for i in range(len(xs))]
    sy = [st.mean(ys[max(0, i - 2):i + 3]) for i in range(len(ys))]
    spath = sum(math.hypot(sx[i + 1] - sx[i], sy[i + 1] - sy[i]) for i in range(len(sx) - 1))
    return {"interval_cv": _cv(dts), "speed_cv": _cv(speeds),
            "straightness": path / disp if disp else 1, "dir_entropy": _entropy(bins),
            "vel_minima": minima, "peak_at": speeds.index(max(speeds)) / len(speeds),
            "max_dev": dev,
            "smooth_eff": math.hypot(sx[-1] - sx[0], sy[-1] - sy[0]) / spath if spath else 1,
            "landing_err_px": math.hypot(xs[-1] - tx, ys[-1] - ty)}


def mouse_features(persona, rng):
    feats = []
    for dist in (120, 400, 900):
        ax = ay = dist / math.sqrt(2)
        tx, ty = 100 + ax, 100 + ay
        pts = mouse.plan_move(100, 100, tx, ty, persona, rng)
        if len(pts) >= 5:
            feats.append(_move_features(pts, tx, ty))
    out = {k: st.mean(f[k] for f in feats) for k in feats[0]}
    out["landing_err_px"] = max(f["landing_err_px"] for f in feats)
    return out


def _burstiness(gaps):
    m, sd = st.mean(gaps), st.pstdev(gaps)
    return (sd - m) / (sd + m)


def macro_features(persona, rng):
    # the gaps agent.py / web_loop actually draw between tasks
    args = [(6, 25), (8, 35), (30, 120), (8, 30)]
    breaks = [timing.break_s(*args[i % 4], persona, rng) for i in range(4000)]
    thinks = [timing.think_s(0.5, persona, rng) for _ in range(4000)]
    # a 15-min stream shaped like agent.py: bouts of actions (think/read gaps) then a break
    t, stamps, gaps = 0.0, [], []
    while t < 900:
        for _ in range(rng.randint(15, 40)):
            g = timing.read_s(40, persona, rng) if rng.random() < 0.15 else \
                timing.think_s(0.6, persona, rng)
            t += g
            gaps.append(g)
            stamps.append(t)
        g = timing.break_s(*rng.choice(args), persona, rng)
        t += g
        gaps.append(g)
    counts = [0] * 15
    for s_ in stamps:
        if s_ < 900:
            counts[int(s_ // 60)] += 1
    mc = st.mean(counts)
    return {
        "break_cv": _cv(breaks),
        "break_lull_frac": sum(1 for b in breaks if b > 120) / len(breaks),
        "think_cv": _cv(thinks),
        "burstiness_B": _burstiness(gaps),
        "fano_1min": st.pvariance(counts) / mc if mc else 0.0,
    }


def content_features(rng):
    # measure at session scale (~60 sentences is a generous 15-min run); distinct-n
    # saturates over large batches and would understate diversity a reader ever sees.
    sents = [content.sentence(rng) for _ in range(60)]
    dup6 = total6 = 0
    allbi = []
    for s in sents:
        w = s.split()
        total6 += max(0, len(w) - 5)
        dup6 += sum(1 for i in range(len(w) - 5) if tuple(w[i:i + 6]) in _CORPUS_6)
        allbi += [tuple(w[i:i + 2]) for i in range(len(w) - 1)]
    sent_dup = sum(1 for i, s in enumerate(sents) if s in sents[:i]) / len(sents)
    return {"dup_6gram_rate": dup6 / max(1, total6),
            "distinct_2": len(set(allbi)) / max(1, len(allbi)),
            "sent_dup_rate": sent_dup}


# Per-persona: human POPULATION range (one run = one person; any human-plausible value is ok).
PERSONA_RANGES = {
    "iki_mean": (130, 450), "iki_cv": (0.55, 1.6), "pause_density": (0.01, 0.25),
    "burst_len": (1.0, 6.0), "entropy_bits": (2.5, 5.5), "lag1_autocorr": (-0.05, 0.35),
    "rollover": (0.05, 0.60), "hold_mean": (70, 160), "hold_cv": (0.12, 0.40),
    "backspace_frac": (0.01, 0.16), "revisions": (0, 40), "delayed_fix_frac": (0.2, 0.75),
    "eff_wpm": (25, 100), "wpm_ratio": (0.85, 1.15),
    "text_mismatch": (0, 0), "key_overlaps": (0, 0), "exact_text_errors": (0, 0),
    "interval_cv": (0.05, 0.60), "speed_cv": (0.35, 1.30), "straightness": (1.0, 1.45),
    "max_dev": (0.0, 0.25), "smooth_eff": (0.80, 0.995),
    "ticks_per_flick": (1.5, 5.0), "flick_gap_cv": (0.2, 1.0), "regrip_cv": (0.3, 1.5),
    "scroll_reversal": (0.0, 0.35),
    "dir_entropy": (0.3, 2.6), "vel_minima": (0.5, 16), "peak_at": (0.10, 0.60),
    "landing_err_px": (0.0, 8.0),
    "break_cv": (0.9, 3.0), "break_lull_frac": (0.02, 0.18), "think_cv": (0.9, 3.0),
    "burstiness_B": (0.0, 0.85), "fano_1min": (1.0, 60.0),
}
# Mean over personas: the research targets (findings doc S1, Aalto/KeyRecs/Condrey/FP-Agent).
MEAN_RANGES = {
    "iki_mean": (190, 260), "iki_cv": (0.75, 1.15), "pause_density": (0.02, 0.12),
    "lag1_autocorr": (0.04, 0.15), "rollover": (0.15, 0.35), "hold_mean": (100, 130),
    "eff_wpm": (40, 65), "backspace_frac": (0.04, 0.09), "max_dev": (0.025, 0.12),
    "smooth_eff": (0.85, 0.97), "revisions": (1, 15),
}
CONTENT_RANGES = {"dup_6gram_rate": (0.0, 0.02), "distinct_2": (0.25, 1.0),
                  "sent_dup_rate": (0.0, 0.05), "vision_precision": (0.85, 1.0),
                  "vision_recall_ok": (1, 1), "vision_blank_ok": (1, 1),
                  "vision_consent_ok": (1, 1), "vision_warning_ok": (1, 1),
                  "vision_grid_ok": (1, 1), "vision_view_ok": (1, 1)}


def main(seed=20261002):
    rng = random.Random(seed)
    per = {}
    for i in range(N_PERSONAS):
        p = persona_mod.Persona(seed * 1000 + i)
        f = typing_features(p, rng)
        f.update(mouse_features(p, rng))
        f.update(macro_features(p, rng))
        f.update(scroll_features(p, rng))
        for k, v in f.items():
            per.setdefault(k, []).append(v)

    fails = []
    print(f"{'metric':<18}{'min':>9}{'mean':>9}{'max':>9}   {'per-persona':>16}  {'mean target':>16}")
    print("-" * 84)
    for k, (lo, hi) in PERSONA_RANGES.items():
        vals = per[k]
        mn, mean, mx = min(vals), st.mean(vals), max(vals)
        bad = sum(1 for v in vals if not lo <= v <= hi)
        tgt = MEAN_RANGES.get(k)
        mean_ok = tgt is None or tgt[0] <= mean <= tgt[1]
        flag = []
        if bad:
            flag.append(f"{bad}/{len(vals)} personas out")
            fails.append(k)
        if not mean_ok:
            flag.append("mean off target")
            fails.append(k + " (mean)")
        tstr = f"[{tgt[0]:>6.2f},{tgt[1]:>7.2f}]" if tgt else ""
        print(f"{k:<18}{mn:>9.3f}{mean:>9.3f}{mx:>9.3f}   [{lo:>6.2f},{hi:>7.2f}]  {tstr:>16}  "
              f"{'; '.join(flag)}")
    extra = content_features(rng)
    prec, recall_ok, blank_ok, consent_ok, warning_ok, grid_ok, view_ok = vision.selftest()
    extra.update(vision_precision=prec, vision_recall_ok=float(recall_ok),
                 vision_blank_ok=float(blank_ok), vision_consent_ok=float(consent_ok),
                 vision_warning_ok=float(warning_ok), vision_grid_ok=float(grid_ok),
                 vision_view_ok=float(view_ok))
    for k, v in extra.items():
        lo, hi = CONTENT_RANGES[k]
        ok = lo <= v <= hi
        if not ok:
            fails.append(k)
        print(f"{k:<18}{'':>9}{v:>9.3f}{'':>9}   [{lo:>6.2f},{hi:>7.2f}]  {'':>16}  "
              f"{'' if ok else 'FAIL'}")
    print("-" * 84)
    if fails:
        print(f"OUT OF HUMAN RANGE: {', '.join(fails)}")
        sys.exit(1)
    print(f"all metrics within human range ({N_PERSONAS} personas, each gated individually)")


if __name__ == "__main__":
    main(int(sys.argv[sys.argv.index("--seed") + 1]) if "--seed" in sys.argv else 20261002)
