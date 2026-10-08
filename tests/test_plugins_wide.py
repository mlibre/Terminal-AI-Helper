"""A file name in a double-width script, drawn correctly and highlighted right.

The filesystem answer is drawn as a menu, and the menu is laid out in columns —
characters for ASCII, two cells each for CJK and emoji. The Screen model has to
count columns the way the terminal does or its assertions are about a terminal
that does not exist.

    python3 tests/test_plugins_wide.py
"""
import os
import pathlib
import shutil
import sys
import time
import unicodedata

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from plugin_env import *  # noqa: F401,F403  (constants, fixture writers, setup)
from plugin_screen import *  # noqa: F401,F403  (Screen and the styled-menu helpers)
from plugin_pty import *  # noqa: F401,F403  (Session, Ghosts, check, probe, failures)

def _columns(text: str) -> int:
    """What a terminal spends on `text`: two columns per wide character."""
    return sum(2 if unicodedata.east_asian_width(c) in ("W", "F") else 1
               for c in text)


def _column_of(screen: Screen, row: int, text: str) -> int:
    """The column `text` starts at in `row`, counted the way a terminal counts.

    Continuation columns are dropped first, because a name in a double-width
    script is one character per cell *and* has a cell per column: searching the
    grid by cell index would run out of slice half way through the name.
    """
    cells, col = [], 0
    for ch, _ in screen.grid[row]:
        if ch == WIDE_TAIL:            # the rest of a wide character
            continue
        cells.append((col, ch))
        col += _columns(ch)
    for i in range(len(cells) - len(text) + 1):
        if "".join(ch for _, ch in cells[i:i + len(text)]) == text:
            return cells[i][0]
    return -1


def test_wide_file_names_are_measured_in_columns() -> None:
    """A name in a double-width script is still measured and highlighted right.

    The column width is computed from `${#name}`, which counts characters, not
    columns — and that is not a shortcut, it is what ZLE counts: `region_highlight`
    takes character offsets, so a width measured in columns would put the selection
    on the wrong characters for every name that is not ASCII. A row of Japanese or
    emoji names is therefore *wider on screen* than the arithmetic intends, which
    is a cosmetic cost with no cheap fix (a real wcwidth is a per-character loop
    on the menu path, and the emulated screen counts one cell per character so it
    cannot even see the result). What must hold is the part that is arithmetic:
    the reverse-video block covers the selected entry exactly, before and after
    the mark moves, and Enter writes that entry.
    """
    if not SHELLS["zsh"]:
        return
    print("zsh menu, double-width names")
    wide = pathlib.Path("/tmp/opencode/tai_wide_names")
    shutil.rmtree(wide, ignore_errors=True)
    wide.mkdir(parents=True)
    # A `wide-` prefix, because an empty word after `cat` answers from the
    # download directories as well as the current one, and what is newest on this
    # machine is not what this test wrote.
    names = ["wide-日本語.txt", "wide-español.txt", "wide-🎉party.txt", "wide-ascii.txt"]
    for name in names:
        (wide / name).write_text("")
    # The file list is newest first, and four files written in the same instant
    # have no order at all — so the mtimes are set, `names[0]` newest, and the
    # assertions can name an entry instead of reading whichever one came first.
    for i, name in enumerate(reversed(names)):
        os.utime(wide / name, (1_700_000_000 + i, 1_700_000_000 + i))
    s = Session("zsh")
    try:
        s.run(f"cd {wide}")
        s.send("cat wide-")
        s.write(CTRL_SPACE)
        s.settle()
        # `clear=False` between the keys and the screen: reading the line the
        # other way abandons it, and an abandoned line closes the menu.
        s.menu(clear=False)
        scr = screen_of(s)
        check("the wide names are on screen", selected_entry(scr), names[0])
        # Containment, not exact offsets: the column width is padded in
        # characters (ZLE counts regions in characters, and padding in columns
        # would misplace the selection), so how many columns of padding a cell
        # has is the plugin's business and not this test's. What has to hold is
        # that the block covers the whole selected name and stops before the
        # next one — counted in columns, which is what the terminal counts and
        # what this Screen now models.
        def _covers(scr: Screen, name: str, after: str | None) -> None:
            row = next(r for r in range(Screen.ROWS) if name in scr.lines()[r])
            block = [c for c, (_, a) in enumerate(scr.grid[row]) if a[3]]
            at = _column_of(scr, row, name)
            check(f"the block covers all of {name}",
                  (block[0] <= at, block[-1] >= at + _columns(name) - 1), (True, True))
            # …and no further: two cells on one row must not share a column, or
            # the second name is drawn inside the first one's selection. Only
            # when the next name is on this row — the last entry of a row has
            # nothing beside it, and that is not a pass by default.
            after_col = _column_of(scr, row, after) if after else -1
            if after_col >= 0:
                check("and stops short of the next name", block[-1] < after_col, True)

        _covers(scr, names[0], names[1])
        for step in range(1, len(names)):
            s.write(TAB)
            s.settle()
            s.menu(clear=False)
            scr = screen_of(s)
            check(f"the mark moves to {names[step]}",
                  selected_entry(scr), names[step])
            _covers(scr, names[step], names[step + 1] if step + 1 < len(names) else None)
        check("no noise", s.noise(), [])
    finally:
        s.close()
        shutil.rmtree(wide, ignore_errors=True)




def main() -> int:
    setup()
    test_wide_file_names_are_measured_in_columns()
    check_fixture_intact("the run")
    print("\nOK — wide file names: the menu's columns are the terminal's columns."
          if not failures else f"\nFAILED ({len(failures)}): {', '.join(failures)}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
