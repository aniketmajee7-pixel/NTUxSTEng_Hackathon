"""Pure timing models shared by the live agent and the offline judge-simulator (no I/O).

The code that drives SendInput on the VM is the code `judge_sim.py` scores off-VM.

  * keystroke planning -> logical strokes with independent press-to-press IKI and hold, so
    keys overlap (rollover), OU pace drift and typo correction;
  * key scheduling -> those strokes as a physically consistent down/up event list: Shift is
    released before the next unshifted key (no "THe", no "https:?/"), and a key is never
    pressed again before it is released;
  * macro gaps -> heavy-tailed think/read/break draws with occasional multi-minute lulls.
"""
import math

from persona import PERSONA


# ---------------------------------------------------------------- keystrokes

def _ln(med, sigma, rng):
    return rng.lognormvariate(math.log(med), sigma)


def _resolve(persona, rng):
    persona = persona or PERSONA
    return persona, rng or persona.rng


# The digraph multipliers, lognormal spread and pause tails below all push the mean IKI above
# the base median. Scaling the base by this factor keeps the realised mean IKI (and so the
# effective WPM) at the persona's nominal value; judge_sim checks the ratio.
_IKI_NORM = 0.71
TAIL_WORD, TAIL_SENT, TAIL_SENT_MS = 0.12, 0.50, 900


def hold_ms(persona=None, rng=None):
    """Key-down duration, right-skewed, floored at a physically plausible 40 ms."""
    persona, rng = _resolve(persona, rng)
    return max(40.0, _ln(persona.hold_ms, persona.hold_sigma, rng))


def iki_ms(prev, c, persona=None, rng=None, pace=None):
    """Press-to-press interval (ms) for typing char `c` after char `prev`.

    `prev` is None at the very start. `pace` is the live OU/fatigue multiplier; when omitted
    the persona's current pace is used. Digraph classes are scaled relative to the persona's
    base IKI rather than hard-coded, so a fast and a slow typist stay internally consistent.
    """
    persona, rng = _resolve(persona, rng)
    pace = persona.pace if pace is None else pace
    base = persona.iki_ms * pace * _IKI_NORM
    sig = persona.iki_sigma

    if prev is None:
        return _ln(max(base, 260), 0.5, rng)

    pa, ca = prev.isalpha(), c.isalpha()
    if pa and ca:
        mult, spread = (0.85 if prev.lower() == c.lower() else 1.0), sig
    elif pa and c == " ":
        mult, spread = 1.12, sig
    elif prev == " " and ca:
        mult, spread = 1.25, sig + 0.05             # word onset is slower
    elif pa and c == ",":
        mult, spread = 1.6, sig + 0.05
    elif pa and c in ".!?":
        mult, spread = 1.8, sig + 0.05
    elif prev == ",":
        mult, spread = 1.05, sig
    elif prev in ".!?":
        mult, spread = 1.6, sig                     # fresh thought after a sentence
    elif c.isdigit() or prev.isdigit():
        mult, spread = 1.15, sig
    else:
        mult, spread = 1.0, sig
    x = _ln(base * mult, spread, rng)

    # Heavy tails: word-finding, end-of-sentence thinking, mid-word hesitation. Lognormal
    # (finite variance, so per-run CV is stable) and scaled to the typist's speed.
    tail = persona.pause_scale * math.sqrt(persona.iki_ms / 240.0)
    if prev == " " and rng.random() < TAIL_WORD:
        x += _ln(300 * tail, 0.7, rng)
    elif prev in ".!?" and rng.random() < TAIL_SENT:
        x += _ln(TAIL_SENT_MS * tail, 0.7, rng)
    elif prev == "," and rng.random() < 0.20:
        x += _ln(350 * tail, 0.6, rng)
    elif pa and ca and rng.random() < 0.010:
        x += _ln(300 * tail, 0.6, rng)
    return min(x, 15000.0)


# Logical actions on the keystroke timeline. The live layer maps these to physical keys;
# the sim only needs their timings.
class Stroke:
    __slots__ = ("action", "data", "t_down", "t_up")

    def __init__(self, action, data, t_down, t_up):
        self.action = action        # 'char' | 'back' | 'ctrl_back'
        self.data = data            # the character for 'char', else None
        self.t_down = t_down        # seconds from burst start (key-down)
        self.t_up = t_up            # seconds (key-up); may follow the next key-down -> rollover


NEIGHBOURS = {
    "a": "qwsz", "b": "vghn", "c": "xdfv", "d": "serfcx", "e": "wsdr", "f": "drtgvc", "g": "ftyhbv",
    "h": "gyujnb", "i": "ujko", "j": "huikmn", "k": "jiolm", "l": "kop", "m": "njk", "n": "bhjm",
    "o": "iklp", "p": "ol", "q": "wa", "r": "edft", "s": "awedxz", "t": "rfgy", "u": "yhji",
    "v": "cfgb", "w": "qase", "x": "zsdc", "y": "tghu", "z": "asx",
}


def _logical_actions(text, persona, rng, exact=False):
    """Expand `text` into [(action, data, ctx)] with typos and their corrections inserted.

    `ctx` is the previous char for IKI timing, or "_react"/"_retype" for a correction.
    With `exact`, every typo is corrected (URLs, filenames, cell values must land verbatim).
    """
    out = []
    for i, c in enumerate(text):
        lc = c.lower()
        if lc in NEIGHBOURS and rng.random() < persona.error_rate:
            wrong = rng.choice(NEIGHBOURS[lc])
            wrong = wrong.upper() if c.isupper() else wrong
            out.append(("char", wrong, text[i - 1] if i else None))   # slip typed...
            if exact or rng.random() >= persona.uncorrected_frac:
                out.append(("back", None, "_react"))                   # ...noticed and fixed
                out.append(("char", c, "_retype"))
            continue
        out.append(("char", c, text[i - 1] if i else None))
    return out


def plan_typing(text, persona=None, rng=None, exact=False):
    """Build a time-ordered plan of logical strokes for typing `text`.

    Samples press-to-press IKI and hold independently, so a key can go down before the
    previous one releases (rollover) and the effective WPM tracks the persona's nominal WPM.
    Advances the persona's OU/fatigue pace as it goes. Returns a list of `Stroke`.
    """
    persona, rng = _resolve(persona, rng)
    strokes = []
    t = 0.0
    for action, data, ctx in _logical_actions(text, persona, rng, exact):
        persona.step_pace()
        if action == "back":
            gap = max(60.0, rng.gauss(260, 90))         # reaction time to the slip
        elif ctx == "_retype":
            gap = max(60.0, rng.gauss(140, 50))
        else:
            gap = iki_ms(ctx, data, persona, rng)
        t += gap / 1000.0
        strokes.append(Stroke(action, data, t, t + hold_ms(persona, rng) / 1000.0))
    return strokes


def schedule_keys(strokes, resolve, shift_key, ctrl_key, rng):
    """Turn logical strokes into a physically consistent [(t, is_up, key)] event list.

    `resolve(stroke) -> (key, shift, ctrl)`: `key` is any hashable physical-key id; `shift` is
    True/False, or None when the key ignores Shift (Unicode packets); `ctrl` is a bool.

    Guarantees, so rollover never changes what gets typed:
      * every key-down sees exactly the Shift/Ctrl state it needs - a typist lets go of Shift
        just before the next unshifted key rather than holding it across;
      * Shift goes down after the previous key-down, so it never re-cases that key;
      * a physical key is never pressed again while it is still down.
    Where honouring that needs a few ms, this and all later events shift right.
    """
    ev = []

    def add(t, up, k):
        ev.append((t, len(ev), up, k))          # len(ev) breaks time ties in emit order

    off = 0.0
    down_until = {}             # key -> release time of its latest press
    prev_down = -1.0            # previous key-down time
    shift_on, shift_end, shift_free = False, 0.0, -1.0  # held? natural release? last release
    last_shift_down = -1.0      # down-time of the latest shifted key
    ctrl_free = -1.0            # no key may go down until Ctrl is released

    def push(td, need):
        nonlocal off
        if need > td:
            off += need - td
            return need
        return td

    for s in strokes:
        key, shift, ctrl = resolve(s)
        td = s.t_down + off
        hold = s.t_up - s.t_down
        td = push(td, ctrl_free + 0.01)
        if key in down_until:                           # same key: wait for its release
            td = push(td, down_until[key] + _ln(25, 0.4, rng) / 1000.0)

        if shift_on and (shift is False or ctrl):
            t_rel = min(shift_end, td - _ln(14, 0.4, rng) / 1000.0)
            t_rel = max(t_rel, last_shift_down + 0.012)
            td = push(td, t_rel + 0.004)
            add(t_rel, True, shift_key)
            shift_on, shift_free = False, t_rel

        if shift is True:
            if not shift_on:
                earliest = max(prev_down, shift_free) + 0.006
                ts = max(td - _ln(55, 0.4, rng) / 1000.0, earliest)
                td = push(td, ts + 0.006)
                add(ts, False, shift_key)
                shift_on = True
            last_shift_down = td
            shift_end = max(shift_end, td + hold + _ln(25, 0.5, rng) / 1000.0)

        if ctrl:
            tc = max(td - _ln(80, 0.4, rng) / 1000.0, prev_down + 0.006, shift_free + 0.006)
            td = push(td, tc + 0.006)
            add(tc, False, ctrl_key)
            ctrl_free = td + hold + _ln(40, 0.4, rng) / 1000.0
            add(ctrl_free, True, ctrl_key)

        add(td, False, key)
        add(td + hold, True, key)
        down_until[key] = td + hold
        prev_down = td

    if shift_on:
        add(shift_end, True, shift_key)
    ev.sort(key=lambda e: (e[0], e[1]))
    return [(t, up, k) for t, _, up, k in ev]


# --------------------------------------------------------------- macro gaps

def short_s(med_s, sigma=0.35, rng=None):
    """A brief motor gap (e.g. between Ctrl+L and typing): lognormal, never uniform."""
    return _ln(med_s, sigma, rng or PERSONA.rng)


def think_s(scale=1.0, persona=None, rng=None):
    """A short between-action pause: lognormal, SD >= mean, persona-scaled."""
    persona, rng = _resolve(persona, rng)
    return _ln(700 * scale * persona.pause_scale, 1.0, rng) / 1000.0


def read_s(words=120, persona=None, rng=None):
    """Reading dwell for ~`words` of text at ~4 words/s, heavy-tailed, with a skim branch."""
    persona, rng = _resolve(persona, rng)
    if rng.random() < 0.33:                             # skimmed / bounced quickly
        return _ln(2500, 0.7, rng) / 1000.0
    centre = max(1500, words / 4.0 * 1000.0)
    return _ln(centre * persona.pause_scale, 0.8, rng) / 1000.0


def break_s(lo=20, hi=90, persona=None, rng=None):
    """A between-task break: heavy-tailed (SD > mean) around `lo`..`hi`, with rare 2-5 min lulls."""
    persona, rng = _resolve(persona, rng)
    if rng.random() < 0.05:                             # stepped away
        return min(330.0, max(120.0, _ln(180000, 0.3, rng) / 1000.0))
    med = (lo + hi) / 2.0 * 0.6 * persona.pause_scale
    return max(lo * 0.25, _ln(med * 1000.0, 0.85, rng) / 1000.0)


def perceive_s(persona=None, rng=None):
    """~250-900 ms orienting delay before the first input after a window/page appears."""
    persona, rng = _resolve(persona, rng)
    return min(1.6, max(0.2, _ln(470, 0.35, rng) / 1000.0))
