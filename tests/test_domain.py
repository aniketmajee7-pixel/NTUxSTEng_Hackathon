import sys
from pathlib import Path
import tempfile
import unittest
import zipfile
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from domain import Phase, FINAL, HEADERS, NS, safe_same_page_link, validate_inventory


def fixture(path, budget=5000, mutate=None):
    root = ET.Element('{'+NS['o']+'}document-content')
    table = ET.SubElement(root, '{'+NS['t']+'}table')
    records = {1: [(x, '') for x in HEADERS]}
    for row, record in enumerate(FINAL, 2):
        sku, item, category, qty, price, reorder = record
        records[row] = [(sku, ''), (item, ''), (category, ''), (qty, ''), (price, ''),
                        (qty*price, f'of:=[.D{row}]*[.E{row}]'), (reorder, ''),
                        ('NO', f'of:=IF([.D{row}]<[.G{row}];"YES";"NO")')]
    stocks = [r[3]*r[4] for r in FINAL]
    for row, number, formula in [(11, sum(stocks), 'SUM'), (12, sum(stocks)/7, 'AVERAGE'),
                                  (13, min(stocks), 'MIN'), (14, max(stocks), 'MAX')]:
        records[row] = [('', '')]*5+[(number, f'of:={formula}([.F2:.F8])')]
    records[15] = [('', '')]*5+[(budget, '')]
    records[16] = [('', '')]*5+[('WITHIN BUDGET' if budget >= sum(stocks) else 'OVER BUDGET',
                               'of:=IF([.F11]<=[.F15];"WITHIN BUDGET";"OVER BUDGET")')]
    if mutate:
        mutate(records)
    for row in range(1, 17):
        node = ET.SubElement(table, '{'+NS['t']+'}table-row')
        for value, formula in records.get(row, []):
            cell = ET.SubElement(node, '{'+NS['t']+'}table-cell')
            if isinstance(value, (int, float)):
                cell.set('{'+NS['o']+'}value', str(value))
            else:
                ET.SubElement(cell, '{'+NS['x']+'}p').text = value
            if formula:
                cell.set('{'+NS['t']+'}formula', formula)
    with zipfile.ZipFile(path, 'w') as z:
        z.writestr('content.xml', ET.tostring(root))


class DomainTests(unittest.TestCase):
    def test_phase_boundaries_and_overruns(self):
        now = [100.0]
        phase = Phase(480, clock=lambda: now[0])
        for elapsed in (0, 120, 479.999):
            now[0] = 100+elapsed
            self.assertFalse(phase.expired)
        now[0] = 580
        self.assertTrue(phase.expired)
        now[0] += 5
        second = Phase(480, clock=lambda: now[0])
        now[0] += 479
        self.assertFalse(second.expired)
        now[0] += 1
        self.assertTrue(second.expired)
        self.assertGreaterEqual(now[0]-100, 960)

    def test_pilot_duration(self):
        now = [0]
        phase = Phase(120, clock=lambda: now[0])
        now[0] = 119
        self.assertEqual(phase.remaining, 1)
        now[0] = 120
        self.assertTrue(phase.expired)

    def test_known_fragment_links_only(self):
        page = 'https://www.apple.com/'
        self.assertTrue(safe_same_page_link(page, '#main'))
        self.assertTrue(safe_same_page_link(page, page+'#main'))
        for bad in ('', '/shop', 'https://evil.example/#main', 'javascript:alert(1)',
                    'https://www.apple.com/?search=x#main', page):
            self.assertFalse(safe_same_page_link(page, bad))

    def test_inventory_and_budget(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'inventory.ods'
            for budget in (5000, 3486, 1000):
                fixture(path, budget)
                report = validate_inventory(path, budget)
                self.assertEqual(report['total_sgd'], 3486)
                self.assertEqual(report['within_budget'], budget >= 3486)

    def test_reject_wrong_price(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'inventory.ods'
            fixture(path, mutate=lambda r: r[2].__setitem__(4, (999, '')))
            with self.assertRaises(ValueError):
                validate_inventory(path, 5000)

    def test_reject_wrong_formula_even_with_correct_cached_value(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'inventory.ods'
            fixture(path, mutate=lambda r: r[2].__setitem__(5, (450, 'of:=[.D3]*[.E3]')))
            with self.assertRaises(ValueError):
                validate_inventory(path, 5000)

    def test_reject_temporary_or_missing_row(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'inventory.ods'
            fixture(path, mutate=lambda r: r.__setitem__(9, [('INV-TEMP', '')]))
            with self.assertRaises(ValueError):
                validate_inventory(path, 5000)
            fixture(path, mutate=lambda r: r.pop(8))
            with self.assertRaises(ValueError):
                validate_inventory(path, 5000)

    def test_reject_unsorted_records(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'inventory.ods'
            def swap(records):
                records[2], records[3] = records[3], records[2]
            fixture(path, mutate=swap)
            with self.assertRaises(ValueError):
                validate_inventory(path, 5000)


if __name__ == '__main__':
    unittest.main()
