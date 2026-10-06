"""Pure pixel analysis for the agent's eyes (stdlib only, no Win32).

`screen.py` grabs a downscaled grayscale copy of a window region; these functions decide
what is on it:
  * `is_blank` - has the page painted yet (a loading tab is a flat white field)?
  * `text_spots` - text-like patches worth pointing at: a dense run of sharp light/dark
    transitions on a flat background; photos and flat panels are not.
The live layer confirms each spot with the real cursor shape (I-beam over text, hand over a
link) before clicking, so a false positive here only costs a glance, never a click.
`judge_sim.py` self-tests it on synthetic images off-VM.
"""
import random


def is_blank(w, h, gray, tol=4.0):
    """True if the image is (near) uniform - e.g. Chrome before first paint."""
    if not gray:
        return True
    sample = gray[::7] or gray
    m = sum(sample) / len(sample)
    var = sum((p - m) ** 2 for p in sample) / len(sample)
    return var ** 0.5 < tol


def _cell_score(w, gray, cx, cy, cw, ch):
    rows = [gray[y * w + cx: y * w + cx + cw] for y in range(cy, cy + ch)]
    lo, hi = min(map(min, rows)), max(map(max, rows))
    if hi - lo < 70:                                    # too flat to be text
        return 0.0
    mid = (lo + hi) / 2
    trans = dark = 0
    for row in rows:
        bits = [p < mid for p in row]
        trans += sum(a != b for a, b in zip(bits, bits[1:]))
        dark += sum(bits)
    frac = dark / (cw * ch)
    if not 0.08 <= frac <= 0.55:                        # text is mostly background
        return 0.0
    return trans / ch                                   # transitions per row


def text_spots(w, h, gray, cell=(12, 6), limit=24, sample=900, rng=None):
    """Up to `limit` (x, y) centres of text-like cells, best first, in image coordinates.

    Scores a random sample of grid cells to keep CPU low; a spot needs >= 3 light/dark
    transitions per row, real contrast, and a mostly-light background.
    """
    rng = rng or random
    cw, ch = cell
    cells = [(cx, cy) for cy in range(0, h - ch + 1, ch) for cx in range(0, w - cw + 1, cw)]
    if len(cells) > sample:
        cells = rng.sample(cells, sample)
    scored = []
    for cx, cy in cells:
        s = _cell_score(w, gray, cx, cy, cw, ch)
        if s >= 3.0:
            scored.append((s, cx + cw // 2, cy + ch // 2))
    scored.sort(reverse=True)
    return [(x, y) for _, x, y in scored[:limit]]


# ---------------------------------------------------------------- self-test

def _synthetic(rng, w=320, h=200):
    """White page with a block of fake text lines, a flat grey panel and a smooth gradient."""
    g = bytearray([245] * (w * h))
    text_box = (20, 20, 200, 110)                       # x0, y0, x1, y1
    for line_y in range(text_box[1], text_box[3] - 8, 14):
        x = text_box[0]
        while x < text_box[2] - 8:
            for _ in range(rng.randint(3, 8)):                       # glyph: 1-2 px dark stems
                for yy in range(line_y, line_y + 9):
                    for xx in range(x, x + rng.randint(1, 2)):
                        g[yy * w + xx] = rng.randint(20, 60)
                x += rng.randint(4, 6)
            x += 8                                      # space between words
    for yy in range(130, 190):                          # flat panel
        for xx in range(20, 140):
            g[yy * w + xx] = 180
    for yy in range(120, 190):                          # gradient (photo-ish, no edges)
        for xx in range(180, 300):
            g[yy * w + xx] = 60 + (xx - 180) * 1 + (yy - 120) // 2
    return w, h, bytes(g), text_box


def selftest(trials=20):
    """Return (precision, recall_ok, blank_ok) over synthetic pages."""
    rng = random.Random(7)
    hits = total = 0
    recall_ok = blank_ok = True
    for _ in range(trials):
        w, h, g, (x0, y0, x1, y1) = _synthetic(rng)
        spots = text_spots(w, h, g, rng=rng)
        recall_ok &= len(spots) >= 5
        for x, y in spots:
            total += 1
            hits += x0 <= x <= x1 and y0 <= y <= y1
        blank_ok &= not is_blank(w, h, g)
    blank_ok &= is_blank(100, 100, bytes([250] * 10000))
    return (hits / total if total else 0.0), recall_ok, blank_ok


if __name__ == "__main__":
    print(selftest())
