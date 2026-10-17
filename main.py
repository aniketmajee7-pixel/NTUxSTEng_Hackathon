"""Run inside Windows. External dependencies: pyautogui, pywinauto, pywin32.

The agent writes no files of its own while it runs: no logs, no report.  Sysmon (event
ID 11) and Process Monitor show every file pythonw.exe creates, which a person using the
desktop would not do.  Only the applications write: Calc saves the workbook."""
import sys
sys.dont_write_bytecode = True       # no __pycache__ files either, as modules are loaded
import hashlib
import importlib
import json
import os
from pathlib import Path
import time
import tkinter as tk
from tkinter import messagebox, ttk


def launcher(config):
    root = tk.Tk()
    root.title('Windows Desktop Demo')
    root.resizable(False, False)
    choice = tk.StringVar(value='Full demo')
    minutes = lambda seconds: f'{seconds/60:g}'
    ttk.Label(root, text=f'Choose a run. Full demo: at most {minutes(config.get("browser_phase_seconds", config["full_phase_seconds"]))} minutes browsing, '
                         f'then the inventory spreadsheet (about {minutes(config["full_phase_seconds"])} minutes); '
                         f'it ends by the {minutes(config.get("total_demo_seconds", 900))}-minute mark.\n'
                         f'Browser pilot: {minutes(config["pilot_phase_seconds"])} minutes. Inventory pilot: '
                         f'{minutes(config.get("inventory_pilot_seconds", config["full_phase_seconds"]))} minutes.',
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
    # No Name Box calibration: cells are found on the screen (domain.find_grid), with the
    # keyboard as the fallback.
    ttk.Button(root, text='Start', command=start).pack(pady=12)
    root.mainloop()
    return selection


CONFIG = Path(__file__).with_name('config.json')
# Written by make_zip.py into the ZIP: the SHA-256 of each agent file.
BUILD = Path(__file__).with_name('BUILD.json')
# The agent's modules a run loads after it has started.  A copy into the VM left
# save_dialog.py wrong, and a Full demo failed at the 4.5-minute mark, when the spreadsheet
# part was loaded: "cannot import name 'browse_to' from 'save_dialog'".  They are loaded
# before anything is typed now.
RUN_MODULES = {'Full demo': ('browser_action', 'inventory_action', 'notes_action'),
               'Browser pilot': ('browser_action',),
               'Inventory pilot': ('inventory_action', 'notes_action')}


class NotStarted(Exception):
    """The pre-flight check found a problem and the run was cancelled before any input."""


def build_differences(build_file=None):
    """(build name, agent files that differ from it).  Line endings do not count.  A
    working copy has no BUILD.json and is not compared: (None, [])."""
    build_file = BUILD if build_file is None else build_file
    try:
        build = json.loads(build_file.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None, []
    changed = []
    for name, digest in sorted(build.get('files', {}).items()):
        try:
            data = (build_file.parent/name).read_bytes().replace(b'\r\n', b'\n')
        except OSError:
            changed.append(f'{name} (missing)')
            continue
        if hashlib.sha256(data).hexdigest() != digest:
            changed.append(name)
    return build.get('name'), changed


def check_files(mode):
    """The agent's own files, before the run: the modules it will use are loaded now, not
    minutes in, and the files are compared with the ZIP they came from."""
    name, changed = build_differences()
    broken = []
    for module in RUN_MODULES.get(mode, ()):
        try:
            importlib.import_module(module)
        except Exception as exc:
            broken.append(f'{module}.py: {exc}')
    return {'build': name, 'changed': changed, 'broken': broken}


def confirm_files(gui, files):
    """A module that cannot be loaded stops the run before it starts.  Files that only
    differ from the ZIP (perhaps changed on purpose) can be run anyway."""
    if not files['broken'] and not files['changed']:
        return
    gui.event('agent_files', **files)
    source = f'{files["build"]}.zip' if files['build'] else 'the newest ZIP'
    parts = []
    if files['broken']:
        parts.append('These files could not be loaded:\n'+'\n'.join(files['broken']))
    if files['changed']:
        parts.append(f'These files are not the ones in {source}:\n'+'\n'.join(files['changed']))
    text = ('\n\n'.join(parts)+f'\n\nCopy every file from {source} into {Path(__file__).parent} '
            'again (replace the old ones), then start the agent again.')
    root = tk.Tk(); root.withdraw()
    try:
        if files['broken']:
            messagebox.showerror('The agent files are not complete', text, parent=root)
            raise NotStarted('; '.join(files['broken']))
        if not messagebox.askokcancel('Check the agent files',
                                      text+'\n\nChoose OK to start anyway.', parent=root):
            raise NotStarted(f'files differ from {source}: '+', '.join(files['changed']))
    finally:
        root.destroy()


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
        if config.get('notes_phase_seconds'):
            apps.append((config.get('notes_shortcut', 'Notepad'), r'.*Notepad$'))
    desktops = [Path.home()/'Desktop']
    for variable in ('PUBLIC', 'OneDrive'):
        base = os.environ.get(variable) if hasattr(os, 'environ') else None
        if base:
            desktops.append(Path(base)/'Desktop')
    blockers, warnings = [], []
    for name, pattern in apps:
        handles = gui.window_handles(title_pattern=pattern)
        if isinstance(handles, list) and handles:
            if name == config.get('notes_shortcut', 'Notepad'):
                # The notes of the last run are left open on purpose.
                blockers.append(f'{name} is already open, probably with the notes of the last '
                                'run. Close it (there is no need to save them).')
            else:
                blockers.append(f'{name} is already open. Close all of its windows.')
        if not any((folder/f'{name}.lnk').exists() for folder in desktops):
            if name == config.get('notes_shortcut', 'Notepad'):
                warnings.append(f'No desktop shortcut named "{name}" was found: Notepad will '
                                'be opened with Win+R instead.')
            else:
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
                                        text+'\n\nWithout the Chrome or Calc shortcut the run fails.\n'
                                        'Choose OK to start anyway.', parent=root):
                return
            else:
                raise NotStarted('; '.join(warnings))
        finally:
            root.destroy()


def inventory_seconds(config, mode, elapsed, browser_result=None):
    """How long the spreadsheet phase may take.

    Full demo: `full_phase_seconds` (8 minutes), plus the browsing time left unused when
    `carry_unused_browser_time` is on (finishing the spreadsheet comes first), but never
    more than what is left of `total_demo_seconds` (15 minutes) once the notes at the end
    have their `notes_phase_seconds`.  So a slow browser phase can never push the end past
    the 15-minute mark."""
    if mode != 'Full demo':
        return config.get('inventory_pilot_seconds', config['full_phase_seconds'])
    seconds = config['full_phase_seconds']
    unused = browser_result.get('unused_seconds', 0) if isinstance(browser_result, dict) else 0
    if config.get('carry_unused_browser_time', False) and isinstance(unused, (int, float)) and unused > 0:
        seconds += unused
    left = config.get('total_demo_seconds', 900)-elapsed-config.get('notes_phase_seconds', 0)
    return max(60, min(seconds, left))


NOTES_END_MARGIN = 5        # seconds between the end of the notes and the 15-minute mark


def notes_seconds(config, mode, elapsed):
    """How long the Notepad notes at the end may take.  Full demo: all the time left before
    the 15-minute mark.  The spreadsheet keeps `notes_phase_seconds` for them, and what the
    other phases leave over is added: in the 7:55 PM run the notes stopped after 90 s with
    a minute of the 15 unused, in the middle of the spreadsheet line.  Pilots:
    `notes_phase_seconds`.  0 when the notes are switched off."""
    seconds = config.get('notes_phase_seconds', 0)
    if seconds and mode == 'Full demo':
        seconds = config.get('total_demo_seconds', 900)-elapsed-NOTES_END_MARGIN
    return max(0, seconds)


def main():
    if os.name != 'nt':
        print('Run main.py inside the Windows VM.'); return 1
    try:
        config = json.loads(CONFIG.read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        # Started from the desktop shortcut there is no console to show this.
        root = tk.Tk(); root.withdraw()
        messagebox.showerror('Windows Desktop Demo',
            f'config.json could not be read:\n{exc}\n\n'
            'Fix the file, or copy config.json again from the ZIP, then start again.', parent=root)
        root.destroy()
        return 1
    selection = launcher(config)
    if not selection:
        return 0
    mode, budget = selection
    confirmed = time.monotonic()
    started_at = time.localtime()
    gui = None
    # The account of the run stays in memory: it feeds the notes and the result box.
    report = {'mode': mode, 'budget_sgd': budget, 'status': 'running', 'phases': {},
              'windows_vm_validation': 'pending'}
    try:
        from gui_support import GUI, Stopped
        import pyautogui as pg
        gui = GUI()
        style ={k: getattr(gui, k, 1.0) for k in ('pace', 'wheel_speed', 'typo_scale', 'pause_scale')}
        if all(isinstance(v, (int, float)) for v in style.values()):
            # Per-run personality: mistakes happen a little more or less often each run.
            config['browser_typo_probability'] = min(
                0.6, config.get('browser_typo_probability', 0.2)*style['typo_scale'])
            config['spreadsheet_typo_scale'] = config.get('spreadsheet_typo_scale', 1.0)*style['typo_scale']
            gui.event('run_style', **{k: round(v, 3) for k, v in style.items()})
        files = report['agent_files'] = check_files(mode)
        confirm_files(gui, files)
        confirm_preflight(gui, config, mode)
        gui.event('run_started', mode=mode, budget=budget)
        seconds = config['full_phase_seconds'] if mode == 'Full demo' else config['pilot_phase_seconds']
        if mode != 'Inventory pilot':
            from browser_action import BrowserAction
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
            # Calc support was loaded by check_files before the run started (never in a
            # browser-only pilot); this only fetches it.
            from inventory_action import InventoryAction
            inventory = InventoryAction(gui, config, budget)
            seconds = inventory_seconds(config, mode, time.monotonic()-confirmed,
                                        report['phases'].get('browser'))
            gui.event('inventory_time_allowed', seconds=round(seconds, 1))
            result = inventory.run(seconds)
            if isinstance(result, dict) and not result.get('saved_path'):
                result = {**result, 'saved_path': str(inventory.path)}
            report['phases']['inventory'] = result
            seconds = notes_seconds(config, mode, time.monotonic()-confirmed)
            if seconds >= 45:
                from domain import run_notes
                from notes_action import NotesAction
                gui.event('notes_time_allowed', seconds=round(seconds, 1))
                try:
                    report['phases']['notes'] = NotesAction(gui, config).run(
                        seconds, run_notes(report, started_at))
                except (Stopped, pg.FailSafeException):
                    raise
                except Exception as exc:
                    gui.event('notes_phase_failed', error=str(exc))
                    report['phases']['notes'] = {'error': str(exc), 'saved_path': None}
            elif config.get('notes_phase_seconds'):
                gui.event('notes_skipped', reason='not enough time left', seconds=round(seconds, 1))
                report['phases']['notes'] = {'skipped': 'not enough time left', 'saved_path': None}
        inventory_result = report['phases'].get('inventory')
        if isinstance(inventory_result, dict) and inventory_result.get('complete') is False:
            # The workbook was saved, but it is not the finished table.
            report['status'] = 'partial: spreadsheet incomplete'
            report['error'] = str(inventory_result.get('error') or inventory_result.get('step_error') or '')
        elif not report['phases'].get('browser', {}).get('verified', True):
            report['status'] = 'partial: browsing was not verified'
        elif 'notes' in report['phases'] and not report['phases']['notes'].get('lines_typed'):
            report['status'] = 'partial: notes not written'
        else:
            report['status'] = 'complete'
    except Exception as exc:
        # Includes cancellation. Never declare completion after a stop or failed validation.
        report['status'] = ('stopped' if type(exc).__name__ in ('Stopped', 'TimeUp', 'FailSafeException')
                            else 'not started' if isinstance(exc, NotStarted) else 'failed')
        report['error'] = str(exc)
    finally:
        report['elapsed_seconds'] = round(time.monotonic()-confirmed, 2)
        if gui:
            gui.finish()
    return show_result(report)


def show_result(report):
    """A complete run ends quietly: the notes in Notepad say what was done, and a box on
    top of them would hide them.  Otherwise a box says what happened (nothing is saved)."""
    if report['status'] == 'complete':
        return 0
    inventory_result = report['phases'].get('inventory')
    workbook = inventory_result.get('saved_path') if isinstance(inventory_result, dict) else None
    root = tk.Tk(); root.withdraw()
    messagebox.showinfo('Demo result',
        f'Status: {report["status"]}\nRuntime: {report["elapsed_seconds"]/60:.1f} minutes\n'
        f'{report.get("error", "")}\n'
        +(f'Workbook saved: {workbook}\n' if workbook else '')
        +'\nCheck the workbook and Sysmon before judging this run successful.', parent=root)
    root.destroy()
    return 1


if __name__ == '__main__':
    sys.exit(main())
