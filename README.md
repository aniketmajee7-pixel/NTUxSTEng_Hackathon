# Windows Desktop Demo

Full demo: at least 8 minutes of independent homepage browsing followed by at
least 8 minutes of Calc stock-inventory work. Saving and reopening can take
longer. Two-minute pilots are available for each phase. Inventory creation may
take longer than two minutes because the pilot completes the full task.

## Prepare inside the Windows VM

1. Copy/extract the entire `desktop-demo` folder to
   `C:\Users\User\Desktop\desktop-demo`.
2. You already installed Python 3.12, pyautogui, pywinauto and pywin32 with
   their dependencies. Verify them with:

   ```powershell
   python -c "import pyautogui, pywinauto, win32api, win32clipboard, tkinter; print('Ready')"
   ```

3. Put application shortcuts on the Windows desktop named **Google Chrome**
   and **OpenOffice Calc**. Edit `config.json` if the names differ. These are
   required: the agent double-clicks their visible icons. It never launches
   those applications with subprocess or Application.start.
4. Use an English interface, a fixed display resolution, a US/English keyboard
   layout, and Calc's default semicolon formula separator. Close first-run
   dialogs manually. Set Calc's default currency to SGD with two decimal
   places; the currency keyboard shortcut uses that setting.
5. Confirm organiser certificates are installed and the ten homepages work.
   Close existing Chrome, Calc and File Explorer windows before starting.

## Start

Run these as two separate commands for development:

```powershell
cd C:\Users\User\Desktop\desktop-demo
```

```powershell
python main.py
```

Choose **Browser pilot** first, then **Inventory pilot**, then **Full demo**.
The budget defaults to SGD 5,000. Keep the VM console active. Do not type or
click while the agent works. Hold **F8** or move the pointer to a screen corner
to stop. The latest completed save remains on disk; unsaved edits may remain
open. The agent does not kill applications or dismiss unknown dialogs.

For the final demo, create the launcher shortcut once:

```powershell
powershell -File .\create_shortcut.ps1
```

If your organisation blocks PowerShell scripts, create a shortcut manually:
find the interpreter with `where.exe pythonw`, then set Target to that quoted
pythonw.exe path followed by the quoted full path to `main.py`. Set Start in to
the `desktop-demo` folder. Double-click **Windows Desktop Demo** for judging.
No PowerShell execution-policy change is required by this package.

## Calc controls and calibration

The agent first tries to locate the Name Box through Windows accessibility.
If it cannot, open and maximize Calc, run `main.py`, select **Calibrate Name
Box**, and hover over the box left of the formula bar during the countdown.
Close Calc before running the inventory pilot. Calibration stores only the
point and display resolution in `config.json`. Every selection checks the
typed address before proceeding; an unverifiable selection stops the task.

Menus and dialogs vary across OpenOffice builds. A missing control is a pilot
failure, not a completed demonstration. For diagnosis, leave Calc open and run:

```powershell
python inspect_ui.py calc
```

This writes `ui-controls.txt`, containing the visible control names and bounds.
Run `python inspect_ui.py chrome` for browser accessibility diagnosis. The
agent does not add Chrome accessibility command-line flags. When page content
cannot be read, it logs that limitation and does not claim verified browsing.

## What happens

Browsing uses all ten supplied homepage URLs, shuffled. It scrolls, finds
visible text with Ctrl+F, switches/closes tabs and uses history. Only verified
same-page fragment links are clicked; uncertain links are skipped. No product
pages, purchases, login submissions, downloads or certificate bypasses are
part of this scenario. A blocked site is skipped. Chrome closes before Calc.

Calc creates a fresh inventory, enters six records, edits quantities/prices,
adds Laptop Stand, adds and deletes a temporary row, formats headers/currency,
sorts by Item, writes formulas and a budget summary, saves, and reopens the ODS.
If the required edits finish before the deadline, it reviews ranges and saves
until eight minutes have elapsed. Typos are immediately corrected in item
descriptions only; numeric values and formulas are not deliberately mistyped.

The approved plan contained an arithmetic error: these final rows total
**SGD 3,486**, not SGD 3,674. The saved workbook validator uses the actual total.

## Outputs and verification

Each run has its own `Documents\HackathonDemo\<run-id>` directory:

- `inventory.ods`: workbook, if the inventory phase ran successfully.
- `report.json`: complete, partial, stopped or failed; elapsed time and results.
- `actions.jsonl` and `agent.log`: actions, timestamps and recovery information.
- `resources.csv`: agent-only CPU as a percentage of all logical CPUs and RAM
  working set in MB, sampled every five seconds. This excludes Chrome/Calc.

Success requires seven sorted inventory records, no temporary row, correct
values/formulas, total 3,486, correct budget result, and a successful GUI reopen.
An inaccessible browser or failed inventory validation is reported honestly.
If browsing fails early, the run may be shorter; it cannot qualify as the full
16-minute demonstration and must be repeated after fixing the pilot failure.

Inspect Sysmon Event ID 1 and Process Explorer for Chrome/Calc launcher chains.
Their helper processes need not all have Explorer as parent. Python itself
remains visible: no process names, command lines or telemetry are concealed.
Review Process Monitor timing and whole-VM CPU/RAM during a complete run. A
natural-looking screen alone is not evidence of authentic telemetry.

## Automated checks available on any OS

```powershell
python -m unittest discover -s tests -v
```

These check phase deadlines, safe links, and ODS validation with synthetic
fixtures. They do not prove Windows GUI compatibility. Browser, inventory,
stop-key, focus-loss and full-duration pilots must be run in the actual VM.
