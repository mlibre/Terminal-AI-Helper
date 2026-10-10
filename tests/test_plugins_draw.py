"""What the plugin puts on the screen: hints, the ghost text, and menu styling.

These are the assertions that are worthless without an emulated screen — the
characters can all be right while the selection is painted in nothing at all —
so they read `Screen`, not the plugin's arrays.

    python3 tests/test_plugins_draw.py
"""
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from plugin_env import *  # noqa: F401,F403  (constants, fixture writers, setup)
from plugin_screen import *  # noqa: F401,F403  (Screen and the styled-menu helpers)
from plugin_pty import *  # noqa: F401,F403  (Session, Ghosts, check, probe, failures)


def test_zsh_foreign_hint() -> None:
    """A hint another plugin drew is a hint, and the arrow still takes it.

    The reported case: `nan` with `o .zshrc` on screen, and Tab opening a list
    over it instead of completing. The hint came from zsh-autosuggestions, which
    reads `~/.zsh_history` directly, while tai had no `nan` candidate at all —
    `_TAI_FIRST` is keyed by the whole first word, so a half-typed command name
    is not a key tai can answer.

    So tai's own variable was empty while a hint was plainly visible, and the key
    that takes hints asked the variable instead of the screen. Two facts make this
    the normal case rather than a corner: the two plugins share one POSTDISPLAY,
    and autosuggestions fetches *asynchronously*, so it writes the slot after
    tai's redraw. Load order cannot fix that — it decides who writes first, not
    who writes last — and the arrow is worse off still, because replacing the
    `forward-char` widget is what makes the arrow work in both cursor-key modes
    and also replaces the wrapping that plugin put around that widget.

    The other plugin is simulated by writing POSTDISPLAY from a widget, which is
    precisely what that plugin does to it. Loading zsh-autosuggestions instead
    would make the assertion depend on a distro package being installed, and a
    test that skips itself on the next machine is not a test.
    """
    if not SHELLS["zsh"]:
        return
    print("zsh a hint drawn by another plugin")
    s = Session("zsh")
    s.run("tai_test_foreign() { POSTDISPLAY='o .zshrc'; }; "
          "zle -N tai_test_foreign; bindkey '^[[27~' tai_test_foreign")

    def typed_and_hinted(prefix: str) -> None:
        s.send(prefix)
        s.write("\x1b[27~")      # the other plugin draws its hint
        s.settle()

    # The hint is on screen and tai did not draw it: that is the whole situation.
    typed_and_hinted("nan")
    check("and tai has no answer of its own",
          s.suggestion().split(" PD=")[0], "SUG=[]")

    typed_and_hinted("nan")
    s.write(RIGHT)
    s.settle()
    check("the arrow takes it", s.line(), "nano .zshrc")
    s.write("\x15")

    # And Tab, which no longer answers the question the hint answers, does
    # not swallow it: the list over a foreign hint is the rare case, but the
    # list is what Tab asks for, whatever drew the text it overlaps. A prefix
    # nothing completes, so expand-or-complete has nothing to write either.
    typed_and_hinted(NOT_INSTALLED)
    s.write(TAB)
    s.settle()
    check("Tab does not take it", s.line(), NOT_INSTALLED)
    s.write("\x15")

    # The other two keys that take hints, for the same reason.
    typed_and_hinted("nan")
    s.write(RIGHT)
    s.settle()
    check("the arrow takes it", s.line(), "nano .zshrc")
    s.write("\x15")

    typed_and_hinted("nan")
    s.write(ALT_F)
    s.settle()
    # One *word* of `o .zshrc` is ` o`, so the line becomes `nano` and the rest of
    # the foreign hint is still there — which is the whole point of this key.
    check("and Alt-F takes one word of it", s.line(), "nano")
    s.write("\x15")

    # With no foreign hint, a line tai cannot answer still lists rather than
    # inventing one: the fix reads the screen, and the screen is empty.
    s.send(NOT_INSTALLED)
    check("and with nothing on screen, nothing is invented",
          s.suggestion().split(" PD=")[0], "SUG=[]")
    s.send(NOT_INSTALLED)
    s.write(TAB)
    s.settle()
    line, drawn, size, idx = s.menu(clear=False)
    check("a line with no hint still lists", (line, size), (NOT_INSTALLED, 0))
    s.write("\x15")
    check("no noise", s.noise(), [])
    s.close()


def test_zsh_tab_and_list() -> None:
    """Tab cycles the completions. The right arrow takes the hint. Ctrl-T lists.

    Tab used to take the hint, which is the arrow's question: reaching for a
    readable hint and having Tab swallow it, while the completions went unshown.
    So Tab's question is the word it is on — files, folders, options — and the
    hint belongs to `→` alone.

    One completion is still the key's: unambiguous, and the hint was already
    saying it, so `docker` completes rather than dangling a list of one.

    The `--help` case is the exception and it is deliberate: `true ` has never
    been run, so `true --help` is tai guessing at what was meant from no evidence
    at all, and the file listing is the answer to the question actually typed. It
    stays a hint — `→` still takes it — and Tab still lists.
    """
    if not SHELLS["zsh"]:
        return
    print("zsh Tab lists, the right arrow takes the hint")
    s = Session("zsh")
    s.run(f"cd {MENU_DIR}")
    s.run(f"export PATH={MENU_DIR}:{MENU_BIN}:$PATH")

    # The hint is taken by the arrow, not by Tab. Tab on a word with several
    # completions opens the list over the hint and leaves the line alone.
    s.send("git ")
    check("a hint is on screen",
          s.suggestion().split(" PD=")[0], "SUG=[pull --rebase]")
    s.send("git ")
    s.write(TAB)
    s.settle()
    line, drawn, size, idx = s.menu(clear=False)
    check("Tab opens the list rather than taking the hint",
          menu_entries(drawn)[:2], ["pull", "status"])
    check("and leaves the line alone", line, "git ")
    s.write("\x15")

    # The one completion the hint was already showing: Tab writes it, because
    # a list of one is a question that has answered itself.
    s.send("cat tzz_dir")
    s.write(TAB)
    s.settle()
    line, drawn, size, idx = s.menu()
    check("one completion is taken, not listed",
          (line, size), ("cat tzz_dir/", 0))
    s.send("docker")
    s.write(RIGHT)
    s.settle()
    line, drawn, size, idx = s.menu()
    check("and the arrow takes the hint",
          (line, size), ("docker ps", 0))

    # The same key with no hint is the list, which is what every shell does
    # for the word it is on — readline's listing writes the shared head `tzz_`
    # into the line, and so does the menu: the shells agree on the one thing
    # every entry already says.
    s.send("tzz")
    check("with no hint there is nothing to take",
          s.suggestion().split(" PD=")[0], "SUG=[]")
    s.send("tzz")
    s.write(TAB)
    s.settle()
    line, drawn, size, idx = s.menu(clear=False)
    check("no hint means Tab lists", menu_entries(drawn), MENU_ENTRIES)
    check("leaving the line at the shared head", line, "tzz_")
    s.write("\x15")

    # Ctrl-Space lists *with* a hint on screen. This is the key the change
    # needed: browsing the candidates has to be possible without first deleting
    # the line. Ctrl-T is the same widget, kept because terminals disagree about
    # what to send for Ctrl-Space — so both are asserted, since a terminal that
    # sends neither is a user with no way to see the list.
    s.send("git ")
    check("a hint is on screen", s.suggestion().split(" PD=")[0],
          "SUG=[pull --rebase]")
    s.send("git ")
    s.write(CTRL_SPACE)
    s.settle()
    line, drawn, size, idx = s.menu(clear=False)
    check("Ctrl-Space lists anyway", menu_entries(drawn)[:2], ["pull", "status"])
    check("and still does not touch the line", line, "git ")
    check("with the first entry selected", selected_entry(screen_of(s)), "pull")
    s.write(CTRL_SPACE)
    s.settle()
    check("and Ctrl-Space again moves the selection",
          selected_entry(screen_of(s)), "status")
    check("still without touching the line", s.menu(clear=False)[0], "git ")
    s.write("\x15")
    s.send("git ")
    s.write(LIST)
    s.settle()
    check("Ctrl-T is the same list",
          menu_entries(s.menu(clear=False)[1])[:2], ["pull", "status"])
    s.write("\x15")

    # One word of the hint, on both keys. `git ` hints `git pull --rebase`, so
    # one word is `git pull` and the rest is still there to type — the behaviour
    # Alt-F has always had, on a key the hand already makes for "next word".
    for label, keys in (("Alt-F", ALT_F), ("Ctrl-Right", CTRL_RIGHT)):
        s.send("git ")
        s.write(keys)
        s.settle()
        check(f"{label} takes one word of the hint", s.line(), "git pull")
        s.write("\x15")
    # And neither invents a word: with nothing on screen the key falls through to
    # `forward-word`, which moves the cursor and rewrites nothing. `zzzz` is in no
    # fixture entry and is not installed, so it has no hint and no candidate —
    # `git pull` would not do, because it does have one.
    s.send(NOT_INSTALLED)
    check("and there is no hint to take",
          s.suggestion().split(" PD=")[0], "SUG=[]")
    s.send(NOT_INSTALLED)
    s.write(CTRL_A)
    s.write(CTRL_RIGHT)
    s.settle()
    check("with no hint the key rewrites nothing", s.line(), NOT_INSTALLED)
    s.write("\x15")

    # The `--help` exception. `true` is installed and appears in no fixture
    # entry, so this is the "a tool you have never run" path with a real binary.
    s.send(INSTALLED + " ")
    check("the fallback is still a hint",
          s.suggestion().split(" PD=")[0], "SUG=[--help]")
    s.send(INSTALLED + " ")
    s.write(TAB)
    s.settle()
    line, drawn, size, idx = s.menu(clear=False)
    check("but Tab still lists for it", size > 1, True)
    check("with --help in the list", "--help" in menu_entries(drawn), True)
    check("and the line untouched", line, INSTALLED + " ")
    s.write("\x15")
    # The arrow still takes it, so the hint is not merely decoration.
    s.send(INSTALLED + " ")
    s.write(RIGHT)
    s.settle()
    check("the arrow takes the --help hint", s.line(), INSTALLED + " --help")
    s.write("\x15")

    check("no noise from either key", s.noise(), [])
    s.close()


def test_zsh_menu_colour() -> None:
    """The menu is drawn in colour: a selected cell reversed, a directory blue.

    Both are read off the screen rather than out of the plugin, because both are
    drawn by ZLE: the plugin asks for a region, the terminal is told, and the
    only evidence that it listened is the bytes on the wire. The assertions in
    test_zsh_menu already lean on the selection being painted — selected_entry
    finds an entry by its reverse video and by nothing else — so what is left to
    cover here is the colours themselves, and the one way the offset arithmetic
    can be wrong while every character is still in the right place.
    """
    if not SHELLS["zsh"]:
        return
    print("zsh menu colour")
    s = Session("zsh")
    s.run(f"cd {MENU_DIR}")
    s.run(f"export PATH={MENU_DIR}:{MENU_BIN}:$PATH")
    s.send("tzz")
    s.write(TAB)
    s.settle()
    scr = screen_of(s)
    # The first entry is selected, so the block is its gutter, its name and the
    # column's padding — one run, which is what a block is.
    check("the selected cell is one block in reverse video",
          style_of(scr, "  tzz_a"), SELECTED_ATTR)
    # Exactly the cell and no more. A region one character too long looks the
    # same — a space is a space — but it reaches into the gap, and off the end
    # of a row it reaches into the next one. Counting the block is the only
    # check that can tell the two apart.
    row = next(r for r in range(Screen.ROWS) if "tzz_a" in scr.lines()[r])
    cells = scr.grid[row]
    # Two columns of gutter, columns padded to the widest name, two of gap.
    cell_width = max(len(name) for name in MENU_ENTRIES) + 2
    block = [c for c, (_, a) in enumerate(cells) if a[3]]
    check("and the block is the cell, the gap, and nothing past them",
          (block[0], block[-1]), (0, cell_width - 1))
    check("so the two columns of gap are not in it",
          [a[3] for _, a in cells[cell_width:cell_width + 2]], [False, False])
    # The directory's name is blue and the slash that says it is a directory is
    # not: a blue slash as well would make the entry read as a word.
    check("a directory's name is drawn in the directory colour",
          style_of(scr, "tzz_dir"), (DIR_FG, 0, True, False))
    check("and its slash is left alone", style_of(scr, "/"), PLAIN)
    s.write(TAB * (len(MENU_ENTRIES) - 1))        # on to the directory
    s.settle()
    scr = screen_of(s)
    # One block and not a blue name inside one: two regions over the same
    # characters are combined by ZLE rather than replacing each other, so the
    # directory's colour is not asked for at all on the entry that is selected.
    check("a selected directory is one block too",
          style_of(scr, "  tzz_dir/"), SELECTED_ATTR)
    check("with no directory colour left inside it",
          style_of(scr, "tzz_dir"), NOT_DRAWN)
    s.write("\x15")

    # Both colours are settings, read when the plugin is sourced, so a palette
    # without blue — or a terminal where reverse video is unwelcome — has an
    # answer that does not need a patched plugin. Re-sourced in the same shell,
    # because that is what a user does after editing the file.
    s.run(f"_TAI_MENU_STYLE=fg=yellow; _TAI_DIR_STYLE=fg=magenta; source {PLUGINS['zsh']}")
    s.send("tzz")
    s.write(TAB)
    s.settle()
    scr = screen_of(s)
    check("the selection colour is a setting",
          style_of(scr, "  tzz_a"), (33, 0, False, False))
    check("and so is the directory colour",
          style_of(scr, "tzz_dir"), (35, 0, False, False))
    s.write("\x15")
    check("no noise", s.noise(), [])
    s.close()


def test_zsh_load_order() -> None:
    """tai keeps the ghost text when other plugins drive POSTDISPLAY too.

    POSTDISPLAY is a single slot with no namespacing. zsh-autosuggestions and
    zsh-syntax-highlighting both write it, and distro configs (Manjaro's
    manjaro-zsh-prompt loads autosuggestions) load them *before* a
    user-installed plugin. So the only safe order is: recompute POSTDISPLAY on
    every line-pre-redraw, and be the last writer. This is the configuration
    that broke in the field, so it gets a test rather than a comment.
    """
    if not SHELLS["zsh"]:
        return
    available = [p for p in (SYNTAX_HIGHLIGHTING, ZSH_AUTOSUGGESTIONS) if p.exists()]
    if not available:
        print("zsh POSTDISPLAY load order — skipped, no zsh plugins installed")
        return
    print(f"zsh POSTDISPLAY load order ({len(available)} competing plugins)")
    s = Session("zsh", source=False)
    # The trailing newline matters: without it the line is typed but never run,
    # and every later send concatenates onto it.
    for path in available:
        s.send(f"source {path} 2>/dev/null\n")
    # Now source tai, exactly as install.sh appends it to .zshrc: last.
    s.send(f"source {PLUGINS['zsh']}\n")
    s.send("docker")
    got = s.suggestion()
    # Assert on what tai controls: it still computes the tail and styles it, and
    # it never puts escape bytes in POSTDISPLAY. Whether the text survives to
    # the screen is not tai's to decide — a competing plugin may clear
    # POSTDISPLAY in the same redraw pass, which is why the documented fix is
    # to unhook it rather than to try to out-race it.
    check("ghost tail computed with competing writers", got.split(" PD=")[0], "SUG=[ ps]")
    check("ghost styled with competing writers", got.split(" RH=")[-1], "[6 9 fg=8,bold]")
    check("no escape bytes leak into the hint", "\x1b" in got, False)
    check("no hook errors with plugins loaded", s.noise(), [])
    s.close()


def test_zsh_ghost_styling() -> None:
    """The hint must be styled by ZLE, not by escape bytes in POSTDISPLAY.

    ZLE writes POSTDISPLAY verbatim, so an embedded \\e[2;3;90m reaches the
    screen as the literal characters "^[[2;3;90m" and the hint is unstyled.
    region_highlight is the supported way to colour it. Assert the hint text is
    clean and that a highlight region covers exactly the hint.
    """
    if not SHELLS["zsh"]:
        return
    print("zsh ghost styling")
    s = Session("zsh")
    s.send("docker")
    got = s.suggestion()
    check("POSTDISPLAY holds bare hint text", got.split(" RH=")[0], "SUG=[ ps] PD=[ ps]")
    lo, hi, style = got.split(" RH=[")[1].rstrip("]").split(" ")
    # ZLE counts these offsets in characters of the editable line, and the
    # prompt is not part of it: the hint sits exactly at the end of the buffer.
    # (The menu in menu.zsh measures its cells the same way, which is what makes
    # the selection land on the right cell in `test_zsh_menu_colour`.)
    check("the region covers exactly the hint",
          (int(lo), int(hi)), (len("docker"), len("docker") + len(" ps")))
    check("and it is styled dim and gray", style, "fg=8,bold")
    check("no escape bytes in the hint", "\x1b" in got, False)
    s.close()


def test_zsh_menu_on_screen() -> None:
    """The menu goes on a line of its own, and leaves nothing behind.

    A row laid out to the terminal width and started after the prompt does not
    fit: the terminal wraps it, the right prompt is written over, and ZLE — which
    believes the whole post-display is one line — then erases in the wrong place
    and leaves the pieces of the previous menu on screen. Every other assertion in
    this file reads the plugin's own words and would have passed through all of
    that, so this one reads the screen.

    The prompt here is deliberately long and there is deliberately a right prompt
    to collide with, because that is the shape the bug showed up in.
    """
    if not SHELLS["zsh"]:
        return
    print("zsh menu on screen")
    s = Session("zsh")
    s.run(f"cd {MENU_DIR}")
    s.run(f"export PATH={MENU_DIR}:{MENU_BIN}:$PATH")
    s.run("PS1='%F{cyan}/run/media/mlibre/B/Projects%f %# '")
    s.run("RPROMPT='%F{blue}18:04%f'")
    s.send("tzz")
    s.settle()
    s.write(TAB)
    s.settle()
    line, drawn, size, idx = s.menu(clear=False)
    lines = screen_of(s).lines()
    start = next(i for i, ln in enumerate(lines)
                 if ln.startswith("/run/media/mlibre/B/Projects % tzz"))
    check("the input line keeps the prompt in front of it",
          lines[start].startswith("/run/media/mlibre/B/Projects % tzz"), True)
    check("and the right prompt, which the menu used to be written over",
          lines[start].endswith("18:04"), True)
    check("the screen shows the menu on the rows below it, and nothing else",
          [ln for ln in lines[start + 1:] if ln.strip()],
          [row.rstrip() for row in drawn.split("\\n") if row.strip()])
    # Cycle: a menu whose mark moves must not disturb the prompt line.
    s.write(TAB * 2)
    s.settle()
    cycled = screen_of(s).lines()
    check("the prompt line is the same after the mark moves", cycled[start], lines[start])
    check("no noise", s.noise(), [])
    s.close()


def main() -> int:
    setup()
    test_zsh_foreign_hint()
    test_zsh_tab_and_list()
    test_zsh_menu_colour()
    test_zsh_load_order()
    test_zsh_ghost_styling()
    test_zsh_menu_on_screen()
    check_fixture_intact("the run")
    print("\nOK — ghost text, hints, menu selection and colours, load order."
          if not failures else f"\nFAILED ({len(failures)}): {', '.join(failures)}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
