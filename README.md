# NUS x ST Engineering Hackathon 2026: Synthetic Human Agent

An agent that does routine office and web work on the hackathon Windows VM. It is built to look human at the telemetry level (Sysmon, Process Explorer, Procmon).

Input is injected through Win32 `SendInput` using only the Python standard library (`ctypes`), so it needs no pip installs. Apps are opened through Win+R, which makes their parent process `explorer.exe`.

## Layout

| File | Purpose |
|---|---|
| `agent/agent.py` | The workday planner: interleaves the research goal with ambient tasks (Markov), 15+ min |
| `agent/agent.pyw` | The real entry point: runs the planner under pythonw (no console window) |
| `agent/research_task.py` | The research goal as dependent steps: shops → notes → sheet → reopen + finish → memo |
| `agent/office_loop.py` | Writer status update, Calc costing sheet (cells picked by mouse when the grid is found, total read back via clipboard), Impress deck (flagged) |
| `agent/web_loop.py` | Chrome on the mock sites: read, follow real links (cursor = hand), copy real words (cursor = I-beam) |
| `agent/desk_tasks.py` | To-dos in the scratch Notepad, a look over Documents in File Explorer |
| `agent/session.py` | The day's long-lived Chrome / OpenOffice / Notepad; save, reopen, recover, bounded teardown |
| `agent/human_input.py` | Win32 input: `SendInput` playback, focus, window lookup, `launch()` via Win+R, operator-abort |
| `agent/screen.py` | Win32 perception (read-only): GDI grab, cursor shape, clipboard |
| `agent/vision.py` | Pure pixel analysis: page painted? where is the text? privacy-error button, Calc grid geometry |
| `agent/persona.py` | Per-session persona drawn once per run, with OU pace drift + fatigue. Pure |
| `agent/timing.py` | Pure timing: keystroke plans, the Shift-safe key scheduler, heavy-tailed macro gaps |
| `agent/mouse.py` | Pure sub-movement mouse paths |
| `agent/content.py` | Coherent office prose, to-dos, and the `WorldState` that ties notes/sheet/memo together |
| `agent/smoke_test.py` | Minimal check: opens Notepad and types symbols, a formula and a URL |
| `agent/judge_sim.py` | Offline red-team gate (macOS): scores timing/mouse/content/vision per persona and on average |
| `tools/fake_desktop.py` | Dev-only simulated Windows desktop: runs the real flows on macOS and checks invariants |
| `docs/prior-art-report.md` | Research notes on human-input modelling and detection |
| `docs/research-findings.md` | Follow-up research sweep and the P0-P3 roadmap |
| `legacy/desktop-demo/` | The teammates' earlier pyautogui/pywinauto line (reference + its 317 tests; not shipped). Its VM findings are folded into `agent/` |

## Running (on the VM)

Put all `agent/*.py` files and `agent/agent.pyw` in one folder (not `__pycache__`, not `tools/`).
Open OpenOffice and Chrome once by hand beforehand so first-run prompts are gone. Then:

```
python -B smoke_test.py       # check the typed line comes out exactly
python -B research_task.py    # the research goal alone (~10 min), prints step durations
agent.pyw                     # the real 15+ min run: double-click, no console window
```

For the real run, prefer a shortcut whose target is `pythonw.exe "<folder>\agent.pyw"`, started
from Explorer: then the agent's parent is `explorer.exe` and there is no console. (Double-clicking
the .pyw may go via the `pyw.exe` launcher; check the tree in Process Explorer.) **To stop it,
move the mouse.** `python -B agent.py` runs the same day with a console, for debugging.

On a dev machine (no VM needed):

```
python3 -B agent/judge_sim.py [--seed N]          # behaviour models vs human ranges
python3 tools/fake_desktop.py agent [--seed N]    # whole day on a simulated desktop
```

## VM notes

- No internet. Request software in `#queries`; it appears in the team SMB folder `\\172.29.101.2`.
- The office suite is Apache OpenOffice 4 (`swriter`, `scalc`); there is no Microsoft Office.
- Install `root_ca.crt` from the FTP before browsing. Mock sites serve homepages only.
- Even so, some sites show Chrome's "Your connection is not private" page. The agent clicks
  Advanced, then Proceed. It skips sites that have no Proceed link or stay blank.
- Screen is 1280x800. Start a host screen recording *before* clicking into the noVNC console:
  Cmd+Shift+5 with the console focused leaves Win+Shift held in the VM. The agent releases
  stray modifiers itself, but it is better not to cause them.
