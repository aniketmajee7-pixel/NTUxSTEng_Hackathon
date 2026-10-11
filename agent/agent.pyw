"""Console-less entry point: double-click this file (or a shortcut to pythonw.exe + this file).

Running under pythonw.exe means no console window: nothing for a stray click to freeze with
QuickEdit, no terminal to steal keystrokes, and launched from Explorer the agent's parent is
explorer.exe like any other app. Output is discarded (pythonw has no stdout). To stop the
agent, move the mouse - it notices within a few seconds and hands the desktop back.

Check in Process Explorer which parent you get: a direct shortcut to pythonw.exe gives
explorer.exe -> pythonw.exe; the .pyw file association may go via the py launcher
(explorer.exe -> pyw.exe -> pythonw.exe).
"""
import sys
sys.dont_write_bytecode = True  # keep pythonw from writing __pycache__/*.pyc (EID 11)

import agent

agent.main(console=False)
