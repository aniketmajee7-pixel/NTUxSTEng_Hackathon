# Development log

The team's running notes from building the agent and testing it on the organisers' VM,
kept as written at the time. Early sections describe behaviour that later rounds changed;
where sections disagree, the later one is right. For an overview, see [README.md](README.md).

## Scope at the time of writing

Full demo: at most 4:30 of homepage browsing (`browser_phase_seconds`), then 8 minutes
of Calc stock-inventory work (`full_phase_seconds`, plus any browsing time left
unused), then up to 1:30 of notes typed in Notepad (`notes_phase_seconds`) and left on
screen, unsaved.  The whole run ends by the 15-minute mark (`total_demo_seconds`).  The
workbook is saved in the agent folder (the folder holding `main.py`).  Browser pilot:
2 minutes.  Inventory pilot: the spreadsheet and the notes, the second half of the Full
demo.

The latest behaviour is described in the last section, **After the seventh VM
recording**; where an older section disagrees, that one is right.

## Prepare inside the Windows VM

1. Copy/extract the entire `desktop-demo` folder to
   `C:\Users\User\Desktop\desktop-demo`.
   When upgrading, keep a copy of your old folder and replace all source files
   together. Transfer any shortcut-name settings or Name Box calibration from
   your old `config.json`; keep the new settings as well.
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
The budget for the totals block comes from `default_budget_sgd` in `config.json`
(SGD 5,000); there is no prompt. Keep the VM console active. Do not type or
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

## Calc controls

Cells are found on the screen (`domain.find_grid`) and clicked with the mouse.  If the
grid cannot be read, the Name Box is used when Calc exposes it, else Ctrl+Home and the
arrow keys.  There is no calibration step any more (the **Calibrate Name Box** button
was removed).  Final ODS validation verifies saved contents.

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

Browsing uses all ten supplied homepage URLs, shuffled. It scrolls, switches/closes tabs and uses history. Only verified
same-page fragment links are clicked; uncertain links are skipped. No product
pages, purchases, login submissions or downloads are part of this scenario.
For the organisers' approved HTTPS homepages, a recognised Chrome privacy
notice is handled by clicking visible **Advanced**, then **Continue/Proceed**.
The current hostname is checked against the approved homepage first; same-host
redirect paths and query strings are allowed. After Advanced, the agent accepts
Chrome's host-named wording, generic **Continue/Proceed** wording, or Chrome's
`proceed-link` accessibility identifier. If none exists, it moves to the next site.
This does not disable certificate checks globally. The agent always tests the
page by expanding **Advanced** first. If the expanded page says the site uses
**HSTS** and cannot be visited (as in the manual Coinbase recording), it is
skipped immediately. Missing controls time out after four seconds; there
is no manual-attention popup. A warning that remains after Proceed is logged and
skipped. Other navigation errors are logged and skipped. Chrome closes before Calc; failed browser cleanup is logged and Calc
still runs independently.

Typing includes varied pauses for letters (0.12–0.30 seconds), punctuation
(0.35–0.95 seconds) and actual Shift key presses on the US layout, scaled by the
run pace. `browser_typo_probability` defaults to 0.2: roughly 20% of URL and
page-find entries include one wrong letter, a 0.4–1.2-second correction pause,
and Backspace before the correct letter. Wrong URLs are never deliberately
submitted. This setting does not affect formulas, numbers or save paths.
Selected address-bar autocomplete suffixes are cleared before correcting a typo
and before submission, so browser history cannot append a different page.

Pointer movement uses the distance-based ranges measured from the manual video:
0.20–0.42 seconds locally, 0.35–0.72 seconds across a section and 0.60–1.08
seconds for a long route, scaled by the run pace. Routes are shallow curves that
accelerate and slow near the destination. Some longer trips overshoot by 2–6
pixels and settle. Clicks still land in the central half of identified controls.

Browsing follows the recording's section rhythm. Each section change uses 2–3
wheel pulses of 5–9 ticks, with 0.05–0.16-second gaps and a 0.7–1.7-second review.
A normal homepage makes 5–8 sustained, section-sized downward movements and a
quick 1–2-step upward review near the bottom. It stops wheeling when Chrome's
accessible scroll position confirms that the page limit has been reached. One visible card
or content control receives a 4–8-second interest hover; other hover pauses are
short. This preserves the recording's occasional longer inspection without
leaving every section stationary. The agent clicks Chrome's visible new-tab and
tab controls when accessibility exposes them, falling back to Ctrl+T/Ctrl+number.
The typo-and-correction behaviour remains enabled. The full 223-second analysis
and every whole-second observation are in `MANUAL_VIDEO_ANALYSIS.md`.

Before wheel input, the agent closes Find and attempts to clear page selection
and restore document focus. Accessible vertical scroll percentages are checked
before and after a burst, when Chrome supplies them. `wheel_input` logs distinguish
true movement, an unchanged position (with one retry), and unavailable verification
(`null`). `scrolled_visits` counts visits with wheel input; it does not prove the
page moved. `verified_scroll_bursts` counts only observed position changes.
Pages lacking readable document content remain unverified.

Browser document discovery and control traversal have node/depth/time budgets;
page text is briefly cached and invalidated after navigation, warning expansion,
history changes and tab switching. Text-pattern reading avoids selecting the
entire page; the clipboard fallback is attempted once per page state. Native
top-level window discovery avoids repeated UIA scans during application startup.
A slow individual Windows accessibility-provider call can still exceed a traversal
budget. Calc support is imported only when its phase begins. These changes reduce
unnecessary work; CPU improvement still requires measurement inside the VM.

Calc creates a fresh inventory, enters six records, edits quantities/prices,
adds Laptop Stand, adds and deletes a temporary row, formats headers/currency,
sorts by Item, writes formulas and a budget summary, saves, and reopens the ODS.
If the required edits finish before the deadline, it reviews ranges and saves
until eight minutes have elapsed. Typos are immediately corrected in item
descriptions only; numeric values and formulas are not deliberately mistyped.

The approved plan contained an arithmetic error: these final rows total
**SGD 3,486**, not SGD 3,674. The saved workbook validator uses the actual total.

## Outputs and verification

**Out of date since 17 Oct:** the agent writes none of the log files listed here (no run
folder, `report.json`, `actions.jsonl`, `agent.log` or `resources.csv`), and the workbook
goes into the agent folder.  See the last section.

The workbook is saved as `Documents\HackathonDemo\inventory_<mon><day>.ods` (for
example `inventory_oct10.ods`; `_2`, `_3` ... if that name is taken). Its full path
is in `report.json` under `phases.inventory.saved_path`. The logs of each run are in
their own `Documents\HackathonDemo\<run-id>` directory:

- `report.json`: complete, partial, stopped or failed; elapsed time, results,
  startup stage durations/agent CPU seconds, and pending Windows validation.
- `actions.jsonl` and `agent.log`: actions, timestamps and recovery information.
- `resources.csv`: agent-only CPU as a percentage of all logical CPUs and RAM
  working set in MB, sampled every five seconds. This excludes Chrome/Calc.

Success requires the seven inventory records, no temporary row, correct
values/formulas, total 3,486 and a correct budget result. Sorting by Item is
reported (`sorted_by_item`) but not required.
An inaccessible browser or failed inventory validation is reported honestly.
If browsing fails early, the run may be shorter; it cannot qualify as the full
16-minute demonstration and must be repeated after fixing the pilot failure.

Inspect Sysmon Event ID 1 and Process Explorer for Chrome/Calc launcher chains.
Their helper processes need not all have Explorer as parent. Python itself
remains visible: no process names, command lines or telemetry are concealed.
Review Process Monitor timing and whole-VM CPU/RAM during a complete run. A
natural-looking screen alone is not evidence of authentic telemetry.

### Windows acceptance checks — pending

The source was updated and checked locally; no Windows VM pilot has been run
from this Mac environment. In the VM, run Browser pilot, Inventory pilot, then
Full demo. Test a warning with Proceed, an HSTS warning without Proceed, missing
controls, actual page movement, retained-tab return, F8, lost focus, and Calc
save/reopen. Compare startup CPU peaks and sustained CPU/RAM with the previous
version on the same VM and Chrome profile. `resources.csv` samples every five
seconds and can miss shorter peaks; use Process Explorer for live whole-VM/app
measurements and `startup_stages` for Python-stage CPU time. Record observed
usage; no CPU reduction or resource limit is claimed before those measurements.

## Automated checks available on any OS

```powershell
python -m unittest discover -s tests -v
```

These check phase deadlines, HSTS/approved-homepage notice controls, persistent
warnings, bounded discovery, unavailable browser text, visible/fallback tab
controls, tab revisits, corrected typing, distance-based pointer timing, bounded
click positions, scroll verification, focus/stop behaviour, and ODS validation
with synthetic fixtures. They do not prove Windows GUI compatibility. Browser, inventory,
stop-key, focus-loss and full-duration pilots must be run in the actual VM.

## Mistakes the agent makes and corrects

Mistakes happen only while typing, modelled on the manual recording (AWS entry, about 127 s: `www.w` instead of `www.a`, left for about 0.5-0.75 s, one Backspace, a short beat, then the right key):

- The wrong key is one physically next to the intended key on a QWERTY keyboard (`typo_kind_weights` can re-enable doubled and swapped letters; default is neighbour keys only).
- It is noticed after 0.45-0.9 s, fixed with Backspace, then 0.25-0.55 s passes before retyping.
- Mistakes cluster right after a `.` (60% of the time), as in the recording.
- `browser_typo_probability` (0.2, about one in five URLs) for the address bar; spreadsheet text and number cells use per-column chances scaled by `spreadsheet_typo_scale`. Formulas never get mistakes.
- URLs are typed like the manual run, without `https://` (for example `www.apple.com`), with a pause of about 1 s after `www.`.
- There are no scroll or tab mistakes.
- Browser pilot is 2 minutes (`pilot_phase_seconds`); Inventory pilot is 8 minutes (`inventory_pilot_seconds`).

## Spreadsheet: reuse instead of retyping

Only the first record is typed in full. The serial `INV-001` is copied from A2 and pasted down the column, then each row has just its last digit deleted and retyped (F2, Backspace, digit). A category already on the sheet (for example `Accessories`) is copied from its first cell and pasted. Formulas in F and H are copied down from row 2 so references adjust themselves. Every paste is verified through the clipboard and falls back to typing the value if it did not take. The temporary row and the post-sort retype fallback still type everything.

## Timing variation

- Reading, hover and click-dwell pauses are drawn from skewed (lognormal) distributions around the measured bands, capped so none becomes absurd; CV is about 0.5.
- The same pointer move varies in duration by about 25%.
- Each key is held 50-120 ms (keyDown, wait, keyUp) instead of being sent instantly.
- Every run draws its own style (wheel speed, typo rate, pause scale; logged as `run_style` in `actions.jsonl`).
- `report.json` names the saved workbook (`phases.inventory.saved_path`); the Save As dialog is retried once if it is not found.

## Added in the review round

- **Held keys everywhere.** Enter, Tab, Backspace, arrows and shortcuts (Ctrl+S, Ctrl+L ...) are pressed, held and released like typed characters; nothing is sent instantly.
- **Blocked sites are remembered.** A site with no Proceed link is not typed again in the same run; other failures get one more visit. `report.json` lists them under `blocked_sites`.
- **Address bar read without the clipboard** (`address_bar_method`: `accessibility`, falling back to Ctrl+L / Ctrl+C only if Chrome does not expose it). Set it to `clipboard` to force the old way.
- **On-page habits:** Ctrl+Tab / Ctrl+number tab switching (`tab_keyboard_probability`), Page Down / Space scrolling (`keyboard_scroll_probability`), and at most one of zoom-in-and-reset, reload, or double-clicking a word per visit (`browser_extra_action_probability`). Set any of these to 0 to switch it off.
- **Calc review work** replaces the old select-and-save loop: reading with the arrow keys, checking totals, a what-if quantity that is undone (Ctrl+Z, sometimes Ctrl+Y), notes typed in column J, and Find & Replace (Ctrl+F). The workbook is saved only after a real change.
- **Fill Down (Ctrl+D)** is used for formulas about half the time (`fill_down_probability`), checked, with copy/paste as the fallback.
- **Reopening the workbook is best-effort:** the file is already saved and validated, so a failure there is logged (`reopened: false`) and does not fail the run.
- **Pre-flight check** before any input: an application that is already open blocks the start (fix it and choose Retry); a shortcut that cannot be found gives a warning you can override.
- **Resource summary:** `report.json` has `agent_resources` (mean and peak CPU and memory of the agent process, from `resources.csv`).

## One visit per site

- Each listed address is typed **once** per run. `report.json` lists every site with whether it loaded and why not (`phases.browser.sites`, `blocked_sites`).
- A temporary failure is retried by refreshing the page (F5), never by typing the address again.
- When every listed site has been visited, browsing ends early (`ended_early: true`) instead of looping to fill 7 minutes. In Full demo the unused time is given to the spreadsheet phase so the whole run keeps its planned length (`carry_unused_browser_time`; set it to `false` to end sooner).
- Typing mistakes are a little more frequent: `browser_typo_probability` 0.3 and `spreadsheet_typo_scale` 1.25, with the per-run variation narrowed to 0.8-1.4.

## Clicking cells with the pointer

When Calc exposes its cells to Windows UI Automation, a cell is selected by moving the pointer to it and clicking (a range: click, then Shift+click), instead of typing its address in the Name Box. The cell is found by its exact name, never by guessed coordinates. If Calc exposes no cells, the agent logs `pointer_selection_unavailable` once and keeps using the Name Box. `actions.jsonl` shows `cell_selected` with `method: pointer` when it is working. Switch it off with `"calc_pointer_selection": false`.

To check what Calc exposes: open one Calc window, run `python inspect_ui.py calc`, and look at `ui-controls.txt` for lines named like `A1` or `B2`.

## Fixes from the full code check

- **Page judged loaded too early.** Right after Enter the tab still holds the previous page (or the New Tab page); its text used to count as "loaded", so a privacy warning that appeared a moment later was never handled. The agent now waits until the tab really shows the requested site (the page's own URL, or a changed title when Chrome does not expose it) and looks twice before accepting it. A warning that appears mid-visit is also continued. A page that never arrives is reported as `load_timeout` and retried with F5.
- **Paste inserted the cell address.** In Name Box mode, moving to the paste target copied the typed address to check it, replacing the copied cell. Moving between Copy and Paste now never touches the clipboard.
- **Formulas are verified** after Fill Down / paste (`ensure_formulas`) and typed in if missing, instead of failing validation at the very end.
- **AutoInput.** Calc proposes existing entries while typing; a correction's Backspace could remove only the proposal and leave the wrong letter. Delete now clears a proposal before a correction and before Enter.
- **Clearing cells** uses Backspace and confirms OpenOffice's "Delete Contents" dialog if it appears.
- The Find & Replace dialog is confirmed closed; a failed row falls back to typing (a second paste would raise the overwrite prompt); pointer selection is not switched off by one early empty answer; the Name Box is looked up once.
- Chrome refusing to close, or an error after a site loaded, no longer discards or duplicates browsing results. The Select All + Copy page-text fallback is off (`page_text_clipboard_fallback`).
- Pre-flight also stops on Caps Lock and warns about a non-US keyboard layout. The pointer path is kept away from the screen corners (the emergency-stop position).

## Mouse, copy/paste, time caps

Based on the 18-minute screen recording of a Full demo in the VM.

**What went wrong in that run**

- Ranges were never selected. The agent held Shift and pressed arrow keys, but arrows
  were sent as numeric-keypad keys and Windows dropped the Shift. Every "paste to a
  range" landed on one cell, so everything was then typed in full.
- OpenOffice shows neither its cells nor the Name Box to UI Automation, so every move
  was: click the middle of the sheet, Esc, Ctrl+Home, arrow keys.
- Only the final review step watched the clock. When the Sort dialog could not be
  driven, the fallback retyped all seven rows.

**What the agent does now**

- **Finds the grid on screen.** After Ctrl+Home it reads the window once (a plain GDI
  copy through pywin32) and locates the highlighted headers of cell A1 and the lines
  between header cells. From then on it clicks cells, drags or Shift+clicks ranges,
  and confirms every selection from the highlighted headers before typing or pasting.
  `actions.jsonl`: `grid_calibrated`, then `cell_selected` with `method: pointer`.
  Checked against frames of the recording: columns, rows and the selected cell were
  read correctly.
- **Arrow keys are sent as real arrow keys** (extended-key flag), so Shift+arrow
  selects. This is the fallback when the grid cannot be read.
- **Copy/paste.** `INV-001` is pasted down A3:A8 in one go and confirmed with one look
  at the column; each row then gets F2, Backspace, last digit. A repeated category is
  copied from the cell that has it. Both formula columns are pasted (or filled down
  with Ctrl+D) from the row above. One copy of the whole table replaces the
  cell-by-cell checks; only cells that differ are retyped.
- **Every row starts with a click on its first cell**, and inside a row the next cell
  is sometimes clicked instead of pressing Tab (`calc_click_next_cell_probability`).
- **Save As:** click Documents in the left pane, double-click `HackathonDemo`, type
  only the file name, click Save. The file is then looked for on disk; the full path
  is typed only if it cannot be found.
- **Order of work.** Everything the file needs comes first and is saved: headers,
  rows, formulas, the two corrections, the extra record, the totals block. Then, only
  if it fits before the cap: bold header and currency, sort by Item (Alt+D, S, I,
  Enter, checked afterwards). The temporary row is added and removed before the totals
  block, and is the first thing dropped when time is short. Skipped steps are logged
  as `step_skipped`.
- **Sorting never retypes the table.** If the sort cannot be confirmed, the entry
  order is kept and `sorted_by_item` is `false`.
- **Hard time limit.** 20 seconds before the 8-minute cap (`calc_wrap_up_seconds`)
  every key press and wait stops; the agent presses Esc, saves and closes. A run cut
  short is reported as `partial: spreadsheet incomplete` with `cut_short_at` and the
  saved path. The browser phase has the same kind of cap at 7 minutes and starts no
  new site in its last 30 seconds (`browser_min_visit_seconds`).
- **If ranges cannot be selected at all** (no readable grid and Shift+arrow still not
  working), every record is typed in full, one cell at a time, as before, but without
  the repair loops. `report.json` then shows `copy_paste_used: false`.

**Browser**

- The address bar is no longer selected to read the address. On a privacy-warning
  page the address is read through accessibility, or taken as the one just typed. The
  old Ctrl+L, Ctrl+C route is used only with `"address_bar_method": "clipboard"`.
  (The blue tail while typing is Chrome's own suggestion.)
- 17 more homepages were added (27 in total). A site may land on its own sub-domain
  (cnn.com to edition.cnn.com), on a host listed under `also` for it (bit.ly to
  bitly.com), or on `index.<locale>.html`.

**Check before a run**

With one Calc window open:

```powershell
python inspect_ui.py grid
```

It prints `OK: the agent can click cells with the mouse`, or saves `calc-screen.bmp`
to send for a look at the colours.

**New settings**

| Setting | Default | Meaning |
|---|---|---|
| `total_demo_seconds` | 900 | The run ends by this mark |
| `browser_phase_seconds` | 420 | Browser cap |
| `full_phase_seconds` | 480 | Spreadsheet cap |
| `inventory_pilot_seconds` | 480 | Length of the Inventory pilot |
| `carry_unused_browser_time` | false | Give unused browsing time to the spreadsheet |
| `calc_wrap_up_seconds` | 20 | Time kept for the final save and close |
| `browser_min_visit_seconds` | 30 | No new site is started with less than this left |
| `calc_click_next_cell_probability` | 0.3 | Click the next cell instead of Tab |
| `calc_drag_select_probability` | 0.6 | Drag a range instead of click, Shift+click |
| `reopen_after_save` | false | Reopen the saved file from Explorer at the end |

**Not yet seen working in the VM:** clicking and dragging in the real Calc window, the
keyboard sort, and the folder clicks in Save As. Each one is checked by the agent as it
goes and has a fallback.

## After the second VM recording (Inventory pilot, cut off at the totals)

The mouse, the range selection, the copy/paste and the Save As clicks all worked in
the VM.  The run still ended as `partial` because two things cost about 2 minutes:

- **Ctrl+D is not Fill Down in OpenOffice.** It opens the Selection List (it is Fill
  Down only in LibreOffice).  A list dropped open under the cell and column H stayed
  empty.  Formulas are now always brought down by copy and paste; `fill_down_probability`
  is gone.
- **After a paste, Tab does not leave the cell.**  The pasted cell stays marked, and
  with a marked range Tab and Enter only move inside it.  The quantity and the price
  typed after a pasted category both landed on the category.  After a paste the agent
  now clicks the next cell (or presses the Right arrow when it has no mouse route).

Both left wrong cells behind, and putting them right by typing each formula took the
time the totals block needed.  Repairs are now quick as well: a wrong value is retyped,
a missing formula is copied from a row that has it and pasted.

**The end of the run**

- The last save goes through the File menu (Alt+F, then S) so it can be seen, and is
  confirmed from the file on disk; Ctrl+S follows if the file did not change.
  `"final_save_from_menu": false` switches back to Ctrl+S only.
- If Calc still asks "Do you want to save your changes?" when closing, the agent
  answers Save.  (That question ended the first Full demo as `failed`.)
- The review ends with a note in the Notes column, so the last save has work to save.
- The result box shows `Workbook saved: <path>`.

**Keys held down by something else**

Starting a Mac screen recording with Cmd+Shift+5 while the VM console has the keyboard
sends Windows+Shift into the VM and leaves them held.  Ctrl+S then becomes
Windows+Ctrl+S (Voice access) and Ctrl+V becomes Windows+Ctrl+V (Sound output); this is
what derailed the first Full demo at its eleventh minute.  The agent now lets go of any
modifier it is not holding itself before every shortcut and before typing
(`stuck_key_released` in `actions.jsonl`).  It is still better to start the recording
first, or to click outside the console before pressing the shortcut.

**Pilot and Full demo** run the same code: the Inventory pilot is exactly the second
half of the Full demo with the same 8-minute cap, and the Browser pilot is the first
half with 2 minutes instead of 7.

`tests/sim_calc.py` now behaves like the VM in both respects, so a step that relies on
Ctrl+D or on Tab after a paste fails in `tests/test_inventory_flow.py`.


## After the third VM recording (Full demo, 11:04 PM): spreadsheet first, notes at the end

The run started from the desktop shortcut and ended at 14.7 minutes with the totals block
unfinished.  What the recording showed, and what changed:

**Time** (the spreadsheet comes first)

| Part | Before | Now |
|---|---|---|
| Browsing | 7:00 | 4:30 (`browser_phase_seconds: 270`) |
| Spreadsheet | 8:00, cut to 7:35 | 8:00 plus any browsing time left unused (`carry_unused_browser_time: true`) |
| Notes in Notepad | none | up to 1:45 (`notes_phase_seconds: 105`), skipped under 45 s |

The whole run still ends by 15:00.  In Calc, five checkpoints compare the run's own
speed with the pilot's (`calc_schedule` in `actions.jsonl`).  While the workbook would
not be finished in time, pauses get shorter (pace down to 0.75, `calc_hurry`) and the
optional steps wait; once the totals are saved, the polish and review run as usual.  The
pace that drifted during browsing (up to x1.35) is reset to 0.85-1.0 when Calc starts.
In the Calc model every run up to 1.4 times slower than the pilot completes.

**Spreadsheet**

- The workbook goes into the agent folder.  Save As clicks **Desktop** in the left
  pane (rolling the pane up with the wheel if Desktop is out of view), then
  double-clicks the agent folder; only the file name is typed.  When the dialog already
  shows the folder (Windows reopens the last one), nothing is clicked.  `save_folder` in
  `config.json` overrides the folder.
- A category still on the clipboard is pasted again without going back to copy it.

**Notes** (new, `notes_action.py`)

Notepad is opened from a desktop shortcut named **Notepad** (`notes_shortcut`; without it,
Win+R and `notepad`).  A short account of the run is typed: the sites visited, pop-ups
accepted, keyword searches, sites skipped, and the workbook with its totals.  When time
is short, the least important lines are left out; a line is never cut inside a word.
The notes are saved like the workbook (Desktop, then the agent folder, then the name,
e.g. `notes_oct17.txt`) and Notepad is closed.  The result box shows both paths.  An
open Notepad blocks the start; a missing shortcut only warns.

**Browsing**

- **Wrongly dropped sites.**  archive.org, samsung, bumble and binance were dropped
  within seconds: each was typed into a tab that still showed the previous site's
  privacy warning, and that warning was taken for theirs.  A warning now counts only
  once it names the requested host (`previous_warning_ignored`).
- **Consent pop-ups** such as CNN's "Legal Terms and Privacy" are read and their accept
  button clicked (Agree, Accept all ...).  Reject, Manage and Settings are never
  clicked; OK/Continue only when the page talks about cookies.  At most two per page,
  logged as `consent_accepted`; `browser_accept_consent: false` turns this off.
- **Going back to the first site's tab** happens at most twice a run
  (`browser_max_revisits: 2`), never right after the first site and never back to back.
  With nothing left to read near the end, browsing stops early and Calc gets the time.
- **A dead tab is reused**: after a site fails, the next address goes into that tab.
- **Visits last 30-45 s** (`browser_visit_seconds`), so more sites fit in 4:30.
- **Ctrl+F keyword search is back** on about half the visits
  (`browser_find_probability: 0.5`): the site's `term` is typed, Enter, the match count
  is read from Chrome's find bar, one or two more matches are visited, Esc.
- **Sites that failed before are tried last**: outcomes are kept across runs in
  `Documents\HackathonDemo\site_history.json`; `browser_known_blocked` lists the two seen
  with no Proceed link (coursera.org, cloud.google.com).

**Other**

- Clicks and drags check that the pointer is still where it was put before pressing; the
  host's mouse over the VM console moved it in the 10:05 PM recording (`pointer_moved`).
- A broken `config.json` now shows a message box: with the desktop shortcut there is no
  console to show the error.

**Set up once in the VM:** a desktop shortcut named **Notepad** (right-click the desktop,
New, Shortcut, `notepad.exe`, name it Notepad).

## After the fifth VM recording (Full demo, 12:42 AM): notes left open, fewer searches

The run ended at 14.5 minutes as `partial: notes not saved`: the spreadsheet was
complete, but it was saved in `Documents\HackathonDemo`, and the Notepad save failed.
What changed:

**Notes** (`notes_action.py`): typed and **left open, unsaved**, so whoever comes back to
the computer can read what the run did.  No Ctrl+S, no Save dialog, no closing; all of
`notes_phase_seconds` (90) goes to typing.  The run counts as `complete` once the notes
are written.  **No result box** when the run is complete, so nothing covers the notes
(`report.json` is written as before); the box still appears when something went wrong.
Close the notes before the next run: the pre-flight check says so if Notepad is open.

**Spreadsheet** (`inventory_action.py`):
- No temporary row (it took 48 s and left no time for bold, currency and sort).
- No notes typed into the sheet.  The review looks over cells and totals, tries a
  quantity and puts it back, and finds an item.
- File > Save right after the totals; anything done after them is saved the same way at
  the very end.
- Save As reaches `Desktop\agent` even when the dialog's left pane is scrolled past
  Desktop (it was, in both dialogs of this run): the pane is rolled with the wheel until
  Desktop is really in it, with This PC > Desktop as a second way.  A pane item counts
  only when it lies inside the pane (`save_folder_step`, `save_folder_browsed reached=`).

**Browsing** (`browser_action.py`):
- **Search with Chrome's find bar, now and then**: `browser_find_probability` 0.3, at most
  `browser_max_searches` (2) a run, never on two sites in a row, never on a page with
  almost no text.  The word comes from the page itself, below its menu (the site's
  `term` when it qualifies), so the page visibly goes to it: Enter for the first match,
  the find bar's **Next** arrow clicked with the mouse for the next ones, the pointer
  rests on the word, then Esc.
- **Advanced that does not open** (coinmarketcap): clicked once more after 2 s; if the
  details never open the reason is `advanced_not_opened` (retried with F5, not held
  against the site).
- **The site's own 404/403** (arstechnica after Proceed) is `page_error`: not reloaded,
  remembered, tried last.
- **Blank pages** (archive.org showed only its header) are recorded as `blank` in
  `site_history.json` and tried last.
- **Tried last**: cloudflare, amazon, booking, bing (no Proceed link), coinbase,
  arstechnica, coursera and cloud.google.com (`browser_known_blocked`).

One-off in the VM: the earlier run recorded coinmarketcap as having no Proceed link,
which was wrong.  Deleting `Documents\HackathonDemo\site_history.json` once clears it.

## After the sixth VM recording (Full demo, 3:26 PM): files checked first, offers closed

The run browsed for 4.5 minutes, then stopped as `failed` when the spreadsheet part was
loaded: `cannot import name 'browse_to' from 'save_dialog'`.  The VM's `save_dialog.py`
was not the one from the ZIP: every version built has `browse_to`, and an empty file
gives exactly this error.  What changed:

**The agent's files are checked before anything is typed** (`main.py`):
- Every module the chosen run needs is loaded at the start, not when the run reaches it.
  One that cannot be loaded stops the run with a message naming the file and what to
  copy again.
- The ZIP carries `BUILD.json`, the SHA-256 of each module, written by `make_zip.py`.
  Files that differ from it are listed and can still be run (changed on purpose).  Line
  endings do not count, and `config.json` is not checked.  `report.json` has
  `agent_files`.
- `make_zip.py` puts the files at the top of the ZIP: extract it straight into
  `C:\Users\User\Desktop\agent`, replacing the files there.

```bash
python3 make_zip.py ../desktop-demo-8.zip
```

**Pop-ups that only want something are closed with their X** (`browser_action.py`):
- Bitly's "Want to try Bitly for free?" opens when the pointer leaves the page at the
  top (the find bar's Next arrow, the + button), once its cookie banner has been
  answered.  The page keeps scrolling behind it, so the stuck-scroll check never saw it
  and it stayed on screen.
- The agent looks for a pop-up when a page opens (also on a return to the first tab),
  after a scroll that does not move, and after a find-bar search.  A consent notice is
  accepted as before; any other pop-up is looked at, closed with its X ("Close modal",
  "Close dialog", "No thanks", "Not now" ...), and Esc is pressed if it stays.  At most
  two a page; `browser_close_popups: false` turns it off.
- Only buttons inside the page count: Chrome's tabs and find bar have "Close" buttons
  too.  A plain "Close" or X counts only inside a dialog (`role="dialog"`).  A click that
  leaves the homepage is undone with Alt+Left.
- One walk through the page's buttons serves both the consent check and the close
  check; names are read only for controls on screen.
- Logged as `popup_closed` (label, method, closed); the notes say "Closed the pop-up on
  ...".

## No files written during a run

Sysmon (file-create events, ID 11) and Process Monitor showed pythonw.exe creating files
all through a run: a folder per run in `Documents\HackathonDemo` with `agent.log`,
`actions.jsonl`, `report.json` and `resources.csv` (written every 5 s), and
`site_history.json`.  A person at the desktop leaves no such trace, so all of it is gone:

- `main.py` keeps the account of the run in memory only: it feeds the notes typed into
  Notepad and the result box shown when something went wrong.  Its first line sets
  `sys.dont_write_bytecode`, so Python writes no `__pycache__` files either.
- `gui_support.py`: `event()` records nothing (the tests still watch the calls), and
  the CPU/memory sampler thread is gone.  Measure resources in Process Explorer.
- `browser_action.py`: no `site_history.json`.  The sites of no use found in the VM are
  listed in `config.json` (`browser_known_blocked`) and tried last; archive.org (blank)
  and binance (403) were added from the 3:26 PM recording.
- `config.json` is only read.  It used to be rewritten by the "Calibrate Name Box"
  button, which was removed in `a1a3a09` (17 Oct).

What the agent does on screen is unchanged.  The only files created during a run come
from the applications: Calc saves the workbook in the agent folder.  `inspect_ui.py`, a
diagnostic run by hand, still writes its dumps; it is never part of a run.

## After the seventh VM recording (Full demo, 7:55 PM): notes end with END, Save As fixed

**Notes** (`notes_action.py`, `domain.py`, `main.py`).  They stopped after
"inventory_oct17.ods: 7 items,".  Three causes:
- Typing in Notepad ran slower than planned.  The first line took 16 s; the old flat
  estimate (0.32 s a key, 0.7 s with Shift) said 11.6 s.  `typing_seconds` now follows
  `type_text`: longer gaps after a space and before symbols, the key held down, Shift,
  and the odd longer pause.
- The lines were chosen once, at the start, so a minor line ("Searched for ...") used up
  the time of the spreadsheet line.  Now the choice is made again before each line, with
  the speed measured so far, most important first.  A minor line gives way; a major one
  is no longer cut.
- The notes had 90 s even though a minute of the 15 was left.  In a Full demo they now
  get all the time left before the 15-minute mark, minus 5 s.

The notes end with a line **END**.  Time is kept for it, and it is typed whatever
happened to the lines, as long as Notepad is open.

**Save As** (`save_dialog.py`, `inventory_action.py`).  The dialog opened in This PC.
Desktop was in view in the left pane but was not clicked; This PC was, and on Windows 11
This PC does not list Desktop.  So Save put the workbook in This PC, which Windows keeps
in `%APPDATA%\Microsoft\Windows\Network Shortcuts`, answered Yes to replacing an earlier
copy there, and took an old `Documents\HackathonDemo\inventory_oct17.ods` for the new
file.  As a result the full path was never typed, every later save waited for the old
file to change, and the old file was the one checked.  Now:
- every left-pane item with the name is looked at, and the one in view is clicked;
- Save is never pressed while the address bar shows another folder: the full path is
  typed instead;
- a replace question outside the agent folder is answered No, then the full path is
  typed;
- only a file modified since Save counts as the saved workbook.
