"""A small terminal-screen model, so the tests can read what was drawn.

The pty tells you what bytes the shell wrote; a menu test needs to know which
cell was selected and in what colour, which is a question about a screen rather
than about a stream. So feed the stream into a Screen and ask it.
"""
import re
import unicodedata

from plugin_env import ANSI

WIDE_TAIL = "\x00"  # the second column of a double-width character; never drawn


class Timeout(Exception):
    """A wait ran out of time. The terminal tail says what happened."""


def tail(text: str, n: int = 700) -> str:
    return ANSI.sub("", text)[-n:]


def indent(text: str, prefix: str = "         ") -> str:
    return "\n".join(prefix + line for line in text.splitlines())


def menu_entries(drawn: str) -> list[str]:
    """The menu as the user reads it: the entries, in order.

    Columns are padded to the widest name in the list, so the spacing between
    entries is not a fixed number of characters and cannot be asserted on. The
    entries themselves never contain a space — a menu completes one word — which
    is what makes a run of spaces a reliable separator here.
    """
    flat = drawn.replace("\\n", " ")
    return [name for name in re.split(r" +", flat.strip()) if name]


CSI = re.compile(r"\x1b\[([0-9;?]*)([@-~])")
# What a cell carries: (foreground colour, background, bold, reverse video). The
# selection is drawn in reverse video and nothing else on the screen is, so that
# one attribute is what finds it — see selected_entry.
PLAIN = (0, 0, False, False)


class Screen:
    """The terminal as a grid, so "what the screen shows" can be asserted.

    ZLE's redraw is incremental, so a run of escape bytes is unreadable as text:
    splitting it on newlines shows the two cells that moved and none of the line
    they moved on. Feeding the bytes to a grid is the only way to catch a menu
    that lands on the prompt line or leaves the pieces of the last one behind,
    which is a class of bug that no assertion on the plugin's own arrays can see.

    Each cell keeps its attributes as well as its character. A menu can be right
    in every character and wrong in every colour, and the only way to see that is
    to keep what the terminal was told about each one.

    Only the escapes zsh's redraw actually emits are handled. Anything else is
    dropped rather than guessed at, so an unhandled sequence shows up as a wrong
    screen instead of quietly passing.
    """
    COLS, ROWS = 80, 24

    def __init__(self) -> None:
        self.grid = [[(" ", PLAIN)] * self.COLS for _ in range(self.ROWS)]
        self.row = self.col = 0
        self.attr = PLAIN                # the attributes in force right now

    def _put(self, ch: str) -> None:
        if self.col >= self.COLS:            # a terminal wraps at the edge
            self.col = 0
            self.row += 1
        if 0 <= self.row < self.ROWS:
            self.grid[self.row][self.col] = (ch, self.attr)
        # A character in a double-width script — Japanese, Chinese, Korean, and
        # most emoji — takes two columns, and a model that gives it one shifts
        # every column after it by one. That is not a detail: a redraw of a line
        # holding such a character then interleaves with itself, and the harness
        # reports the plugin's arithmetic as broken when the terminal drew it
        # correctly. The second column is a continuation: occupied, so a region
        # that reaches it is seen, and invisible in `lines()`, because the
        # character to its left is the whole story.
        width = 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
        if width == 2 and self.col + 1 < self.COLS and 0 <= self.row < self.ROWS:
            self.grid[self.row][self.col + 1] = (WIDE_TAIL, self.attr)
        self.col += width

    def _blank(self, row: int, start: int, stop: int) -> None:
        for c in range(start, stop):
            if 0 <= row < self.ROWS:
                self.grid[row][c] = (" ", PLAIN)

    def feed(self, data: str) -> None:
        i = 0
        while i < len(data):
            ch = data[i]
            if data.startswith("\x1b[", i):
                match = CSI.match(data, i)
                if not match:
                    i += 1
                    continue
                self._escape(match.group(1), match.group(2))
                i = match.end()
                continue
            if ch == "\r":
                self.col = 0
            elif ch == "\n":
                self.row += 1
                if self.row >= self.ROWS:    # the grid scrolls
                    self.grid.pop(0)
                    self.grid.append([(" ", PLAIN)] * self.COLS)
                    self.row = self.ROWS - 1
            elif ch == "\b":
                self.col = max(0, self.col - 1)
            elif ch == "\t":
                self.col = min(self.COLS - 1, (self.col // 8 + 1) * 8)
            elif ch >= " ":                  # printable, and ^X for a control
                self._put(ch)
            i += 1

    def _escape(self, args: str, cmd: str) -> None:
        nums = [int(a) for a in args.split(";") if a.isdigit()]
        first = nums[0] if nums else 0
        if cmd == "m":
            self._sgr(nums or [0])
        elif cmd == "C":
            self.col = min(self.COLS - 1, self.col + (first or 1))
        elif cmd == "D":
            self.col = max(0, self.col - (first or 1))
        elif cmd == "A":
            self.row = max(0, self.row - (first or 1))
        elif cmd == "B":
            self.row = min(self.ROWS - 1, self.row + (first or 1))
        elif cmd == "K":
            start, stop = ((self.col, self.COLS) if args in ("", "0")
                           else (0, self.col + 1) if args == "1"
                           else (0, self.COLS))
            self._blank(self.row, start, stop)
        elif cmd == "J":
            if args in ("", "0"):
                self._blank(self.row, self.col, self.COLS)
                for r in range(self.row + 1, self.ROWS):
                    self.grid[r] = [(" ", PLAIN)] * self.COLS
            else:
                for r in range(0, self.row):
                    self.grid[r] = [(" ", PLAIN)] * self.COLS
                self._blank(self.row, 0, self.col + 1)

    def _sgr(self, nums: list[int]) -> None:
        """Apply SGR parameters. Only the ones a redraw actually emits."""
        fg, bg, bold, reverse = self.attr
        for n in nums:
            if n == 0:
                fg, bg, bold, reverse = PLAIN
            elif n == 1:
                bold = True
            elif n == 7:
                reverse = True
            elif n == 22:
                bold = False               # normal intensity
            elif n == 27:
                reverse = False
            elif n == 39:
                fg = 0
            elif n == 49:
                bg = 0
            elif 30 <= n <= 37 or 90 <= n <= 97:
                fg = n
            elif 40 <= n <= 47 or 100 <= n <= 107:
                bg = n
            # 24 (underline off) is not tracked: nothing here draws underlined
            # text, and carrying an attribute no assertion reads is noise.
        self.attr = (fg, bg, bold, reverse)

    def lines(self) -> list[str]:
        # Continuation columns are dropped: the character to their left is the
        # one the terminal draws, so a line read as text has one character per
        # character and not one per column.
        return ["".join(ch for ch, _ in row if ch != WIDE_TAIL).rstrip()
                for row in self.grid]

    def bottom(self, count: int = 6) -> list[str]:
        """The last `count` rows that have something on them."""
        return [line for line in self.lines() if line][-count:]

    def styled(self, row: int) -> list[tuple[str, tuple]]:
        """A row as (text, attributes) runs, trailing blanks dropped.

        A run that is not plain is something the plugin asked ZLE to draw that
        way, and the text it holds is what was drawn that way — which is how
        "is the selection highlighted" and "is that a directory blue" are
        answered without reading the plugin's own arrays.
        """
        runs: list[list] = []
        for ch, attr in self.grid[row]:
            if ch == WIDE_TAIL:            # part of the character to its left
                continue
            if runs and runs[-1][1] == attr:
                runs[-1][0] += ch
            else:
                runs.append([ch, attr])
        return [(text.rstrip(), attr) for text, attr in runs if text.strip()]


def selected_entry(screen: Screen) -> str:
    """The entry the screen shows as selected — the one Enter would take.

    Read off the screen rather than out of the plugin's arrays, because the
    selection is now drawn by ZLE and not by a character in the menu text: there
    is no marker in the text any more, and the only evidence that an entry is
    selected is that its cell was painted in reverse video.
    """
    for row in range(Screen.ROWS - 1, -1, -1):
        for text, attr in screen.styled(row):
            if attr[3]:
                return text.strip()
    return ""


NOT_DRAWN = ("not on screen",)


def style_of(screen: Screen, text: str) -> tuple:
    """The attributes a piece of text is drawn in, wherever it sits on screen.

    A whole row is one run, so the text of a run is only a single entry when the
    run is that entry — which is exactly the case the menu's colours are about.
    """
    for row in range(Screen.ROWS):
        for got, attr in screen.styled(row):
            if got == text:
                return attr
    return NOT_DRAWN


def screen_of(session: "Session") -> Screen:
    screen = Screen()
    screen.feed(session.raw())
    return screen


