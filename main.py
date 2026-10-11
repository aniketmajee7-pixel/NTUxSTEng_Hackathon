"""Run inside Windows. External dependencies: pyautogui, pywinauto, pywin32."""
import json
import logging
import os
from pathlib import Path
import sys
import time
import tkinter as tk
from tkinter import messagebox, ttk
import uuid


def launcher(config):
    root = tk.Tk()
    root.title('Windows Desktop Demo')
    root.resizable(False, False)
    choice = tk.StringVar(value='Full demo')
    ttk.Label(root, text=f'Choose a run. Full demo: at most {config.get("browser_phase_seconds", config["full_phase_seconds"])//60} minutes browsing, '
                         f'then at most {config["full_phase_seconds"]//60} minutes inventory.\n'
                         f'Browser pilot: {config["pilot_phase_seconds"]//60} minutes. Inventory pilot: '
                         f'{config.get("inventory_pilot_seconds", config["full_phase_seconds"])//60} minutes.',
              padding=12).pack()
    for name in ('Full demo', 'Browser pilot', 'Inventory pilot'):
        ttk.Radiobutton(root, text=name, variable=choice, value=name).pack(anchor='w', padx=20)
    ttk.Label(root, text='Browser pilot only browses. Full demo opens a new Calc workbook\n'
              'after the browsing phase. Inventory pilot starts Calc immediately.', padding=12).pack()
    ttk.Label(root, text='Close existing Chrome and Calc windows. Keep the VM console active.\n'
              'Desktop shortcuts must be named Google Chrome and OpenOffice Calc.\n'
              'F8 or moving the mouse to a screen corner stops the run.', padding=12).pack()
    selection = []
    def start():
        # The budget only feeds two cells of the summary block, so it comes from
        # config.json instead of a prompt before every run.
        selection.extend([choice.get(), float(config['default_budget_sgd'])]); root.destroy()
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


class NotStarted(Exception):
    """The pre-flight check found a problem and the run was cancelled before any input."""


def preflight(gui, config, mode):
    """Problems worth knowing before any input is sent.

    Returns (blockers, warnings).  An application that is already open is a blocker,
    because the demo must launch it itself.  A shortcut that cannot be found as a file
    is only a warning: the desktop folder may be redirected (for example to OneDrive)."""
    apps = []
    if mode != 'Inventory pilot':
        apps.append((config['chrome_shortcut'], r'.* - Google Chrome$'))
    if mode != 'Browser pilot':
        apps.append((config['calc_shortcut'], r'.*Calc.*'))
    desktops = [Path.home()/'Desktop']
    for variable in ('PUBLIC', 'OneDrive'):
        base = os.environ.get(variable) if hasattr(os, 'environ') else None
        if base:
            desktops.append(Path(base)/'Desktop')
    blockers, warnings = [], []
    for name, pattern in apps:
        handles = gui.window_handles(title_pattern=pattern)
        if isinstance(handles, list) and handles:
            blockers.append(f'{name} is already open. Close all of its windows.')
        if not any((folder/f'{name}.lnk').exists() for folder in desktops):
            warnings.append(f'No desktop shortcut named "{name}" was found.')
    try:
        import win32api
        if win32api.GetKeyState(0x14) & 1:      # VK_CAPITAL toggled on
            blockers.append('Caps Lock is on. Turn it off: it reverses the case of '
                            'everything typed, which breaks the spreadsheet check.')
        if win32api.GetKeyboardLayout(0) & 0xFFFF != 0x0409:
            warnings.append('The keyboard layout is not English (US). Symbols such as '
                            ': ; " < = may be typed as different characters.')
    except Exception:
        pass    # keyboard state could not be read; not a reason to stop
    return blockers, warnings


def confirm_preflight(gui, config, mode):
    """Show pre-flight problems; let the user fix and retry, continue, or cancel."""
    while True:
        blockers, warnings = preflight(gui, config, mode)
        if not blockers and not warnings:
            return
        gui.event('preflight', blockers=blockers, warnings=warnings)
        root = tk.Tk(); root.withdraw()
        try:
            text = '\n'.join(blockers+warnings)
            if blockers:
                if not messagebox.askretrycancel('Before the run can start',
                                                 text+'\n\nFix this, then choose Retry.', parent=root):
                    raise NotStarted('; '.join(blockers))
            elif messagebox.askokcancel('Check before starting',
                                        text+'\n\nThe run will fail if the shortcut is missing.\n'
                                        'Choose OK to start anyway.', parent=root):
                return
            else:
                raise NotStarted('; '.join(warnings))
        finally:
            root.destroy()


def inventory_seconds(config, mode, elapsed, browser_result=None):
    """How long the spreadsheet phase may take.

    Full demo: capped at `full_phase_seconds` (8 minutes) and at whatever is left of
    `total_demo_seconds` (15 minutes), so a slow browser phase can never push the end
    past the 15-minute mark.  Time the browser did not use is only added when
    `carry_unused_browser_time` is switched on."""
    if mode != 'Full demo':
        return config.get('inventory_pilot_seconds', config['full_phase_seconds'])
    seconds = config['full_phase_seconds']
    unused = browser_result.get('unused_seconds', 0) if isinstance(browser_result, dict) else 0
    if config.get('carry_unused_browser_time', False) and isinstance(unused, (int, float)) and unused > 0:
        seconds += unused
    left = config.get('total_demo_seconds', 900)-elapsed
    return max(60, min(seconds, left))


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
    report = {'mode': mode, 'budget_sgd': budget, 'status': 'running', 'phases': {},
              'startup_stages': [], 'windows_vm_validation': 'pending'}
    previous_stage = [confirmed, time.process_time()]
    def stage_finished(stage):
        now, cpu = time.monotonic(), time.process_time()
        entry = {'stage': stage, 'elapsed_seconds': round(now-confirmed, 3),
                 'duration_seconds': round(now-previous_stage[0], 3),
                 'agent_cpu_seconds': round(cpu-previous_stage[1], 4)}
        report['startup_stages'].append(entry)
        logging.info('stage_finished %s', entry)
        if gui:
            gui.event('stage_finished', stage=stage,
                      startup_elapsed_seconds=entry['elapsed_seconds'],
                      duration_seconds=entry['duration_seconds'],
                      agent_cpu_seconds=entry['agent_cpu_seconds'])
        previous_stage[:] = [now, cpu]
    try:
        from gui_support import GUI, Stopped
        import pyautogui as pg
        stage_finished('gui_dependencies_imported')
        gui = GUI(run_dir)
        stage_finished('gui_initialized')
        style = {k: getattr(gui, k, 1.0) for k in ('pace', 'wheel_speed', 'typo_scale', 'pause_scale')}
        if all(isinstance(v, (int, float)) for v in style.values()):
            # Per-run personality: mistakes happen a little more or less often each run.
            config['browser_typo_probability'] = min(
                0.6, config.get('browser_typo_probability', 0.2)*style['typo_scale'])
            config['spreadsheet_typo_scale'] = config.get('spreadsheet_typo_scale', 1.0)*style['typo_scale']
            gui.event('run_style', **{k: round(v, 3) for k, v in style.items()})
        confirm_preflight(gui, config, mode)
        gui.event('run_started', mode=mode, budget=budget)
        seconds = config['full_phase_seconds'] if mode == 'Full demo' else config['pilot_phase_seconds']
        if mode != 'Inventory pilot':
            from browser_action import BrowserAction
            stage_finished('browser_module_imported')
            browser = BrowserAction(gui, config)
            try:
                browser_seconds = (config.get('browser_phase_seconds', seconds)
                                   if mode == 'Full demo' else seconds)
                report['phases']['browser'] = browser.run(browser_seconds)
            except (Stopped, pg.FailSafeException):
                raise
            except Exception as exc:
                gui.event('browser_phase_failed', error=str(exc))
                report['phases']['browser'] = {'error': str(exc), 'verified': False}
                if browser.window:
                    try:
                        gui.close_window(browser.window)
                    except (Stopped, pg.FailSafeException):
                        raise
                    except Exception as close_error:
                        gui.event('browser_close_failed', error=str(close_error))
                        report['phases']['browser']['close_error'] = str(close_error)
                        # A browser cleanup failure does not disable the independent inventory task.
        if mode != 'Browser pilot':
            gui.event('starting_inventory_phase')
            # Do not import Calc support during a browser-only pilot or Chrome launch.
            inventory_import = time.monotonic(), time.process_time()
            from inventory_action import InventoryAction
            gui.event('inventory_module_imported',
                      duration_seconds=round(time.monotonic()-inventory_import[0], 3),
                      agent_cpu_seconds=round(time.process_time()-inventory_import[1], 4))
            inventory = InventoryAction(gui, config, budget)
            seconds = inventory_seconds(config, mode, time.monotonic()-confirmed,
                                        report['phases'].get('browser'))
            gui.event('inventory_time_allowed', seconds=round(seconds, 1))
            result = inventory.run(seconds)
            if isinstance(result, dict) and not result.get('saved_path'):
                result = {**result, 'saved_path': str(inventory.path)}
            report['phases']['inventory'] = result
        inventory_result = report['phases'].get('inventory')
        if isinstance(inventory_result, dict) and inventory_result.get('complete') is False:
            # The workbook was saved, but it is not the finished table.
            report['status'] = 'partial: spreadsheet incomplete'
            report['error'] = str(inventory_result.get('error') or inventory_result.get('step_error') or '')
        elif not report['phases'].get('browser', {}).get('verified', True):
            report['status'] = 'partial: browsing was not verified'
        else:
            report['status'] = 'complete'
    except Exception as exc:
        # Includes cancellation. Never declare completion after a stop or failed validation.
        report['status'] = ('stopped' if type(exc).__name__ in ('Stopped', 'TimeUp', 'FailSafeException')
                            else 'not started' if isinstance(exc, NotStarted) else 'failed')
        report['error'] = str(exc)
        logging.exception('Run ended early')
    finally:
        report['elapsed_seconds'] = round(time.monotonic()-confirmed, 2)
        if gui:
            gui.finish()  # stops the sampler, so resources.csv is complete before it is read
        from domain import summarize_resources
        resources = summarize_resources(run_dir/'resources.csv')
        if resources:
            report['agent_resources'] = resources
        (run_dir/'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    inventory_result = report['phases'].get('inventory')
    workbook = inventory_result.get('saved_path') if isinstance(inventory_result, dict) else None
    root = tk.Tk(); root.withdraw()
    messagebox.showinfo('Demo result',
        f'Status: {report["status"]}\nRuntime: {report["elapsed_seconds"]/60:.1f} minutes\n'
        f'{report.get("error", "")}\n'
        +(f'Workbook saved: {workbook}\n' if workbook else '')
        +f'Logs: {run_dir}\n\n'
        'Review report.json, the workbook, and Sysmon before judging this run successful.', parent=root)
    root.destroy()
    return 0 if report['status'] == 'complete' else 1


if __name__ == '__main__':
    sys.exit(main())
