"""Pure mouse-path generation (no Win32), shared by the live agent and the judge-simulator.

`plan_move` returns [(dt, x, y)] samples for one pointer move: a primary min-jerk stroke with
an asymmetric velocity peak that bows to one side (the arm pivots at the elbow/wrist, so
aimed moves curve, mostly the same way for a given hand), occasional overshoot, 1-3
corrective sub-movements, correlated (AR(1)) tremor scaled up when slow, and jittered 8-16 ms
sampling so Chrome coalesces >=2 samples/frame. WindMouse/raw Bezier is deliberately not
used: it is among the most detectable generators per "Kinematic Features Are All You Need".
"""
import math

from persona import PERSONA


def _minjerk(p0, p1, dur, persona, rng, peak, bow=0.0, settle=False):
    x0, y0 = p0
    x1, y1 = p1
    dist = math.hypot(x1 - x0, y1 - y0)
    nx, ny = (-(y1 - y0) / dist, (x1 - x0) / dist) if dist > 1 else (0.0, 0.0)
    exp = math.log(0.5) / math.log(peak)
    out = []
    t = 0.0
    prev = (x0, y0)
    jx = jy = 0.0
    while t < dur:
        dt = rng.uniform(0.008, 0.016)
        t = min(dur, t + dt)
        tau = t / dur
        w = tau ** exp                                  # warp so the velocity peak sits near `peak`
        f = 10 * w ** 3 - 15 * w ** 4 + 6 * w ** 5
        side = bow * 4.0 * f * (1.0 - f)                # sideways bulge, largest mid-move
        x = x0 + (x1 - x0) * f + nx * side
        y = y0 + (y1 - y0) * f + ny * side
        v = math.hypot(x - prev[0], y - prev[1]) / dt
        slow = max(0.0, 1.0 - v / 2500.0)               # 0 fast .. 1 slow
        amp = persona.tremor_amp * (0.3 + slow)
        jx = 0.8 * jx + rng.gauss(0, amp * 0.6)         # tremor is smooth, not white noise
        jy = 0.8 * jy + rng.gauss(0, amp * 0.6)
        prev = (x, y)
        calm = 1.0 - 0.7 * tau ** 4 if settle else 1.0   # the hand steadies as it lands
        out.append((dt, x + jx * calm, y + jy * calm))
    return out


def plan_move(x0, y0, x, y, persona=None, rng=None):
    """Plan a human pointer move from (x0,y0) to near (x,y). Returns [(dt, px, py), ...]."""
    persona = persona or PERSONA
    rng = rng or persona.rng
    dist = math.hypot(x - x0, y - y0)
    if dist < 3:
        return []
    ids = math.log2(dist / 8.0 + 1.0)                   # Fitts index of difficulty
    total = (persona.fitts_a + persona.fitts_b * ids) * rng.uniform(0.85, 1.2) * persona.pace

    if dist > 180 and rng.random() < persona.overshoot_prob:
        over = rng.uniform(0.04, 0.12)
        px = x + (x - x0) * over
        py = y + (y - y0) * over
    else:
        px = x + rng.gauss(0, 3)
        py = y + rng.gauss(0, 3)

    side = persona.curve_side or rng.choice((-1, 1))   # no habitual side: either way
    if rng.random() < 0.12:
        side = -side                                    # the odd move bows the other way
    bow = dist * side * persona.curve * rng.lognormvariate(0, 0.5)   # never dead straight
    samples = _minjerk((x0, y0), (px, py), total * 0.78, persona, rng,
                       peak=rng.uniform(0.35, 0.45), bow=bow)
    pos = (px, py)

    # 1-3 corrective sub-movements; each covers most of the remaining error, the last one
    # nearly all of it. If the pointer is still visibly off (big overshoot), add one more -
    # people keep correcting until they are on the target.
    n_corr = max(1, min(3, int(round(rng.gauss(1.6 * persona.correction_bias, 0.7)))))
    k = 0
    while k < 4:
        err = math.hypot(x - pos[0], y - pos[1])
        if k and (err < 1.5 or (k >= n_corr and err <= 4.0)):   # always one correction
            break
        tx = x + rng.gauss(0, 1.2)                       # endpoint scatter: not the exact pixel
        ty = y + rng.gauss(0, 1.2)
        last = k >= n_corr - 1
        frac = rng.uniform(0.92, 1.0) if last else rng.uniform(0.7, 1.0)
        cx = pos[0] + (tx - pos[0]) * frac
        cy = pos[1] + (ty - pos[1]) * frac
        samples += _minjerk(pos, (cx, cy), max(0.04, total * 0.12), persona, rng, peak=0.5,
                            settle=last)
        pos = (cx, cy)
        k += 1
    return samples
