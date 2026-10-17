"""Pure pixel analysis for the agent's eyes (stdlib only, no Win32).

`screen.py` grabs a downscaled grayscale copy of a window region; these functions decide
what is on it:
  * `is_blank` - has the page painted yet (a loading tab is a flat white field)?
  * `text_spots` - text-like patches worth pointing at: a dense run of sharp light/dark
    transitions on a flat background; photos and flat panels are not.
  * `consent_button` - the accept button of a consent modal: a dark panel with a mid-tone
    button, or a bright centred card on a dimmed page (CNN's "Legal Terms and Privacy").
  * `warning_button` - the filled "Back to safety" button on Chrome's privacy warning; the
    caller looks for "Advanced" on the same row, at the left of the text column.
  * `same_view` - did a scroll move the page (the view is unchanged at the bottom)?
  * `calc_grid` / `highlighted` - where Calc's cells are on screen, and which column/row
    headers are lit (the selected cell). AOO exposes no cells to UI Automation (VM), so the
    agent finds them by eye, like a person, and clicks them.
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


def same_view(a, b, tol=1.5):
    """True if two grabs of the same region (equal size) show the same picture: mean absolute
    pixel difference under `tol` grey levels. A page that stopped moving after a wheel flick
    is at its end."""
    if len(a) != len(b) or not a:
        return False
    sa, sb = a[::5], b[::5]
    return sum(abs(x - y) for x, y in zip(sa, sb)) / len(sa) < tol


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


def consent_button(w, h, gray):
    """(x, y) of the accept button on a consent modal, or None: a dark panel with a mid-tone
    button (cookie banners), else a bright card on a dimmed page. The caller still checks the
    cursor turns into a hand before clicking."""
    if not gray or h < 10:
        return None
    return _panel_button(w, h, gray) or _card_button(w, h, gray)


def _panel_button(w, h, gray):
    """A large dark panel; its button is a solid mid-tone (e.g. blue) block in the panel's
    lower part, wide relative to the panel. Full-width dark page sections with a small pill
    button don't qualify."""
    dark_rows = [sum(1 for p in gray[y * w:(y + 1) * w] if p < 60) >= 0.25 * w
                 for y in range(h)]
    best, start = (0, 0), None
    for y, d in enumerate(dark_rows + [False]):
        if d and start is None:
            start = y
        elif not d and start is not None:
            if y - start > best[1] - best[0]:
                best = (start, y)
            start = None
    y0, y1 = best
    if y1 - y0 < 0.2 * h:
        return None
    cols = [sum(1 for y in range(y0, y1) if gray[y * w + x] < 60) for x in range(w)]
    xs = [x for x, n in enumerate(cols) if n >= 0.5 * (y1 - y0)]
    if not xs:
        return None
    x0, x1 = min(xs), max(xs) + 1
    if x1 - x0 > 0.9 * w:                               # a full-width dark section, not a modal
        return None
    pts = []
    for y in range(y0 + int((y1 - y0) * 0.55), y1):
        row = [x for x in range(x0, x1) if 95 <= gray[y * w + x] <= 200]
        if len(row) >= 0.15 * (x1 - x0):
            pts += [(x, y) for x in row]
    if len(pts) < 20:
        return None
    return (sum(x for x, _ in pts) // len(pts), sum(y for _, y in pts) // len(pts))


def _card(w, h, gray, bright=220, gap=45):
    """(x0, y0, x1, y1, bg) of a bright dialog card sitting on a dimmed page, or None. The
    card is the one tall run of rows holding a good share of near-white pixels, narrower than
    the page, with margins on both sides (and above/below, where there is room) darker than it
    by `gap`: an ordinary white page has no such margins."""
    best, start = (0, 0), None
    for y in range(h + 1):
        lit = y < h and sum(1 for p in gray[y * w:(y + 1) * w] if p >= bright) >= 0.15 * w
        if lit and start is None:
            start = y
        elif not lit and start is not None:
            if y - start > best[1] - best[0]:
                best = (start, y)
            start = None
    y0, y1 = best
    if not 0.15 * h <= y1 - y0 <= 0.85 * h:
        return None
    cols = [sum(1 for y in range(y0, y1) if gray[y * w + x] >= bright) for x in range(w)]
    xs = [x for x, n in enumerate(cols) if n >= 0.5 * (y1 - y0)]
    if not xs:
        return None
    x0, x1 = min(xs), max(xs) + 1
    if not 0.2 * w <= x1 - x0 <= 0.85 * w or x0 < 0.04 * w or w - x1 < 0.04 * w:
        return None
    lit = [gray[y * w + x] for y in range(y0, y1) for x in range(x0, x1) if gray[y * w + x] >= bright]
    bg = sum(lit) / len(lit)

    def mean(xa, xb, ya, yb):
        px = [gray[y * w + x] for y in range(ya, yb) for x in range(xa, xb)]
        return sum(px) / len(px)

    margins = [mean(0, x0, y0, y1), mean(x1, w, y0, y1)]
    if y0 >= 0.04 * h:
        margins.append(mean(0, w, 0, y0))
    if h - y1 >= 0.04 * h:
        margins.append(mean(0, w, y1, h))
    if max(margins) > bg - gap:
        return None
    return x0, y0, x1, y1, bg


def _card_button(w, h, gray):
    """The accept button of a bright card (see `_card`): the solid block of one flat tone in
    the card's lower half - unlike text, which is thin strokes spread over a wide box. Of
    several such blocks (Manage / Agree) the rightmost is taken, where the primary action
    sits. A label inside the button just punches holes in the block."""
    card = _card(w, h, gray)
    if not card:
        return None
    x0, y0, x1, y1, bg = card
    tones = {}
    for y in range(y0 + (y1 - y0) * 45 // 100, y1 - 1):
        for x in range(x0 + 1, x1 - 1):
            p = gray[y * w + x]
            if abs(p - bg) >= 60:
                tones.setdefault(p >> 3, []).append((x, y))
    found = []
    for pts in tones.values():
        if len(pts) < 25:
            continue
        xs, ys = sorted(x for x, _ in pts), sorted(y for _, y in pts)
        k = len(pts) // 20                              # trim stray pixels off the box
        bx0, bx1, by0, by1 = xs[k], xs[-1 - k], ys[k], ys[-1 - k]
        wide, tall = bx1 - bx0 + 1, by1 - by0 + 1
        if len(pts) >= 0.55 * wide * tall and wide >= 2 * tall and wide >= 0.1 * (x1 - x0):
            found.append(((bx0 + bx1) // 2, (by0 + by1) // 2))
    return max(found) if found else None


def warning_button(w, h, gray, min_w=30, max_w=130, min_h=5):
    """(x0, x1, y) of the widest solid mid-tone block on a white page, or None.

    Chrome's privacy warning is a white page with one filled blue button ("Back to safety",
    gray ~120 here) at the right end of the row that also holds "Advanced". Sizes are in
    image pixels (a 1/2 grab: the button is ~150x36 px on screen).
    """
    if not gray or h < min_h:
        return None
    done, live = [], []                                 # chains: [x0, x1, last_x0, last_x1, ys]
    for y in range(h):
        row = gray[y * w:(y + 1) * w]
        nxt, x = [], 0
        while x < w:
            if not 70 <= row[x] <= 190:
                x += 1
                continue
            v, x0 = row[x], x
            while x < w and abs(row[x] - v) <= 6:
                x += 1
            if min_w <= x - x0 <= max_w:                # a pill's rows drift a little at its ends
                c = next((c for c in live if abs(c[2] - x0) <= 3 and abs(c[3] - x) <= 3), None)
                if c:
                    live.remove(c)
                    c[0], c[1], c[2], c[3] = min(c[0], x0), max(c[1], x), x0, x
                    c[4].append(y)
                else:
                    c = [x0, x, x0, x, [y]]
                nxt.append(c)
        done += live                                    # chains that got no row here have ended
        live = nxt
    blocks = [c for c in done + live if min_h <= len(c[4]) <= 0.3 * (c[1] - c[0])]
    if len(blocks) != 1:                                # none, or a busy page: not the warning
        return None
    x0, x1, _, _, ys = blocks[0]
    return x0, x1, (ys[0] + ys[-1]) // 2


_BLUE = bytes(1 if v >= 60 else 0 for v in range(256))     # header highlight: b - r ~ 146


def _blue_blocks(w, h, mask):
    """Solid rectangles of the mask, as [x0, x1, y0, y1] (x1/y1 exclusive), via row runs.
    Gaps up to 10 px inside a run (a header's letter) are bridged."""
    done, live = [], []
    for y in range(h):
        row, nxt, x = mask[y * w:(y + 1) * w], [], 0
        while True:
            x0 = row.find(1, x)
            if x0 < 0:
                break
            while True:                                 # bridge the header's own label
                x = row.find(0, x0 if x <= x0 else x)
                x = w if x < 0 else x
                nx = row.find(1, x)
                if nx < 0 or nx - x > 10:
                    break
                x = nx
            c = next((c for c in live if abs(c[0] - x0) <= 1 and abs(c[1] - x) <= 1), None)
            if c:
                live.remove(c)
                c[3] = y + 1
            else:
                c = [x0, x, y, y + 1]
            nxt.append(c)
        done += live
        live = nxt
    return done + live


def _pitch(line, start, sep=170):
    """(pitch, count) of the dark separator lines along `line` from `start`: the cells' size
    in pixels and how many evenly spaced cells follow before the band ends."""
    seps, x = [], start
    while x < len(line):
        if line[x] < sep:
            seps.append(x)
            while x < len(line) and line[x] < sep:
                x += 1
        x += 1
    gaps = sorted(b - a for a, b in zip(seps, seps[1:]))
    if len(gaps) < 2:
        return None
    pitch = gaps[len(gaps) // 2]
    if pitch < 8:
        return None
    n = 1
    for a, b in zip(seps, seps[1:]):                    # count while the spacing stays regular
        if abs(b - a - pitch) > 2:
            break
        n += 1
    return pitch, n


def calc_grid(w, h, gray, blue):
    """Find the sheet in a full-resolution grab of the Calc window while A1 is selected.

    The selected cell's column header and row header are lit blue; A1's are the two blocks
    that touch corner to corner (row "1" directly left of and below column "A"). From there
    the header separators give the column and row pitch. Returns a dict in image pixels -
    x0/y0 (A1's top-left), cw/rh (pitch), ncol/nrow (visible cells), hy/sx (the header
    scan lines `highlighted` reads) - or None if the picture isn't a sheet with A1 selected.
    """
    if not gray or not blue:
        return None
    blocks = _blue_blocks(w, h, blue.translate(_BLUE))
    heads = [b for b in blocks if 30 <= b[1] - b[0] <= 250 and 8 <= b[3] - b[2] <= 40]
    sides = [b for b in blocks if 10 <= b[1] - b[0] <= 120 and 8 <= b[3] - b[2] <= 40]
    for cx0, cx1, cy0, cy1 in heads:
        for sx0, sx1, sy0, sy1 in sides:
            if not (-1 <= cx0 - sx1 <= 4 and -1 <= sy0 - cy1 <= 4):
                continue
            hy, sx = cy0 + 2, sx0 + 2                   # clear of the letters and digits
            col = _pitch(gray[hy * w:(hy + 1) * w], cx1 - 1)
            row = _pitch(bytes(gray[y * w + sx] for y in range(h)), sy1 - 1)
            if not col or not row:
                continue
            (cw, nc), (rh, nr) = col, row
            if abs(cw - (cx1 - cx0)) > 3 or abs(rh - (sy1 - sy0)) > 3:
                continue                                # lit block isn't one header cell
            return {"x0": cx0, "y0": sy0, "cw": cw, "rh": rh, "ncol": nc, "nrow": nr,
                    "hy": hy, "sx": sx}
    return None


def highlighted(blue_line, origin, pitch, count):
    """Indices of the lit header cells along a 1-px `blue_line` (header row or side column)."""
    return [i for i in range(count)
            if 0 <= origin + i * pitch + pitch // 2 < len(blue_line)
            and blue_line[origin + i * pitch + pitch // 2] >= 60]


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


def _consent_page(rng, w=320, h=200):
    """Dimmed page with a dark modal: text lines plus a blue accept button bottom-right."""
    g = bytearray([200] * (w * h))
    mx0, my0, mx1, my1 = 60, 30, 260, 180
    for yy in range(my0, my1):
        for xx in range(mx0, mx1):
            g[yy * w + xx] = 25
    for line_y in range(45, 110, 12):                   # white text on the panel
        for xx in range(mx0 + 15, mx1 - 20):
            if rng.random() < 0.4:
                for yy in range(line_y, line_y + 7):
                    g[yy * w + xx] = 250
    bx0, by0, bx1, by1 = 180, 145, 245, 168
    for yy in range(by0, by1):
        for xx in range(bx0, bx1):
            g[yy * w + xx] = 145
    return w, h, bytes(g), ((bx0 + bx1) // 2, (by0 + by1) // 2)


def _consent_card_page(rng, w=320, h=200):
    """A bright centred card on a page dimmed to ~45%: text lines, a flat button (blue or
    black, with a light label punched in) bottom-right or centred, sometimes next to a quiet
    outline button. Returns (w, h, gray, button centre)."""
    g = bytearray([110] * (w * h))                      # the dimmed page
    for _ in range(300):                                # its dimmed text
        x, y = rng.randrange(w - 6), rng.randrange(h)
        for xx in range(x, x + rng.randint(2, 6)):
            g[y * w + xx] = 14
    cx0, cx1 = rng.randint(55, 80), rng.randint(240, 265)
    cy0, cy1 = rng.randint(20, 35), rng.randint(165, 180)
    for yy in range(cy0, cy1):
        for xx in range(cx0, cx1):
            g[yy * w + xx] = 250
    for line_y in range(cy0 + 14, cy0 + 80, 12):        # the card's text
        for xx in range(cx0 + 15, cx1 - 15):
            if rng.random() < 0.4:
                for yy in range(line_y, line_y + 7):
                    g[yy * w + xx] = 40
    tone = rng.choice((35, 105, 140))                   # black / blue / grey fill
    bw, bh = rng.randint(55, 70), rng.randint(16, 22)
    bx1 = cx1 - 14 if rng.random() < 0.5 else (cx0 + cx1 + bw) // 2
    bx0, by0 = bx1 - bw, cy1 - 14 - bh
    for yy in range(by0, by0 + bh):
        for xx in range(bx0, bx1):
            g[yy * w + xx] = tone
    for xx in range(bx0 + 14, bx1 - 14):                # "Agree": light dashes on the fill
        if rng.random() < 0.6:
            for yy in range(by0 + bh // 2 - 3, by0 + bh // 2 + 3):
                g[yy * w + xx] = 245
    if bx0 - 90 > cx0 + 10 and rng.random() < 0.5:      # an outline "Manage": border only
        ox0 = bx0 - 80
        for xx in range(ox0, ox0 + 65):
            for yy in (by0, by0 + bh - 1):
                g[yy * w + xx] = 90
        for yy in range(by0, by0 + bh):
            for xx in (ox0, ox0 + 64):
                g[yy * w + xx] = 90
    return w, h, bytes(g), ((bx0 + bx1 - 1) // 2, (by0 + by0 + bh - 1) // 2)


def _warning_page(rng, w=640, h=380):
    """Chrome's privacy warning at 1/2 scale: heading/text lines, Advanced, a blue button."""
    g = bytearray([255] * (w * h))
    col = rng.randint(150, 180)                         # left edge of the 300 px text column
    for line_y in (60, 95, 115, 135):
        for xx in range(col, col + rng.randint(200, 290)):
            if rng.random() < 0.45:
                for yy in range(line_y, line_y + 8):
                    g[yy * w + xx] = 40
    by = rng.randint(170, 200)
    for xx in range(col + 5, col + 45):                 # "Advanced" (blue text, no fill)
        if rng.random() < 0.5:
            for yy in range(by + 5, by + 12):
                g[yy * w + xx] = 120
    bx1 = col + 300
    for yy in range(by, by + 18):                       # a pill: fully rounded ends
        dy = abs(yy - (by + 8.5))
        inset = int(round(9 - (81 - min(dy, 9) ** 2) ** 0.5))
        for xx in range(bx1 - 75 + inset, bx1 - inset):
            g[yy * w + xx] = 122
    return w, h, bytes(g), (bx1 - 75, bx1, by + 9)


# The selected headers on the VM (HANDOFF section 6, 10 Oct): blue from (97,168,233) to
# (60,120,212). As (gray, blue-minus-red) that is (166, 136) to (128, 152); the grab turns
# colour into gray as (b + 2g + r) / 4.
LIT_LIGHT, LIT_DARK = (166, 136), (128, 152)


def _calc_window(rng, w=1280, h=800, sel=(0, 0), lit=(LIT_LIGHT, LIT_DARK)):
    """An AOO Calc window: blue toolbar icon, header bands with separators and labels, the
    selected cell's headers lit - `lit` gives the (gray, blue) of the top and bottom rows of
    each lit header, with a gradient between (equal ends: a flat fill). Returns
    (w, h, gray, blue, truth)."""
    g, bl = bytearray([240] * (w * h)), bytearray(w * h)
    x0, y0 = rng.randint(30, 45), rng.randint(130, 160)  # A1's top-left
    cw, rh = rng.choice((81, 80, 88)), rng.choice((17, 18))
    hy0 = y0 - rh

    def fill(xa, xb, ya, yb, v, b=0):
        for yy in range(ya, yb):
            for xx in range(xa, xb):
                g[yy * w + xx], bl[yy * w + xx] = v, b

    def lit_fill(xa, xb, ya, yb):                       # a lit header: gradient top to bottom
        (g0, b0), (g1, b1) = lit
        for yy in range(ya, yb):
            f = (yy - ya) / max(1, yb - ya - 1)
            fill(xa, xb, yy, yy + 1, round(g0 + (g1 - g0) * f), round(b0 + (b1 - b0) * f))

    fill(300, 316, 60, 76, 110, 120)                    # a blue toolbar icon (decoy)
    for yy in range(y0, h - 40):                        # white sheet with light gridlines
        for xx in range(x0, w):
            g[yy * w + xx] = 192 if (xx - x0) % cw == cw - 1 or (yy - y0) % rh == rh - 1 else 255
    ncol, nrow = (w - x0) // cw, (h - 40 - y0) // rh
    for c in range(ncol):
        if c == sel[0]:
            lit_fill(x0 + c * cw, x0 + c * cw + cw - 1, hy0, y0)
        else:
            fill(x0 + c * cw, x0 + c * cw + cw - 1, hy0, y0, 240)
        fill(x0 + c * cw + cw - 1, x0 + c * cw + cw, hy0, y0, 105)              # separator
        fill(x0 + c * cw + cw // 2 - 3, x0 + c * cw + cw // 2 + 3, hy0 + 5, y0 - 4, 20)  # letter
    for r in range(nrow):
        if r == sel[1]:
            lit_fill(0, x0 - 1, y0 + r * rh, y0 + r * rh + rh - 1)
        else:
            fill(0, x0 - 1, y0 + r * rh, y0 + r * rh + rh - 1, 240)
        fill(0, x0 - 1, y0 + r * rh + rh - 1, y0 + r * rh + rh, 105)
        fill(x0 // 2 - 3, x0 // 2 + 3, y0 + r * rh + 4, y0 + r * rh + rh - 4, 20)       # digit
    return w, h, bytes(g), bytes(bl), (x0, y0, cw, rh)


def selftest(trials=20):
    """Return (precision, recall_ok, blank_ok, consent_ok, warning_ok, grid_ok, view_ok) over
    synthetic pages."""
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
    consent_ok = True
    for _ in range(5):
        w, h, g, (bx, by) = _consent_page(rng)
        spot = consent_button(w, h, g)
        consent_ok &= spot is not None and abs(spot[0] - bx) <= 6 and abs(spot[1] - by) <= 4
        w, h, g, _ = _synthetic(rng)
        consent_ok &= consent_button(w, h, g) is None   # ordinary page: nothing to click
    for _ in range(12):                                 # a bright centred card on a dimmed page
        w, h, g, (bx, by) = _consent_card_page(rng)
        spot = consent_button(w, h, g)
        consent_ok &= spot is not None and abs(spot[0] - bx) <= 6 and abs(spot[1] - by) <= 4
        w, h, g, _ = _warning_page(rng)
        consent_ok &= consent_button(w, h, g) is None   # a white page with one button: no modal
    warning_ok = True
    for _ in range(5):
        w, h, g, (x0, x1, y) = _warning_page(rng)
        got = warning_button(w, h, g)
        warning_ok &= got is not None and abs(got[0] - x0) <= 2 and abs(got[1] - x1) <= 2 \
            and abs(got[2] - y) <= 2
        w, h, g, _ = _synthetic(rng)
        warning_ok &= warning_button(w, h, g) is None   # ordinary page: no warning
        w, h, g, _ = _consent_page(rng)
        warning_ok &= warning_button(w, h, g) is None
    grid_ok = True
    for seed, lit in ((0, (LIT_LIGHT, LIT_LIGHT)), (1, (LIT_DARK, LIT_DARK)), (2, (LIT_LIGHT, LIT_DARK)),
                      (3, (LIT_DARK, LIT_LIGHT))):      # both ends of the VM's blue, and between
        w, h, g, bl, (x0, y0, cw, rh) = _calc_window(random.Random(seed), lit=lit)
        got = calc_grid(w, h, g, bl)
        grid_ok &= got is not None and (got["x0"], got["y0"], got["cw"], got["rh"]) == \
            (x0, y0, cw, rh) and got["ncol"] >= 10 and got["nrow"] >= 20
        if not got:
            continue
        w, h, g, bl, _ = _calc_window(random.Random(seed), sel=(1, 2), lit=lit)    # B3 selected
        grid_ok &= calc_grid(w, h, g, bl) is None       # needs A1 lit to calibrate
        hy, sx = got["hy"], got["sx"]
        grid_ok &= highlighted(bl[hy * w:(hy + 1) * w], x0, cw, got["ncol"]) == [1]
        grid_ok &= highlighted(bytes(bl[y * w + sx] for y in range(h)), y0, rh, got["nrow"]) == [2]
    w, h, g, _ = _synthetic(rng)
    grid_ok &= calc_grid(w, h, g, bytes(w * h)) is None
    w, h, g, _ = _synthetic(rng)
    w2, h2, g2, _ = _synthetic(rng)                     # another page; same size, other text
    shifted = g[w * 14:] + g[:w * 14]                   # the page scrolled by one text line
    noisy = bytes(min(255, p + rng.randint(0, 2)) for p in g)   # same view, a little sensor noise
    view_ok = same_view(g, g) and same_view(g, noisy) and not same_view(g, g2) \
        and not same_view(g, shifted) and not same_view(g, g[:-1]) and not same_view(b"", b"")
    return ((hits / total if total else 0.0), recall_ok, blank_ok, consent_ok, warning_ok, grid_ok,
            view_ok)


if __name__ == "__main__":
    print(selftest())
