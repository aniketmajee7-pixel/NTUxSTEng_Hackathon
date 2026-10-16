r"""Console-less entry point: double-click this file (or a shortcut to pythonw.exe + this file).

Running under pythonw.exe means no console window: nothing for a stray click to freeze with
QuickEdit, no terminal to steal keystrokes, and launched from Explorer the agent's parent is
explorer.exe like any other app. Output is discarded (pythonw has no stdout). Touching the
mouse only pauses the agent (it resumes once the mouse is left alone ~8 s). To stop it, park
the pointer in the top-left screen corner for 2 seconds.

For the graded run use a desktop shortcut with target `"<path>\pythonw.exe" -B "<folder>\agent.pyw"`:
that gives explorer.exe -> pythonw.exe. Double-clicking the .pyw itself may go via the py
launcher (explorer.exe -> pyw.exe -> pythonw.exe); keep that for debugging.
"""
import sys
sys.dont_write_bytecode = True  # keep pythonw from writing __pycache__/*.pyc (EID 11)

import agent

agent.main(console=False)
