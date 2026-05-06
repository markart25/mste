#!/usr/bin/env python3
"""
mste - a small nano-like terminal text editor written in Python.

Keybindings:
  Ctrl-S  Save
  Ctrl-O  Save As
  Ctrl-X  Quit (prompts if unsaved)
  Ctrl-G  Help
  Ctrl-K  Cut current line
  Ctrl-U  Paste cut line
  Ctrl-W  Search
  Ctrl-A  Select all
  Ctrl-Z  Undo
  Ctrl-Y  Redo
  Arrows / PgUp / PgDn / Home / End  Move
  Backspace / Delete / Enter / Tab    Edit
"""

import curses
import os
import sys
import argparse

VERSION = "0.2.0"
TAB_SIZE = 4
UNDO_LIMIT = 200  # max number of snapshots kept on the undo stack


class Editor:
    def __init__(self, stdscr, filename=None):
        self.stdscr = stdscr
        self.filename = filename
        self.lines = [""]
        self.cy = 0          # cursor line index
        self.cx = 0          # cursor column index (in line)
        self.row_off = 0     # vertical scroll offset
        self.col_off = 0     # horizontal scroll offset
        self.dirty = False
        self.status = ""
        self.cut_buffer = []
        self.quit = False
        # Selection: (anchor_y, anchor_x). When None, no selection is active.
        # The active selection runs from anchor to (cy, cx).
        self.sel_anchor = None
        # Undo / redo stacks. Each entry is a snapshot tuple:
        #   (lines_tuple, cy, cx, dirty)
        # `lines` is stored as a tuple so it's immutable and cheap to compare.
        self.undo_stack = []
        self.redo_stack = []
        # Coalescing: consecutive printable-char insertions merge into one
        # undo entry until the user does something else. We tag each snapshot
        # with the kind of edit that produced it; matching kinds coalesce.
        self.last_edit_kind = None

        if filename and os.path.exists(filename):
            self.load(filename)

        curses.raw()
        self.stdscr.keypad(True)
        try:
            curses.start_color()
            curses.use_default_colors()
            curses.init_pair(1, curses.COLOR_BLACK, curses.COLOR_WHITE)  # status bar
            curses.init_pair(2, curses.COLOR_WHITE, curses.COLOR_BLUE)   # title bar
            curses.init_pair(3, curses.COLOR_WHITE, -1)                  # gutter (dim)
            curses.init_pair(4, curses.COLOR_YELLOW, -1)                 # gutter, current line
            curses.init_pair(5, curses.COLOR_BLACK, curses.COLOR_CYAN)   # selection highlight
        except curses.error:
            pass

    # ---------- file I/O ----------
    def load(self, path):
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
            self.lines = content.split("\n") if content else [""]
            if self.lines == []:
                self.lines = [""]
            self.status = f'Read "{path}" ({len(self.lines)} lines)'
        except OSError as e:
            self.status = f"Error reading {path}: {e}"

    def save(self, path=None):
        path = path or self.filename
        if not path:
            path = self.prompt("File Name to Write: ")
            if not path:
                self.status = "Save cancelled"
                return False
            self.filename = path
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write("\n".join(self.lines))
            self.dirty = False
            self.status = f'Wrote {len(self.lines)} lines to "{path}"'
            return True
        except OSError as e:
            self.status = f"Error saving: {e}"
            return False

    # ---------- selection ----------
    def selection_bounds(self):
        """Return ((start_y, start_x), (end_y, end_x)) normalized so start <= end,
        or None if there's no active selection."""
        if self.sel_anchor is None:
            return None
        ay, ax = self.sel_anchor
        cy, cx = self.cy, self.cx
        if (ay, ax) == (cy, cx):
            return None  # zero-width selection counts as no selection
        if (ay, ax) <= (cy, cx):
            return (ay, ax), (cy, cx)
        return (cy, cx), (ay, ax)

    def clear_selection(self):
        self.sel_anchor = None

    def delete_selection(self):
        bounds = self.selection_bounds()
        if bounds is None:
            return False
        self.push_undo("replace")
        (sy, sx), (ey, ex) = bounds
        if sy == ey:
            self.lines[sy] = self.lines[sy][:sx] + self.lines[sy][ex:]
        else:
            head = self.lines[sy][:sx]
            tail = self.lines[ey][ex:]
            self.lines[sy] = head + tail
            del self.lines[sy + 1 : ey + 1]
        self.cy, self.cx = sy, sx
        self.clear_selection()
        self.dirty = True
        return True

    def select_all(self):
        if not self.lines:
            return
        self.sel_anchor = (0, 0)
        self.cy = len(self.lines) - 1
        self.cx = len(self.lines[self.cy])
        self.status = "Selected all"

    # ---------- undo / redo ----------
    def _snapshot(self):
        """Return a snapshot of buffer state suitable for undo."""
        return (tuple(self.lines), self.cy, self.cx, self.dirty)

    def _restore(self, snap):
        lines, cy, cx, dirty = snap
        self.lines = list(lines)
        if not self.lines:
            self.lines = [""]
        self.cy = min(cy, len(self.lines) - 1)
        self.cx = min(cx, len(self.lines[self.cy]))
        self.dirty = dirty
        self.clear_selection()

    def push_undo(self, kind):
        """Save current state to undo stack before an edit.

        `kind` is a tag describing the edit type ('type', 'newline',
        'backspace', 'delete', 'cut', 'paste', 'replace', etc.). Consecutive
        edits of the kinds 'type', 'backspace', or 'delete' are coalesced
        into a single undo step so the user doesn't have to mash Ctrl-Z to
        undo a word. Any edit immediately after a 'replace' (selection
        deleted by a keystroke) also coalesces — this means 'select-all +
        type', 'select-all + paste', etc. each count as a single undo.
        """
        coalesce_kinds = ("type", "backspace", "delete")
        coalesce = self.undo_stack and (
            (kind in coalesce_kinds and kind == self.last_edit_kind)
            or (self.last_edit_kind == "replace")
        )
        if not coalesce:
            self.undo_stack.append((kind, self._snapshot()))
            if len(self.undo_stack) > UNDO_LIMIT:
                self.undo_stack.pop(0)
        # Any new edit invalidates the redo history.
        self.redo_stack.clear()
        self.last_edit_kind = kind

    def undo(self):
        if not self.undo_stack:
            self.status = "Nothing to undo"
            return
        # Save current state onto redo stack so the user can re-apply.
        kind, snap = self.undo_stack.pop()
        self.redo_stack.append((kind, self._snapshot()))
        self._restore(snap)
        self.last_edit_kind = None  # break coalescing chain
        self.status = "Undo"

    def redo(self):
        if not self.redo_stack:
            self.status = "Nothing to redo"
            return
        kind, snap = self.redo_stack.pop()
        self.undo_stack.append((kind, self._snapshot()))
        self._restore(snap)
        self.last_edit_kind = None
        self.status = "Redo"

    # ---------- drawing ----------
    def gutter_width(self):
        # digits needed for the largest line number, plus one space of padding
        return len(str(max(1, len(self.lines)))) + 1

    def draw(self):
        self.stdscr.erase()
        h, w = self.stdscr.getmaxyx()
        text_h = h - 3  # title + status + help
        gw = self.gutter_width()
        text_w = max(1, w - gw - 1)

        # Title bar
        title = f" mste {VERSION} "
        fname = self.filename or "[New File]"
        modified = " [Modified]" if self.dirty else ""
        center = f" File: {fname}{modified} "
        bar = title + center.center(max(0, w - len(title) - 1))
        bar = bar[: w - 1]
        try:
            self.stdscr.addstr(0, 0, bar.ljust(w - 1), curses.color_pair(2) | curses.A_REVERSE)
        except curses.error:
            pass

        # Text area + gutter
        num_width = gw - 1  # digits only, last char is the padding space
        sel = self.selection_bounds()
        for screen_row in range(text_h):
            file_row = self.row_off + screen_row
            if file_row >= len(self.lines):
                # empty gutter for blank lines past EOF
                try:
                    self.stdscr.addstr(1 + screen_row, 0, " " * gw,
                                       curses.color_pair(3) | curses.A_DIM)
                except curses.error:
                    pass
                continue

            # Gutter
            num_str = str(file_row + 1).rjust(num_width) + " "
            is_current = (file_row == self.cy)
            attr = (curses.color_pair(4) | curses.A_BOLD) if is_current \
                else (curses.color_pair(3) | curses.A_DIM)
            try:
                self.stdscr.addstr(1 + screen_row, 0, num_str, attr)
            except curses.error:
                pass

            # Line text
            line = self.lines[file_row]
            visible = line[self.col_off : self.col_off + text_w]
            try:
                self.stdscr.addstr(1 + screen_row, gw, visible)
            except curses.error:
                pass

            # Selection overlay (gutter is excluded - line numbers never highlight)
            if sel is not None:
                (sy, sx), (ey, ex) = sel
                if sy <= file_row <= ey:
                    # range of columns in this row that are selected
                    col_start = sx if file_row == sy else 0
                    if file_row == ey:
                        col_end = ex
                    else:
                        col_end = len(line) + 1  # +1 to highlight the trailing newline visually
                    # clip against the visible window
                    vis_start = max(col_start, self.col_off)
                    vis_end = min(col_end, self.col_off + text_w)
                    if vis_end > vis_start:
                        # build the highlight string: actual chars + one trailing space
                        # for the newline if this row's selection extends past EOL
                        end_in_line = min(vis_end, len(line))
                        chunk = line[vis_start:end_in_line]
                        if vis_end > len(line):
                            chunk += " " * (vis_end - len(line))
                        screen_x = gw + (vis_start - self.col_off)
                        try:
                            self.stdscr.addstr(1 + screen_row, screen_x, chunk,
                                               curses.color_pair(5))
                        except curses.error:
                            pass

        # Status line
        status_text = self.status if self.status else f"Ln {self.cy + 1}, Col {self.cx + 1}"
        try:
            self.stdscr.addstr(h - 2, 0, status_text[: w - 1].ljust(w - 1),
                               curses.color_pair(1) | curses.A_REVERSE)
        except curses.error:
            pass

        # Help bar
        help_text = "^S Save  ^X Quit  ^Z Undo  ^Y Redo  ^A SelAll  ^K Cut  ^U Paste  ^W Search  ^G Help"
        try:
            self.stdscr.addstr(h - 1, 0, help_text[: w - 1])
        except curses.error:
            pass

        # Position cursor (offset by gutter)
        screen_y = 1 + (self.cy - self.row_off)
        screen_x = gw + (self.cx - self.col_off)
        if 0 <= screen_y < h and 0 <= screen_x < w:
            try:
                self.stdscr.move(screen_y, screen_x)
            except curses.error:
                pass

        self.stdscr.refresh()
        self.status = ""  # status is one-shot

    # ---------- scrolling ----------
    def scroll(self):
        h, w = self.stdscr.getmaxyx()
        text_h = h - 3
        text_w = max(1, w - self.gutter_width() - 1)

        if self.cy < self.row_off:
            self.row_off = self.cy
        if self.cy >= self.row_off + text_h:
            self.row_off = self.cy - text_h + 1
        if self.cx < self.col_off:
            self.col_off = self.cx
        if self.cx >= self.col_off + text_w:
            self.col_off = self.cx - text_w + 1

    # ---------- editing ----------
    def insert_char(self, ch):
        self.push_undo("type")
        line = self.lines[self.cy]
        self.lines[self.cy] = line[: self.cx] + ch + line[self.cx :]
        self.cx += 1
        self.dirty = True

    def insert_newline(self):
        self.push_undo("newline")
        line = self.lines[self.cy]
        self.lines[self.cy] = line[: self.cx]
        self.lines.insert(self.cy + 1, line[self.cx :])
        self.cy += 1
        self.cx = 0
        self.dirty = True

    def backspace(self):
        if self.cx == 0 and self.cy == 0:
            return  # nothing to undo
        self.push_undo("backspace")
        if self.cx > 0:
            line = self.lines[self.cy]
            self.lines[self.cy] = line[: self.cx - 1] + line[self.cx :]
            self.cx -= 1
            self.dirty = True
        elif self.cy > 0:
            prev = self.lines[self.cy - 1]
            cur = self.lines[self.cy]
            self.cx = len(prev)
            self.lines[self.cy - 1] = prev + cur
            del self.lines[self.cy]
            self.cy -= 1
            self.dirty = True

    def delete(self):
        line = self.lines[self.cy]
        if self.cx >= len(line) and self.cy >= len(self.lines) - 1:
            return  # at EOF, nothing to delete
        self.push_undo("delete")
        if self.cx < len(line):
            self.lines[self.cy] = line[: self.cx] + line[self.cx + 1 :]
            self.dirty = True
        elif self.cy < len(self.lines) - 1:
            self.lines[self.cy] = line + self.lines[self.cy + 1]
            del self.lines[self.cy + 1]
            self.dirty = True

    def cut_line(self):
        if not self.lines:
            return
        self.push_undo("cut")
        self.cut_buffer = [self.lines[self.cy]]
        if len(self.lines) == 1:
            self.lines[0] = ""
        else:
            del self.lines[self.cy]
            if self.cy >= len(self.lines):
                self.cy = len(self.lines) - 1
        self.cx = 0
        self.dirty = True
        self.status = "Cut 1 line"

    def paste(self):
        if not self.cut_buffer:
            self.status = "Nothing to paste"
            return
        self.push_undo("paste")
        for i, line in enumerate(self.cut_buffer):
            self.lines.insert(self.cy + i, line)
        self.cy += len(self.cut_buffer)
        self.cx = 0
        self.dirty = True

    # ---------- prompts ----------
    def prompt(self, prompt_text, initial=""):
        h, w = self.stdscr.getmaxyx()
        buf = list(initial)
        pos = len(buf)
        while True:
            display = prompt_text + "".join(buf)
            try:
                self.stdscr.addstr(h - 2, 0, display[: w - 1].ljust(w - 1),
                                   curses.color_pair(1) | curses.A_REVERSE)
                self.stdscr.move(h - 2, min(len(prompt_text) + pos, w - 1))
            except curses.error:
                pass
            self.stdscr.refresh()
            try:
                ch = self.stdscr.get_wch()
            except curses.error:
                continue
            if isinstance(ch, str):
                if ch in ("\n", "\r"):
                    return "".join(buf)
                if ch == "\x1b":  # ESC
                    return ""
                if ch in ("\x7f", "\b"):
                    if pos > 0:
                        del buf[pos - 1]
                        pos -= 1
                    continue
                if ch.isprintable():
                    buf.insert(pos, ch)
                    pos += 1
            else:
                if ch == curses.KEY_BACKSPACE:
                    if pos > 0:
                        del buf[pos - 1]
                        pos -= 1
                elif ch == curses.KEY_LEFT and pos > 0:
                    pos -= 1
                elif ch == curses.KEY_RIGHT and pos < len(buf):
                    pos += 1

    def confirm_quit(self):
        if not self.dirty:
            return True
        ans = self.prompt("Save modified buffer? (y/n): ").strip().lower()
        if ans.startswith("y"):
            return self.save()
        if ans.startswith("n"):
            return True
        return False

    def search(self):
        query = self.prompt("Search: ")
        if not query:
            return
        # search forward from current position
        for i in range(len(self.lines)):
            row = (self.cy + i) % len(self.lines)
            start = self.cx + 1 if i == 0 else 0
            idx = self.lines[row].find(query, start)
            if idx != -1:
                self.cy = row
                self.cx = idx
                self.status = f'Found "{query}"'
                return
        self.status = f'"{query}" not found'

    def show_help(self):
        help_lines = [
            "mste help",
            "",
            "Movement:  Arrows, PgUp, PgDn, Home, End",
            "Edit:      Type to insert, Backspace/Delete to remove, Enter for newline",
            "Ctrl-S     Save",
            "Ctrl-O     Save As",
            "Ctrl-X     Quit",
            "Ctrl-K     Cut current line (or selection)",
            "Ctrl-U     Paste cut text",
            "Ctrl-A     Select all",
            "Ctrl-Z     Undo",
            "Ctrl-Y     Redo",
            "Ctrl-W     Search",
            "Ctrl-G     This help",
            "",
            "Press any key to return...",
        ]
        self.stdscr.erase()
        for i, line in enumerate(help_lines):
            try:
                self.stdscr.addstr(i, 0, line)
            except curses.error:
                pass
        self.stdscr.refresh()
        self.stdscr.get_wch()

    # ---------- main loop ----------
    def handle_key(self, ch):
        h, w = self.stdscr.getmaxyx()
        text_h = h - 3
        line = self.lines[self.cy]

        # Keys that count as edits — they should replace any active selection.
        # Keys that are pure movement clear the selection without deleting it.
        # Selection-aware control keys (Ctrl-A, Ctrl-K, Ctrl-S, etc.) are handled
        # individually below.

        if isinstance(ch, str):
            code = ord(ch) if len(ch) == 1 else None
            if code == 1:    # Ctrl-A — select all
                self.select_all()
            elif code == 26:  # Ctrl-Z — undo
                self.undo()
            elif code == 25:  # Ctrl-Y — redo
                self.redo()
            elif code == 24:  # Ctrl-X
                if self.confirm_quit():
                    self.quit = True
            elif code == 19:  # Ctrl-S
                self.save()
            elif code == 15:  # Ctrl-O
                name = self.prompt("File Name to Write: ", self.filename or "")
                if name:
                    self.save(name)
                    self.filename = name
            elif code == 11:  # Ctrl-K — cut: if selection active, cut it; else cut line
                if self.selection_bounds() is not None:
                    self._cut_selection()
                else:
                    self.cut_line()
            elif code == 21:  # Ctrl-U
                self.delete_selection()
                self.paste()
            elif code == 23:  # Ctrl-W
                self.clear_selection()
                self.search()
            elif code == 7:   # Ctrl-G
                self.show_help()
            elif ch in ("\n", "\r"):
                self.delete_selection()
                self.insert_newline()
            elif ch == "\t":
                self.delete_selection()
                for _ in range(TAB_SIZE):
                    self.insert_char(" ")
            elif ch == "\x7f" or ch == "\b":
                if not self.delete_selection():
                    self.backspace()
            elif ch == "\x1b":
                self.clear_selection()  # ESC clears selection
            elif ch.isprintable():
                self.delete_selection()
                self.insert_char(ch)
        else:
            # Arrow keys and other movement clear the selection
            moved = False
            if ch == curses.KEY_UP and self.cy > 0:
                self.cy -= 1
                self.cx = min(self.cx, len(self.lines[self.cy]))
                moved = True
            elif ch == curses.KEY_DOWN and self.cy < len(self.lines) - 1:
                self.cy += 1
                self.cx = min(self.cx, len(self.lines[self.cy]))
                moved = True
            elif ch == curses.KEY_LEFT:
                if self.cx > 0:
                    self.cx -= 1
                elif self.cy > 0:
                    self.cy -= 1
                    self.cx = len(self.lines[self.cy])
                moved = True
            elif ch == curses.KEY_RIGHT:
                if self.cx < len(line):
                    self.cx += 1
                elif self.cy < len(self.lines) - 1:
                    self.cy += 1
                    self.cx = 0
                moved = True
            elif ch == curses.KEY_HOME:
                self.cx = 0
                moved = True
            elif ch == curses.KEY_END:
                self.cx = len(line)
                moved = True
            elif ch == curses.KEY_PPAGE:
                self.cy = max(0, self.cy - text_h)
                self.cx = min(self.cx, len(self.lines[self.cy]))
                moved = True
            elif ch == curses.KEY_NPAGE:
                self.cy = min(len(self.lines) - 1, self.cy + text_h)
                self.cx = min(self.cx, len(self.lines[self.cy]))
                moved = True
            elif ch == curses.KEY_BACKSPACE:
                if not self.delete_selection():
                    self.backspace()
            elif ch == curses.KEY_DC:
                if not self.delete_selection():
                    self.delete()
            elif ch == curses.KEY_RESIZE:
                pass

            if moved:
                self.clear_selection()
                self.last_edit_kind = None  # break undo coalescing

    def _cut_selection(self):
        """Cut current selection into cut_buffer, then delete it."""
        bounds = self.selection_bounds()
        if bounds is None:
            return
        self.push_undo("cut")
        (sy, sx), (ey, ex) = bounds
        if sy == ey:
            self.cut_buffer = [self.lines[sy][sx:ex]]
            self.lines[sy] = self.lines[sy][:sx] + self.lines[sy][ex:]
        else:
            buf = [self.lines[sy][sx:]]
            buf.extend(self.lines[sy + 1 : ey])
            buf.append(self.lines[ey][:ex])
            self.cut_buffer = buf
            head = self.lines[sy][:sx]
            tail = self.lines[ey][ex:]
            self.lines[sy] = head + tail
            del self.lines[sy + 1 : ey + 1]
        self.cy, self.cx = sy, sx
        self.clear_selection()
        self.dirty = True
        self.status = f"Cut {len(self.cut_buffer)} line(s)"

    def run(self):
        while not self.quit:
            self.scroll()
            self.draw()
            try:
                ch = self.stdscr.get_wch()
            except curses.error:
                continue
            except KeyboardInterrupt:
                if self.confirm_quit():
                    break
                continue
            self.handle_key(ch)


def main():
    parser = argparse.ArgumentParser(description="mste - simple terminal text editor")
    parser.add_argument("file", nargs="?", help="file to edit")
    parser.add_argument("--version", action="version", version=f"mste {VERSION}")
    args = parser.parse_args()

    def _run(stdscr):
        editor = Editor(stdscr, args.file)
        editor.run()

    try:
        curses.wrapper(_run)
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()