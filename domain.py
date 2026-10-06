"""Pure scheduling and ODS validation; does not import GUI libraries."""
import math
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
    for row, record in enumerate(FINAL, 2):
        sku, item, category, qty, price, reorder = record
        for col, expected in ((1, sku), (2, item), (3, category)):
            if value(row, col) != expected:
                raise ValueError(f'Wrong record/order at row {row}')
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
            'within_budget': sum(stocks) <= budget}


def normalized_formula(formula):
    formula = formula.upper().replace(' ', '')
    formula = re.sub(r'^[A-Z0-9_]+:=', '', formula).lstrip('=')
    return formula.replace('[', '').replace(']', '').replace('.', '').replace('$', '')
