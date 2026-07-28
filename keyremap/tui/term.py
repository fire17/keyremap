"""Tiny cross-platform terminal layer — stdlib only, no curses, no pip.

POSIX uses termios raw mode; Windows uses msvcrt plus VT-mode enablement.
Both speak plain ANSI, so one renderer serves macOS, Linux and Windows.

Design notes that keep it fast:
  * one write() per frame (a single joined string) — no flicker, no tearing
  * diffing is unnecessary at this size; a full frame is ~2 KB
  * input is non-blocking with a timeout, so the event loop idles at ~0% CPU
"""

import os
import sys
import time

IS_WINDOWS = os.name == "nt"

if not IS_WINDOWS:
    import select
    import termios
    import tty
else:  # pragma: no cover - exercised only on Windows
    import ctypes
    import msvcrt

ESC = "\x1b"
CSI = ESC + "["

# --- keys ------------------------------------------------------------------
UP, DOWN, RIGHT, LEFT = "UP", "DOWN", "RIGHT", "LEFT"
ENTER, ESCAPE, TAB, BACKSPACE, DELETE = "ENTER", "ESCAPE", "TAB", "BACKSPACE", "DELETE"
HOME, END, PGUP, PGDN = "HOME", "END", "PGUP", "PGDN"

_CSI_MAP = {
    "A": UP, "B": DOWN, "C": RIGHT, "D": LEFT,
    "H": HOME, "F": END,
    "1~": HOME, "3~": DELETE, "4~": END, "5~": PGUP, "6~": PGDN,
}


class Term:
    def __init__(self, stream=None):
        self.out = stream or sys.stdout
        self._saved = None
        self._buf = ""

    # -- lifecycle
    def __enter__(self):
        if not IS_WINDOWS:
            self._saved = termios.tcgetattr(sys.stdin.fileno())
            tty.setraw(sys.stdin.fileno())
        else:  # enable ANSI on legacy consoles
            k = ctypes.windll.kernel32
            h = k.GetStdHandle(-11)
            mode = ctypes.c_uint32()
            if k.GetConsoleMode(h, ctypes.byref(mode)):
                k.SetConsoleMode(h, mode.value | 0x0004)
        self.write(CSI + "?1049h" + CSI + "?25l")  # alt screen, hide cursor
        return self

    def __exit__(self, *exc):
        self.write(CSI + "?25h" + CSI + "?1049l")  # show cursor, restore screen
        if not IS_WINDOWS and self._saved is not None:
            termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, self._saved)
        self.flush()

    # -- output
    def write(self, s: str):
        self.out.write(s)

    def flush(self):
        try:
            self.out.flush()
        except (BrokenPipeError, ValueError):
            pass

    def frame(self, lines: list[str]):
        """Paint a whole frame in one syscall."""
        self.write(CSI + "H" + CSI + "2J" + "\r\n".join(lines))
        self.flush()

    @staticmethod
    def size() -> tuple[int, int]:
        try:
            s = os.get_terminal_size()
            return s.columns, s.lines
        except OSError:
            return 80, 24

    # -- input
    def read_key(self, timeout: float = 0.1):
        """Return a key name, a character, or None on timeout. Never blocks long."""
        if IS_WINDOWS:  # pragma: no cover
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                if msvcrt.kbhit():
                    ch = msvcrt.getwch()
                    if ch in ("\x00", "\xe0"):
                        code = msvcrt.getwch()
                        return {"H": UP, "P": DOWN, "M": RIGHT, "K": LEFT,
                                "G": HOME, "O": END, "I": PGUP, "Q": PGDN,
                                "S": DELETE}.get(code)
                    return self._named(ch)
                time.sleep(0.005)
            return None

        r, _, _ = select.select([sys.stdin], [], [], timeout)
        if not r:
            return None
        ch = sys.stdin.read(1)
        if ch != ESC:
            return self._named(ch)
        # escape sequence — read the rest without blocking
        seq = ""
        while True:
            r, _, _ = select.select([sys.stdin], [], [], 0.02)
            if not r:
                break
            seq += sys.stdin.read(1)
            if seq and seq[-1].isalpha() or seq.endswith("~"):
                break
        if not seq:
            return ESCAPE
        if seq.startswith("["):
            body = seq[1:]
            return _CSI_MAP.get(body, _CSI_MAP.get(body[-1:], None))
        return ESCAPE

    @staticmethod
    def _named(ch: str):
        if ch in ("\r", "\n"):
            return ENTER
        if ch == "\t":
            return TAB
        if ch in ("\x7f", "\b"):
            return BACKSPACE
        if ch == "\x03":
            return "CTRL_C"
        if ch == ESC:
            return ESCAPE
        return ch


# --- styling ---------------------------------------------------------------
class S:
    RESET = ESC + "[0m"
    BOLD = ESC + "[1m"
    DIM = ESC + "[2m"
    ITALIC = ESC + "[3m"
    UNDER = ESC + "[4m"
    REV = ESC + "[7m"

    @staticmethod
    def fg(n: int) -> str:
        return f"{ESC}[38;5;{n}m"

    @staticmethod
    def bg(n: int) -> str:
        return f"{ESC}[48;5;{n}m"


# a restrained palette that reads well on light and dark terminals
ACCENT = S.fg(39)     # cyan-blue
GOOD = S.fg(78)       # green
WARN_C = S.fg(214)    # amber
BAD = S.fg(203)       # red
MUTED = S.fg(245)     # grey
KEYC = S.fg(183)      # lilac for key names


def visible_len(s: str) -> int:
    """Length ignoring ANSI escapes — needed for padding/centering."""
    out, i = 0, 0
    while i < len(s):
        if s[i] == ESC:
            j = s.find("m", i)
            if j == -1:
                break
            i = j + 1
            continue
        out += 1
        i += 1
    return out


def pad(s: str, width: int) -> str:
    n = visible_len(s)
    return s + " " * max(0, width - n)


def truncate(s: str, width: int) -> str:
    if visible_len(s) <= width:
        return s
    out, count, i = "", 0, 0
    while i < len(s) and count < width - 1:
        if s[i] == ESC:
            j = s.find("m", i)
            out += s[i:j + 1]
            i = j + 1
            continue
        out += s[i]
        count += 1
        i += 1
    return out + "…" + S.RESET
