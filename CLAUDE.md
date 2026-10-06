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
python3 tools/fake_desktop.py [agent|research|office|web|desk|impress] [--seed N] [--quiet]
```

`judge_sim.py` is the acceptance gate for any change to `persona`/`timing`/`mouse`/`content`/
`vision`: keep it green (spot-check a few `--seed`s). It gates **every one of 40 personas**
against the human population range *and* the persona mean against the research targets, replays
typing through `timing.schedule_keys` (the scheduler `human_input` plays) to require verbatim
output, and self-tests `vision` on synthetic pages.

`tools/fake_desktop.py` is the gate for flow/planner changes: it swaps `ctypes.WinDLL` for a
simulated desktop (Chrome, AOO with a Calc that evaluates our formulas, Notepad, Explorer,
dialogs, clipboard, cursor shapes, page pixels), runs an entry point in simulated time, and
fails on keys into the console/wrong app, escaped errors, windows left open, or `Err:` cells.
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
**Moving the real mouse stops the agent** (`OperatorAbort`) — the stop button under pythonw.
Verifying VM behaviour means running it and checking Process Explorer/Procmon.

## Architecture

Layered, with the timing/geometry/pixel math kept pure so the offline harnesses test the code that ships:

- `persona.py` — one `Persona` per run (WPM, hold, error rate, Fitts a/b, overshoot, tremor, pause scale) with an Ornstein-Uhlenbeck pace term and a slow fatigue drift. No I/O.
- `timing.py` — pure timing models. `plan_typing()` returns logical `Stroke`s: press-to-press IKI and hold are sampled independently, so keys overlap (real rollover), and `_IKI_NORM` keeps effective WPM ≈ nominal. `schedule_keys()` turns strokes into physical down/up events: Shift is released before the next unshifted key and a key never re-presses while down, so rollover can't change the typed text. `exact=True` corrects every typo (URLs, filenames, cells). Macro gaps (`think_s`/`read_s`/`break_s`/`perceive_s`/`short_s`) are lognormal with SD ≥ mean and rare multi-minute lulls — no `random.uniform`.
- `mouse.py` — pure `plan_move()`: min-jerk primary stroke (asymmetric velocity peak) + 1-3 corrections + occasional overshoot + velocity-scaled tremor + jittered 8-16 ms sampling. Lands near, never on, the exact pixel.
- `vision.py` — pure pixel analysis: `is_blank` (has the page painted?) and `text_spots` (text-like patches worth pointing at).
- `human_input.py` — the Win32 **input** layer. Plays the timing/mouse plans out over `SendInput` (checking every return value), sets `KEYEVENTF_EXTENDEDKEY` for arrows/Home/Del/Win, guards clicks against console/Terminal windows, raises `OperatorAbort` (a `BaseException`) when the real mouse moves, and does window lookup + `launch()`. **`focus(hwnd)`** (Alt+Tab when the target is next in line, else `SetForegroundWindow` via `AttachThreadInput`, verified) must precede every keystroke burst into a reused window.
- `screen.py` — the Win32 **perception** layer, read-only: GDI grab of a window region (downscaled gray), the live cursor shape (I-beam / hand), clipboard text + sequence number. `web_loop` points at `vision` spots and clicks only where the cursor confirms a link or text; Calc values are read back by Ctrl+C.
- `content.py` — a Tracery-style slot grammar for coherent office prose (an order-2 Markov over a tiny air-gapped corpus just copies it), to-do lines, and `WorldState`, which carries shops browsed / models noted / words copied / files saved so notes, sheet and memo cohere.
- `session.py` — the day's long-lived apps: **one Chrome, one OpenOffice, one scratch Notepad** (`open_url`, `new_doc`, `reopen` via Ctrl+O or File > Recent Documents, `note`, `save_notes`, `read_cell`, `recover`, `shutdown`). `new_doc` identifies the new window by handle. `recover()` saves a failed task's doc as a draft; `shutdown()` is bounded. `save_as` lives here (AOO and Notepad share it).
- `research_task.py` — the research **goal** as steps with dependencies (`Research.ready()`): visit 3 of 4 shops (copy a word off each homepage into the notes), start the sheet after two, reopen and finish it later with clipboard-checked summary formulas, write the memo (with a mid-memo glance back at Chrome), save the notes.
- `office_loop.py` / `web_loop.py` / `desk_tasks.py` — ambient tasks (status update, costing sheet, Impress deck behind `ENABLE_IMPRESS`; news browsing; to-dos, a look over Documents in File Explorer). All take a `Session`.
- `agent.py` — the **workday planner**: each turn either advances the goal or picks an ambient task from an order-1 Markov chain, leaning on the goal as slack (time left − estimated remaining goal work) runs out. Day length = 15 min floor + 1.5–3.5 min; per-task exceptions → `session.recover()`. `agent.pyw` runs it under pythonw (no console; parent explorer.exe).

## Constraints that shape the code

- **Stdlib only.** The VM has no internet, so there is no pip. Libraries have to be requested from the organisers, who drop them in the SMB share. Don't add third-party dependencies unless it's unavoidable. (pyautogui and pywinauto bundles exist in the team share, but pyautogui moves the cursor with `SetCursorPos` and straight lines, which is easier to detect.)
- **Launch apps through `launch()`, which uses Win+R.** The child process's parent is then `explorer.exe`; this was verified on the VM. Never use `subprocess`, `os.startfile` or COM, because those make `python.exe` (or a COM host) the parent. **Keep the Run string bare** (`chrome`, `swriter`, `scalc`) — no URLs, no flags — so the `RunMRU` entry doesn't match a ClickFix-style rule; type URLs in Chrome's omnibox afterwards. The apps are launched once and kept alive by `session.py` (close docs/tabs, not the process) to avoid cold-start churn.
- **`python.exe`/`pythonw.exe` must not write files.** No log files, no creating folders. Saving happens through the apps' own Save As dialogs, and progress goes only to stdout.
- **The office suite is Apache OpenOffice 4** (`swriter`, `scalc`); there is no MS Office.
  - Calc formulas use `;` as the argument separator.
  - Press Delete after each Calc cell to drop AutoInput suggestions.
- **The mock websites serve homepages only**, and every inner link returns an nginx 404. Navigate by typing URLs in the address bar. When a click navigates away, go back with Alt+Left. Use only the 10 "clean" sites in `web_loop.SITES`; the others show certificate warnings. Product and price data is generated locally (`research_task.CATALOGUE`), not scraped.
- **Find windows by title substring**, e.g. "OpenOffice Writer", "Google Chrome", "Save As". `launch()` matches the Run dialog's title exactly.

## Repo rules

- Don't commit or push until the user confirms the GitHub repo (`aniketmajee7-pixel/NUSxSTEng_Hackathon`) has been made private.
