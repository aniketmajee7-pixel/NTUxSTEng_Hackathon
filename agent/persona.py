"""Per-session persona and pacing state (pure, stdlib only).

Detectors that fit population distributions flag identical per-run parameters, so typing
speed, hold, error rate, Fitts coefficients, overshoot and tremor are drawn ONCE per run from
human-plausible ranges, and a slow fatigue drift plus an Ornstein-Uhlenbeck pace term vary
them over the 15-minute session.
"""
import math
import random
import time

OU_RHO, OU_SD = 0.85, 0.08  # OU pace in log-IKI; gives human lag-1 autocorr ~0.08

# Eight coarse typist clusters (mean IKI ms, hold-mean ms) loosely after Aalto 136M /
# "How We Type"; a run lands near one of them, then is jittered so no two runs match.
_CLUSTERS = [
    (178, 102), (198, 108), (214, 112), (228, 116),
    (240, 118), (262, 122), (288, 126), (320, 132),
]


class Persona:
    """Fixed traits for one session plus the live pace/fatigue state."""

    def __init__(self, seed=None):
        self.rng = random.Random(seed)
        r = self.rng
        base_iki, base_hold = r.choice(_CLUSTERS)
        self.iki_ms = base_iki * r.uniform(0.92, 1.08)          # press-to-press centre
        self.iki_sigma = r.uniform(0.42, 0.52)                  # log-space spread of IKI
        self.hold_ms = base_hold * r.uniform(0.93, 1.07)        # key-down duration centre
        self.hold_sigma = r.uniform(0.20, 0.26)                 # log-space spread of hold
        self.error_rate = r.uniform(0.03, 0.07)                 # typos per char
        self.uncorrected_frac = r.uniform(0.04, 0.12)           # share of typos left in
        self.fitts_a = r.uniform(0.045, 0.085)                  # Fitts intercept (s)
        self.fitts_b = r.uniform(0.085, 0.125)                  # Fitts slope (s / bit)
        self.overshoot_prob = r.uniform(0.18, 0.38)             # long-move overshoot chance
        self.tremor_amp = r.uniform(0.6, 1.4)                   # px, scaled by slowness
        self.correction_bias = r.uniform(0.7, 1.3)              # how many corrective submovements
        self.pause_scale = r.uniform(0.85, 1.25)                # multiplier on think/read gaps
        self.scroll_speed = r.uniform(0.8, 1.3)
        self.pace = 1.0                                         # live OU multiplier in IKI space
        self._pace_ln = 0.0
        self.start = time.time()

    def fatigue(self):
        """Slow 1.0 -> ~1.12 drift across a 15-min session; people slow down a little."""
        mins = (time.time() - self.start) / 60.0
        return 1.0 + min(0.12, 0.008 * mins) + 0.02 * math.sin(mins / 3.0)

    def step_pace(self):
        """Ornstein-Uhlenbeck random walk in log space; keeps lag-1 autocorrelation human."""
        self._pace_ln = OU_RHO * self._pace_ln + self.rng.gauss(0, OU_SD)
        self.pace = max(0.72, min(1.45, math.exp(self._pace_ln))) * self.fatigue()
        return self.pace


# One persona per process; other modules bind this object at import time.
PERSONA = Persona()
