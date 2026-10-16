"""Main entry point: a workday planner that interleaves the laptop-research goal with the
small things a person does in between, for at least RUN_MINUTES.

Not a fixed storyline (a replayed order is itself a fingerprint, "Known By Their Actions"):
  * the research goal is a set of steps with dependencies (research_task.Research);
  * each turn the planner either advances the goal or does something ambient - read the news,
    jot a to-do, look over Documents, a short status update, a costing sheet, or nothing -
    picked by an order-1 Markov chain biased to browser<->editor ping-pong;
  * it tracks slack (time left minus the goal's estimated remaining work) and leans on the
    goal as slack runs out, so the goal finishes inside the window without a fixed order.

Run on the VM (prefer no console - see README):
    double-click agent.pyw            # pythonw, parent explorer.exe, no console window
    python -B agent.py                # with a console, for debugging
Moving the real mouse pauses the agent: it waits until the mouse has been left alone for a
few seconds, abandons (saves) the task in hand and carries on. To stop it, park the pointer
in the top-left screen corner for 2 s (OperatorAbort). Progress goes to stdout only (none
under pythonw); nothing is written to disk by python.
"""
import sys
sys.dont_write_bytecode = True  # keep python from writing __pycache__/*.pyc (EID 11)

import time
import traceback

import timing
from desk_tasks import jot_todo, look_over_documents
from human_input import OperatorAbort, disable_quickedit, idle
from office_loop import calc_task, impress_task, writer_task
from research_task import Research
from session import Session
from web_loop import web_task

RUN_MINUTES = 15                # floor: the day never ends before this
TARGET_EXTRA = (1.5, 3.5)       # planned day length = RUN_MINUTES + this many minutes
HARD_STOP_MINUTES = 5           # never run more than this past the plan to finish the goal
ENABLE_IMPRESS = False          # flip on once `python office_loop.py impress` works on the VM

AMBIENT = {"web": web_task, "todo": jot_todo, "explorer": look_over_documents,
           "writer": writer_task, "calc": calc_task, "impress": impress_task}
# Order-1 Markov over ambient activities, given the last one. Browser<->editor ping-pong,
# short desk tasks, and idle spells; heavy editor tasks rarely back to back.
TRANSITIONS = {
    None:       [("web", 4), ("todo", 2), ("explorer", 1), ("idle", 1)],
    "web":      [("web", 1), ("todo", 3), ("writer", 2), ("calc", 2), ("explorer", 1), ("idle", 2), ("impress", 1)],
    "todo":     [("web", 4), ("explorer", 1), ("writer", 1), ("idle", 2)],
    "explorer": [("web", 3), ("todo", 2), ("writer", 1), ("idle", 2)],
    "writer":   [("web", 4), ("todo", 2), ("calc", 1), ("idle", 2)],
    "calc":     [("web", 4), ("todo", 1), ("writer", 1), ("idle", 2)],
    "impress":  [("web", 3), ("todo", 2), ("idle", 2)],
    "idle":     [("web", 4), ("todo", 2), ("explorer", 1), ("writer", 1), ("calc", 1)],
}
AMBIENT_EST = {"web": 90, "todo": 20, "explorer": 50, "writer": 120, "calc": 120,
               "impress": 150, "idle": 60}


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def recover(session):
    """Save + close the half-done doc; a failing recovery must not end the day either."""
    try:
        session.recover()
    except Exception as e:
        log(f"recovery failed too ({e}); carrying on")


def run(name, fn, session):
    """Run one task; on failure clean up and carry on, as a person would."""
    started = time.time()
    log(f"{name} ...")
    status = "finished"
    try:
        fn()
    except Exception:                                   # OperatorAbort is a BaseException: passes through
        status = "failed"
        traceback.print_exc()
        recover(session)
    print(f"    {status} in {time.time() - started:.0f}s", flush=True)


class Workday:
    def __init__(self, session, minutes=RUN_MINUTES):
        self.s = session
        self.rng = session.rng
        self.start = time.time()
        self.floor = self.start + minutes * 60
        self.deadline = self.floor + self.rng.uniform(*TARGET_EXTRA) * 60
        self.goal = Research(session)
        self.last = None

    def _ambient(self, slack):
        # idle always fits (its length is capped by the slack); never two to-dos in a row,
        # which is what the end of a tight day used to collapse into
        weights = [(k, w) for k, w in TRANSITIONS[self.last]
                   if (k != "impress" or ENABLE_IMPRESS) and (k == "idle" or AMBIENT_EST[k] < max(slack, 30))
                   and not (k == "todo" and self.last == "todo")]
        if not any(k == "idle" for k, _ in weights):
            weights.append(("idle", 1))
        kinds, ws = zip(*weights)
        return self.rng.choices(kinds, ws)[0]

    def step(self):
        """One planner turn. Returns False when the day is over."""
        now = time.time()
        goal_done = self.goal.done()
        # deadline is always past the floor, so the day never ends before RUN_MINUTES
        if goal_done and now >= self.deadline:
            return False
        if now >= self.deadline + HARD_STOP_MINUTES * 60:
            log("hard stop: the goal is unfinished, ending the day anyway")
            return False
        # x1.4: the teammates' full VM run was ~1.25x slower than their pilot (10 Oct), and the
        # estimates are fake-desktop timings, so keep a margin above that on the goal's work
        slack = self.deadline - now - self.goal.remaining_est() * 1.4
        ready = self.goal.ready()
        if ready and (slack < 60 or self.rng.random() < 0.55):
            # mostly continue the obvious next step, sometimes another eligible one
            name, fn = ready[0] if self.rng.random() < 0.7 else self.rng.choice(ready)
            run(name, lambda: self.goal.run_step(name, fn), self.s)
            gap = timing.break_s(6, 25)
        else:
            kind = self._ambient(slack)
            self.last = kind
            if kind == "idle":
                log("idle")
                idle(min(timing.break_s(30, 120), max(10.0, slack)))
                return True
            run(kind, lambda: AMBIENT[kind](self.s), self.s)
            gap = timing.break_s(8, 35)
        idle(gap)
        return True


def main(console=True):
    if console:
        disable_quickedit()
    if sys.version_info < (3, 11):
        log(f"WARNING: Python {sys.version.split()[0]} quantizes time.sleep to ~15.6 ms; "
            "keystroke/mouse intervals may show a comb pattern. Prefer >= 3.11.")
    log("Starting in 5s - let go of the mouse (park it in the top-left corner to stop).")
    time.sleep(5)
    session = Session()
    day = Workday(session)
    try:
        while True:
            try:
                if not day.step():
                    break
            except Exception:                           # e.g. Interrupted during a break
                traceback.print_exc()
                recover(session)
                time.sleep(5)                           # never spin on a failure
        for _ in range(2):
            try:
                session.shutdown()
                break
            except Exception:
                traceback.print_exc()
                time.sleep(2)
        log("Session complete.")
    except OperatorAbort as e:
        log(f"Stopped: {e}. Leaving the desktop as it is.")


if __name__ == "__main__":
    main()
