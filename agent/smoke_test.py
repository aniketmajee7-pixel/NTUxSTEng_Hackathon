"""Smoke test: open Notepad via Win+R and type like a person.

Run on the VM:  python smoke_test.py   (then let go of the mouse)
Check notepad.exe's parent in Process Explorer afterwards (should be explorer.exe), and
that the typed text (capitals, punctuation, "S$1,499!") came out exactly as written.
"""
import sys
sys.dont_write_bytecode = True  # no __pycache__/*.pyc from python.exe (EID 11)

import random
import time

from human_input import (SCREEN_H, SCREEN_W, disable_quickedit, focus, launch, move_to,
                         type_text, wait_for_window)

if __name__ == "__main__":
    disable_quickedit()
    print("Starting in 3s - let go of the mouse.", flush=True)
    time.sleep(3)
    move_to(random.randint(SCREEN_W // 4, 3 * SCREEN_W // 4),
            random.randint(SCREEN_H // 4, 3 * SCREEN_H // 4))
    time.sleep(random.uniform(0.5, 1.2))
    launch("notepad")
    focus(wait_for_window("Notepad", timeout=10))
    time.sleep(random.uniform(1.5, 2.5))
    type_text("Meeting notes for Thursday. Need to follow up with the vendor about the quote "
              "(S$1,499!), and check the budget sheet before Friday.\n"
              '=IF(C5<=$F$1;"Yes";"No")  https://www.dell.com', typo_rate=0, exact=True)
    print("Done.", flush=True)
