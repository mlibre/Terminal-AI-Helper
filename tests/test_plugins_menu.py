"""The Tab menu: what it lists, what Enter takes, and when it opens.

Menu behaviour is the part of the plugin most likely to be *nearly* right — the
entries are correct, the line is untouched, and the selection is never drawn —
so these tests assert on an emulated screen rather than on the plugin's arrays.

    python3 tests/test_plugins_menu.py
"""
import os
import pathlib
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from plugin_env import *  # noqa: F401,F403  (constants, fixture writers, setup)
from plugin_env import _zq  # underscore names are not re-exported by *
from plugin_screen import *  # noqa: F401,F403  (Screen and the styled-menu helpers)
from plugin_pty import *  # noqa: F401,F403  (Session, Ghosts, check, probe, failures)


def test_bash_menu() -> None:
    """bash cannot draw a list below the line; it can list without choosing.

    readline has no menu of its own. `menu-complete` draws a row at the *top* of
    the terminal and inserts the entry it is on, and it needs a terminal that
    answers a cursor-position query, which is not something a plugin loaded from
    an rc file may assume. What readline does have is `show-all-if-ambiguous`,
    and that is the half that matters: the first Tab shows what matches, and Tab
    still completes the moment the word is no longer ambiguous. Both halves are
    asserted, because a setting that made Tab only ever list would be worse than
    no setting at all.
    """
    if not SHELLS["bash"]:
        return
    print("bash tab menu")
    s = Session("bash")
    s.run(f"cd {MENU_DIR}")
    s.send("cat tzz")
    mark = len(s.seen)
    s.write(TAB)
    s.settle()
    check("the first Tab lists the matches", "tzz_a" in s.raw()[mark:], True)
    line = s.line()
    # readline also inserts the longest common prefix of the matches, which is
    # the one thing it does that a zsh listing does not: `tzz` becomes `tzz_`
    # here, because every name in that directory shares it. What must not happen
    # is a whole match being chosen — the line being rewritten before the list
    # has even been read — and a common prefix is not that.
    check("and no match is chosen", [n for n in MENU_ENTRIES if n in line], [])
    check("no noise from the listing", s.noise(), [])
    s.send(CTRL_U)
    s.send("cat tzz_s")
    s.write(TAB)
    s.settle()
    check("Tab still completes a unique word", s.line(), "cat tzz_solo ")
    s.close()
    check("the first Tab lists", tabs_to_list({}), 1)
    check("TAI_NO_MENU leaves readline to list in its own time",
          tabs_to_list({"TAI_NO_MENU": "1"}), 3)

    # The listing is coloured from the user's LS_COLORS: a directory's name in the
    # directory colour, its trailing slash left in the default one. It is read
    # off an emulated screen because that is the only place the colour exists —
    # the plugin sets a readline variable and readline does the drawing.
    #
    # LS_COLORS is set here rather than left to the environment because a shell
    # started with --norc has no colour scheme, and the claim under test is
    # "readline paints the listing in the colours it is given", not "every shell
    # has a colour scheme". With nothing to paint, readline emits nothing, which
    # is the same as a user who has set no colours.
    s = Session("bash")
    s.run(f"cd {MENU_DIR}")
    s.run("LS_COLORS='di=01;34:*.txt=01;32'; export LS_COLORS")
    s.send("ls tzz")
    mark = len(s.seen)
    s.write(TAB)
    s.settle()
    scr = Screen()
    scr.feed(s.raw()[mark:])
    check("bash colours a directory's name from LS_COLORS",
          style_of(scr, "tzz_dir"), (34, 0, True, False))
    check("and leaves the trailing slash alone", style_of(scr, "/"), PLAIN)
    check("no noise from the coloured listing", s.noise(), [])
    s.close()


def test_zsh_menu() -> None:
    """Tab lists the completions, Tab moves the mark, Enter takes one.

    The rule is that the line is not touched while the selection moves, and it
    is worth a test of its own because zsh's own menu completion does the
    opposite: each Tab rewrites the line with the entry it lands on. That is fine
    when you already know which entry you want, and useless when the point of
    opening a menu is to compare three candidates and then choose — with the line
    rewritten, there is nothing left to choose between.

    So: every Tab below checks the line as well as the selection, and the last
    one checks that Enter took the entry that was selected rather than the first.
    """
    if not SHELLS["zsh"]:
        return
    print("zsh tab menu")
    s = Session("zsh")
    s.run(f"cd {MENU_DIR}")
    # The names are on PATH so the chosen one can actually be run by Enter, which
    # is the only way a test can see which entry was taken: the line is gone by
    # then. The same names then arrive from the command list as well as from the
    # directory, and have to appear once. The second directory on PATH is here
    # for the names in it that cannot be run.
    s.run(f"export PATH={MENU_DIR}:{MENU_BIN}:$PATH")

    s.send("tzz")
    s.write(TAB)
    s.settle()
    line, drawn, size, idx = s.menu(clear=False)
    check("Tab lists every match", menu_entries(drawn), MENU_ENTRIES)
    check("a directory is listed with its slash", "tzz_dir/" in menu_entries(drawn), True)
    check("a name on PATH that cannot be run is not offered",
          [name for name in MENU_UNRUNNABLE if name in menu_entries(drawn)], [])
    check("Tab leaves the line alone", line, "tzz")
    check("the first entry is selected", selected_entry(screen_of(s)), MENU_ENTRIES[0])

    for step, want in enumerate(MENU_ENTRIES[1:], start=2):
        s.write(TAB)
        s.settle()
        line, drawn, size, idx = s.menu(clear=False)
        check(f"Tab {step} selects the next entry", selected_entry(screen_of(s)), want)
        check(f"Tab {step} still leaves the line alone", line, "tzz")
        check(f"Tab {step} reports the same menu", size, len(MENU_ENTRIES))

    s.write(TAB)
    s.settle()
    line, drawn, size, idx = s.menu(clear=False)
    check("the selection wraps round", selected_entry(screen_of(s)), MENU_ENTRIES[0])
    s.write("\x15")

    # `tzz_d` is both a file in that directory and a prefix of the directory next
    # to it. Offering `tzz_d` for `tzz_d` would be a menu with one entry that says
    # nothing the line does not already say — the same rule the ghost text
    # follows, and the reason `ls -l` must not beat `ls -la`. What is left is one
    # entry, and one entry is not a menu: the key takes it, which is the whole
    # point of a completion that has only one answer. The name is in the
    # directory the user is standing in, which is what makes it something they
    # have been shown.
    s.send("tzz_d")
    s.write(TAB)
    s.settle()
    line, drawn, size, idx = s.menu(clear=False)
    check("one entry is taken, not offered", (line, size), ("tzz_dir/", 0))
    s.write("\x15")

    # The same single entry, but a name that exists only because it matched on
    # PATH: the reported case, where `exe` had exactly one match anywhere and it
    # was a script the user had never run. PATH alone says *it exists*, and one
    # entry is one entry — every other shell completes the ambiguity that
    # remains.
    s.send(MENU_FAR[:3])
    check("and nothing is hinted for it",
          s.suggestion().split(" PD=")[0], "SUG=[]")
    s.send(MENU_FAR[:3])
    s.write(TAB)
    s.settle()
    line, drawn, size, idx = s.menu(clear=False)
    check("one entry is taken even when only PATH knows it",
          (line, size), (MENU_FAR, 0))
    s.write("\x15")

    # A learned name and the directory of the same name are one completion, not
    # two. `cd tzz_dir` is in the history and `tzz_dir/` is in the directory, and
    # a menu holding both would be asking the user to choose between a name and
    # itself. The learned form wins, so the menu agrees with the hint.
    s.send("cd tzz_di")
    check("and the hint was already saying it",
          s.suggestion().split(" PD=")[0], "SUG=[r]")
    s.send("cd tzz_di")
    s.write(TAB)
    s.settle()
    line, drawn, size, idx = s.menu(clear=False)
    check("a learned name and the path of the same name are one entry",
          (line, size), ("cd tzz_dir", 0))
    s.write("\x15")

    # The same rule, and this one was reported: every `cd …` line in the history
    # offers its next word, so `cd ..` offers `..` and `cd -` offers `-`. Neither
    # starts with `t`, so neither completes `cd t`, and a menu listing them is
    # noise with nothing behind it. The hint has always filtered on "extends the
    # line" inside _tai_best, so the menu has to ask the same question.
    s.send("cd t")
    s.write(LIST)
    s.settle()
    line, drawn, size, idx = s.menu(clear=False)
    check("a learned word that does not extend the line is not a completion",
          [name for name in menu_entries(drawn) if name in ("..", "-", "/tmp")], [])
    check("while the ones that do extend it are still there",
          "tzz_dir" in menu_entries(drawn), True)
    s.write("\x15")
    s.send("cd t")
    check("and the hint agrees on what the word can be",
          s.suggestion().split(" PD=")[0], "SUG=[zz_dir]")
    s.write("\x15")

    # The reported case was `cd ` answering `cd ..`, so the index now ranks a
    # real destination above a relative move. What has to survive that is the
    # move itself: the rule is about ranking, and a fix that dropped `cd ..` from
    # the index would stop `cd .` completing to it and quietly take away a command
    # that works. Asserted here because this is the surface a user touches.
    s.send("cd ")
    check("a bare `cd ` is answered with a destination, not `cd ..`",
          s.suggestion().split(" PD=")[0], "SUG=[tzz_dir]")
    s.write("\x15")
    # Asked for as a *word*, so the menu is about the word rather than about the
    # directory: `cd .` can only be completed by `..`, which is the assertion
    # that the demotion ranked the move rather than dropping it from the index.
    s.send("cd .")
    check("while `cd .` still completes to it — ranked, not removed",
          s.suggestion().split(" PD=")[0], "SUG=[.]")
    # Re-sent because reading the hint ends the line in zsh (the dump widget is
    # followed by ^C). Tab then *takes* the completion rather than opening a
    # list, because there is exactly one thing the word can become — the rule
    # demoted `cd ..` in the ranking, it did not take it off the menu, and the
    # proof is that the key still writes it.
    s.send("cd .")
    s.write(TAB)
    s.settle()
    line, drawn, size, idx = s.menu(clear=False)
    check("and the key that takes a completion still writes it",
          (line, size), ("cd ..", 0))
    s.write("\x15")

    # A key that was not Tab puts the menu away: the entries belonged to a line
    # that is no longer the one being typed.
    s.send("tzz")
    s.write(TAB)
    s.settle()
    s.send("X")
    line, drawn, size, idx = s.menu(clear=False)
    check("typing closes the menu", (size, idx), (0, 0))
    check("and the character is in the line", line, "tzzX")
    s.write("\x15")

    # A name the directory does not have, and no command has either.
    s.send(NOT_INSTALLED)
    s.write(TAB)
    s.settle()
    line, drawn, size, idx = s.menu(clear=False)
    check("nothing to complete means no menu", (size, line), (0, NOT_INSTALLED))
    s.write("\x15")

    # A word the cursor is not on. The index is keyed on whole lines, so there is
    # no history behind half a line, and a menu that marked an entry here would
    # be marking a word the user is not looking at. Only the menu is asserted:
    # from there Tab is zsh's own completion again, and what that does to a bare
    # cursor is zsh's business, not this plugin's.
    s.send("tzz")
    s.write(CTRL_A)
    s.write(TAB)
    s.settle()
    line, drawn, size, idx = s.menu(clear=False)
    check("a cursor off the end gets the shell's own completion", (size, idx), (0, 0))
    s.write("\x15")

    # What tai learned, in the order it ranked it, and the wrapper rule with it:
    # the menu reads the same lookup as the ghost text, so `sudo git ` offers the
    # git the user has run rather than nothing at all. Only the learned entries
    # are asserted: the word is empty there, so the directory is offered too,
    # which is what any completion does.
    s.send("git ")
    s.write(LIST)
    s.settle()
    line, drawn, size, idx = s.menu(clear=False)
    check("the menu offers what tai learned",
          menu_entries(drawn)[:2], ["pull", "status"])
    s.write("\x15")

    # And the same lookup, asked about a word: `git p` has one answer, because
    # `git status` completes `git ` and not `git p`. That is one completion, so
    # the key takes it — which is also what the hint was saying all along.
    s.send("git p")
    s.write(LIST)
    s.settle()
    line, drawn, size, idx = s.menu(clear=False)
    check("one learned answer completes rather than listing",
          (line, size), ("git pull", 0))
    s.write("\x15")
    s.send("git p")
    check("and the hint was completing to the same word",
          s.suggestion().split(" PD=")[0], "SUG=[ull --rebase]")
    s.write("\x15")
    s.send("sudo git ")
    s.write(LIST)
    s.settle()
    line, drawn, size, idx = s.menu(clear=False)
    check("a wrapper does not hide the menu",
          menu_entries(drawn)[:2], ["pull", "status"])
    s.write("\x15")

    # An installed command the history has never seen still gets its one honest
    # completion, exactly as the ghost text gives it.
    s.send(INSTALLED + " ")
    s.write(TAB)
    s.settle()
    line, drawn, size, idx = s.menu(clear=False)
    check("an unknown tool still offers --help", selected_entry(screen_of(s)), "--help")
    s.write("\x15")
    check("no noise from the menu", s.noise(), [])
    s.close()

    # Enter takes the selected entry and stops there. It must not run the line:
    # filling in a directory you picked out of a menu is not a request to go
    # there, and the line is there to be read before it is run. So this checks
    # both halves — the entry was filled, the first one was not, and nothing ran
    # until the next Enter.
    s = Session("zsh")
    s.run(f"cd {MENU_DIR}")
    s.run(f"export PATH={MENU_DIR}:{MENU_BIN}:$PATH")
    s.send("tzz")
    s.write(TAB + TAB)
    s.settle()
    s.write(ENTER)
    s.settle()
    line, drawn, size, idx = s.menu(clear=False)
    check("Enter fills in the selected entry", line, "tzz_b")
    check("and closes the menu", (size, idx), (0, 0))
    check("Enter did not take the first entry", line.startswith("tzz_a"), False)
    check("and Enter did not run it", "RAN tzz_b" in s.seen, False)
    # The second Enter is the one that runs, which is the whole point of the
    # first one not doing it.
    s.write(ENTER)
    s.wait_re(r"RAN tzz_b", "the line to run on the second Enter")
    check("the second Enter runs the line", s.noise(), [])
    s.close()

    # TAI_NO_MENU=1 puts Tab back to what it did before the menu existed, which
    # is the only way a user who wants the old rhythm gets it.
    probe("zsh", TAB, "docker", "docker ps", env={"TAI_NO_MENU": "1"})

    # Down steps the *normal* menu as well — not through its own binding, but
    # because the same key sequences ask the plugin to drive the selection it
    # already owns.
    s = Session("zsh")
    s.run(f"cd {MENU_DIR}")
    s.send("tzz")
    s.write(LIST)
    s.settle()
    s.write("\x1b[B")
    s.settle()
    s.write(ENTER)
    s.settle()
    line, drawn, size, idx = s.menu(clear=False)
    check("Down steps a normal armed menu", line, "tzz_b")
    check("and nothing ran it", "RAN tzz_b" in s.seen, False)
    check("no noise while stepping", s.noise(), [])
    s.close()


def test_zsh_menu_stem() -> None:
    """A menu shows what an entry adds, not what every entry shares.

    The reported case: `cd media/mlibre/B/` and a Tab drew the ten directories
    under it, each with `media/mlibre/B/` in front of it. The line above the menu
    already said that, so ten rows spent most of the screen on eight words. It
    costs columns as well: the longest entry sets the width of every cell, and one
    51-character `.iso` name put ten entries into a single column on a
    hundred-column terminal.

    So the stem is drawn once, by the line, and a row carries only what it adds.
    Three things are asserted, because each is its own way of being wrong:

    * the rows lose the stem, so the list reads as the contents of the directory
      that was typed;
    * `Enter` still writes the **whole** word — the stem is dropped from the
      drawing and kept in the entry, and a menu that inserted the suffix it drew
      would quietly truncate the line;
    * what the entries share is only dropped when it is a whole word. `tzz_a` and
      `tzz_b` share `tzz`, which is not a word, and cutting there would leave `a`
      and `b`; that is the case the existing `tzz` menu already covers, so what is
      asserted here is the other boundary — a space, on learned lines.

    The learned entries are space-free on purpose. A learned word can hold a
    space (`git checkout --orphan tmp`), and `menu_entries` reads a run of spaces
    as the gap between columns, so such an entry would be read as two.
    """
    if not SHELLS["zsh"]:
        return
    print("zsh menu stem")
    s = Session("zsh")
    s.run(f"cd {MENU_DIR}")

    s.send("cd stemroot/")
    s.write(TAB)
    s.settle()
    line, drawn, size, idx = s.menu(clear=False)
    check("the stem is drawn once, by the line, and not on every row",
          menu_entries(drawn), STEM_NAMES)
    check("the line is untouched", line, "cd stemroot/")
    check("every entry is there", size, len(STEM_NAMES))
    check("the first entry is selected",
          selected_entry(screen_of(s)), STEM_NAMES[0])
    s.write(ENTER)
    s.settle()
    line, drawn, size, idx = s.menu(clear=False)
    # The whole word, stem and all: this is the half that would corrupt the line.
    check("Enter writes the whole word, not the drawn row",
          line, f"cd stemroot/{STEM_NAMES[0]}")
    check("and closes the menu", (size, idx), (0, 0))
    s.write("\x15")

    # The other boundary, and the only one a menu can reach with anything but a
    # path: two file names that share a whole *word*. The word being typed is
    # `note`, what the entries share is `note `, and the rows are what each name
    # adds — the stem is what they share, not what was typed.
    s.send("womb")
    s.write(TAB)
    s.settle()
    line, drawn, size, idx = s.menu(clear=False)
    check("entries sharing a word are drawn without it",
          menu_entries(drawn), ["book.txt", "cards.txt"])
    check("and the line still says what was typed", line, "womb")
    s.write(ENTER)
    s.settle()
    line, drawn, size, idx = s.menu(clear=False)
    # The whole name, and quoted: this file is called `wombat book.txt`, so the
    # bare name in the line is two words and `cat wombat book.txt` is a command
    # that cannot work. The row is still drawn without the quoting — the drawing
    # shows the file, the line holds the text a shell passes as one word.
    check("Enter writes the whole name", line, "wombat\\ book.txt")
    s.write("\x15")

    # A shared *fragment* is not a stem, however long it is. These seven share
    # `tzz_`, and `tzz_` is not a word — cutting there would leave `a`, `b`, `dir/`
    # and every other name reduced to its own tail, which is a menu answering a
    # different question.
    s.send("tzz_")
    s.write(LIST)
    s.settle()
    line, drawn, size, idx = s.menu(clear=False)
    # Six of them, not seven: `tzz_other` is on PATH, and this session did not
    # put MENU_BIN there. The names here are the ones next to the cursor, which is
    # where the files are.
    check("a shared fragment is not a stem", menu_entries(drawn),
          ["tzz_a", "tzz_b", "tzz_c", "tzz_d", "tzz_dir/", "tzz_solo"])
    check("and the line is still the whole word", line, "tzz_")
    s.write("\x15")
    check("no noise from the stem", s.noise(), [])
    s.close()


def test_loose_menu() -> None:
    """The loose menu: previewed while typing, armed by the down arrow,
    kept after Enter until a second one runs it. Also the key-sequence
    wiring: an rc file that bound the arrows somewhere else still gets
    the armed list from the same key."""
    print("zsh loose menu, armed by the down arrow")
    # Words that no learned line starts with, but that learned lines do
    # contain: the glimpse comes up by itself, and Down steps into it.
    s = Session("zsh")
    s.send("pull --rebase")
    s.settle()
    got = s._dump(clear=False)
    check("loose lines glimpsed while typing", got[2],
          "MENU=[\\n  git pull --rebase] N=[1] IDX=[1]")
    check("disarmed: no selection highlight yet", "standout" in got[1], False)
    s.write("\x1b[B")
    s.settle()
    got = s._dump(clear=False)
    check("Down arms the glimpse", "standout" in got[1], True)
    s.write(ENTER)
    s.settle()
    got = s._dump(clear=False)
    check("Enter fills in the loose match", got[0], "git pull --rebase")
    check("and did not run it", "RAN git" in s.seen, False)
    check("and the menu closed", got[2], "MENU=[] N=[0] IDX=[0]")
    s.close()

    # Same glimpse in the key mode the terminal actually uses once it has
    # emitted smkx: application cursor keys.
    s = Session("zsh")
    s.send("pull --rebase")
    s.settle()
    s.write("\x1bOB")
    s.settle()
    got = s._dump(clear=False)
    check("Down in application cursor mode also arms", "standout" in got[1], True)
    s.close()

    # Up from a glimpse nothing marked yet puts the list away *for that line*:
    # nothing re-opens on the same text, and a new line gets a fresh glimpse.
    s = Session("zsh")
    s.send("pull --rebase")
    s.settle()
    s.write("\x1bOA")
    s.settle()
    got = s._dump(clear=False)
    check("Up closes the glimpse", got[2], "MENU=[] N=[0] IDX=[0]")
    s.write("\x05")     # ^E: cursor to the end, line unchanged
    s.settle()
    got = s._dump(clear=False)
    check("nothing re-opened on the same line", got[2], "MENU=[] N=[0] IDX=[0]")
    s.write("\x15")     # ^U: clear; ask again with a different word
    s.send("status")
    s.settle()
    got = s._dump(clear=False)
    check("a new line re-opens the glimpse", got[2],
          "MENU=[\\n  git status] N=[1] IDX=[1]")
    s.close()

    # A stock startup that binds the arrows somewhere else — the way
    # manjaro-zsh-config hands them to zsh-history-substring-search. The
    # down arrow has to step into the loose list *anyway*, and Enter takes it.
    if pathlib.Path("/usr/share/zsh/manjaro-zsh-config").exists():
        s = Session("zsh")
        s.run("source /usr/share/zsh/manjaro-zsh-config")
        s.run(f"source {REPO}/plugins/tai.zsh")
        s.send("pull --rebase")
        s.settle()
        s.write("\x1b[B")
        s.settle()
        got = s._dump(clear=False)
        check("Down steps into the loose list even when the rc bound it elsewhere",
              "standout" in got[1], True)
        s.write(ENTER)
        s.settle()
        got = s._dump(clear=False)
        check("and Enter writes the armed loose match into the line", got[0],
              "git pull --rebase")
        check("no zle mishap from the stash wiring", s.noise(), [])
        s.close()

    # Enter with a *disarmed* glimpse does not swallow the line: the typed
    # text runs as typed.
    s = Session("zsh")
    s.send("pull --rebase")
    s.settle()
    s.write(ENTER)
    s.settle()
    check("Enter on a disarmed glimpse runs the typed line", s.line(), "")
    s.close()

    # Typos: when no line exactly continues the text, the loose glimpse still
    # finds a line whose letters, in order, hold every letter of the typed
    # word — `gst` is a tap away from `git status`.
    s = Session("zsh")
    s.send("gst")
    s.settle()
    got = s._dump(clear=False)
    check("a short typo still reaches the learned line", got[2],
          "MENU=[\\n  git status] N=[1] IDX=[1]")
    # And the two clauses that keep that from being arithmetic: too short to be
    # fuzzy, and nothing to be found. `ts` is not a command on this machine, so
    # nothing else can answer it — `gs`, which is one, gets the `--help`
    # fallback instead and is checked in test_zsh_menu.
    s.write("\x15")     # ^U: clear
    s.send("ts")
    s.settle()
    got = s._dump(clear=False)
    check("but two letters are not a typo", got[2], "MENU=[] N=[0] IDX=[0]")
    s.write("\x15")
    s.send("zqx")
    s.settle()
    got = s._dump(clear=False)
    check("and letters nothing shares stay silent", got[2], "MENU=[] N=[0] IDX=[0]")
    s.close()

    # The same prefix twice is one answer, not two scans: ZLE redraws without the
    # buffer changing (cursor moves, a menu repaint, a resize), and the glimpse
    # has to still be on screen for the second one. A cache that skips the scan
    # but forgets to hand the answer back loses the menu on the redraw that
    # follows, which is the same bug seen one step later.
    s = Session("zsh")
    s.send("gst")
    s.settle()
    first = s._dump(clear=False)
    s.write("\x01\x1b[C")      # ^A, Right: move, redraw, buffer unchanged
    s.settle()
    again = s._dump(clear=False)
    check("the redraw keeps the same glimpse", again[2], first[2])
    s.close()

    # Very long learned lines must not smear the whole screen: the loose list
    # trims its cells to the terminal width instead of letting one curl
    # monster row overflow every column.
    long_cmd = "LONG_MARKER curl https://example.com/" + "verylong" * 80 + " --compressed -H UA:abcd"
    text = ZSH_INDEX.read_text()
    list_probe = text + f"_TAI_SCORE+=({_zq(long_cmd)} 50)\n_TAI_FIRST+=({_zq('curl')} {_zq(long_cmd)})\n_TAI_WORD+=({_zq('curl')} {_zq(long_cmd)})\n"
    ZSH_INDEX.write_text(list_probe)
    try:
        s = Session("zsh")
        s.send("LONG_MARKER")
        s.settle()
        # The rows are read from the drawn menu, not from the raw dump line:
        # the dump appends `] N=[…] IDX=[…]` to the same string, and counting
        # that bookkeeping as part of a row would make a row that fits look
        # like one that does not.
        _line, drawn, size, _idx = s.menu(clear=False)
        rows = [r for r in drawn.split("\\n") if r]
        check("loose list shows the long line", any("LONG_MARKER" in r for r in rows), True)
        check("but each row fits the screen",
              all(len(r) <= Screen.COLS for r in rows), True)
        s.close()
    finally:
        write_index()


def test_pasted_text_is_not_a_typo() -> None:
    """A paste is not a half-remembered command, and the scan has to say so
    before it reads a single learned line.

    `_tai_loose` turns each typed word into a pattern of its letters joined by
    `*` and runs that against every line the index holds. The cost grows with the
    length of the word in a way nothing about typing suggests: 1.7s for one redraw
    at 500 characters, 3.9s at 1100, 34s at 6000. So a pasted token is matched as
    a plain substring instead of becoming a pattern, and a pasted *line* — longer
    than `_TAI_LOOSE_MAX_LINE` — is refused before the scan starts at all.

    Matched rather than dropped is the other half of the rule, and it is the half
    that has to be tested: a word that leaves the question takes its veto with it,
    so `cd` followed by a pasted path would only ask whether a line mentions `cd`,
    and every learned `cd` command would answer. That is what it did, and the
    "and closes the menu" assertion in `test_zsh_menu_stem` is what caught it.
    Both bounds change what the glimpse answers, so they are asserted as answers
    rather than left as a comment, and the cost is asserted as a bound rather than
    as a benchmark: the same call with the bounds raised is 8.9s on this fixture,
    so 5 seconds is the fix rather than a slow machine.
    """
    if not SHELLS["zsh"]:
        return
    print("pasted text is not a typo")

    # A learned line holding one word longer than `_TAI_LOOSE_MAX_WORD`, and
    # short enough that the line bound cannot be what answers for it.
    line = "curl https://example.com/aaabbbccddeeffgg theend"
    verbatim = "https://example.com/aaabbbccddeeffgg"     # 36 chars, in `line`
    # The same word with the space taken out: the line holds its letters *in
    # order* and not the word itself, which is what an unbounded fuzzy pattern
    # would match and a substring never can.
    in_order = "curlhttps://example.com/aaabbbccddeeffgg"  # 40 chars
    assert verbatim in line and in_order not in line
    text = ZSH_INDEX.read_text()
    ZSH_INDEX.write_text(
        text + f"_TAI_SCORE+=({_zq(line)} 9)\n"
        f"_TAI_FIRST+=({_zq('curl')} {_zq(line)})\n"
        f"_TAI_WORD+=({_zq('curl')} {_zq(line)})\n")
    try:
        s = Session("zsh")
        # A pasted token that really is the text of a learned line still finds
        # it: the bound is about the *pattern*, not about losing the answer.
        # Rows in a loose list are capped at _TAI_MENU_LOOSE_CELL=50 chars, so
        # what is painted is a prefix of the line, not the whole thing.
        s.send(verbatim)
        s.settle()
        check("a pasted token typed verbatim still answers",
              s._dump(clear=False)[2],
              "MENU=[\\n  curl https://example.com/aaabbbccddeeffgg theend] N=[1] IDX=[1]")
        # …and one that is not in any line does not, because nothing may build a
        # 40-segment pattern out of it.
        s.write(CTRL_U)
        s.send(in_order)
        s.settle()
        check("a pasted token that is not text is not fuzzy-matched either",
              s._dump(clear=False)[2], "MENU=[] N=[0] IDX=[0]")
        # A line past `_TAI_LOOSE_MAX_LINE` is refused outright.
        s.write(CTRL_U)
        s.send("curl " + "z" * 220)
        s.settle()
        check("a pasted line is refused", s._dump(clear=False)[2],
              "MENU=[] N=[0] IDX=[0]")
        s.close()
    finally:
        write_index()

    # The bound as a cost. 2000 learned lines, because the pathology is the
    # product of the pattern's length and the number of lines it is run against —
    # a fixture of ten lines cannot see it. The token is a real URL's worth of
    # hex, which is what makes it expensive: a word of a few repeated letters is
    # matched by the first path the engine tries. Unbounded, the same call is
    # 8.9s on this fixture, so 5 seconds separates the bound from the machine.
    word = ("https://release-assets.githubusercontent.com/github-production/"
            "3f5d6f8-f335-4b20-b2cc-cd45fbbea0a4?sp=r&sv=2018-11-09&sr=b&"
            "spr=https&se=2026-04-05T20%3A20%3A35Z" * 4)[:6000]
    write_index([f"echo line{i} filler{i} tail{i}" for i in range(2000)])
    try:
        env = dict(os.environ, TAI_INDEX=str(ZSH_INDEX), TAI_DB=str(DB),
                   TAI_HISTORY_FILES="", TAI_DATA_DIR="/tmp/opencode/tai_loose")
        try:
            subprocess.run(
                ["zsh", "-f", "-c",
                 f"source {REPO}/plugins/tai.zsh\n_tai_loose {word!r}"],
                capture_output=True, text=True, env=env, timeout=5)
        except subprocess.TimeoutExpired:
            failures.append("a pasted token does not cost a redraw")
    finally:
        write_index()


def test_unpaintable_rows_are_never_drawn() -> None:
    """A learned row with raw control bytes never reaches the terminal.

    The history held two rows that were really bracketed-paste envelopes, and
    the index inherited them: one began with ESC, the other with SYN. Painted
    from either into POSTDISPLAY, those bytes went to the terminal unescaped
    and were escape sequences, not text. The store, the engine, and the zsh
    plugin's candidate lists now all refuse them; this rides the one path a
    user hits — a glimpse that would have drawn one — and asserts nothing is
    drawn at all.
    """
    if not SHELLS["zsh"]:
        return
    print("a control byte in a row is never drawn")
    text = ZSH_INDEX.read_text()
    dirty = '\x1b[200~zqx run~'
    ZSH_INDEX.write_text(
        text + f"_TAI_SCORE+=({_zq(dirty)} 9)\n"
               f"_TAI_FIRST+=({_zq('zqx')} {_zq(dirty)})\n"
               f"_TAI_WORD+=({_zq('zqx run')} {_zq(dirty)})\n")
    try:
        s = Session("zsh")
        s.send("zqx")
        s.settle()
        # The loose list was the only answer: the row that would have fed it
        # is filtered, so nothing may appear — before, the raw ESC line was.
        check("no raw bytes are glimpsed", s._dump(clear=False)[2],
              "MENU=[] N=[0] IDX=[0]")
        s.close()
    finally:
        write_index()


def main() -> int:
    setup()
    test_bash_menu()
    test_zsh_menu()
    test_zsh_menu_stem()
    test_loose_menu()
    test_pasted_text_is_not_a_typo()
    test_unpaintable_rows_are_never_drawn()
    check_fixture_intact("the run")
    print("\nOK — the Tab menu in bash and zsh, entry by entry."
          if not failures else f"\nFAILED ({len(failures)}): {', '.join(failures)}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
