import sys
import random
from pathlib import Path
import tempfile
import unittest
import zipfile
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from domain import (Phase, FINAL, HEADERS, NS, safe_same_page_link, validate_inventory,
                    typing_pause, movement_profile, inventory_range,
                    pointer_path, fitts_duration, min_jerk, adjacent_keys, heavy_pause,
                    summarize_resources, find_grid, address_of, is_highlight, is_separator)
from fake_sheet import FakeSheet, BLUE


def fixture(path, budget=5000, mutate=None, order=None):
    root = ET.Element('{'+NS['o']+'}document-content')
    table = ET.SubElement(root, '{'+NS['t']+'}table')
    records = {1: [(x, '') for x in HEADERS]}
    for row, record in enumerate(order or FINAL, 2):
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
    def test_symbols_take_longer_than_letters(self):
        rng = random.Random(10)
        for _ in range(30):
            self.assertGreater(typing_pause(':', rng=rng), typing_pause('a', rng=rng))

    def test_movement_reaches_target_with_variable_pace(self):
        profile = movement_profile(16, 0.8, random.Random(10))
        self.assertEqual(profile[-1][0], 1.0)
        self.assertAlmostEqual(sum(delay for _, delay in profile), 0.8)
        self.assertGreater(len(set(delay for _, delay in profile)), 1)
        self.assertTrue(all(a[0] < b[0] for a, b in zip(profile, profile[1:])))

    def test_keyboard_navigation_is_bounded(self):
        self.assertEqual(inventory_range('E11:F16'), (4, 10, 5, 15))
        self.assertEqual(inventory_range('A1'), (0, 0, 0, 0))
        self.assertEqual(inventory_range('J8'), (9, 7, 9, 7))  # notes column
        for invalid in ('A0', 'A17', 'K1', 'H1:A1', 'A8:A2', 'A1;B2'):
            with self.assertRaises(ValueError):
                inventory_range(invalid)

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

    def test_rows_moved_without_their_formulas_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'inventory.ods'
            def swap(records):
                records[2], records[3] = records[3], records[2]     # formulas still point at the old rows
            fixture(path, mutate=swap)
            with self.assertRaises(ValueError):
                validate_inventory(path, 5000)

    def test_entry_order_is_valid_and_reported_as_not_sorted(self):
        """Sorting is a nicety: if the sort was skipped the workbook is still correct."""
        entry = sorted(FINAL, key=lambda record: record[0])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'inventory.ods'
            fixture(path, order=entry)
            report = validate_inventory(path, 5000)
            self.assertFalse(report['sorted_by_item'])
            self.assertEqual(report['total_sgd'], 3486)
            fixture(path)
            self.assertTrue(validate_inventory(path, 5000)['sorted_by_item'])

    def test_a_record_under_the_wrong_serial_number_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'inventory.ods'
            fixture(path, mutate=lambda r: r[4].__setitem__(1, ('Tablet', '')))
            with self.assertRaises(ValueError):
                validate_inventory(path, 5000)
            fixture(path, mutate=lambda r: r[4].__setitem__(0, ('2', '')))     # only a digit landed
            with self.assertRaises(ValueError):
                validate_inventory(path, 5000)


class GridFromPixelsTests(unittest.TestCase):
    """OpenOffice shows no cells to UI Automation, so the sheet is found on screen."""

    def find(self, sheet):
        return find_grid(sheet.pixel, 0, 0, sheet.width-1, sheet.height-1)

    def test_colours_measured_in_the_vm_are_told_apart(self):
        self.assertTrue(is_highlight((67, 115, 213)))
        for other in ((247, 247, 247), (105, 105, 105), (255, 255, 255), (190, 210, 240), (0, 0, 0)):
            self.assertFalse(is_highlight(other), other)       # incl. the pale tint of selected cells
        self.assertTrue(is_separator((105, 105, 105)))
        self.assertFalse(is_separator((67, 115, 213))); self.assertFalse(is_separator((247, 247, 247)))

    def test_columns_and_rows_are_found_with_a1_selected(self):
        sheet = FakeSheet()
        grid = self.find(sheet)
        self.assertEqual(len(grid.cols), 13); self.assertEqual(len(grid.rows), 33)
        self.assertEqual(grid.cols[0], (33, 112))                  # column A: 80 px between lines
        self.assertEqual(grid.cols[1][0]-grid.cols[0][0], 81)
        self.assertEqual(grid.rows[1][0]-grid.rows[0][0], 17)
        self.assertEqual(grid.header, (144, 158))
        for col in range(10):
            for row in (1, 2, 8, 16):
                left, top, right, bottom = grid.box(col, row)
                self.assertEqual((left, top), (sheet.column_left(col), sheet.row_top(row)))

    def test_click_points_always_fall_inside_their_cell(self):
        grid = self.find(FakeSheet())
        rng = random.Random(4)
        for _ in range(300):
            col, row = rng.randrange(10), rng.randrange(1, 17)
            x, y = grid.point(col, row, rng)
            left, top, right, bottom = grid.box(col, row)
            self.assertTrue(left < x < right and top < y < bottom)
        x, y = grid.row_header_point(9, rng)
        self.assertTrue(grid.side[0] <= x <= grid.side[1])
        self.assertTrue(grid.rows[8][0] <= y <= grid.rows[8][1])

    def test_selected_range_is_read_from_the_highlighted_headers(self):
        sheet = FakeSheet()
        grid = self.find(sheet)
        for selection, address in (((0, 0, 1, 1), 'A1'), ((0, 0, 3, 8), 'A3:A8'), ((4, 5, 2, 8), 'E2:F8'),
                                   ((7, 7, 2, 2), 'H2'), ((0, 7, 2, 8), 'A2:H8'), ((9, 9, 5, 5), 'J5')):
            sheet.select(*selection)
            self.assertEqual(grid.selection(sheet.pixel, sheet.pixel), selection)
            self.assertEqual(address_of(selection), address)

    def test_other_blue_things_on_screen_are_not_mistaken_for_the_sheet(self):
        sheet = FakeSheet()
        sheet.fill(0, 0, sheet.width-1, 30, (0, 120, 215))        # accent-coloured title bar
        sheet.fill(700, 60, 716, 76, BLUE)                        # a toolbar icon
        sheet.fill(1100, 400, 1116, 416, BLUE)                    # a ticked box in the sidebar
        grid = self.find(sheet)
        self.assertEqual(grid.cols[0], (33, 112))
        self.assertEqual(grid.rows[0][0], sheet.row_top(1))

    def test_a_header_sized_blue_block_elsewhere_does_not_hide_the_real_header(self):
        sheet = FakeSheet()
        sheet.fill(300, 90, 380, 104, BLUE)                       # e.g. a selected toolbar field
        sheet.fill(0, 110, 30, 124, BLUE)
        grid = self.find(sheet)
        self.assertIsNotNone(grid)
        self.assertEqual(grid.header, (144, 158))
        self.assertEqual(grid.cols[0], (33, 112))

    def test_gradient_headers_and_blended_edges_as_on_the_real_screen(self):
        """Frames of the VM showed a two-tone selected header whose last pixel is a
        blend; the first version stopped at that pixel and found one column."""
        sheet = FakeSheet(realistic=True)
        grid = self.find(sheet)
        self.assertIsNotNone(grid)
        self.assertEqual(len(grid.cols), 13); self.assertEqual(len(grid.rows), 33)
        self.assertEqual(grid.cols[1][0], sheet.column_left(1))
        self.assertEqual(grid.rows[1][0], sheet.row_top(2))
        for selection in ((0, 0, 1, 1), (3, 3, 4, 4), (0, 0, 3, 8), (4, 5, 2, 8), (6, 6, 8, 8)):
            sheet.select(*selection)
            self.assertEqual(grid.selection(sheet.pixel, sheet.pixel), selection)

    def test_something_covering_a_few_row_numbers_does_not_misnumber_the_rows_below(self):
        """In the recording a console handle covered rows 10 to 12 at the left edge;
        counting lines one by one then called row 16 "row 13"."""
        sheet = FakeSheet(realistic=True)
        sheet.fill(0, sheet.row_top(10)-2, 10, sheet.row_top(13)-3, (114, 114, 114))
        grid = self.find(sheet)
        self.assertEqual(len(grid.rows), 33)
        for row in (1, 2, 9, 10, 13, 16, 30):
            self.assertEqual(grid.rows[row-1][0], sheet.row_top(row), row)

    def test_a_sheet_with_uneven_columns_is_reported_as_found_not_forced_even(self):
        from domain import _regular
        uneven = [(33, 112), (114, 300), (302, 340), (342, 600), (602, 640), (642, 900)]
        self.assertEqual(_regular(uneven), uneven)
        even = [(34, 111), (114, 193), (195, 274), (276, 355), (357, 436)]
        self.assertEqual(_regular(even), [(33+81*i, 33+81*i+79) for i in range(5)])

    def test_other_sizes_and_zoom_levels_are_measured_not_assumed(self):
        sheet = FakeSheet(width=1920, height=1080, col_width=101, row_height=21,
                          header_top=180, header_bottom=198, side_right=40, columns=14, rows=38)
        grid = self.find(sheet)
        self.assertEqual(grid.cols[3][0], sheet.column_left(3))
        self.assertEqual(grid.rows[15][0], sheet.row_top(16))

    def test_no_sheet_or_a_different_selected_cell_gives_no_grid(self):
        blank = FakeSheet(); blank.fill(0, 0, blank.width-1, blank.height-1, (255, 255, 255))
        self.assertIsNone(self.find(blank))
        moved = FakeSheet(); moved.select(2, 2, 5, 5)             # C5, not A1: positions unknown
        self.assertIsNone(self.find(moved))

    def test_nothing_or_separate_blocks_selected_reads_as_unknown(self):
        sheet = FakeSheet()
        grid = self.find(sheet)
        sheet.select(1, 0, 1, 0)                                  # nothing highlighted
        self.assertIsNone(grid.selection(sheet.pixel, sheet.pixel))


class PointerModelTests(unittest.TestCase):
    def test_path_ends_on_target_and_starts_at_origin(self):
        for seed in range(30):
            rng = random.Random(seed)
            path = pointer_path((100, 100), (900, 500), 0.9, rng)
            self.assertLess(abs(path[0][1]-100)+abs(path[0][2]-100), 2)
            self.assertEqual(path[-1][1:], (900, 500))
            times = [t for t, _, _ in path]
            self.assertEqual(times, sorted(times))

    def test_sample_intervals_are_irregular_and_fast(self):
        path = pointer_path((0, 0), (800, 300), 1.0, random.Random(4))
        gaps = [b[0]-a[0] for a, b in zip(path, path[1:])]
        self.assertGreater(len(set(round(g, 4) for g in gaps)), 20)
        self.assertLess(sorted(gaps)[len(gaps)//2], 0.016)

    def test_velocity_peaks_early_with_slow_approach(self):
        path = pointer_path((0, 0), (1000, 0), 1.0, random.Random(8))
        speeds = [(abs(b[1]-a[1])/max(b[0]-a[0], 1e-6), a[0]) for a, b in zip(path, path[1:])]
        peak_time = max(speeds)[1]
        self.assertLess(peak_time, 0.5*path[-1][0])

    def test_longer_reaches_take_longer_and_smaller_targets_take_longer(self):
        rng = random.Random(1)
        mean = lambda d, w: sum(fitts_duration(d, w, 1.0, rng) for _ in range(200))/200
        self.assertLess(mean(80, 40), mean(900, 40))
        self.assertLess(mean(500, 200), mean(500, 12))

    def test_min_jerk_is_monotone_between_zero_and_one(self):
        values = [min_jerk(i/50) for i in range(51)]
        self.assertEqual((values[0], values[-1]), (0.0, 1.0))
        self.assertEqual(values, sorted(values))


class KeyboardTests(unittest.TestCase):
    def test_manual_recording_slip_is_a_neighbour(self):
        self.assertIn('w', adjacent_keys('a'))      # typed 'www.w' instead of 'www.a'

    def test_neighbours_are_symmetric_local_and_same_kind(self):
        for ch in 'abcdefghijklmnopqrstuvwxyz0123456789':
            near = adjacent_keys(ch)
            self.assertTrue(1 <= len(near) <= 6, (ch, near))
            self.assertNotIn(ch, near)
            for other in near:
                self.assertEqual(other.isdigit(), ch.isdigit())
                self.assertIn(ch, adjacent_keys(other), (ch, other))

    def test_unknown_characters_have_no_neighbours(self):
        self.assertEqual(adjacent_keys('.'), [])


class HeavyPauseTests(unittest.TestCase):
    def test_cv_is_above_workshop_guide_and_tail_is_capped(self):
        import statistics as st
        rng = random.Random(2)
        draws = [heavy_pause(1.5, 0.5, 0.4, 6.0, rng) for _ in range(3000)]
        self.assertGreater(st.pstdev(draws)/st.mean(draws), 0.4)
        self.assertGreaterEqual(min(draws), 0.4)
        self.assertLessEqual(max(draws), 6.0)
        self.assertLess(abs(st.median(draws)-1.5), 0.15)

    def test_repeated_pointer_move_varies_about_a_quarter(self):
        import statistics as st
        rng = random.Random(5)
        draws = [fitts_duration(500, 40, 1.0, rng) for _ in range(3000)]
        self.assertGreater(st.pstdev(draws)/st.mean(draws), 0.2)


class ResourceSummaryTests(unittest.TestCase):
    def test_mean_and_peak_are_reported(self):
        import tempfile
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'resources.csv'
            path.write_text('elapsed_seconds,agent_cpu_percent_total,working_set_mb\n'
                            '5,1.0,60\n10,3.0,64\n15,8.0,70\n')
            summary = summarize_resources(path)
        self.assertEqual((summary['cpu_percent_mean'], summary['cpu_percent_peak']), (4.0, 8.0))
        self.assertEqual((summary['memory_mb_mean'], summary['memory_mb_peak']), (64.7, 70.0))
        self.assertEqual(summary['samples'], 3)

    def test_missing_or_empty_file_gives_no_summary_instead_of_failing(self):
        import tempfile
        with tempfile.TemporaryDirectory() as folder:
            self.assertIsNone(summarize_resources(Path(folder)/'none.csv'))
            empty = Path(folder)/'resources.csv'
            empty.write_text('elapsed_seconds,agent_cpu_percent_total,working_set_mb\n')
            self.assertIsNone(summarize_resources(empty))


if __name__ == '__main__':
    unittest.main()
