"""Run inside Windows. External dependencies: pyautogui, pywinauto, pywin32."""
import json
import logging
import os
from pathlib import Path
import sys
import time
import tkinter as tk
from tkinter import messagebox, simpledialog, ttk
import uuid


def launcher(config):
    root = tk.Tk()
    root.title('Windows Desktop Demo')
    root.resizable(False, False)
    choice = tk.StringVar(value='Full demo')
    ttk.Label(root, text='Choose a run. Full demo: 8 minutes browsing + 8 minutes inventory.', padding=12).pack()
    for name in ('Full demo', 'Browser pilot', 'Inventory pilot'):
        ttk.Radiobutton(root, text=name, variable=choice, value=name).pack(anchor='w', padx=20)
    ttk.Label(root, text='Close existing Chrome and Calc windows. Keep the VM console active.\n'
              'Desktop shortcuts must be named Google Chrome and OpenOffice Calc.\n'
              'F8 or moving the mouse to a screen corner stops the run.', padding=12).pack()
    selection = []
    def start():
        budget = simpledialog.askfloat('Inventory budget', 'Inventory budget in SGD:',
                                       initialvalue=config['default_budget_sgd'], minvalue=0, parent=root)
        if budget is not None:
            selection.extend([choice.get(), budget]); root.destroy()
    def calibrate():
        messagebox.showinfo('Name Box calibration',
            'Open Calc and maximize it first. After clicking OK, you have 8 seconds\n'
            'to hover over the Name Box at the left of the formula bar. Do not click.\n'
            'Keep this display resolution for subsequent runs.', parent=root)
        root.withdraw()
        root.after(8000, capture)
    def capture():
        import pyautogui as pg
        config['calc_name_box_point'] = list(pg.position())
        config['calibration_resolution'] = list(pg.size())
        CONFIG.write_text(json.dumps(config, indent=2), encoding='utf-8')
        root.deiconify()
        messagebox.showinfo('Captured', 'Name Box position saved. Close Calc before starting the pilot.', parent=root)
    ttk.Button(root, text='Calibrate Name Box (only if needed)', command=calibrate).pack(pady=5)
    ttk.Button(root, text='Start', command=start).pack(pady=12)
    root.mainloop()
    return selection


CONFIG = Path(__file__).with_name('config.json')


def main():
    if os.name != 'nt':
        print('Run main.py inside the Windows VM.'); return 1
    config = json.loads(CONFIG.read_text(encoding='utf-8'))
    selection = launcher(config)
    if not selection:
        return 0
    mode, budget = selection
    confirmed = time.monotonic()
    run_id = time.strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:6]
    run_dir = Path.home()/'Documents'/'HackathonDemo'/run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    logging.basicConfig(filename=run_dir/'agent.log', level=logging.INFO,
                        format='%(asctime)s %(levelname)s %(message)s')
    gui = None
    report = {'mode': mode, 'budget_sgd': budget, 'status': 'running', 'phases': {}}
    try:
        from gui_support import GUI, Stopped
        from browser_action import BrowserAction
        from inventory_action import InventoryAction
        import pyautogui as pg
        gui = GUI(run_dir)
        gui.event('run_started', mode=mode, budget=budget)
        seconds = config['full_phase_seconds'] if mode == 'Full demo' else config['pilot_phase_seconds']
        if mode != 'Inventory pilot':
            browser = BrowserAction(gui, config)
            try:
                report['phases']['browser'] = browser.run(seconds)
            except (Stopped, pg.FailSafeException):
                raise
            except Exception as exc:
                gui.event('browser_phase_failed', error=str(exc))
                report['phases']['browser'] = {'error': str(exc), 'verified': False}
                if browser.window:
                    try:
                        gui.close_window(browser.window)
                    except Exception:
                        raise RuntimeError('Cannot close the browser safely; close it before running the inventory pilot')
        if mode != 'Browser pilot':
            inventory = InventoryAction(gui, config, budget)
            report['phases']['inventory'] = inventory.run(seconds)
        report['status'] = ('complete' if report['phases'].get('browser', {}).get('verified', True)
                            else 'partial: browsing was not verified')
    except Exception as exc:
        # Includes cancellation. Never declare completion after a stop or failed validation.
        report['status'] = 'stopped' if type(exc).__name__ in ('Stopped', 'FailSafeException') else 'failed'
        report['error'] = str(exc)
        logging.exception('Run ended early')
    finally:
        report['elapsed_seconds'] = round(time.monotonic()-confirmed, 2)
        (run_dir/'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        if gui:
            gui.finish()
    root = tk.Tk(); root.withdraw()
    messagebox.showinfo('Demo result',
        f'Status: {report["status"]}\nRuntime: {report["elapsed_seconds"]/60:.1f} minutes\n'
        f'{report.get("error", "")}\nOutputs: {run_dir}\n\n'
        'Review report.json, the workbook, and Sysmon before judging this run successful.', parent=root)
    root.destroy()
    return 0 if report['status'] == 'complete' else 1


if __name__ == '__main__':
    sys.exit(main())
