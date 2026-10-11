"""Optional diagnostics for the currently open Calc or Chrome window.

    python inspect_ui.py calc      list the controls Calc exposes        -> ui-controls.txt
    python inspect_ui.py chrome    list the controls Chrome exposes      -> ui-controls.txt
    python inspect_ui.py grid      check that the Calc grid can be read from the screen
                                   (presses Ctrl+Home in Calc; saves calc-screen.bmp)

Open exactly one window of the application first.  The `grid` check takes a few
seconds and tells you whether the agent will be able to click cells with the mouse."""
import os
from pathlib import Path
import struct
import sys
import time

if os.name != 'nt':
    raise SystemExit('Run this inside Windows')
from pywinauto import Desktop

mode = sys.argv[1] if len(sys.argv) > 1 else 'calc'
pattern = r'.* - Google Chrome$' if mode == 'chrome' else r'.*Calc.*'
windows = Desktop(backend='uia').windows(title_re=pattern, visible_only=True)
if len(windows) != 1:
    raise SystemExit('Open exactly one matching application window first')
folder = Path(__file__).parent

if mode != 'grid':
    path = folder/'ui-controls.txt'
    with path.open('w', encoding='utf-8') as file:
        for control in [windows[0]]+windows[0].descendants(depth=12):
            info = control.element_info
            file.write(f'{info.control_type} | {control.window_text()} | {info.automation_id} | {control.rectangle()}\n')
    print(f'Wrote {path}. Share this file if a named control cannot be found.')
    raise SystemExit(0)

import pyautogui as pg
import win32gui
import win32ui
from domain import find_grid, address_of

window = windows[0]
window.set_focus(); window.maximize()
time.sleep(1.0)
pg.hotkey('ctrl', 'home')                      # select A1, as the agent does
time.sleep(1.0)
rect = window.rectangle()
screen_width, screen_height = pg.size()
left, top = max(0, rect.left), max(0, rect.top)
width, height = min(screen_width, rect.right)-left, min(screen_height, rect.bottom)-top

device = win32gui.GetWindowDC(0)
source = win32ui.CreateDCFromHandle(device)
memory = source.CreateCompatibleDC()
bitmap = win32ui.CreateBitmap()
bitmap.CreateCompatibleBitmap(source, width, height)
memory.SelectObject(bitmap)
memory.BitBlt((0, 0), (width, height), source, (left, top), 0x00CC0020)
bits = bitmap.GetBitmapBits(True)
memory.DeleteDC(); source.DeleteDC(); win32gui.ReleaseDC(0, device)
win32gui.DeleteObject(bitmap.GetHandle())
stride = len(bits)//height


def pixel(x, y):
    index = (int(y)-top)*stride+(int(x)-left)*4
    return bits[index+2], bits[index+1], bits[index]


picture = folder/'calc-screen.bmp'
with picture.open('wb') as file:               # top-down 32-bit bitmap of what was read
    file.write(b'BM'+struct.pack('<IHHI', 54+len(bits), 0, 0, 54))
    file.write(struct.pack('<IiiHHIIiiII', 40, width, -height, 1, 32, 0, len(bits), 2835, 2835, 0, 0))
    file.write(bits)

grid = find_grid(pixel, left, top, left+width-1, top+height-1)
if grid is None:
    print('NOT FOUND: no sheet with cell A1 selected was recognised.')
    print('The agent will use the keyboard to move between cells instead of the mouse.')
    print(f'Please send {picture} so the colours can be checked.')
    raise SystemExit(1)
print(f'Grid found: {len(grid.cols)} columns and {len(grid.rows)} rows are visible.')
print(f'  column letters at y={grid.header}, row numbers at x={grid.side}')
print(f'  column A: x={grid.cols[0]}   column B: x={grid.cols[1]}')
print(f'  row 1: y={grid.rows[0]}   row 2: y={grid.rows[1]}')
print(f'  header colour {pixel(grid.cols[0][0]+2, grid.header_y)}, '
      f'unselected header {pixel(grid.cols[1][0]+2, grid.header_y)}')
selection = grid.selection(pixel, pixel)
print(f'  selected cell read from the screen: {address_of(selection) if selection else "unknown"}')
enough = len(grid.cols) >= 10 and len(grid.rows) >= 16
print('OK: the agent can click cells with the mouse.' if enough and selection == (0, 0, 1, 1)
      else 'PROBLEM: the window is too small or the selection was not read as A1. '
           f'Please send {picture}.')
