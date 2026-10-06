"""Optional read-only diagnostic for the currently open Calc or Chrome window."""
import os
from pathlib import Path
import sys

if os.name != 'nt':
    raise SystemExit('Run this inside Windows')
from pywinauto import Desktop

pattern = r'.*Calc.*' if len(sys.argv) < 2 or sys.argv[1] == 'calc' else r'.* - Google Chrome$'
windows = Desktop(backend='uia').windows(title_re=pattern, visible_only=True)
if len(windows) != 1:
    raise SystemExit('Open exactly one matching application window first')
path = Path(__file__).with_name('ui-controls.txt')
with path.open('w', encoding='utf-8') as file:
    for control in [windows[0]]+windows[0].descendants(depth=6):
        info = control.element_info
        file.write(f'{info.control_type} | {control.window_text()} | {info.automation_id} | {control.rectangle()}\n')
print(f'Wrote {path}. Share this file if a named control cannot be found.')
