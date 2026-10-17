# Synthetic Human Agent — Research Findings & Improvement Roadmap

**NTU × ST Engineering Hackathon 2026 · "Building a Synthetic Human Agent"**

> **Context & authorization.** Working material for the NUS × ST Engineering 2026 "Synthetic Human Agent" hackathon — a sanctioned cyber-range competition. The agent performs ordinary office and web tasks on an organiser-provided, isolated Windows VM and is judged on how closely its activity resembles a genuine human user under routine telemetry review. No real target, no third-party system, nothing deployed outside the competition VM. Sources are published papers, open-source user-emulation frameworks, and vendor docs, used for the defensive/competition purpose of building and self-evaluating a realistic synthetic user. "Tell," "detectable," "indicator," and "process lineage" describe how a reviewer scores human-likeness, not how to attack any protected system.

Follow-up to [`prior-art-report.md`](prior-art-report.md). Synthesised from a deep multi-agent sweep (2 Oct 2026) of ~260 sources: 90 GitHub repos, 78 papers, 47 docs, 16 datasets, 28 blogs — including the actual Apache OpenOffice 4.1.6 source (launch chain, lock/temp files, AutoRecovery), pywinauto source, Sysmon rule libraries, and a self-audit of our own `agent/` code against published human-likeness feature sets.

Scoring recap: **Process Authenticity 30% · Human Entropy & Timing 30% · Complexity of Action 20% · Resource Efficiency 20%.**

---

## 0. Bottom line — what this research changes

1. **We have a concrete, unfixed process-authenticity bug.** Running `python agent.py` imports the sibling modules, so `python.exe` writes `__pycache__/*.pyc` — a Sysmon EID 11 file-create by `python.exe`, which violates the repo's own "python.exe must not write files" rule. One-line fix (`sys.dont_write_bytecode = True` before imports, or `python -B`). This is the highest-leverage, lowest-effort change found. **(P0)**
2. **Our timing is high-variance but the wrong *shape*, and mis-calibrated.** A self-audit of `human_input.py` (replayed without sleeping) shows: effective ~32 WPM despite `wpm=55` (the log-normal gap is added *after* the hold, so realised IKI = hold + gap); IKI CV ≈ 0.53 vs human ≈ **1.0**; **0 % rollover** vs human ~25 %; mean burst length 1.0 (humans type in bursts of sub-150 ms intervals); lag-1 autocorrelation ≈ 0.02 vs human 0.087. Macro gaps (`idle(20,90)`) are **uniform**, CV ≈ 0.35 — the exact signature USENIX-2008-style entropy tests were built to catch. **(P0)**
3. **The biggest architectural tells are structural, not per-sample.** Open-act-close *every task* churns EID 1/5 and cold-starts; a fixed storyline order is itself a fingerprint ("Known By Their Actions"); `chrome https://site` writes URLs into RunMRU (a ClickFix-style pattern). None of these need more entropy — they need different structure. **(P0–P1)**
4. **Our SendInput architecture is the right call and is confirmed by new evidence.** The TUM and FP-Agent papers both show DOM/CDP automation is caught ~100 % regardless of trajectory quality, and both note OS-level injection is *outside* that threat model. FP-Agent's **#1 tell is a paste event** — we never paste, which is correct. Do not regress to pywinauto/COM/Selenium for "smoother" input.
5. **One hard limit to state in the write-up:** every `SendInput` event carries `LLKHF_INJECTED`/`LLMHF_INJECTED`. It is invisible to Sysmon / Process Explorer / Procmon, but visible to any low-level input hook. Nothing short of a kernel driver hides it (don't — that breaks constraints and adds EID 6). So **we must ask the organisers whether input-hook/raw-input/screen telemetry is part of the review.** If it is, only behavioural *quality* helps; if not, concentrate on Sysmon-visible macro timing + side-effects.
6. **A 15-minute run is data-starved at the macro level** (~5–15 EID-1/11/22 events per category — below the sample-size floor of RITA ≥12 conns, USENIX ~100 msgs, FCaptcha ≥20 keystrokes). So heavy statistical scrutiny will fall on the **keystroke/mouse streams if recorded**, and on **rule-based lineage checks** for the few process events. Every single macro gap matters.
7. **Prior-report correction:** WindMouse (recommended there as the low-CPU default) is, per the 2026 "Kinematic Features Are All You Need" paper, among the **most detectable** generators. A min-jerk / sub-movement generator costs ~the same CPU and beats it on every measured feature family. Also: original WindMouse is **GPL** (not "licence unstated"); and `humaninput` (towa0) ≠ `human-input` (Purgso) — two different repos.

---

## 1. Findings by scoring dimension

### Process Authenticity (30%)
- **`python.exe` cleanliness is our whole exposure** — the app trees are already authentic. The verified chains are `explorer.exe → swriter.exe → soffice.exe → soffice.bin` (four deep; AOO source confirms `swriter.exe`/`scalc.exe` are real wrapper launchers, `soffice.bin` cmdline begins with the wrapper path — document this so a reviewer doesn't read it as masquerading) and `explorer.exe → chrome.exe → {crashpad, gpu, network/storage/audio utility, renderers}`. Keep `python.exe` with **zero children, zero network, zero file writes**.
- **Keep Run-dialog strings bare.** Every Win+R launch writes `…\Explorer\RunMRU` (EID 13). `chrome https://<site>` is the only one that matches a ClickFix lure rule (Sigma/Splunk/Elastic). Launch plain `chrome`/`swriter`/`scalc`; type URLs in the omnibox. No automation/headless/remote-debugging flags on anything (they surface in EID 1 CommandLine).
- **Let the real apps emit their own side-effects.** Genuine AOO saves produce `.~lock.<name>#` create/delete (EID 11), `sv*.tmp` working copies, `registrymodifications.xcu` temp+replace, `ComDlg32\OpenSavePidlMRU`/`LastVisitedPidlMRU`, and AutoRecovery under `%APPDATA%\OpenOffice\4\user\backup\` if a doc stays open past the 15-min default. Chrome emits DNS (EID 22) → TCP (EID 3) plus ~3 random-hostname probes (IntranetRedirectDetector) and a `BLBeacon` registry key. Don't suppress these with flags.
- **Close cleanly** (Ctrl+Q / Alt+F4 / Ctrl+W only). Never `taskkill`/`tasklist` (they'd appear as `python.exe` children and in Sysmon EID 3 include-lists); a killed `soffice.bin` leaves `.~lock`/`sv*.tmp` and a recovery dialog on next launch.
- **Save native `.odt`/`.ods`.** One `.docx`/`.xlsx` per session is fine (handle the "Keep Current Format" dialog). Never `.xls/.ppt/.rtf/.docm/.xlsm` or `.exe/.dll/.bat/.ps1` (Swift/Sigma include rules); save only under `Documents`.

### Human Entropy & Timing (30%)
- **Two observability regimes — cover both.** Sysmon/Procmon expose only **macro** timing (process start/stop, DNS, TCP, Save-As file-creates). Keystroke/mouse micro-features (Condrey's 7, isHumanCadence's 6, TUM's 17) are scored *only* if the organisers run an input hook / raw-input / screen capture. Spend effort on macro gap shape first (confirmed link to rubric), micro-realism as insurance.
- **Typing — rewrite as an event-timeline scheduler.** Sample key-*down* times (press-to-press IKI) and key-*hold* independently, then emit events sorted by timestamp so a key can go down before the previous releases → **rollover emerges naturally (~20–25 %, up to 40–60 % in bursts)**, and the "IKI = hold + gap" calibration bug disappears. Targets (from Aalto 136M, KeyRecs, Condrey): mean IKI 200–240 ms, hold mean ~110–120 ms (SD ~24, right-skewed), IKI CV ~0.8–1.0, skew ~4–4.5, kurtosis ~25–30, lag-1 autocorrelation ~0.05–0.1 (add an **AR(1)/Ornstein-Uhlenbeck pace term** in log-space), pause density (>500 ms) a few–10 %, non-zero share of <150 ms bursts, backspace 3–9 % of keystrokes by profile, uncorrected error ~1 %.
- **Model revision, not just typos.** Monotonic left-to-right typing with only typo fixes is the documented signature of *transcribed* text (Condrey §VII-C; Mehta 2025). Add real editing: Ctrl+Backspace word delete, Ctrl+←/Home navigation, insert a word earlier, select-and-retype, occasional Ctrl+Z — raises Complexity **and** lowers the copied-text signal at once.
- **Mouse — replace constant-cadence smoothstep.** Current `move_to` steps at a fixed ~10 ms, symmetric speed peak at 50 %, single ballistic stroke, lands exactly on target, white 1 px jitter → low interval CV (~0.01) and no corrections. Move to: asymmetric velocity (peak at 35–45 %, long deceleration), 1–3 corrective sub-movements + occasional 5–15 px overshoot on long moves, **correlated velocity-scaled tremor** (larger when slow — pydoll's trick), jittered 8–16 ms sampling (so Chrome coalesces ≥2 samples/frame), never snap to the exact pixel, varied click-landing offset, lognormal hover-before-click.
- **Macro gaps — kill every `random.uniform`.** Use heavy-tailed (lognormal σ≈1.0–1.4 or Weibull k≈0.7–0.9) think/read/break times with SD ≥ mean, plus occasional 2–5 min lulls. Make per-minute activity non-stationary (PHASE): plan 15 one-minute buckets with 1–2 near-silent minutes and one burst-heavy minute; avoid sustained >95 % activity. Compute **burstiness B (Goh-Barabási)** and **Fano factor over 1-min bins**, not just inter-event CV.
- **Per-session persona.** Draw WPM, hold mean, error rate, Fitts b, overshoot propensity, tremor amplitude, pause scale *once per run* (Aalto has 8 distinct typist clusters), with a slow fatigue/OU drift over the 15 min — detectors that fit population distributions flag identical per-run parameters.

### Complexity of Action (20%)
- **Breadth in the channels that are measured.** PHASE scored default Caldera Human low for thin connection diversity/volume. Real Chrome on mock sites already gives multi-connection pages; add action-order variety and richer in-app actions.
- **Verified AOO keyboard actions** (from mitre/human recipes + AOO help tables — test each on the VM build): Writer headings Ctrl+1/2/3, lists F12/Shift+F12, table Ctrl+F12, Find Ctrl+F, spellcheck F7, Styles F11, Navigator F5, page break Ctrl+Enter, comment Ctrl+Alt+C, centre Ctrl+E, 1.5 spacing Ctrl+5, Export-PDF via Alt+F→D; Calc Name Box Shift+Ctrl+F5, number formats Ctrl+Shift+1..6, show formulas Ctrl+`, sheet switch Ctrl+PgUp/PgDn, Function Wizard Ctrl+F2, define names Ctrl+F3.
- **Cross-app realism:** select a price in Chrome → Ctrl+C → Alt+Tab → Ctrl+V into Calc (real clipboard, EID 24 where enabled) instead of pulling numbers only from the local `CATALOGUE`. Reopen a saved doc via File→Recent or an Explorer double-click (`swriter.exe -o "<path>"` — real EID 11 + RecentDocs). Impress (`simpress`) is available and unused.
- **Coherent persona day** (GHOSTS "knowledge horizon", CUE "Bob", badactors): tie content to what was "learned" while browsing. The project's browse→Calc→Writer storyline is the right shape — make it genuinely knowledge-driven and randomise step order per run.

### Resource Efficiency (20%)
- **No detector penalises CPU directly**, but event-loop *polling* is a smell (PHASE/GHOSTS). Our 250 ms `EnumWindows` poll is fine; avoid busy loops.
- **Fewer cold starts is the win on both readings** (per-`python.exe` or whole-VM): keeping one `soffice.bin` + one Chrome alive avoids repeated 25–34k-file AOO cold starts and 5–10-process Chrome trees. Prefer Ctrl+T tabs over relaunching Chrome.
- **Everything recommended is O(1)/O(steps) stdlib math** (`random.lognormvariate/weibullvariate/gammavariate/paretovariate`, `math.erf`). Cache `GetSystemMetrics` instead of calling per `_move_abs`. Content generation (order-2 Markov ~6 µs/sentence, Tracery ~130 µs) is effectively free.

---

## 2. New prior-art discovered (not in `prior-art-report.md`)

### Frameworks / full projects
| Source | Why it matters | Reuse vs breaks |
|---|---|---|
| **SOCBED** `userbehavior/profile.py` (Fraunhofer FKIE, GPL-3.0) | Best **architecture** match: ~100 lines of seeded, stdlib, config-driven named distributions (Lognormal/Weighted/Constant) over a Session→Routine→Stay-Time loop. | Lift the *pattern + numbers* (Stay-Time `lognormal(2.5, 2.75)` cap 600 s; Session `lognormal(3.15, 1.45)` min). Don't paste (GPL); it drives Selenium + is `subprocess`-launched + writes a log. |
| **mitre/human** `pyhuman` (no LICENSE file; re-implement) | **Only OpenOffice-specific automation found anywhere** — hardcodes `OpenOffice 4\program\soffice`, with ready keystroke recipes for Writer/Calc. Judges may know it. | Reuse the shortcut recipes + "cluster-then-long-gap" shape. Breaks: `os.startfile` (parent=python), `subprocess` shells, pyautogui/selenium deps, **uniform `randrange` timing** (the USENIX-2008 trap). |
| **CAPEv2 `human.py`** (kevoreilly, actively maintained; NOASSERTION) | Most direct **pure-ctypes** human-activity simulator; written so an analysis environment registers a present user. Source of a bug fix **we need**: a synthetic click on a console window (`ConsoleWindowClass`) toggles QuickEdit and **suspends the process writing to it** — and our agent prints progress to stdout. | Reuse (ideas only): console-window click-guard (`WindowFromPoint`+`GetClassNameW`), a dialog auto-clicker watchdog (allow-list "Keep format"/"Yes"), foreground-window cycling. Breaks: uses `SetCursorPos` (a weaker human-likeness match) + fixed 50 ms click + 1 s tick. |
| **D2U** (ORNL, CSET 2021) + `D2U-artifacts` dataset | Data-driven foreground-app emulation: Markov/HMM over the frontmost app sampled every 0.5 s for 42 days. Shows real users have **long spells in one app with brief hops** + long idle periods are normal. | Model `agent.py`'s task picker as an order-1 Markov chain over {Chrome, Writer, Calc, idle} with sampled spell durations, biased to browser↔editor ping-pong. |
| Others surveyed (patterns only): GHOSTS v9, CUE, casper, CyberSim, Sheepl (stick-breaking session schedule + window watchdog), badactors, BUP/BUDA, Invoke-UserSimulator, RangeForge, Sinon, NotARobot (AutoIt, Explorer-launched), **EvidenceForge** (Cisco Talos — a blind-reviewer "tells of synthetic telemetry" checklist worth reading as the judge's-eye view). |

### Papers (new, highest-value)
- **FP-Agent** (UC Davis, arXiv 2605.01247) — 7 agents vs 56 humans; behavioural-only F1 = 0.9993, converges in ~3 min (no warm-up grace). **Top tells: paste event (#1), mouse angle-of-curvature range, hold-latency median, inter-key median.** Human typing: inter-key 120 ms (SD 79), hold 97.5 ms (SD 27); agents <12 ms. Humans do down-down-up-up (rollover). We never paste → good; gives exact numbers to hit.
- **Kinematic Features Are All You Need** (Zenodo 18872499, 2026) — the WindMouse-is-detectable result; 17 kinematic features; use as our mouse self-test.
- **Known By Their Actions** (arXiv 2605.14786) — fingerprints agents from UI-trace *structure*; identifies from <15 events; **a fixed storyline survives delay-randomisation** → randomise action order, never replay stored paths.
- **Condrey timing-forgery** (arXiv 2601.17280, already known) — new detail: human IKI lag-1 autocorr 0.087, skew 4.46, kurtosis 28.3; histogram sampling gives autocorr ~0 (detectable) → **AR(1) α≈0.3 patches it**; IKI 50-bin entropy is the strongest single separator.
- **QUACK / González liveness** — pure i.i.d. histogram sampling is flagged as "too smooth"; the hardest-to-detect generator is an **empirical joint (hold,flight) resampler** → blend ~70/30 empirical/parametric.
- **Chimera** (NDSS 2026) — realism metrics worth adopting as our own: action-vocabulary entropy, gzip-compressibility of the event stream, semantic coherence of documents with browsing.
- Timing/biomechanics canon for parameters: Feit "How We Type" (Aalto), Plamondon sigma-lognormal / iDeLog, MacKenzie Fitts models, Accot-Zhai steering law, Goh-Barabási burstiness, Weibull/lognormal dwell (Nielsen NN/g, Blenn-Van Mieghem), Brysbaert reading-speed, Grudin transcription-error patterns, Mark attention/screen-switching.

### Stdlib-portable input libraries (algorithms/numbers to lift, not import)
- **gba3124/typingnorm** — keystroke norms + 3 validated "is this synthetic?" checks; `check()` is stdlib, needs ≥150 keystrokes. **Use as our offline typing validator.**
- **RoloBits/isHumanCadence** — open keystroke human-score lib (6 features + thresholds).
- **ck0i/SigmaDrift** — "biomechanical replacement for WindMouse"; min-jerk + sub-movement + OU drift params (no licence → params only).
- **martymcenroe/silphe** — stdlib-only human pointer generator + Windows ctypes + analysis; gamma-distributed dt pacing.
- **Purgso/human-input** — per-key log-normal flight/hold CSV tables (dev-time → inline constants).
- **wczwdnb666/computer-control** — single-file pure-ctypes desktop driver with sub-movement maths.
- Reference-only (break stdlib: numpy/scipy/pyautogui/CDP): pydoll (velocity-scaled tremor formula, typo weights), ghost-cursor internals, HumanScroll (reading-aware Weibull scroll), HumanTyping (semi-Markov + proofreading), nothingtoseehere.
- Content/UI helpers to vendor as small pure files if needed: **markovify** `chain.py`, **pytracery**, **pyperclip** (ctypes clipboard read-back to confirm entries landed), **python-mss** `gdi.py` (GDI capture for state checks), tiny `llama2.py`/`llama2.c` (stretch LM; keep Markov as default).

### Datasets (dev-machine calibration → inline constant tables; VM is air-gapped)
Aalto 136M + "How We Type" (typing), KeyRecs (CC-BY-4.0), González/Mendeley liveness (CC-BY-NC), CMU free-vs-transcribed, Kaggle writing-process logs; BOUN/Balabit/SapiMouse/CaptchaSolve30k (mouse); Gooding scroll CSV; LANL UHN & DARPA OpTC & NextronSystems evtx-baseline (host-telemetry shape). Reduce to 50–100 quantiles on a laptop, paste as literals, cite the CC-BY sources in the write-up.

### Windows / OpenOffice telemetry references worth keeping
Sysmon docs + SwiftOnSecurity `sysmon-config` (note issue #177: EID 22 often not generated for Chrome) + olafhartong `sysmon-modular` (use as the "strict judge" config in dev) + TrustedSec Sysmon guide; SigmaHQ Explorer-process-tree-break / Office-susp-child / RunMRU-ClickFix / browser-remote-debugging rules; MITRE ATT&CK T1674 Input Injection (DET0063/0568), T1059.010 (AutoHotKey/AutoIt), T1134.004 (PPID spoof); AOO source for the launch chain, `lockfile.cxx`/`docfile.cxx`/`tempfile.cxx` (save artifacts), `Recovery.xcs` (AutoRecovery defaults), word-completion & first-start-wizard behaviour; Chrome `process_singleton_win.cc` (2nd-launch WM_COPYDATA hand-off), child `/prefetch:N` cmdlines, HTTPS-first interstitial (Chrome 154, Oct 2026).

---

## 3. Improvement roadmap (prioritised avenues)

### P0 — cheap, high leverage, do first
1. **Stop bytecode writes.** `import sys; sys.dont_write_bytecode = True` as the first statement (before sibling imports) of `agent.py`, `smoke_test.py`, `research_task.py`, `office_loop.py`, `web_loop.py`; ship with no `__pycache__`; document "run as `python -B`". Verify in Procmon (`python.exe` CreateFile/WriteFile under the project folder = 0).
2. **Launch bare `chrome`, type the URL in the omnibox** (remove `https://` from RunMRU). Add a `Delete`-before-`Enter` in `launch()` as insurance against Run-dialog inline AutoComplete.
3. **Rewrite `type_text` as an event-timeline scheduler** (fixes the hold+gap calibration bug; produces rollover; press-to-press IKI = two-state burst/pause + AR(1) log-space drift; hold ~N(110,24) right-skewed; nominal WPM = effective WPM). Add digraph-class multipliers (same-key/alt-hand/same-finger/to-digit) *re-fit to speed* (don't hard-code 0.6).
4. **Replace every macro `random.uniform`** (`idle(20,90)`, page-load and read waits) with heavy-tailed draws + occasional multi-minute lulls; add a 250–900 ms "perceive" delay before the first input after a window/page appears.
5. **Guard clicks against the console window** (`WindowFromPoint`+`GetClassNameW == ConsoleWindowClass`) and/or clear QuickEdit with `SetConsoleMode` via ctypes — otherwise a stray click can freeze the agent mid-run.
6. **Check `SendInput`'s return value** and abort loudly (UIPI silently drops injection into higher-integrity or unfocused targets, wasting the 15 min).

### P1 — structural realism
7. **Keep apps alive across tasks.** One Chrome (Ctrl+T/Ctrl+L/Ctrl+W/Ctrl+Tab), one AOO (Ctrl+N, File→Recent, Ctrl+Tab); close once at the end. Cuts EID 1/5 churn, RunMRU writes, cold-start CPU.
8. **Markov/stick-breaking scheduler** for task order + durations (D2U/Sheepl) instead of `random.choices` + fixed storyline; randomise which sites, step order, scroll depth; never replay a stored path.
9. **Sub-movement mouse generator** (min-jerk + 1–3 corrections + overshoot + correlated velocity-scaled tremor + jittered 8–16 ms sampling); retire the WindMouse-style/raw-Bezier default. Click: endpoint ~N with ~4 % natural misses, 100–250 ms settle, lognormal hold ~110 ms, 1 px down→up drift.
10. **Content layer** (fixes 58–77 % duplicate sentences): Mac-authored 200–500 KB office-prose corpus → compact order-2 Markov (overlap-rejected) + Tracery frames + a **WorldState** (persona, facts learned from Chrome titles/clipboard, saved filenames) so Calc/Writer content is coherent with what was browsed. Add distinct-n / duplicate-rate to the validation harness.
11. **Scroll + dwell models:** reading-aware scroll bursts (1–4 notches, lognormal ~270 ms gaps, occasional reversal/overshoot) vs fast-flick mode; page dwell = Weibull(k 0.65–0.8) with a ~⅓ "screened-out <10 s" branch (mock sites are homepage-only → short-tailed, quick 404 bounces).
12. **Diversify launch vectors** (Start-menu, Explorer double-click of a saved doc) for a share of launches — *after verifying parent = explorer.exe in Process Explorer on the VM for each.*
13. **Offline judge-simulator / red-team harness (Mac, stdlib + numpy off-VM).** Refactor generators into pure functions emitting `(t, event)` schedules; score with ports of Condrey-7 + AR(1), TUM Table-1, isHumanCadence, typingnorm `check()`, HOP efficiency/duration, beacon CV/autocorrelation, Fano factor, burstiness B. **Fail the build if any value leaves its human range.** Doubles as judge-facing evidence. (A draft `judge_sim.py` already exists in the research scratchpad.)

### P2 — robustness & verification
14. **Dialog/first-run resilience:** title-based handlers for AOO first-start wizard, "Keep Current Format", Document Recovery, Save-As overwrite, Chrome first-run/default-browser bar, HTTPS-first interstitial. Pre-warm AOO + Chrome once before the graded window.
15. **Fix `hotkey()` with try/finally** (release every key — a mid-chord failure leaves modifiers stuck); assert `GetForegroundWindow` before each typing burst; set `KEYEVENTF_EXTENDEDKEY` for arrows/Home/End/Insert/Delete/Win (they currently lack the E0 prefix a real keyboard sends — a possible hook-level tell; verify with a local logger page).
16. **Human-shaped Calc data** (Benford/lognormal magnitudes, retail .99/.95/.00 price endings, business-day dates, totals as formulas) and varied filenames (not always `<Topic> <ddmm hhmm>`).
17. **Stray-window watchdog** (EnumWindows + WM_CLOSE for windows we didn't open) — no taskkill.
18. **Dev-time Sysmon validation:** install Sysmon + `sysmon-modular`/Swift config on a spare box, run `research_task.py`, export with `wevtutil` (from a normal shell, not the agent), diff against a manual human run and against NextronSystems/evtx-baseline goodware.

### P3 — environment facts to confirm on the VM
19. **`python --version`** — needs ≥ 3.11 for 100 ns `time.sleep`; older quantises to ~15.6 ms (a comb pattern on keystroke/mouse intervals). If < 3.11, add per-step jitter decoupled from sleep granularity (prefer that over `winmm.timeBeginPeriod(1)`, which raises the system timer rate and shows in power analysis).
20. **Pre-flight checklist (run by hand, not by python):** Process Explorer confirms both app trees; EID 1 `ParentCommandLine = Explorer.EXE` with no `-Embedding` and no automation flags; `python.exe` parent is cmd/explorer from a standard path; RunMRU/AutoComplete-Append-Completion state; whether AOO uses the system Save-As dialog.

---

## 4. Open questions — ask the organisers / team (collapses most unknowns at ~zero cost)
1. **Which log sources are scored** beyond Sysmon/Process Explorer/Procmon — specifically, is there any **input-level telemetry** (low-level hook / raw input / screen recording)? This decides whether micro-realism matters at all.
2. **How is "Human Entropy & Timing" computed** — which event series, and is there a hook feed?
3. **How is "Resource Efficiency" measured** — `python.exe` only, or whole-VM?
4. **VM Windows + Python versions**, DPI scaling, and whether the Sysmon config (and which EIDs — is EID 7/10/13 logged?) is public.
5. Whether the **agent's own launch** (how `python.exe` is started) is part of the reviewed tree — if so, start it via Explorer/Run so its parent is `explorer.exe`, not `cmd`/`powershell`.

---

## 5. Hard limits to state honestly in the submission
- **Injected-input flag is unavoidable.** Mitigation is organisational (confirm what's recorded), not technical. Don't attempt kernel-driver injection.
- **Do not split mouse input** between `SetCursorPos` (moves) and `SendInput` (clicks) — a hook would then see clicks with no preceding motion (the TUM teleport/`click-without-movement` signal). Keep everything on `SendInput`.
- **15 min is short.** A single stuck dialog can halve the observed session — robustness is itself a scoring risk across all four dimensions.

---

*Everything above is implementable in the Python standard library + `ctypes`, needs no pip and no on-VM file writes (calibration tables become source literals extracted on a dev machine). GPL/NOASSERTION projects (SOCBED, WindMouse, CAPE, mitre/human, SigmaDrift) are patterns-and-numbers only — re-implement, don't vendor. Full source list (259 entries) in the session's research scratchpad.*
