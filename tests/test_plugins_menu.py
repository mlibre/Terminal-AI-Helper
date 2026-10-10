"""The Tab menu: what it lists, what Enter takes, and when it opens.

Menu behaviour is the part of the plugin most likely to be *nearly* right — the
entries are correct, the line advances only by what every entry shares, and the
selection is never drawn — so these tests assert on an emulated screen rather
than on the plugin's arrays.

    python3 tests/test_plugins_menu.py
"""
import os
import pathlib
import re
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
    # readline also inserts the longest common prefix of the matches — once the
    # one thing it did that a zsh listing did not; the zsh menu writes the same
    # head now, and test_zsh_menu_prefix pins both shells on it. What must not
    # happen is a whole match being chosen — the line being rewritten before the
    # list has even been read — and a common prefix is not that.
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

    # A directory argument is answered by directories and by nothing else: the
    # shell's own `compgen -d` list, learned words tested for liveness, and no
    # file anywhere in it — `cd tzz_a` is an error the user would have to
    # notice and retype. TAI_COMPLETE_ALL=1 is what puts tai on the default
    # completion, which is where a `cd ` Tab lands.
    s = Session("bash", env_extra={"TAI_COMPLETE_ALL": "1"})
    s.run(f"cd {MENU_DIR}")
    s.send("cd ")
    mark = len(s.seen)
    s.write(TAB)
    s.settle()
    raw = s.raw()[mark:]
    check("bash offers the directories after cd", "stemroot/" in raw, True)
    check("and the learned destination", "tzz_dir" in raw, True)
    check("but no file, however fresh",
          "tzz_a" not in raw and "tzz_solo" not in raw, True)
    check("no noise from the cd listing", s.noise(), [])
    s.close()


def test_zsh_menu() -> None:
    """Tab lists the completions, Tab moves the mark, Enter takes one.

    The rule is that the line moves only by what every entry shares: the press
    that opens the list first writes the head all candidates start with — `tzz`
    becomes `tzz_`, because every entry in the menu begins there — and after
    that the line stands while the selection moves. That is worth a test of its
    own because zsh's own menu completion does the opposite: each Tab rewrites
    the line with the entry it lands on. That is fine when you already know
    which entry you want, and useless when the point of opening a menu is to
    compare three candidates and then choose — with the line rewritten, there
    is nothing left to choose between.

    So: the first Tab below checks the head it writes, every later Tab checks
    the line as well as the selection, and the last one checks that Enter took
    the entry that was selected rather than the first.
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
    # The head every entry shares — `tzz_` — is in the line before the list
    # draws, the same thing readline writes on this key (see test_bash_menu).
    check("Tab writes the head every entry shares", line, "tzz_")
    check("the first entry is selected", selected_entry(screen_of(s)), MENU_ENTRIES[0])

    for step, want in enumerate(MENU_ENTRIES[1:], start=2):
        s.write(TAB)
        s.settle()
        line, drawn, size, idx = s.menu(clear=False)
        check(f"Tab {step} selects the next entry", selected_entry(screen_of(s)), want)
        check(f"Tab {step} still leaves the line at the head", line, "tzz_")
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
    # line" inside _tai_best, so the menu has to ask the same question. The
    # directories-only answer leaves `cd t` exactly one live completion — the
    # directory the history names — and the key that completes takes it instead
    # of listing a menu of one.
    s.send("cd t")
    s.write(TAB)
    s.settle()
    line, drawn, size, idx = s.menu(clear=False)
    check("a cd word with one live directory completes to it",
          (line, size), ("cd tzz_dir", 0))
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
    # that is no longer the one being typed. The line was left at the head the
    # menu wrote — `tzz_` — and the keystroke lands after it. The menu's own
    # rows are gone with it; what the dump may hold instead is the loose
    # glimpse, which the edited word `tzz_X` is one deletion from earning —
    # a reader's list, not the menu that was open.
    s.send("tzz")
    s.write(TAB)
    s.settle()
    s.send("X")
    line, drawn, size, idx = s.menu(clear=False)
    check("typing closes the menu", "tzz_a" in drawn, False)
    check("and the character is in the line", line, "tzz_X")
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


def test_zsh_menu_prefix() -> None:
    """Tab writes the head every candidate already shares, then lists.

    The reported case: `cat .zs` answered with `.zshrc` and `.zsh/` — two
    candidates that are the same word until the `h` — and the menu drew them
    under a line still reading `.zs`. Every shell completes the certain part
    first, and readline had been doing it here all along (the bash half of
    test_bash_menu pins `tzz` becoming `tzz_`); the menu made the user type the
    shared tail personally, or take a whole entry to get it. So the press that
    opens the list first advances the line to the longest head every entry
    starts with, and the list draws under the advanced line.

    Four ways to be wrong, each asserted: writing it twice (the second Tab
    cycling must not append the head again); writing it when the word already
    IS the head (`cat .zsh` stays `cat .zsh`); writing a head the typed word
    does not start with (the case-folded head takes the candidates' case, and
    only folds from the word onward); and writing anything at all when the
    word is empty — `cat ` asks for a listing, not for a guess typed on the
    user's behalf.
    """
    if not SHELLS["zsh"]:
        return
    print("zsh menu writes the shared head first")
    s = Session("zsh")
    s.run(f"cd {PREFIX_DIR}")

    # The reported pair. The file answer answers `.zs` with `.zshrc` from the
    # disk's (#i) fallback — a directory is not a file, so `.zsh/` arrives from
    # the directory listing — and the head of the two written forms is `.zsh`.
    s.send("cat .zs")
    s.write(TAB)
    s.settle()
    line, drawn, size, idx = s.menu(clear=False)
    check("the line takes the head both candidates share", line, "cat .zsh")
    check("and the list shows both candidates",
          (".zshrc" in menu_entries(drawn), ".zsh/" in menu_entries(drawn)),
          (True, True))
    check("with the first one selected", selected_entry(screen_of(s)), ".zshrc")

    # The next press is the choosing press it always was: the selection moves
    # and the line stands. A head written twice would read `.zsh_` here.
    s.write(TAB)
    s.settle()
    line, drawn, size, idx = s.menu(clear=False)
    check("the next Tab cycles without writing again",
          (line, selected_entry(screen_of(s))), ("cat .zsh", ".zsh/"))

    # Enter lays the whole entry over the span the head fills — head included,
    # exactly once.
    s.write(ENTER)
    s.settle()
    line, drawn, size, idx = s.menu(clear=False)
    check("Enter takes an entry over the advanced line",
          (line, size), ("cat .zsh/", 0))
    s.write("\x15")

    # A word that already IS the head: nothing to write, the menu just lists.
    s.send("cat .zsh")
    s.write(TAB)
    s.settle()
    line, drawn, size, idx = s.menu(clear=False)
    check("a word that is the head is not extended",
          (line, size), ("cat .zsh", 2))
    s.write("\x15")

    # The head folds case, from the word onward: `down` is answered by two
    # files whose shared head is `Downloads`, and the line takes the head's
    # case — the rule every accept key already keeps.
    s.send("cat down")
    s.write(TAB)
    s.settle()
    line, drawn, size, idx = s.menu(clear=False)
    check("a head reached through the case fold is written in the candidates' case",
          line, "cat Downloads")
    check("and both folded files are on the list",
          ("Downloads.zip" in menu_entries(drawn),
           "Downloads2.txt" in menu_entries(drawn)), (True, True))
    s.write("\x15")

    # A head that only exists quoted. Both candidates are `My Docs…`, so the
    # head of the *written* forms is `My\ Docs` — one shell word, safe to stand
    # in the line however long the user keeps typing. The rows are drawn raw,
    # and read as names — which is a containment assertion on the drawn text,
    # because menu_entries reads the space inside the name as a column gap.
    s.send("cat My")
    s.write(TAB)
    s.settle()
    line, drawn, size, idx = s.menu(clear=False)
    check("a head with a space in it is written quoted", line, "cat My\\ Docs")
    flat = drawn.replace("\\n", " ")
    check("and the rows are drawn as the names they are",
          ("My Docs2.txt" in flat, "My Docs/" in flat), (True, True))
    # Cycle to the directory and take it: the entry replaces the head — no
    # doubled `Docs`, no half a quote left behind.
    s.write(TAB)
    s.settle()
    s.write(ENTER)
    s.settle()
    line, drawn, size, idx = s.menu(clear=False)
    check("Enter replaces the quoted head with the whole entry",
          (line, size), ("cat My\\ Docs/", 0))
    s.write("\x15")

    # An empty word is nothing to extend: the line ends in a space, the menu
    # is the listing, and nothing is typed on the user's behalf.
    s.send("cat ")
    s.write(TAB)
    s.settle()
    line, drawn, size, idx = s.menu(clear=False)
    check("an empty word is listed, not filled in",
          (line, size > 0), ("cat ", True))
    s.write("\x15")
    check("no noise from the shared head", s.noise(), [])
    s.close()

    # readline has been completing the certain part first for as long as it
    # has existed; the point of the half above is that the shells now agree.
    # (That readline also *lists* what remains ambiguous is test_bash_menu's
    # own assertion; here the listing arrives coloured, and the colour's own
    # bytes separate a directory's name from its slash.)
    if SHELLS["bash"]:
        print("bash writes the shared head first (readline's own)")
        b = Session("bash")
        b.run(f"cd {PREFIX_DIR}")
        b.send("cat .zs")
        b.write(TAB)
        b.settle()
        check("readline completes the shared head too", b.line(), "cat .zsh")
        check("no noise from the bash head", b.noise(), [])
        b.close()


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

    # Four entries, not five: `stemroot/` holds a directory argument's menu,
    # and `stem_zeta.iso` is a file — the shell answers `cd stem_zeta.iso`
    # with "not a directory", so the menu does not offer it. The long
    # directory name still sets the width of every cell, which is the half
    # of the cost the stem rule answers.
    stem_dirs = [n for n in STEM_NAMES if n.endswith("/")]
    s.send("cd stemroot/")
    s.write(TAB)
    s.settle()
    line, drawn, size, idx = s.menu(clear=False)
    check("the stem is drawn once, by the line, and not on every row",
          menu_entries(drawn), stem_dirs)
    check("the line is untouched", line, "cd stemroot/")
    check("every entry is there", size, len(stem_dirs))
    check("a file is not offered after cd",
          "stem_zeta.iso" in menu_entries(drawn), False)
    check("the first entry is selected",
          selected_entry(screen_of(s)), stem_dirs[0])
    s.write(ENTER)
    s.settle()
    line, drawn, size, idx = s.menu(clear=False)
    # The whole word, stem and all: this is the half that would corrupt the line.
    check("Enter writes the whole word, not the drawn row",
          line, f"cd stemroot/{STEM_NAMES[0]}")
    check("and closes the menu", (size, idx), (0, 0))
    s.write("\x15")

    # Two file names that share a whole *word* — `wombat book.txt` and
    # `wombat cards.txt` under a word typed as `womb`. The head of the two
    # written forms is `wombat\ ` — one shell word, every entry extending it —
    # and Tab writes it before the list draws, so the line advances to the
    # part that is certain. The rows are then drawn whole: the stem clamp
    # cannot cut a shared word into the line when the line holds it quoted and
    # the rows are raw, and a row that reads `book.txt` beside a line reading
    # `wombat\ ` asks the reader to glue a quote into a name nobody typed.
    s.send("womb")
    s.write(TAB)
    s.settle()
    line, drawn, size, idx = s.menu(clear=False)
    # menu_entries reads a run of spaces as a column gap, and these names hold
    # a space of their own, so the assertion is on the drawn text itself.
    flat = drawn.replace("\\n", " ")
    check("entries sharing a word past the word are drawn whole",
          ("wombat book.txt" in flat and "wombat cards.txt" in flat), True)
    check("and the line took the quoted head they share", line, "wombat\\ ")
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
    if not SHELLS["zsh"]:
        return
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
        # A paste does not open the list on its own. It arrives wrapped in the
        # bracketed-paste envelope — what a terminal sends when the user pastes —
        # and nobody has read a line that arrived at once, so three rows of
        # guesses under it is noise on top of noise. The learned line IS here
        # (the pasted fragment is its verbatim text), so the empty menu below is
        # the paste rule answering, not the scan finding nothing.
        s.send(PASTE_START + "pull --re" + PASTE_END)
        s.settle()
        check("a paste does not open the list on its own",
              s._dump(clear=False)[2], "MENU=[] N=[0] IDX=[0]")
        # …and one real keystroke re-arms the glimpse: the buffer changed by a
        # character, which is typing, which is what the list is for. The glimpse
        # answers for the line as it now stands, same as it would have before
        # the paste.
        s.write(BACKSPACE)
        s.settle()
        check("and removing a character brings it back",
              s._dump(clear=False)[2],
              "MENU=[\\n  git pull --rebase] N=[1] IDX=[1]")
        # A pasted token that is not the text of any line is not fuzzy-matched
        # either: the letters-in-order rule is the tail of the same scan, and
        # re-arming it does not invent an answer the scan does not have.
        # (36 and 40 characters: each also crosses the raw-arrival bound, so
        # both rules — the envelope and the length — answer the same way.)
        s.write(CTRL_U)
        s.send(PASTE_START + in_order + PASTE_END)
        s.settle()
        check("a pasted token that is not text is not fuzzy-matched either",
              s._dump(clear=False)[2], "MENU=[] N=[0] IDX=[0]")
        s.write(BACKSPACE)
        s.settle()
        check("and re-arming it does not invent an answer either",
              s._dump(clear=False)[2], "MENU=[] N=[0] IDX=[0]")
        # A line past `_TAI_LOOSE_MAX_LINE` is refused outright, before the scan
        # starts at all — and a keystroke after it does not change that.
        s.write(CTRL_U)
        s.send(PASTE_START + "curl " + "z" * 220 + PASTE_END)
        s.settle()
        check("a pasted line is refused", s._dump(clear=False)[2],
              "MENU=[] N=[0] IDX=[0]")
        s.write(BACKSPACE)
        s.settle()
        check("and stays refused once the character is removed",
              s._dump(clear=False)[2], "MENU=[] N=[0] IDX=[0]")
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
                   TAI_HISTORY_FILES="", TAI_DATA_DIR="/tmp/tai/tai_loose")
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


def test_unpaintable_rows_are_never_drawn_bash() -> None:
    """The control-byte rule's bash half — the candidate filter in bash.

    The zsh twin above rides the glimpse; bash delivers the same row through
    the ghost hint, accepted by rewriting the readline buffer on Ctrl-F. The
    dirty row is the only answer the fixture holds for `zqx`, so an unfiltered
    list would put raw escape bytes into the buffer, and the line after the
    accept would be the paste envelope instead of what was typed.
    """
    if not SHELLS["bash"]:
        return
    print("a control byte in a row is never drawn (bash)")
    text = BASH_INDEX.read_text()
    dirty = '\x1b[200~zqx run~'
    BASH_INDEX.write_text(
        text + f"_TAI_SCORE[{_zq(dirty)}]=9\n"
               f"_TAI_FIRST[{_zq('zqx')}]={_zq(dirty)}\n"
               f"_TAI_WORD[{_zq('zqx run')}]={_zq(dirty)}\n")
    try:
        s = Session("bash")
        s.send("zqx")
        s.write(CTRL_F)
        s.settle()
        # An empty hint accepts nothing: the line must still be what was typed,
        # never the envelope that arrived raw from an old store.
        check("no raw bytes are hinted in bash", s.line(), "zqx")
        check("and no noise from it", s.noise(), [])
        s.close()
    finally:
        write_index()


def test_stem_never_cuts_untyped_text() -> None:
    """A stem may only cut what the line already shows.

    The reported case: `cd tm` in a home directory where every learned
    destination sits under `tmp/`. The menu held `tmp/vllm`, `tmp/fun-game/`
    and the directory `tmp/` itself — every entry shared `tmp/`, the boundary
    cut handed that back as the stem, and the rows were drawn as `vllm`,
    `fun-game/`, under a line reading `cd tm`. Nothing on the line says `tmp/`,
    so the list read as completions of `tmvllm` — names nobody typed and
    nothing offers. The stem is a reminder of text that is already on screen;
    anything longer than the typed word is information taken away.

    Two halves are asserted: a prefix the user did *not* type is written into
    the line by the shared-head rule (`cd tm` advances to `cd tmp/`, because
    every candidate begins there) and the rows then carry only what it adds —
    the head has moved out of the rows and into the line, which is the stem
    rule's promise kept by a shorter road. And a prefix the user *did* type is
    still cut (`cd tmp/`), because that is the half the original stem rule
    exists for.
    """
    if not SHELLS["zsh"]:
        return
    print("zsh menu stem stays inside the typed word")
    tmp_dir = MENU_DIR / "tmp"
    shutil.rmtree(tmp_dir, ignore_errors=True)
    (tmp_dir / "vllm").mkdir(parents=True)
    (tmp_dir / "x").mkdir(parents=True)
    try:
        write_index(commands=COMMANDS + ["cd tmp/vllm", "cd tmp/x", "cd tmp/"])
        s = Session("zsh")
        s.run(f"cd {MENU_DIR}")

        s.send("cd tm")
        s.write(LIST)
        s.settle()
        line, drawn, size, idx = s.menu(clear=False)
        entries = menu_entries(drawn)
        # The head every candidate shares is now in the line — `tmp/`, which
        # the user had not typed — and the rows carry the tails alone.
        check("the untyped root moves into the line", line, "cd tmp/")
        check("the rows carry what it adds", "vllm" in entries, True)
        check("and nothing is drawn twice", "tmp/vllm" in entries, False)
        s.write("\x15")

        # The half the stem rule is for: the prefix the user typed is drawn
        # once, by the line, and the rows carry only what it adds.
        s.send("cd tmp/")
        s.write(LIST)
        s.settle()
        line, drawn, size, idx = s.menu(clear=False)
        entries = menu_entries(drawn)
        check("a typed stem is still cut from the rows", "vllm" in entries, True)
        check("and the line still says what was typed", line, "cd tmp/")
        s.write("\x15")
        check("no noise from the clamped stem", s.noise(), [])
        s.close()
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        write_index()


def test_loose_tiers() -> None:
    """The glimpse ranks what the history holds verbatim, then one edit away.

    The reported case: `forest` answered with the lines that mention it, and
    `forestt` — one keystroke of typo — answered with aria2c URLs, because the
    old gap matcher let f, o, r, e, s, t and t be *anywhere* in a line, in
    order. The tiers now read: verbatim first, one deletion away second, and
    the bounded-gap matcher only when both of those are silent.
    """
    if not SHELLS["zsh"]:
        return
    print("zsh loose glimpse tiers")
    junk = ("aria2c -x 15 https://files.example.com/"
            "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")
    try:
        write_index(commands=COMMANDS + ["cd tmp/fun-game/beauty-forest", junk])
        s = Session("zsh")

        # A typo of a word the history holds: the one-edit tier answers with
        # the forest line, and the scatter matcher is not allowed to argue.
        s.send("forestt")
        line, drawn, size, idx = s.menu(clear=False)
        check("a typo is answered by the line it is one letter from",
              "beauty-forest" in drawn, True)
        check("a typo is not answered by scattered letters",
              "aria2c" in drawn, False)
        s.write("\x15")

        # A word the history holds verbatim: the exact tier, and nothing
        # guessed underneath it.
        s.send("forest")
        line, drawn, size, idx = s.menu(clear=False)
        check("the verbatim word is the answer", "beauty-forest" in drawn, True)
        check("verbatim leaves no room for guesses", "aria2c" in drawn, False)
        s.write("\x15")

        # The gap matcher still reads across the words a person remembers:
        # g, s, t sit in `git status` a few characters apart.
        s.send("gst")
        line, drawn, size, idx = s.menu(clear=False)
        check("in-order letters still find git status",
              "git status" in drawn, True)
        s.write("\x15")
        check("no noise from the tiers", s.noise(), [])
        s.close()
    finally:
        write_index()


def test_history_browsing_opens_no_list() -> None:
    """Up through the history and Down again is history, not a menu.

    The reported case: a few Ups to read previous commands, a Down or two to
    come back, and somewhere in the middle the loose list opened — armed on a
    line nobody typed, its only row the very line already on the prompt. Down
    on a buffer that arrived from the history means the next history entry;
    the list is for a line the user is composing. The gate is a typed flag:
    set by the widgets that mean an edit, cleared by the arrows on their way
    to history, and required before Down (or a redraw) opens the loose list.
    """
    if not SHELLS["zsh"]:
        return
    print("zsh up-down history browsing opens no list")
    s = Session("zsh")
    s.run(f"cd {MENU_DIR}")
    # Three lines in the history, typed the way a user types them. The
    # harness's `run` wraps its command in a done-marker, and Up would recall
    # the wrapper rather than the command — so the lines enter the history as
    # keystrokes, newline and all.
    s.send("git status" + ENTER)
    s.settle()
    s.send(f"cd {MENU_DIR}" + ENTER)
    s.settle()
    # A builtin line, not `docker ps`: this line is EXECUTED as it is typed
    # (that is how it reaches the history), and the noise check below reads
    # everything the session printed. A command that may not exist on the
    # machine running the suite — docker is preinstalled on the GitHub CI
    # image but is not a fact of every machine — would print
    # "command not found" here and be noise the walk itself never caused.
    s.send("print tzz-walk-end" + ENTER)
    s.settle()
    s.write(UP)
    s.settle()
    s.write(UP)
    s.settle()
    s.write(UP)
    s.settle()
    line, drawn, size, idx = s.menu(clear=False)
    check("Up still recalls history", line, "git status")
    s.write(DOWN)
    s.settle()
    line, drawn, size, idx = s.menu(clear=False)
    check("Down over a history line does not open the list", size, 0)
    check("and the history moved instead", line, f"cd {MENU_DIR}")
    s.write(DOWN)
    s.settle()
    line, drawn, size, idx = s.menu(clear=False)
    check("and still no list", size, 0)
    check("until the walk leaves the oldest line", line, "print tzz-walk-end")
    s.write(DOWN)
    s.settle()
    line, drawn, size, idx = s.menu(clear=False)
    check("and Down still walks out of the history", line, "")
    # That Down still opens the list for a line the user *is* composing is
    # test_loose_menu's whole subject; this test is about the history half.
    check("no noise from the history walk", s.noise(), [])
    s.close()


def test_cd_answers_directories() -> None:
    """`cd` is answered by directories, and only by ones that exist here.

    The reported case: `cd ` listed `AGENTS.md` and the other files of the
    current directory, because the directory glob offered every entry and the
    file answer answers any path-shaped word. Enter on any of it answers
    "not a directory". The other half: a learned destination is alive where it
    was recorded, and from here bare `vllm` — two directories away from vllm —
    is the same error with a history lesson attached.
    """
    if not SHELLS["zsh"]:
        return
    print("zsh a cd menu of directories only")
    s = Session("zsh")
    s.run(f"cd {MENU_DIR}")
    s.send("cd ")
    s.write(LIST)
    s.settle()
    line, drawn, size, idx = s.menu(clear=False)
    entries = menu_entries(drawn)
    check("the menu opened", size > 0, True)
    check("no file is offered after cd",
          [e for e in entries if e in ("tzz_d", "tzz_a", "tzz_solo")], [])
    check("the learned destination is, once",
          sum(e in ("tzz_dir", "tzz_dir/") for e in entries), 1)
    check("the shell's own directories are, marked", "stemroot/" in entries, True)
    s.write("\x15")
    check("no noise from the cd menu", s.noise(), [])
    s.close()


def test_first_word_menu() -> None:
    """Tab on a one-word line offers the learned lines first, whole.

    The report: typing `g` hinted one learned line, Tab replaced it with a
    screen of installed command names, and the first suggestion was `godot .`
    where the dashboard panel ranked `go mod vendor` first. The menu's learned
    source looks the index up by the text *in front of* the word, and a first
    word has none — so the learned lines were never offered, and one head per
    key was the only learned candidate anywhere. Now the first-word menu opens
    with the learned whole lines, merged by score across their keys — the
    panel's own order, the `go` key's second line included — and the installed
    names keep their place below. Committing a learned row onto a one-word
    line writes the whole line, because for a first word the completion and
    the line it came from are one span.

    Both shells are asserted, because the two implementations of the rule are
    where the drift would start.

    The report that returned here: the same Tab also echoed one `d=N` line
    per matching key above the menu — `_tai_menu_first_lines` declared its
    peel counter with `local d` *inside* the key loop, and zsh's `local`,
    applied to a parameter that already exists, prints its value. Silent on
    the first key, one echo per later key. This file ran green through all
    of it, because noise() hears errors and the dump widget reads arrays,
    not the screen — so the press's own bytes are now the assertion, and
    the function is asked for its stdout directly. (bash is not wired for
    the twin: its `local` never prints without -p, which is why the bug
    was zsh-only.)
    """
    fixture = ["go mod vendor", "go mod download", "godot .", "got log",
               "goose web", "git status"]
    learned = ["go mod vendor", "go mod download", "godot .", "got log",
               "goose web"]
    try:
        write_index(fixture)

        if SHELLS["zsh"]:
            print("zsh first-word menu")
            s = Session("zsh")
            s.run(f"cd {MENU_DIR}")
            s.send("g")
            check("the ghost hints the engine's winner",
                  s.suggestion().split(" PD=")[0], "SUG=[o mod vendor]")
            # The dump above abandons the line, so the word is typed again.
            s.send("g")
            mark = len(s.seen)
            s.write(TAB)
            s.settle()
            stray = [ln for ln in (ANSI.sub("", raw).strip()
                                   for raw in s.raw()[mark:].replace("\r", "\n").splitlines())
                     if re.match(r"[A-Za-z_][A-Za-z_0-9]*=", ln)]
            check("the Tab press echoes no variable assignment", stray, [])
            line, drawn, size, idx = s.menu(clear=False)
            # Whole learned lines are the rows here, and menu_entries' space
            # split would tear them into words; the rows are asserted as the
            # text the user reads, in the order it is painted.
            at = [drawn.find(row) for row in learned]
            check("Tab opens with the learned lines, ranked like the panel",
                  all(a >= 0 for a in at) and at == sorted(at), True)
            check("the installed names still follow",
                  any(e not in learned for e in drawn.split("\\n") if e), True)
            check("the line is untouched while the menu stands", line, "g")
            check("no noise from the first-word menu", s.noise(), [])
            # The selection is armed on an opened menu; Enter takes the row
            # and writes the whole line, the panel's promise.
            s.write(ENTER)
            s.settle()
            check("Enter takes the whole line", s.line(), "go mod vendor")
            s.write("\x15")
            # The same net at the source, where the screen cannot blur it:
            # the function's stdout, captured, must be empty. Any future
            # typeset echo inside it lands in this substitution verbatim.
            s.run('tai_stray=$(_tai_menu_first_lines g); '
                  'print -r -- "STRAY[$tai_stray]"')
            check("_tai_menu_first_lines prints nothing",
                  "STRAY[]" in s.raw(), True)
            s.close()

        if SHELLS["bash"]:
            print("bash first-word completion")
            s = Session("bash", env_extra={"TAI_COMPLETE_ALL": "1"})
            s.run(f"cd {MENU_DIR}")

            def offered(line: str) -> list[str]:
                REPLY.unlink(missing_ok=True)
                s.run(f'COMP_LINE={line!r}; COMP_POINT={len(line)}; COMPREPLY=(); '
                      f'_tai_complete; printf "%s\\n" "${{#COMPREPLY[@]}}" '
                      f'"${{COMPREPLY[@]}}" > {REPLY}')
                s.wait_file(REPLY, f"the completion list for {line!r}")
                got = REPLY.read_text().splitlines()
                return got[1:] if got and got[0].isdigit() else got

            got = offered("g")
            check("bash offers the learned lines first, ranked",
                  got[:len(learned)], learned)
            check("and the installed names after", len(got) > len(learned), True)
            check("a complete word keeps its lines first",
                  offered("go")[:len(learned)], learned)
            check("no noise from the bash listing", s.noise(), [])
            s.close()
    finally:
        write_index()         # every other suite reads the default fixture


def test_glob_words_are_not_patterns() -> None:
    """A word the shell would refuse to compile is a quiet no-op, not an error.

    `_tai_globable` exists because a word can be a pattern the shell refuses —
    `foo[bar` is an unclosed class — and the report that found it was Tab
    printing `bad pattern: foo[bar*(N)` from inside the widget. The guard sits
    on the ghost's path and on the menu's, and this pins the menu's half, in a
    directory where nothing else could answer either: no menu, the key handed
    to zsh's own completion, and nothing on the terminal but the prompt.
    """
    if not SHELLS["zsh"]:
        return
    print("zsh menu refuses to compile a word")
    s = Session("zsh")
    s.run(f"cd {EMPTY_DIR}")
    for word in ("cat foo[bar", "cat foo(bar", "cat foo<bar"):
        s.send(word)
        s.write(TAB)
        s.settle()
        line, drawn, size, idx = s.menu(clear=False)
        check(f"{word!r} opens no menu", (size, idx), (0, 0))
        s.write("\x15")
    check("and no word became a pattern error", s.noise(), [])
    s.close()


def test_empty_line_menu() -> None:
    """Tab on an empty line answers with the directory, not with the history.

    The report: Tab on an empty prompt filled the first two rows of the menu —
    `godot .`, `cd`, then a band of one-key entries (`y`, `s`, `n`, `d`, `c`),
    paste fragments (`\\`, `)`, `"`, a run of markdown backticks), a word in
    Persian — before the directory listing drew underneath. How that was
    calculated: the menu's learned source looks the index up by the first word,
    and an empty word is every key in it. Each key lent its best lines, the
    merge ranked them by score, and the top sixteen drew. The score rewards
    what ran recently, and an accidental Enter on a stray character is recorded
    like any other command — so the entries an empty line drew were whichever
    ones the recency of a typo had put at the top, out of thousands.

    An empty line asks nothing, so no ranking of the whole history can be the
    answer. Two sources of this plugin had already decided exactly that, and
    the menu now agrees with them: the ghost text refuses an empty word
    (_tai_first_values), bash's completion refuses it (_tai_first_lines), and
    the menu's first-word source refuses it too. What Tab answers with is the
    one listing an empty line has that is not arbitrary — the current
    directory. Typing any first word brings the learned lines back ahead of
    the rest; test_first_word_menu owns that half.
    """
    fixture = ["go mod vendor", "go mod download", "godot .", "got log",
               "git status", "y", "n", ")", "ca"]
    learned = ["go mod vendor", "go mod download", "godot .", "got log",
               "git status"]
    junk = ["y", "n", ")", "ca"]
    try:
        write_index(fixture)

        if SHELLS["zsh"]:
            print("zsh empty-line menu")
            s = Session("zsh")
            s.run(f"cd {MENU_DIR}")
            s.write(TAB)
            s.settle()
            line, drawn, size, idx = s.menu(clear=False)
            check("the empty line's menu is the directory listing",
                  size > 0 and "tzz_dir/" in drawn, True)
            check("the line it stood on is still empty", line, "")
            check("no learned line stands in it",
                  [r for r in learned if r in drawn], [])
            check("no one-key entry from the history either",
                  [e for e in menu_entries(drawn) if e in junk], [])
            check("no noise from the empty-line menu", s.noise(), [])
            s.close()

        if SHELLS["bash"]:
            print("bash empty-line completion")
            s = Session("bash")
            s.run(f"cd {MENU_DIR}")
            REPLY.unlink(missing_ok=True)
            s.run('COMP_LINE=""; COMP_POINT=0; COMPREPLY=(); _tai_complete; '
                  f'printf "%s\\n" "${{#COMPREPLY[@]}}" '
                  f'"${{COMPREPLY[@]}}" > {REPLY}')
            s.wait_file(REPLY, "the empty-line completion list")
            got = REPLY.read_text().splitlines()
            got = got[1:] if got and got[0].isdigit() else got
            check("bash declines an empty line outright", got, [])
            s.close()
    finally:
        write_index()         # every other suite reads the default fixture


def test_junk_first_word_key() -> None:
    """A key that holds only the word itself must not shadow its siblings.

    The reported world: `openc` sat in the index — Enters on a half-typed
    line had recorded it — and the ghost for `openc` answered nothing at all,
    while `ope` and `op` still hinted `opencode`. Why: the exact-key hit in
    _tai_first_values returned the bare word and never reached the keys that
    begin with the same letters, and the bare word extends nothing. The hit
    now peels word-equal heads and falls through to the sibling scan — in
    both shells, the two implementations of the lookup being where the drift
    would start. The full word keeps its own behaviour: its bare name is
    peeled the same way and what follows it is the hint, which is what the
    hit always meant to answer.
    """
    fixture = ["openc", "opencode", "opencode web", "git status"]
    try:
        write_index(fixture)

        if SHELLS["zsh"]:
            print("zsh ghost past a junk first-word key")
            g = Ghosts()
            check("the ghost for the junk word comes from its sibling",
                  g.of("openc"), "ode")
            check("the full word still hints what follows it",
                  g.of("opencode"), " web")
            g.close()
            print("zsh menu past a junk first-word key")
            s = Session("zsh")
            s.send("openc")
            s.write(TAB)
            s.settle()
            line, drawn, size, idx = s.menu(clear=False)
            check("the menu lists the sibling's lines", "opencode" in drawn, True)
            check("the junk word itself is not an entry",
                  [e for e in menu_entries(drawn) if e == "openc"], [])
            check("the line is untouched while the menu stands", line, "openc")
            check("no noise from the menu", s.noise(), [])
            s.close()

        if SHELLS["bash"]:
            print("bash lookup past a junk first-word key")
            s = Session("bash")
            out = pathlib.Path("/tmp/tai/tai_lookup_out.txt")
            s.run(f'_tai_first_values openc; printf "%s|%s\\n" "$_TAI_VALUES" '
                  f'"$_TAI_VALUES_ONE" > {out}')
            s.wait_file(out, "the lookup answer for openc")
            vals, one = out.read_text().rstrip("\n").split("|", 1)
            check("the junk key falls through to its siblings",
                  vals.split(), ["openc", "opencode"])
            check("one head per key, so the ranking loop runs", one, "0")
            s.run(f'_tai_first_values opencode; printf "%s|%s\\n" "$_TAI_VALUES" '
                  f'"$_TAI_VALUES_ONE" > {out}')
            s.wait_file(out, "the lookup answer for opencode")
            vals, one = out.read_text().rstrip("\n").split("|", 1)
            check("the full word keeps its own extension",
                  vals.split("\n"), ["opencode web"])
            check("and answers from its own list", one, "1")
            # The completion list a Tab would offer, through the real entry
            # point: the learned lines first, the sibling's among them.
            REPLY.unlink(missing_ok=True)
            s.run('COMP_LINE="openc"; COMP_POINT=5; COMPREPLY=(); _tai_complete; '
                  f'printf "%s\\n" "${{#COMPREPLY[@]}}" "${{COMPREPLY[@]}}" > {REPLY}')
            s.wait_file(REPLY, "the completion list for openc")
            got = REPLY.read_text().splitlines()
            got = got[1:] if got and got[0].isdigit() else got
            check("Tab lists the sibling's lines", "opencode" in got, True)
            check("no noise from the bash lookups", s.noise(), [])
            s.close()
    finally:
        write_index()         # every other suite reads the default fixture


def main() -> int:
    setup()
    test_bash_menu()
    test_zsh_menu()
    test_zsh_menu_prefix()
    test_zsh_menu_stem()
    test_loose_menu()
    test_stem_never_cuts_untyped_text()
    test_loose_tiers()
    test_pasted_text_is_not_a_typo()
    test_unpaintable_rows_are_never_drawn()
    test_unpaintable_rows_are_never_drawn_bash()
    test_history_browsing_opens_no_list()
    test_cd_answers_directories()
    test_first_word_menu()
    test_junk_first_word_key()
    test_glob_words_are_not_patterns()
    test_empty_line_menu()
    check_fixture_intact("the run")
    print("\nOK — the Tab menu in bash and zsh, entry by entry."
          if not failures else f"\nFAILED ({len(failures)}): {', '.join(failures)}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
