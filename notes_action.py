"""The last task of a run: open Notepad and type what the run did.  The notes are left on
screen, unsaved, so whoever comes back to the computer can read what was done."""
import random
import time

import pyautogui as pg

from domain import NOTE_LINE_SECONDS, Phase, choose_note_indexes, typing_seconds

TITLE = r'.*Notepad$'          # "Untitled - Notepad", "*Demo run 17 Oct... - Notepad"
END_RESERVE = 3                # seconds kept after typing, so the run ends on time
END_MARK = 'END'               # the last line of the notes: the run is over


class NotesAction:
    def __init__(self, gui, config):
        self.gui, self.config = gui, config
        self.window = None
        self.typed = 0             # lines typed, the last one possibly shortened
        self.on_page = False       # something has been typed on the page
        self.end_written = False

    def open(self):
        """Double-click the Notepad shortcut on the desktop, like Chrome and Calc.  Without
        one, Win+R and "notepad", which also starts it from the Windows shell."""
        g = self.gui
        name = self.config.get('notes_shortcut', 'Notepad')
        try:
            self.window = g.launch_shortcut(name, TITLE)
            return
        except RuntimeError as exc:
            if 'desktop shortcut' not in str(exc):
                raise               # e.g. a Notepad window was already open
            g.event('notes_shortcut_missing', shortcut=name)
        g.hotkey('win', 'r')
        run = g.window(r'^Run$', 10)
        g.type_text('notepad', run)
        g.press('enter')
        self.window = g.window(TITLE, 20)
        g.focus(self.window)
        self.window.maximize()
        g.event('run_dialog_launch', program='notepad', hwnd=self.window.handle)

    def has_text(self):
        """Whether the page already holds text.  The editor's name ("Text editor") is not
        its text, so the text itself is read, through the Text or the Value pattern."""
        for kind in ('Document', 'Edit'):
            try:
                found = self.window.descendants(control_type=kind)
            except Exception:
                continue
            if not isinstance(found, (list, tuple)):
                continue
            for control in found:
                for read in (lambda: control.iface_text.DocumentRange.GetText(200),
                             lambda: control.iface_value.CurrentValue):
                    try:
                        if control.is_visible() and str(read() or '').strip():
                            return True
                    except Exception:
                        continue
        return False

    def blank_page(self):
        """Windows 11 Notepad reopens the notes of the last session: start a new page."""
        g = self.gui
        title = self.window.window_text().casefold()
        if title.startswith('untitled') and not self.has_text():
            return
        g.hotkey('ctrl', 'n', window=self.window)
        g.sleep(random.uniform(0.6, 1.1))
        g.event('notes_new_page', previous=title[:60])

    def pace(self):
        return self.gui.pace if isinstance(self.gui.pace, (int, float)) else 1.0

    def write(self, lines, end_at):
        """Type the lines that fit before `end_at`, the most important ones kept, in the
        order they happened.  The choice is made again before each line with the speed
        measured so far: in the 7:55 PM run typing ran slower than planned, a minor line
        took the time and the spreadsheet line was cut after "7 items,".  A word is never
        cut on purpose: a line that no longer fits ends after the last word that does."""
        g = self.gui
        pace = self.pace()
        slower, planned, spent = 1.0, 0.0, 0.0   # measured typing time / planned typing time
        pending = list(lines)
        while pending:
            keep = choose_note_indexes(pending, end_at-time.monotonic(), pace*slower)
            if not keep:
                break
            if keep[0]:
                g.event('notes_lines_left_out', lines=keep[0])
            line = pending[keep[0]][1]
            pending = pending[keep[0]+1:]
            if self.on_page:
                g.press('enter'); g.pause()
            plan = typing_seconds(line, pace)
            started = time.monotonic()
            if plan*slower <= end_at-started:
                self.on_page = True
                g.type_text(line, self.window, corrected_typo=random.random() < 0.2)
                self.typed += 1
                planned += plan; spent += time.monotonic()-started
                slower = max(1.0, spent/planned)
                continue
            words = []
            for word in line.split(' '):
                piece = (' ' if words else '')+word
                if typing_seconds(piece, pace)*slower > end_at-time.monotonic():
                    break
                self.on_page = True
                g.type_text(piece, self.window)
                words.append(word)
            if words:
                self.typed += 1
                g.event('notes_line_shortened', words=len(words), of=len(line.split(' ')))
            break

    def write_end(self):
        """The last line, END: the run is over (asked for after the 7:55 PM run)."""
        g = self.gui
        if self.on_page:
            g.press('enter'); g.pause()
        g.type_text(END_MARK, self.window)
        self.end_written = True
        g.event('notes_end_written')

    def run(self, seconds, lines):
        """Open Notepad, type the notes, end them with END, and leave them on screen: no
        Save, no closing.  The lines stop in time for END, which is typed whatever happened
        to them, as long as Notepad is open."""
        from gui_support import Stopped, TimeUp
        g = self.gui
        phase = Phase(seconds)
        result = {'lines_planned': len(lines)}
        end_cost = 2*NOTE_LINE_SECONDS+typing_seconds(END_MARK, self.pace())*1.3
        lines_end = phase.deadline-END_RESERVE-end_cost
        g.time_limit = lines_end
        try:
            self.open()
            self.blank_page()
            self.write(lines, lines_end)
        except TimeUp:
            result['cut_short'] = True
            g.event('notes_time_up', lines_typed=self.typed)
        except (Stopped, pg.FailSafeException):
            raise
        except Exception as exc:
            result['error'] = str(exc)[:160]
            g.event('notes_step_failed', error=str(exc)[:160])
        finally:
            g.time_limit = None
        if self.window is not None:
            g.time_limit = phase.deadline
            try:
                self.write_end()
            except TimeUp:
                g.event('notes_end_time_up')
            except (Stopped, pg.FailSafeException):
                raise
            except Exception as exc:
                result.setdefault('error', str(exc)[:160])
                g.event('notes_end_failed', error=str(exc)[:160])
            finally:
                g.time_limit = None
        result['lines_typed'] = self.typed
        result['end_written'] = self.end_written
        result['left_open'] = self.window is not None
        g.event('notes_phase_complete', elapsed=round(time.monotonic()-phase.started, 1), **result)
        return result
