# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

This repo is a hackathon entry (NUS x ST Engineering 2026, "Synthetic Human Agent", Team 21). The agent does a routine office session on an organiser-provided Windows 11 VM within 15 minutes, and has to look human to telemetry review (Sysmon, Process Explorer, Event Viewer). Technical judging weights:
- Process Authenticity 30%
- Human Entropy & Timing 30%
- Complexity of Action 20%
- Resource Efficiency 20%

Finals (6 teams): 10 min presentation, 3 min demo, 2 min Q&A, marked on Problem Understanding (3), Innovation & Creativity (3), Presentation & Q&A (4).

The top-level `*.py`, `config.json`, `requirements.txt` and `create_shortcut.ps1` are the code **submitted on 17 Oct 2026**; `README.md` is the public overview; `DEVLOG.md` is the team's running log of VM rounds (its last section is current). The implementation report, research notes and finals slides are kept off GitHub.

## Running

The agent needs Windows for a real run (pyautogui, pywinauto, pywin32). Offline, from the repo root:

```
python3 -B -m unittest discover -s tests && rm -rf tests/__pycache__   # the gate for any code change
python3 -B tools/make_zip.py [out.zip]    # VM build: top-level code files + BUILD.json (SHA-256 per file)
```

The tests fake pyautogui/pywinauto/win32 (`tests/fake_sheet.py`, `tests/sim_calc.py` simulates OpenOffice Calc with its quirks, a simulated clock and a slowness factor). On the VM: copy the ZIP's files to `C:\Users\User\Desktop\agent`, double-click the **start agent** shortcut (`pythonw.exe ...\main.py`), choose Full demo. F8 or a screen corner stops it.

## Architecture

- `main.py` — orchestrator. Tk launcher (Full demo / Browser pilot / Inventory pilot); `check_files` imports every module the mode needs and compares files with `BUILD.json` before any input; `preflight` (apps already open, shortcuts, Caps Lock, keyboard layout); phases with hard deadlines: browsing `browser_phase_seconds` 270, Calc `full_phase_seconds` 480 + unused browsing time, capped so the notes keep 90 s, notes until 5 s before `total_demo_seconds` 900 (skipped under 45 s). The run's account stays in memory; nothing is written to disk. A complete run ends quietly; otherwise a result box.
- `browser_action.py` — 27 homepages shuffled, known-blocked ones last; new tab, typed address with corrected slips; certificate warning checked against the requested host, Advanced, Proceed (HSTS / own 403-404 skipped); consent accepted, offers closed with X/Esc; reading styles skim/normal/deep (22/58/20%); find-in-page on ≤2 sites; ≤2 returns to the first tab; bounded UIA walks.
- `inventory_action.py` — Calc read from pixels (`domain.find_grid`: highlighted headers + 1-px separators), cells clicked, ranges dragged (p 0.6) or Shift+clicked, keyboard fallback; essentials first (headers, Save As, records, corrections, check, totals, save), polish if it fits (bold, currency, sort by Item), review until the cap; hurry mode at checkpoints; the saved .ods is opened as a ZIP and validated (7 records, total 3,486, budget result).
- `notes_action.py` — Notepad from its icon (Win+R fallback), lines re-chosen by priority against measured typing speed, END last, left open unsaved.
- `gui_support.py` — the only place input is produced: `move` (Fitts time + `domain.pointer_path`), `click` (hover, target re-check, 50-140 ms press), `type_text` (per-key gaps, real Shift, slips fixed), extended-key arrows via `keybd_event`, stuck modifiers released, `launch_shortcut` (Win+D, double-click the icon), `grab` (one GDI BitBlt), `TimeUp`/F8/corner stop.
- `save_dialog.py` — reads the Save dialog's address bar, scrolls the left pane to Desktop, double-clicks the agent folder, answers "replace?" No outside it, picks a free name.
- `domain.py` — pure: timing (`fitts_duration`, `min_jerk`, `pointer_path`, `heavy_pause`, `typing_pause`, `adjacent_keys`), grid detection, ODS validation, `run_notes`.

## Constraints that shape the code

- **Apps are launched by double-clicking their desktop icons** (`launch_shortcut`), so Explorer is the parent. Never `subprocess`, `os.startfile`, `Application.start` or COM.
- **The agent writes no files during a run** (`sys.dont_write_bytecode = True` before imports; no logs, report or history). Only Calc writes the workbook via Save As. Progress lives in memory and the result box.
- **Timing**: `pg.PAUSE = 0`; pacing pauses are drawn from distributions and waits poll for events (every 0.4 s); a few short fixed delays (0.2–1.0 s) remain after Esc/Enter/Win+D/focus (the report's "no fixed delays" overstates this).
- **OpenOffice Calc 4.1.16**: `;` argument separator; exposes no cells, Name Box or menus to UI Automation; Ctrl+D opens the Selection List (not Fill Down), so formulas are copied and pasted; after a paste the range stays marked, leave it with a click or an arrow key.
- **Mock websites serve homepages only**; many show Chrome's privacy warning first (organisers' rule: Advanced, then Proceed; skip if there is no Proceed).
- **VM facts**: screen 1280x800; Calc highlight (67,115,213), header background 247, separators 105; a host mouse resting over the noVNC console moves the VM pointer; a host Cmd+Shift+5 can leave Win+Shift held (released before each chord).

## Repo rules

- The GitHub repo (`aniketmajee7-pixel/NTUxSTEng_Hackathon`, renamed from `NUSxSTEng_Hackathon`; the old URL redirects) is public on purpose (since late Oct 2026), so anything pushed is visible to everyone.
- Keep the top-level `*.py` identical to the submission unless a change is deliberate; `tools/make_zip.py` regenerates `BUILD.json` for any new build.
- GitHub holds code only: no report, slides, videos or research docs (the report PDF was purged from history on 26 Oct 2026). The README's demo video is a GitHub user-attachments upload, not a repo file.
