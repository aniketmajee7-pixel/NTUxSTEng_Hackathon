"""Pure scheduling and ODS validation; does not import GUI libraries."""
import math
import random
import re
import time
import zipfile
import xml.etree.ElementTree as ET
from urllib.parse import urlsplit, urljoin

HEADERS = ('SKU', 'Item', 'Category', 'Quantity', 'Unit Price SGD',
           'Stock Value SGD', 'Reorder Level', 'Reorder Needed')
INITIAL = [
    ('INV-001', 'Keyboard', 'Accessories', 12, 35, 5),
    ('INV-002', 'Mouse', 'Accessories', 20, 18, 8),
    ('INV-003', 'Monitor', 'Displays', 6, 220, 3),
    ('INV-004', 'USB Hub', 'Accessories', 4, 28, 5),
    ('INV-005', 'Webcam', 'Accessories', 8, 65, 4),
    ('INV-006', 'Headset', 'Audio', 10, 45, 5),
]
FINAL = [tuple(9 if i == 3 and r[0] == 'INV-004' else
               210 if i == 4 and r[0] == 'INV-003' else v
               for i, v in enumerate(r)) for r in INITIAL]
FINAL.append(('INV-007', 'Laptop Stand', 'Accessories', 7, 32, 3))
FINAL.sort(key=lambda r: r[1])


class Phase:
    def __init__(self, seconds, clock=time.monotonic):
        if seconds <= 0:
            raise ValueError('Phase duration must be positive')
        self.clock = clock
        self.started = clock()
        self.deadline = self.started + seconds

    @property
    def remaining(self):
        return max(0, self.deadline - self.clock())

    @property
    def expired(self):
        return self.clock() >= self.deadline


def typing_pause(character, pace=1.0, rng=random):
    """Deliberately readable pilot pace, with extra time for symbol keys."""
    if character in ':/\\.?@-_=&;()"':
        return rng.uniform(0.35, 0.95)*pace
    if character.isspace():
        return rng.uniform(0.25, 0.65)*pace
    return rng.uniform(0.12, 0.30)*pace


def inventory_range(address):
    """Bound keyboard navigation to the demo's small worksheet area (A-H data, J notes)."""
    match = re.fullmatch(r'([A-J])(\d+)(?::([A-J])(\d+))?', address.upper())
    if not match:
        raise ValueError('Unsupported demo cell range')
    a, row, b, last = match.groups()
    x1, y1 = ord(a)-65, int(row)-1
    x2, y2 = ord(b or a)-65, int(last or row)-1
    if not (0 <= y1 <= y2 < 16 and x1 <= x2):
        raise ValueError('Cell range is outside the demo inventory')
    return x1, y1, x2, y2


# --- Reading the Calc grid from screen pixels ---------------------------------------
# OpenOffice exposes neither its cells nor the Name Box to UI Automation, so the sheet is
# located the way eyes do it: the headers of the selected cell's column and row are drawn
# in the highlight colour, and header cells are separated by dark one-pixel lines.
# Measured in the VM: highlight (67,115,213), header background 247, separators 105.

def is_highlight(pixel):
    r, g, b = pixel
    return b >= 150 and b-r >= 70 and b-g >= 40


def is_separator(pixel):
    return max(pixel) < 170


class Grid:
    """Screen geometry of the visible sheet: columns, rows and the two header bands."""

    def __init__(self, cols, rows, header, side):
        self.cols, self.rows = cols, rows        # [(left, right)], [(top, bottom)], inclusive
        self.header, self.side = header, side    # (top, bottom) of column letters; (left, right) of row numbers
        self.header_y = header[0]+1              # scan lines that miss the letters and digits
        self.side_x = side[0]+1

    def box(self, col, row):
        """Bounds of a cell: `col` counts from 0 (A), `row` from 1."""
        (left, right), (top, bottom) = self.cols[col], self.rows[row-1]
        return left, top, right, bottom

    def point(self, col, row, rng=random):
        """Somewhere comfortably inside a cell, not always its centre."""
        left, top, right, bottom = self.box(col, row)
        return (left+(right-left)*rng.uniform(0.25, 0.7), top+(bottom-top)*rng.uniform(0.3, 0.7))

    def row_header_point(self, row, rng=random):
        top, bottom = self.rows[row-1]
        left, right = self.side
        return (left+(right-left)*rng.uniform(0.35, 0.65), top+(bottom-top)*rng.uniform(0.3, 0.7))

    def selection(self, header_pixel, side_pixel):
        """(first col, last col, first row, last row) of the highlighted headers, or None.

        `header_pixel` must cover the line y=header_y across the columns and `side_pixel`
        the line x=side_x down the rows (two tiny screen reads)."""
        cols = [i for i, (left, _) in enumerate(self.cols)
                if is_highlight(header_pixel(left+2, self.header_y))]
        rows = [i+1 for i, (top, bottom) in enumerate(self.rows)
                if is_highlight(side_pixel(self.side_x, (top+bottom)//2))]
        if not cols or not rows:
            return None
        if cols != list(range(cols[0], cols[-1]+1)) or rows != list(range(rows[0], rows[-1]+1)):
            return None                          # several separate ranges: not something we make
        return cols[0], cols[-1], rows[0], rows[-1]


def _extent(pixel, x, y, dx, dy, low, high, test):
    """Walk from (x, y) in direction (dx, dy) while `test` holds; return the last coordinate."""
    while True:
        nx, ny = x+dx, y+dy
        if not (low <= (nx if dx else ny) <= high) or not test(pixel(nx, ny)):
            return x if dx else y
        x, y = nx, ny


def _highlight_blocks(pixel, left, top, right, bottom, min_run, max_run, step):
    """Solid highlight rectangles in a region, top to bottom: (l, t, r, b) each."""
    seen = []
    for y in range(top, bottom+1, 3):
        x = left
        while x <= right:
            if not is_highlight(pixel(x, y)):
                x += step
                continue
            run_start = x
            while x <= right and is_highlight(pixel(x, y)):
                x += step
            if x-run_start < min_run or any(l <= run_start <= r and t <= y <= b for l, t, r, b in seen):
                continue
            probe = min(run_start+1, right)                     # near the left edge, clear of the glyph
            block_top = _extent(pixel, probe, y, 0, -1, top, bottom, is_highlight)
            block_bottom = _extent(pixel, probe, y, 0, 1, top, bottom, is_highlight)
            line = min(block_top+1, block_bottom)               # above the glyph
            block_left = _extent(pixel, probe, line, -1, 0, left, right, is_highlight)
            block_right = _extent(pixel, probe, line, 1, 0, left, right, is_highlight)
            seen.append((block_left, block_top, block_right, block_bottom))
            if (min_run <= block_right-block_left+1 <= max_run
                    and 8 <= block_bottom-block_top+1 <= 60):
                yield seen[-1]


def _cells_along(read, start, stop, first_end, limit):
    """Split a header band into cells at its dark separator lines.

    `read(position)` is the pixel on the scan line.  The first cell is the selected one;
    its end is already known from its highlight.  Gaps of a few pixels are not cells: on
    the real screen the edge of the highlight is blended, and a line can be two pixels
    thick."""
    cells = [(start, first_end)]
    begin = position = first_end+1
    while position <= stop and len(cells) < limit:
        if is_separator(read(position)):
            if position-begin >= 6:
                cells.append((begin, position-1))
            begin = position+1
        elif position-begin > 420:
            break                                   # no line for a long way: not a header band
        position += 1
    return cells


def _regular(cells):
    """Evenly spaced cells, rebuilt from the spacing most of them show.

    A fresh sheet has equal columns and equal rows.  If something on screen hides a few
    separator lines (a tooltip, an overlay), the cells found one by one would be
    misnumbered from there on; the regular spacing is not fooled by that.  A sheet
    that really is uneven is returned as it was found."""
    if len(cells) < 4:
        return cells
    starts = [start for start, _ in cells]
    gaps = sorted(b-a for a, b in zip(starts, starts[1:]))
    pitch = gaps[len(gaps)//2]
    if pitch < 8:
        return cells
    anchor = starts[1]-pitch          # the second cell begins right after a crisp line
    on_lattice = [start for start in starts if abs((start-anchor) % pitch) <= 2
                  or pitch-((start-anchor) % pitch) <= 2]
    if len(on_lattice) < 0.7*len(starts):
        return cells
    count = round((on_lattice[-1]-anchor)/pitch)+1
    return [(anchor+index*pitch, anchor+(index+1)*pitch-2) for index in range(count)]


def find_grid(pixel, left, top, right, bottom):
    """Locate the sheet inside the Calc window while cell A1 is selected.

    `pixel(x, y)` returns (r, g, b) for screen points within the given bounds.
    Returns a Grid, or None when the picture does not look like a sheet with A1 selected
    (the caller then falls back to the keyboard)."""
    limit = top+int((bottom-top)*0.6)
    # Anything blue could be a candidate (a toolbar state, a title bar): keep the first
    # one that has a highlighted row number directly below and to its left.
    for col_left, band_top, col_right, band_bottom in _highlight_blocks(
            pixel, left, top, right, limit, 20, 400, 4):
        for side_left, row_top, side_right, row_bottom in _highlight_blocks(
                pixel, left, band_bottom+1, col_left-1, min(bottom, band_bottom+80), 8, 150, 2):
            # A1 sits directly right of the row numbers and directly under the column letters.
            if not (0 <= col_left-side_right-1 <= 4 and 0 <= row_top-band_bottom-1 <= 4):
                continue
            cols = _cells_along(lambda x: pixel(x, band_top+1), col_left, right, col_right, 16)
            rows = _cells_along(lambda y: pixel(side_left+1, y), row_top, bottom, row_bottom, 60)
            if len(cols) >= 2 and len(rows) >= 2:
                return Grid(_regular(cols), _regular(rows),
                            (band_top, band_bottom), (side_left, side_right))
    return None


def address_of(selection):
    """'B2' or 'A3:A7' for a Grid.selection() tuple."""
    c1, c2, r1, r2 = selection
    first, last = f'{chr(65+c1)}{r1}', f'{chr(65+c2)}{r2}'
    return first if first == last else f'{first}:{last}'


def movement_profile(steps, duration, rng=random):
    weights = [rng.uniform(0.7, 1.4) for _ in range(steps)]
    total = sum(weights)
    elapsed = 0.0
    profile = []
    for weight in weights:
        delay = duration*weight/total
        elapsed += delay
        t = min(1.0, elapsed/duration)
        # Smooth acceleration and deceleration, with variable frame durations.
        profile.append((t*t*(3-2*t), delay))
    profile[-1] = (1.0, profile[-1][1])
    return profile


_KEY_ROWS = ('1234567890', 'qwertyuiop', 'asdfghjkl', 'zxcvbnm')


def adjacent_keys(char):
    """Keys physically next to `char` on a QWERTY keyboard.

    Rows are staggered: a key in a lower row sits between two keys of the row
    above, so 'a' touches 'q' and 'w' (the manual recording's one slip was 'w'
    for 'a').  Digits only neighbour digits and letters only letters."""
    char = char.lower()
    for r, row in enumerate(_KEY_ROWS):
        if char not in row:
            continue
        i = row.index(char)
        found = {row[j] for j in (i-1, i+1) if 0 <= j < len(row)}
        if r > 0:                      # row above: columns i and i+1
            above = _KEY_ROWS[r-1]
            found |= {above[j] for j in (i, i+1) if j < len(above)}
        if r+1 < len(_KEY_ROWS):       # row below: columns i-1 and i
            below = _KEY_ROWS[r+1]
            found |= {below[j] for j in (i-1, i) if 0 <= j < len(below)}
        return sorted(k for k in found if k != char and k.isdigit() == char.isdigit())
    return []


def min_jerk(t):
    """Smooth 0..1 profile used for human reaching movements."""
    t = min(1.0, max(0.0, t))
    return t**3*(10-15*t+6*t*t)


def summarize_resources(path):
    """Mean and peak of the agent's own CPU and memory from resources.csv, or None."""
    import csv
    try:
        with open(path, newline='') as handle:
            rows = list(csv.DictReader(handle))
        cpu = [float(r['agent_cpu_percent_total']) for r in rows]
        memory = [float(r['working_set_mb']) for r in rows]
    except (OSError, KeyError, ValueError):
        return None
    if not cpu:
        return None
    return {'samples': len(cpu), 'sample_interval_seconds': 5,
            'cpu_percent_mean': round(sum(cpu)/len(cpu), 2), 'cpu_percent_peak': round(max(cpu), 2),
            'memory_mb_mean': round(sum(memory)/len(memory), 1),
            'memory_mb_peak': round(max(memory), 1),
            'note': 'agent process only, as a share of all CPU cores; excludes Chrome and Calc'}


def heavy_pause(median, sigma=0.55, floor=0.0, cap=None, rng=random):
    """Pause drawn from a lognormal: mostly near `median`, occasionally much longer.

    Human delays are right-skewed, so a flat uniform range under-states their spread
    (a uniform band has a coefficient of variation around 0.25; this gives ~0.5)."""
    value = max(floor, median*rng.lognormvariate(0, sigma))
    return min(value, cap) if cap else value


def fitts_duration(distance, width=40.0, pace=1.0, rng=random):
    """Movement time grows with log2(distance/target width); noisy per movement.

    Measured in the manual recording: ~0.2-0.4 s for short hops and about one
    second for a cross-window trip to a small control."""
    index = math.log2(distance/max(width, 8.0)+1)
    return min(1.7, max(0.18, (0.17+0.15*index)*rng.lognormvariate(0, 0.25)*pace))


def pointer_path(start, end, duration, rng=random):
    """Timed pointer samples (t, x, y) for one human-like reach.

    Fast launch with a long slow approach, a shallow arc, optional
    overshoot-then-correct sub-movement, tremor, and irregular sample times
    (about 70-140 Hz) rather than evenly spaced steps."""
    sx, sy = start
    ex, ey = end
    dx, dy = ex-sx, ey-sy
    distance = math.hypot(dx, dy)
    if distance < 1.5:
        return [(0.0, sx, sy), (max(duration, 0.05), ex, ey)]
    ux, uy = dx/distance, dy/distance
    nx, ny = -uy, ux
    corrected = distance > 90 and rng.random() < 0.4
    miss = rng.uniform(-0.05, 0.06)*distance if corrected else 0.0
    tx, ty = ex+ux*miss+nx*rng.uniform(-0.01, 0.01)*distance*corrected, \
             ey+uy*miss+ny*rng.uniform(-0.01, 0.01)*distance*corrected
    main_time = duration*(0.8 if corrected else 1.0)
    bow = max(-0.1, min(0.1, rng.gauss(0, 0.04)))
    wobble, phase = rng.uniform(0.004, 0.012), rng.uniform(0, 6.283)
    samples = []
    t = 0.0
    while t < main_time:
        u = min(1.0, t/main_time)
        s = min_jerk(u**0.78)  # velocity peaks early; the tail is the slow approach
        arc = math.sin(math.pi*s)*(bow + wobble*math.sin(6.283*1.5*s+phase))  # zero at both ends
        x = sx+(tx-sx)*s+nx*arc*distance+rng.gauss(0, 0.3)
        y = sy+(ty-sy)*s+ny*arc*distance+rng.gauss(0, 0.3)
        samples.append((t, x, y))
        t += rng.uniform(0.007, 0.014)
    samples.append((main_time, tx, ty))
    if corrected:
        pause = rng.uniform(0.05, 0.14)
        fix_time = max(0.08, duration-main_time)
        t0 = main_time+pause
        t = 0.0
        while t < fix_time:
            s = min_jerk(t/fix_time)
            samples.append((t0+t, tx+(ex-tx)*s+rng.gauss(0, 0.2), ty+(ey-ty)*s+rng.gauss(0, 0.2)))
            t += rng.uniform(0.008, 0.016)
        samples.append((t0+fix_time, ex, ey))
    else:
        samples[-1] = (main_time, ex, ey)
    return samples


def safe_same_page_link(current, destination):
    """Only known same-page fragment destinations; never guesses a control."""
    if not destination:
        return False
    dest = urlsplit(urljoin(current, destination))
    source = urlsplit(current)
    return (dest.scheme in ('http', 'https') and bool(dest.fragment)
            and dest.scheme == source.scheme and dest.netloc == source.netloc
            and dest.path == source.path and dest.query == source.query)


NS = {'t': 'urn:oasis:names:tc:opendocument:xmlns:table:1.0',
      'o': 'urn:oasis:names:tc:opendocument:xmlns:office:1.0',
      'x': 'urn:oasis:names:tc:opendocument:xmlns:text:1.0'}


def ods_cells(path):
    """Read bounded rows/columns, including ODF repeat attributes."""
    with zipfile.ZipFile(path) as z:
        root = ET.fromstring(z.read('content.xml'))
    sheet = root.find('.//t:table', NS)
    if sheet is None:
        raise ValueError('ODS has no sheet')
    result = {}
    row_num = 1
    for row in sheet.findall('t:table-row', NS):
        repeat_rows = min(int(row.get('{'+NS['t']+'}number-rows-repeated', '1')),
                          30 - row_num + 1)
        if repeat_rows <= 0:
            break
        col = 1
        row_cells = {}
        for cell in row:
            if cell.tag not in ('{'+NS['t']+'}table-cell', '{'+NS['t']+'}covered-table-cell'):
                continue
            repeat = min(int(cell.get('{'+NS['t']+'}number-columns-repeated', '1')), 10-col+1)
            if repeat <= 0:
                break
            value = cell.get('{'+NS['o']+'}value')
            if value is None:
                value = '\n'.join(''.join(p.itertext()) for p in cell.findall('x:p', NS))
            formula = cell.get('{'+NS['t']+'}formula', '')
            for offset in range(repeat):
                row_cells[col+offset] = (value, formula)
            col += repeat
        for offset in range(repeat_rows):
            for col, value in row_cells.items():
                result[(row_num+offset, col)] = value
        row_num += repeat_rows
    return result


def validate_inventory(path, budget):
    cells = ods_cells(path)
    def value(row, col):
        return cells.get((row, col), ('', ''))[0]
    def number(row, col, expected):
        try:
            actual = float(value(row, col))
        except (TypeError, ValueError):
            raise ValueError(f'Missing numeric value at row {row}, column {col}')
        if not math.isclose(actual, expected, abs_tol=0.001):
            raise ValueError(f'Wrong value at row {row}, column {col}: {actual} != {expected}')
    for col, header in enumerate(HEADERS, 1):
        if value(1, col) != header:
            raise ValueError(f'Header mismatch at column {col}')
    all_skus = [v for (r, c), (v, _) in cells.items() if c == 1 and v.startswith('INV-')]
    if sorted(all_skus) != sorted(r[0] for r in FINAL):
        raise ValueError('Inventory must contain exactly seven final SKUs and no temporary record')
    # The records may be in entry order or sorted by Item: sorting is a nicety, the data
    # and the formulas are what must be right.  Each row is checked against its own SKU.
    by_sku = {r[0]: r for r in FINAL}
    order = []
    for row in range(2, 9):
        record = by_sku.get(value(row, 1))
        if record is None:
            raise ValueError(f'Wrong record at row {row}')
        sku, item, category, qty, price, reorder = record
        order.append(sku)
        for col, expected in ((2, item), (3, category)):
            if value(row, col) != expected:
                raise ValueError(f'Wrong record at row {row}')
        number(row, 4, qty); number(row, 5, price); number(row, 6, qty*price)
        number(row, 7, reorder)
        if value(row, 8) != ('YES' if qty < reorder else 'NO'):
            raise ValueError(f'Wrong reorder flag at row {row}')
        expected_formulas = {6: f'D{row}*E{row}',
                             8: f'IF(D{row}<G{row};"YES";"NO")'}
        for col, expected_formula in expected_formulas.items():
            if normalized_formula(cells[(row, col)][1]) != expected_formula:
                raise ValueError(f'Wrong formula at row {row}, column {col}')
    stocks = [r[3]*r[4] for r in FINAL]
    for row, expected in ((11, sum(stocks)), (12, sum(stocks)/7),
                          (13, min(stocks)), (14, max(stocks)), (15, budget)):
        number(row, 6, expected)
    for row, expected_formula in ((11, 'SUM(F2:F8)'), (12, 'AVERAGE(F2:F8)'),
                                  (13, 'MIN(F2:F8)'), (14, 'MAX(F2:F8)'),
                                  (16, 'IF(F11<=F15;"WITHINBUDGET";"OVERBUDGET")')):
        if normalized_formula(cells.get((row, 6), ('', ''))[1]) != expected_formula:
            raise ValueError(f'Wrong summary formula at F{row}')
    if value(16, 6) != ('WITHIN BUDGET' if sum(stocks) <= budget else 'OVER BUDGET'):
        raise ValueError('Budget comparison is incorrect')
    return {'records': 7, 'total_sgd': sum(stocks), 'budget_sgd': budget,
            'within_budget': sum(stocks) <= budget,
            'sorted_by_item': order == [r[0] for r in FINAL]}


def normalized_formula(formula):
    formula = formula.upper().replace(' ', '')
    formula = re.sub(r'^[A-Z0-9_]+:=', '', formula).lstrip('=')
    return formula.replace('[', '').replace(']', '').replace('.', '').replace('$', '')
