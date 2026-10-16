"""Pure timing models shared by the live agent and the offline judge-simulator (no I/O).

The code that drives SendInput on the VM is the code `judge_sim.py` scores off-VM.

  * keystroke planning -> logical strokes with independent press-to-press IKI and hold, so
    keys overlap (rollover), OU pace drift and typo correction;
  * key scheduling -> those strokes as a physically consistent down/up event list: Shift is
    released before the next unshifted key (no "THe", no "https:?/"), and a key is never
    pressed again before it is released;
  * wheel scrolling -> `plan_scroll` flicks with ramping gaps and re-grip pauses;
  * macro gaps -> heavy-tailed think/read/break draws with occasional multi-minute lulls.
"""
import math
import re

from persona import PERSONA


# ---------------------------------------------------------------- keystrokes

def _ln(med, sigma, rng):
    return rng.lognormvariate(math.log(med), sigma)


def _ln_s(med_ms, sigma, rng):                  # median in ms, result in seconds
    return _ln(med_ms, sigma, rng) / 1000.0


def _resolve(persona, rng):
    persona = persona or PERSONA
    return persona, rng or persona.rng


_REF_IKI_MS = 240.0         # the average typist that motor gaps and pause tails are scaled from

# The digraph multipliers, lognormal spread and pause tails below all push the mean IKI above
# the base median. Scaling the base by this factor keeps the realised mean IKI (and so the
# effective WPM) at the persona's nominal value; judge_sim checks the ratio.
_IKI_NORM = 0.66
TAIL_WORD, TAIL_SENT, TAIL_SENT_MS = 0.12, 0.50, 900


# Big keys are held a little longer than letters (thumb on Space, pinky on Enter/Backspace).
_HOLD_EXTRA = {" ": 8.0, "\n": 15.0, "_back": 12.0, 0x20: 8.0, 0x0D: 15.0, 0x08: 12.0}


def hold_ms(persona=None, rng=None, key=None):
    """Key-down duration, right-skewed, floored at a physically plausible 40 ms. `key` is a
    char, a VK code or '_back'; big keys get a few ms more."""
    persona, rng = _resolve(persona, rng)
    return max(40.0, _ln(persona.hold_ms + _HOLD_EXTRA.get(key, 0.0), persona.hold_sigma, rng))


def click_hold_ms(persona=None, rng=None):
    """Mouse-button down time: around TUM's N(110, 30) ms, slightly below the key hold."""
    persona, rng = _resolve(persona, rng)
    return max(50.0, _ln(persona.hold_ms * 0.95, 0.24, rng))


def motor(persona=None):
    """Per-persona speed factor for motor gaps (1 = a 240 ms typist at average pace)."""
    persona = persona or PERSONA
    return math.sqrt(persona.iki_ms / _REF_IKI_MS) * math.sqrt(persona.pause_scale)


def _tail_scale(persona):                       # slower, more deliberate typists hesitate longer
    return persona.pause_scale * math.sqrt(persona.iki_ms / _REF_IKI_MS)


def flight_s(persona=None, rng=None):
    """Release-to-press gap before a lone tap or chord (Delete then Tab, Esc then Down)."""
    persona, rng = _resolve(persona, rng)
    return max(0.045, _ln_s(150 * motor(persona), 0.45, rng))


# QWERTY touch-typing fingers: 0-3 left pinky..index, 4-7 right index..pinky.
_FINGER = {}
for _f, _keys in enumerate(["qaz1", "wsx2", "edc3", "rfvtgb45", "yhnujm67", "ik,8", "ol.9", "p;/0-"]):
    for _k in _keys:
        _FINGER[_k] = _f


def _digraph(prev, c):
    """IKI multiplier for a letter pair: same key < alternate hands < same hand < same
    finger on a different key (cf. humaninput / 136M keystrokes)."""
    a, b = prev.lower(), c.lower()
    if a == b:
        return 0.85
    fa, fb = _FINGER.get(a), _FINGER.get(b)
    if fa is None or fb is None:
        return 1.0
    if fa == fb:
        return 1.45
    return 1.05 if (fa < 4) == (fb < 4) else 0.88


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
        mult, spread = _digraph(prev, c), sig
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
    tail = _tail_scale(persona)
    if prev == " " and rng.random() < TAIL_WORD:
        x += _ln(300 * tail, 0.7, rng)
    elif prev in ".!?" and rng.random() < TAIL_SENT:
        x += _ln(TAIL_SENT_MS * tail, 0.7, rng)
    elif prev == "," and rng.random() < 0.20:
        x += _ln(350 * tail, 0.6, rng)
    elif pa and ca and rng.random() < 0.010:
        x += _ln(300 * tail, 0.6, rng)
    return min(x, 6000.0)       # longer stalls are think pauses, modelled at task level


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


# Words a writer sometimes starts, thinks better of, and swaps (false start + Ctrl+Backspace).
ALTERNATIVES = {
    "cheapest": "lowest", "option": "choice", "strongest": "best", "compared": "looked",
    "check": "confirm", "models": "laptops", "within": "under", "should": "could",
    "think": "feel", "because": "since", "order": "buy", "budget": "limit", "update": "note",
    "quotes": "prices", "team": "group", "review": "meeting", "delivery": "shipping",
    "recommend": "suggest", "better": "nicer", "price": "cost", "probably": "likely",
}


def _slip(c, rng):
    w = rng.choice(NEIGHBOURS[c.lower()])
    return w.upper() if c.isupper() else w


def _typed(out, chars, first):
    """Append `chars` as 'char' actions, each timed after the one before (the first after `first`)."""
    for j, ch in enumerate(chars):
        out.append(("char", ch, first if j == 0 else chars[j - 1]))


def _logical_actions(text, persona, rng, exact=False, revise=False):
    """Expand `text` into [(action, data, ctx)] with typos and their corrections inserted.

    Slips are mostly neighbour-key substitutions, plus transpositions, doubled keys and
    dropped letters. About half are caught at once; the rest after 1-3 more characters
    (delayed detection), then backspaced over and retyped. `ctx` is the previous char for
    IKI timing, or "_react"/"_back"/"_retype"/"_restart" for a correction.
    With `exact`, every typo is corrected (URLs, filenames, cell values must land verbatim).
    With `revise`, a word in ALTERNATIVES is now and then begun as its alternative, erased
    with Ctrl+Backspace (no space typed, so the word edge is unambiguous) and replaced.
    """
    out = []
    n = len(text)
    i = 0
    while i < n:
        c = text[i]
        prev = text[i - 1] if i else None
        if revise and (i == 0 or not text[i - 1].isalnum()):
            word = re.match(r"[A-Za-z]+", text[i:])
            alt = word and ALTERNATIVES.get(word.group(0).lower())
            if alt and rng.random() < 0.3:
                k = min(len(alt), rng.randint(2, 5))
                _typed(out, alt[:k], prev)
                out.append(("ctrl_back", None, "_restart"))
                prev = "_retype"
        if c.lower() not in NEIGHBOURS or rng.random() >= persona.error_rate:
            out.append(("char", c, prev))
            i += 1
            continue
        r = rng.random()
        nxt = text[i + 1] if i + 1 < n else ""
        if r < 0.72 or not nxt:
            typed, used = [_slip(c, rng)], 1                    # neighbour key
        elif r < 0.86 and nxt.isalpha() and nxt != c:
            typed, used = [nxt, c], 2                           # transposition
        elif r < 0.94:
            typed, used = [c, _slip(c, rng)], 1                 # extra key brushed
        else:
            typed, used = [], 1                                 # dropped letter
        if not exact and typed and rng.random() < persona.uncorrected_frac:
            _typed(out, typed, prev)
            i += used
            continue
        # detection lag: more characters typed before the slip is noticed (not past a word)
        lag = 0
        if rng.random() > 0.5 or not typed:
            lag = 1 + min(2, int(rng.expovariate(1.2)))
        tail = text[i + used:i + used + lag]
        if " " in tail:
            tail = tail[:tail.index(" ")]
        if not typed and not tail:                              # nothing to notice it by
            out.append(("char", c, prev))
            i += 1
            continue
        seq = typed + list(tail)
        _typed(out, seq, prev)
        for j in range(len(seq)):
            out.append(("back", None, "_react" if j == 0 else "_back"))
        _typed(out, text[i:i + used + len(tail)], "_retype")
        i += used + len(tail)
    return out


def plan_typing(text, persona=None, rng=None, exact=False, revise=False):
    """Build a time-ordered plan of logical strokes for typing `text`.

    Samples press-to-press IKI and hold independently, so a key can go down before the
    previous one releases (rollover) and the effective WPM tracks the persona's nominal WPM.
    Advances the persona's OU/fatigue pace as it goes. Returns a list of `Stroke`.
    """
    persona, rng = _resolve(persona, rng)
    strokes = []
    t = 0.0
    tail = _tail_scale(persona)
    for action, data, ctx in _logical_actions(text, persona, rng, exact, revise):
        persona.step_pace()
        if ctx == "_react":
            gap = _ln(330 * tail, 0.45, rng)            # reaction time to the slip
        elif ctx == "_back":
            gap = _ln(105 * persona.pace, 0.3, rng)     # backspace run
        elif ctx == "_retype":
            gap = _ln(190 * tail, 0.4, rng)
        elif ctx == "_restart":
            gap = _ln(900 * tail, 0.6, rng)             # rethinking the word
        else:
            gap = iki_ms(ctx, data, persona, rng)
        t += gap / 1000.0
        key = "_back" if action != "char" else data
        strokes.append(Stroke(action, data, t, t + hold_ms(persona, rng, key) / 1000.0))
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
            td = push(td, down_until[key] + _ln_s(25, 0.4, rng))

        if shift_on and (shift is False or ctrl):
            t_rel = min(shift_end, td - _ln_s(14, 0.4, rng))
            t_rel = max(t_rel, last_shift_down + 0.012)
            td = push(td, t_rel + 0.004)
            add(t_rel, True, shift_key)
            shift_on, shift_free = False, t_rel

        if shift is True:
            if not shift_on:
                earliest = max(prev_down, shift_free) + 0.006
                ts = max(td - _ln_s(55, 0.4, rng), earliest)
                td = push(td, ts + 0.006)
                add(ts, False, shift_key)
                shift_on = True
            last_shift_down = td
            shift_end = max(shift_end, td + hold + _ln_s(25, 0.5, rng))

        if ctrl:
            tc = max(td - _ln_s(80, 0.4, rng), prev_down + 0.006, shift_free + 0.006)
            td = push(td, tc + 0.006)
            add(tc, False, ctrl_key)
            ctrl_free = td + hold + _ln_s(40, 0.4, rng)
            add(ctrl_free, True, ctrl_key)

        add(td, False, key)
        add(td + hold, True, key)
        down_until[key] = td + hold
        prev_down = td

    if shift_on:
        add(shift_end, True, shift_key)
    ev.sort(key=lambda e: (e[0], e[1]))
    return [(t, up, k) for t, _, up, k in ev]


# --------------------------------------------------------------- scrolling

def plan_scroll(notches, persona=None, rng=None):
    """Wheel plan for about `notches` ticks (sign = direction): [(delay_s, +1|-1)].

    A finger rolls the wheel in flicks of 1-6 ticks whose gaps lengthen as it slows
    (~25 -> 40 -> 60 ms), then lifts and re-grips (a few hundred ms) for the next flick.
    Longer scrolls now and then overshoot and come back a tick or two.
    """
    persona, rng = _resolve(persona, rng)
    sign = 1 if notches > 0 else -1
    left = abs(int(notches))
    sp = persona.scroll_speed
    out = []
    first = True
    while left > 0:
        k = min(left, max(1, min(6, int(round(_ln(2.8, 0.5, rng))))))
        for j in range(k):
            if j:
                gap = _ln(24 * 1.45 ** (j - 1) / sp, 0.3, rng)
            else:
                gap = 0.0 if first else _ln(380 / sp, 0.55, rng)
            out.append((gap / 1000.0, sign))
        first = False
        left -= k
    if abs(notches) >= 3 and rng.random() < 0.15:     # went past it: roll back a little
        for j in range(rng.randint(1, 2)):
            out.append((_ln_s(450 if j == 0 else 40, 0.4, rng), -sign))
    return out


# --------------------------------------------------------------- macro gaps

def short_s(med_s, sigma=0.35, rng=None):
    """A brief motor gap (e.g. between Ctrl+L and typing): lognormal, never uniform."""
    return _ln(med_s, sigma, rng or PERSONA.rng)


def think_s(scale=1.0, persona=None, rng=None):
    """A short between-action pause: lognormal, SD >= mean, persona-scaled."""
    persona, rng = _resolve(persona, rng)
    return _ln_s(700 * scale * persona.pause_scale, 1.0, rng)


def read_s(words=120, persona=None, rng=None):
    """Reading dwell for ~`words` of text at ~4 words/s, heavy-tailed, with a skim branch."""
    persona, rng = _resolve(persona, rng)
    if rng.random() < 0.33:                             # skimmed / bounced quickly
        return _ln_s(2500, 0.7, rng)
    centre = max(1500, words / 4.0 * 1000.0)
    return _ln_s(centre * persona.pause_scale, 0.8, rng)


def break_s(lo=20, hi=90, persona=None, rng=None):
    """A between-task break: heavy-tailed (SD > mean) around `lo`..`hi`, with rare 2-5 min lulls."""
    persona, rng = _resolve(persona, rng)
    if rng.random() < 0.05:                             # stepped away
        return min(330.0, max(120.0, _ln_s(180000, 0.3, rng)))
    med = (lo + hi) / 2.0 * 0.6 * persona.pause_scale
    return max(lo * 0.25, _ln_s(med * 1000.0, 0.85, rng))


def perceive_s(persona=None, rng=None):
    """~250-900 ms orienting delay before the first input after a window/page appears."""
    persona, rng = _resolve(persona, rng)
    return min(1.6, max(0.2, _ln_s(470, 0.35, rng)))


def review_s(persona=None, rng=None):
    """Looking a section over after a scroll. A human recording gave a median ~1.1 s (band
    0.7-1.7 s), SD close to the mean; about a quarter of the time something distinct holds the
    eye for 3-5 s."""
    persona, rng = _resolve(persona, rng)
    if rng.random() < 0.25:
        return min(5.5, max(2.6, _ln_s(3900, 0.18, rng)))
    return max(0.3, _ln_s(1100 * persona.pause_scale, 0.7, rng))


def dwell_s(persona=None, rng=None):
    """Holding the pointer over something that caught the eye: 4-8 s, lognormal."""
    persona, rng = _resolve(persona, rng)
    return min(10.0, max(3.2, _ln_s(5600 * persona.pause_scale, 0.22, rng)))
