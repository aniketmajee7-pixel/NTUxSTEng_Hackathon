# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

This repo is a hackathon entry (NUS x ST Engineering 2026, "Synthetic Human Agent"). The agent does routine office and web work on an organiser-provided Windows VM for at least 15 minutes, and has to look human to telemetry review (Sysmon, Process Explorer, Procmon). Judging weights:
- Process Authenticity 30%
- Human Entropy & Timing 30%
- Complexity of Action 20%
- Resource Efficiency 20%

`docs/prior-art-report.md` holds the research behind the timing and detection choices.

## Running

`human_input.py` and `screen.py` only run on the Windows VM (they use `ctypes.WinDLL`). The pure
modules (`persona`, `timing`, `mouse`, `content`, `vision`, `judge_sim`) import and run on macOS.
On a dev machine:

```
python3 -B -m py_compile agent/*.py && rm -rf agent/__pycache__   # syntax (py_compile writes .pyc)
cd agent && python3 -B judge_sim.py [--seed N]   # scores the shipping models, non-zero on fail
python3 tools/fake_desktop.py [agent|research|office|web|desk|impress] [--seed N] [--quiet] [--stuck|--nogrid|--miss|--nudge|--corner|--collide|--restore|--caps|--badsort]
```
(`--nudge`/`--corner` act only on `agent`, which handles the takeover; `--collide`/`--restore` only
matter to flows that use Save As / Notepad; `--badsort` only to flows that sort: research, agent.)

`judge_sim.py` is the acceptance gate for any change to `persona`/`timing`/`mouse`/`content`/
`vision`: keep it green (spot-check a few `--seed`s; about 2% of arbitrary seeds trip one persona marginally on a single metric, which is sampling noise; a cluster of failures is not). It gates **every one of 40 personas**
against the human population range *and* the persona mean against the research targets, replays
typing through `timing.schedule_keys` (the scheduler `human_input` plays) to require verbatim
output, and self-tests `vision` on synthetic pages.

`tools/fake_desktop.py` is the gate for flow/planner changes: it swaps `ctypes.WinDLL` for a
simulated desktop (Chrome, AOO with a Calc that evaluates our formulas, Notepad, Explorer,
dialogs, clipboard, cursor shapes, page pixels), runs an entry point in simulated time, and
fails on keys into the console/wrong app, escaped errors, windows left open, `Err:` cells, an
agent day under 15 min, a spike of ~0 ms key flights, or a live backspace share outside 1-16%.
Its last line is `trace: <sha1>` over every injected input event and the simulated time (1 ms): same seed, same digest, so compare it before/after a refactor that should not change behaviour. It also models page scroll (pages end), Calc copy/paste with relative references and the "Save changes?" box, Chrome tabs (Ctrl+T/W), the find bar (fails if left open or used while open), a privacy warning that stays up ~1 s after Enter, a bright-card consent modal on cnn.com (fails if never accepted), and Calc bold, currency ($ or S$), row-header selection, Data > Sort (real semantics, relative references follow the rows) and Ctrl+Z; `--badsort` makes the sort pick the wrong key, which the agent must undo.
It models the apps from docs and guesses, so it proves the agent's logic, not the apps'
behaviour - VM runs are still the real test. It lives outside `agent/`: never copy it to the VM.

On the VM, put all `agent/*.py` and `agent/agent.pyw` in one folder (never `__pycache__`), then:
```
python -B smoke_test.py      # opens Notepad, types symbols/formula/URL - check they're exact
python -B research_task.py   # the research goal alone, prints per-step durations
python -B agent.py           # full day with a console (debugging)
agent.pyw                    # the real run: double-click / shortcut to pythonw.exe, no console
```
Each task module also runs on its own (`office_loop.py [impress]`, `web_loop.py`, `desk_tasks.py`).
The entry points set `sys.dont_write_bytecode = True` themselves; `-B` is extra insurance.
**Moving the real mouse pauses the agent** (`Interrupted` once it is left alone ~8 s: the task in
hand is recovered, the day goes on). **Parking it in the top-left corner for 2 s stops it**
(`OperatorAbort`) — the stop button under pythonw.
Verifying VM behaviour means running it and checking Process Explorer/Procmon.

## Architecture

Layered, with the timing/geometry/pixel math kept pure so the offline harnesses test the code that ships:

- `persona.py` — one `Persona` per run (WPM, hold, error rate, Fitts a/b, overshoot, tremor, pause scale) with an Ornstein-Uhlenbeck pace term and a slow fatigue drift. No I/O.
- `timing.py` — pure timing models. `plan_typing()` returns logical `Stroke`s: press-to-press IKI and hold are sampled independently, so keys overlap (real rollover), and `_IKI_NORM` keeps effective WPM ≈ nominal. `schedule_keys()` turns strokes into physical down/up events: Shift is released before the next unshifted key and a key never re-presses while down, so rollover can't change the typed text. Typos mix substitution/transposition/doubled/dropped keys, half caught only 1-3 chars later; `exact=True` corrects every typo (URLs, filenames, cells); `revise=True` (Writer prose) adds false starts erased with Ctrl+Backspace. Letter pairs are scaled by hand/finger (same finger slowest, alternating hands fastest). `plan_scroll()` gives wheel flicks with ramping gaps and re-grip pauses. Macro gaps (`think_s`/`read_s`/`break_s`/`perceive_s`/`short_s`/`review_s`/`dwell_s`) are lognormal with SD ≥ mean and rare multi-minute lulls — no `random.uniform`.
- `mouse.py` — pure `plan_move()`: min-jerk primary stroke (asymmetric velocity peak, bowed to a per-persona side) + 1-3 corrections + occasional overshoot + velocity-scaled AR(1) tremor + jittered 8-16 ms sampling. Lands near, never on, the exact pixel.
- `vision.py` — pure pixel analysis: `is_blank` (has the page painted?), `text_spots` (text-like patches worth pointing at), `warning_button` (the privacy-error pill), `consent_button` (cookie-banner accept: a dark panel, or a bright card on a dimmed page), `same_view` (did a scroll move the page? unchanged = bottom), and `calc_grid`/`highlighted` (Calc cell geometry from the blue-lit row/column headers, and which header is lit).
- `human_input.py` — the Win32 **input** layer. Plays the timing/mouse plans out over `SendInput` (checking every return value), sets `KEYEVENTF_EXTENDEDKEY` for arrows/Home/Del/Win, guards clicks against console/Terminal windows, pauses on a foreign mouse move then raises `Interrupted` (corner park: `OperatorAbort`, a `BaseException`), checks the mouse during typing and in `nap()` (use it, not `time.sleep`, for any pause longer than a motor gap), puts a human flight before every `tap`/`hotkey`, and does window lookup + `launch()`. **`focus(hwnd)`** (Alt+Tab when the target is next in line, else `SetForegroundWindow` via `AttachThreadInput`, verified) must precede every keystroke burst into a reused window.
- `screen.py` — the Win32 **perception** layer, read-only: GDI grab of a window region (`grab_gray`, downscaled; `grab_planes` adds a blue-minus-red plane for Calc headers), the live cursor shape (I-beam / hand), clipboard text + sequence number. `web_loop` points at `vision` spots and clicks only where the cursor confirms a link or text; `read_page` follows a human recording (5-8 downward moves of 5-9 notches, ~15% as Page Down, a lognormal look after each (median 1.1 s, 25% 3-5 s), one 4-8 s hover on text, an early small up-scroll, and a 1-2 step upward review once two grabs match = bottom), and `page_extra` adds a reload or zoom-and-back (p≈0.12, once per visit); Calc values are read back by Ctrl+C (`office_loop.read_cell`). `office_loop.calibrate` measures the Calc grid once per session; `goto_cell` then clicks a random point inside the target cell and verifies the lit headers, falling back to Ctrl+Home + arrows (the keyboard path is also used whenever no grid is found). `fake_desktop` renders the grid and fails on a click during formula entry (AOO would insert a reference), on any read-back retype, and on a formula that refers to a non-number; run Calc flows also with `--nogrid` (keyboard path) and `--miss` (fallback after a missed click).
- `content.py` — a Tracery-style slot grammar for coherent office prose (an order-2 Markov over a tiny air-gapped corpus just copies it), to-do lines, and `WorldState`, which carries shops browsed / models noted / words copied / files saved so notes, sheet and memo cohere.
- `session.py` — the day's long-lived apps: **one Chrome, one OpenOffice, one scratch Notepad** (`open_url` types `www.`, pauses ~0.8 s, then the rest, and leaves a page it gives up on with `leave_page`: Ctrl+W if the visit opened the tab, else Alt+Left, judged by the title; `new_doc`, `reopen` via Ctrl+O or File > Recent Documents, `note`, `save_notes`, `recover`, `shutdown`). `new_doc` identifies the new window by handle. `close_doc` answers AOO's "Save changes?" box with Save (Enter); `recover()` saves a failed task's doc as a draft; `shutdown()` is bounded. `save_as` lives here (AOO and Notepad share it).
- `research_task.py` — the research **goal** as steps with dependencies (`Research.ready()`): visit 3 of 4 shops (copy a word off each homepage into the notes), start the sheet after two, reopen and finish it later with clipboard-checked summary formulas (the per-row Within-budget formula is typed once and pasted down, p≈0.7: `office_loop.paste_down`, also used by the costing sheet), write the memo (with a mid-memo glance back at Chrome), save the notes. When the data rows are in (before any summary row), each of bold header / currency on the price column / Data > Sort by price runs with p≈0.6 (`office_loop.polish`); the sort is read back and undone with Ctrl+Z if the order is wrong. Budget sits in G1 with E empty so the sort sees only A:D. Read-backs accept `$1,260.00` / `S$1,260.00` (`office_loop.parse_number`).
- `office_loop.py` / `web_loop.py` / `desk_tasks.py` — ambient tasks (status update, costing sheet with bold/currency polish, Impress deck behind `ENABLE_IMPRESS`; news browsing with a Ctrl+F lookup; to-dos, a look over Documents in File Explorer). All take a `Session`.
- `agent.py` — the **workday planner**: each turn either advances the goal or picks an ambient task from an order-1 Markov chain, leaning on the goal as slack (time left − estimated remaining goal work × 1.4: the VM runs ~1.25× slower than the fake) runs out. Day length = 15 min floor + 1.5–3.5 min; per-task exceptions → `session.recover()`. `agent.pyw` runs it under pythonw (no console; parent explorer.exe).

## Constraints that shape the code

- **Stdlib only.** The VM has no internet, so there is no pip. Libraries have to be requested from the organisers, who drop them in the SMB share. Don't add third-party dependencies unless it's unavoidable. (pyautogui and pywinauto bundles exist in the team share, but pyautogui moves the cursor with `SetCursorPos` and straight lines, which is easier to detect.)
- **Launch apps through `launch()`, which uses Win+R.** The child process's parent is then `explorer.exe`; this was verified on the VM. Never use `subprocess`, `os.startfile` or COM, because those make `python.exe` (or a COM host) the parent. **Keep the Run string bare** (`chrome`, `swriter`, `scalc`) — no URLs, no flags — so the `RunMRU` entry doesn't match a ClickFix-style rule; type URLs in Chrome's omnibox afterwards. The apps are launched once and kept alive by `session.py` (close docs/tabs, not the process) to avoid cold-start churn.
- **`python.exe`/`pythonw.exe` must not write files.** No log files, no creating folders. Saving happens through the apps' own Save As dialogs, and progress goes only to stdout.
- **The office suite is Apache OpenOffice 4** (`swriter`, `scalc`); there is no MS Office.
  - Calc formulas use `;` as the argument separator.
  - Press Delete after each Calc cell to drop AutoInput suggestions.
- **The mock websites serve homepages only**, and every inner link returns an nginx 404. Navigate by typing URLs in the address bar. When a click navigates away, go back with Alt+Left. `web_loop.SITES` holds the organiser homepages that load in the VM. Any of them may first show Chrome's "Privacy error" page; `Session.open_url` clicks Advanced and then Proceed, found by `vision.warning_button` plus a hand cursor. A site with no Proceed link (HSTS), or one that never paints, is put in `Session.dead_sites` and not typed again that run, and its page is left at once (Chrome keeps a warning's title up ~1 s after Enter, so never type over it). About 35% of site visits include a Ctrl+F lookup of a per-site term (`web_loop.FIND_TERMS`) mid-read, closed with Esc before anything else. Product and price data is generated locally (`research_task.CATALOGUE`), not scraped.
- **VM facts from teammates' desktop-demo recordings** (HANDOFF.md, 10 Oct 2026): the screen is 1280x800 and `fake_desktop` uses the same size. coinbase, amazon and coursera stay on the warning; dell and coinmarketcap paint blank; cloud.google.com and cloudflare.com show no Proceed. Calc headers are 81 px wide and rows 17 px; a lit header is a gradient from (97,168,233) to (60,120,212), which `vision.highlighted` accepts end to end (self-tested). AOO 4.1.16 exposes no cells or menus to UI Automation. Data > Sort worked as Alt+D, S, first letter, Enter. The Mac mouse resting over the noVNC console moves the VM pointer, which the agent treats as a takeover. Ctrl+D opens the Selection List, not Fill Down. After Ctrl+V the pasted range stays marked, so Tab/Enter only move inside it. A host shortcut (Cmd+Shift+5 on the Mac running noVNC) can leave Win+Shift held in the VM, so `human_input.release_stuck()` runs before every chord and typing burst (`fake_desktop.py ... --stuck` tests this); it also taps Caps Lock off if `GetKeyState` shows it on (`--caps` starts the fake with it on and swaps letter case, so a missing guard fails the text checks). Paste only into empty cells and leave a pasted range with a click or Ctrl+Home, never Tab/Enter; `fake_desktop` fails on both.
- **Find windows by title substring**, e.g. "OpenOffice Writer", "Google Chrome", "Save As". `launch()` matches the Run dialog's title exactly.

## Repo rules

- The GitHub repo (`aniketmajee7-pixel/NUSxSTEng_Hackathon`) is private (confirmed 12 Oct 2026); keep it so.
- `agent/` is the main implementation. `legacy/desktop-demo/` is the teammates' earlier pyautogui line, kept for reference and its tests (`cd legacy/desktop-demo && python3 -m unittest discover -s tests`); don't ship it to the VM.
