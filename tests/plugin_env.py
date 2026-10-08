"""Paths, constants, and fixture writers shared by the plugin test suite.

The pty tests are split across several entry points — one per theme — and they
all build the same scratch world: a hand-written index fixture, a set of
directories, a fake editor, and the key names the harness sends. That lives
here so a fixture is written once and read the same way everywhere.
"""
import os
import pathlib
import re
import shutil
import sys
import time

# The repository root, which is this file's *parent* now that the tests live in
# tests/. Derived from __file__ rather than from the working directory so the
# suite finds the plugins and the CLI the same way whatever directory it is run
# from — a test that only works from the root is a test nobody can run from an
# editor or a CI step.
REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))     # `_index_rows` reads tai.paths to mark file args
PLUGINS = {"bash": REPO / "plugins" / "tai.bash", "zsh": REPO / "plugins" / "tai.zsh"}
SHELLS = {"bash": shutil.which("bash"), "zsh": shutil.which("zsh")}
DB = pathlib.Path("/tmp/opencode/tai_plugins_test.db")
EDITOR = pathlib.Path("/tmp/opencode/tai_dump_line.sh")
# Where a bash test reads COMPREPLY back from. Its own file, not the dump
# editor's, because sharing one file would make one assertion's output another
# one's synchronisation point.
REPLY = pathlib.Path("/tmp/opencode/tai_reply.txt")
INDEX_DIR = pathlib.Path("/tmp/opencode/tai_test_index")
ZSH_INDEX = INDEX_DIR / "zsh-index.zsh"
BASH_INDEX = INDEX_DIR / "bash-index.bash"
# Where the record subprocess is allowed to write an index. Recording triggers a
# background rebuild every 100 stored commands, and that rebuild writes whatever
# TAI_INDEX points at. Pointing it at the fixture would replace the hand-written
# index with one generated from the scratch database, and the failure then looks
# like a ranking bug in a completely unrelated test.
RECORD_INDEX = INDEX_DIR / "record-index.zsh"
# A database of its own for the recording test, one per iteration — see there.
RECORD_DB = DB.with_name("tai_plugins_record.db")
HISTFILE = pathlib.Path("/tmp/opencode/tai_test_history")
# A directory of names no history entry shares, so the menu it produces is
# exactly these names on any machine. The prefix is deliberately not a real one:
# the fixture index has no key for it, so nothing is learned about it and the
# paths and the commands are all the menu has to work with. Each script prints
# its own name, which is how a test tells *which* entry Enter took without reading
# the terminal's idea of the line — the line is gone by then, because Enter runs
# it. The names go on PATH as well, so the command list has something to offer
# and the two sources have to agree on it.
MENU_DIR = pathlib.Path("/tmp/opencode/tai_menu_cwd")
# A second directory on PATH holding names that are *not* runnable: a file with no
# execute bit, and a directory. zsh puts both in `$commands`, and `whence` calls
# neither a command, so a menu built from that hash without asking would offer
# entries that fail with "permission denied" the moment Enter ran them. They are
# also not in the current directory, so nothing else in the menu can account for
# them: if they show up, the menu believed the hash.
MENU_BIN = pathlib.Path("/tmp/opencode/tai_menu_bin")
MENU_UNRUNNABLE = ["tzz_nodir", "tzz_nope"]
# A runnable name in the second directory and *not* in the current one, with
# nothing in the history behind it: the whole `exe` case, where the only match
# anywhere is a script nobody has run. Its prefix has to be unambiguous, and
# unlike every other name here it must not start with `tzz`, or it would join
# the menus the other assertions are about.
MENU_FAR = "far_side_cmd"
# What the menu holds for "tzz", in the order it shows them: what tai learned
# (nothing), then the commands on PATH, then the files and directories here,
# each source sorted and de-duplicated across all three. `tzz_other` is the
# runnable name in the second directory, so "the runnable ones are offered" is a
# claim and not an accident of the filter dropping everything; `tzz_d` is a file
# the Enter test must never run; `tzz_dir/` is a directory, which is how a
# trailing slash gets covered.
MENU_ENTRIES = ["tzz_a", "tzz_b", "tzz_c", "tzz_other", "tzz_solo",
                "tzz_d", "tzz_dir/"]
# A tree to type *into*, for the stem a menu draws once instead of on every row.
# The word being typed is a path rather than a name, so every entry begins with it
# and the drawn rows are the question "what is in that directory". The names are
# deliberately uneven: the long one is long enough to set the width of every cell
# in the menu, which is the other half of what repeating the stem costs. It is
# named `aaa_…` so it sorts first under any locale collation — zsh's glob order
# ignores `_` in a UTF-8 locale, so a name that merely looked like it sorted first
# did not, and the selection under test was a different entry than intended.
STEM_ROOT = pathlib.Path("/tmp/opencode/tai_menu_cwd/stemroot")
STEM_NAMES = ["aaa_stem_a_very_long_directory_name/", "stem_alpha/", "stem_beta/",
              "stem_gamma/", "stem_zeta.iso"]
# Two files that share a whole *word*, for the boundary that is a space rather
# than a slash. A name with a space in it is the only way menu entries can: a
# learned entry is the next word of a line and stops at the first space, so the
# learned half of a menu cannot produce this case at all. The word is `wombat`
# because nothing on any machine shares a prefix with it — a word like `note` put
# `notepad` and `note` in the same menu, the entries then shared only `note`, and
# the rule under test (a stem has to end at a boundary) had nothing to cut.
STEM_FILES = ["wombat book.txt", "wombat cards.txt"]
# The colours a menu is drawn in: the selected cell, and a directory's name.
# Spelled out here because the tests read them off the screen rather than out of
# the plugin — a selection that is drawn but never looked at is a selection
# nobody notices going stale. `standout` is reverse video on any terminal that
# has it, which is what the tests expect to find.
SELECTED_ATTR = (0, 0, False, True)          # no colour of its own, reverse video
DIR_FG = 34                                  # zsh's `fg=blue`

# Zsh plugins that also write POSTDISPLAY, i.e. the same slot tai paints its
# ghost text into. zsh-autosuggestions is loaded by Manjaro's manjaro-zsh-prompt
# and by many other distro configs, so "tai is sourced last" is the normal case
# and has to keep working. See the load-order test below.
ZSH_AUTOSUGGESTIONS = pathlib.Path(
    "/usr/share/zsh/plugins/zsh-autosuggestions/zsh-autosuggestions.zsh")
SYNTAX_HIGHLIGHTING = pathlib.Path(
    "/usr/share/zsh/plugins/zsh-syntax-highlighting/zsh-syntax-highlighting.zsh")

# A directory whose whole job is to be the newest thing on disk, and a working
# directory with one older file in it. The reported case: `chmod +x ` on a
# history that holds only `chmod +x script` suggests `chmod +x script`, because
# the file that was just downloaded is not in the history yet. The download goes
# in its own HOME so the developer's ~/Downloads is never read and the assertion
# is about this directory's files rather than about whatever the machine has.
FILE_HOME = pathlib.Path("/tmp/opencode/tai_file_home")
FILE_DOWNLOADS = FILE_HOME / "Downloads"
FILE_WORK = pathlib.Path("/tmp/opencode/tai_file_work")
# The file the fake download writes, and the older file already in the working
# directory. Their mtimes are set explicitly with os.utime rather than by the
# order the test happens to create them in, so "newest first" is a fact about
# the fixture and not about how fast the machine is.
# A directory with nothing in it, so a word containing a glob character cannot be
# answered by a file that happens to be in the working directory. The assertion
# that a glob does not become a pattern match is only about the *index* if nothing
# else could answer, and "no file here matches" is a fact about the fixture rather
# than about whatever machine the suite runs on.
EMPTY_DIR = pathlib.Path("/tmp/opencode/tai_empty_cwd")
DOWNLOADED = "freebuff-0.0.154-linux-x86_64.AppImage"
WORK_OLD = "notes.txt"


def setup_files() -> None:
    """A download in its own ~/Downloads, and a directory to run from."""
    for path in (FILE_HOME, FILE_WORK):
        shutil.rmtree(path, ignore_errors=True)
    FILE_DOWNLOADS.mkdir(parents=True)
    FILE_WORK.mkdir(parents=True)
    fresh = FILE_DOWNLOADS / DOWNLOADED
    fresh.write_text("#!/bin/sh\necho 'RAN downloaded'\n")
    fresh.chmod(0o755)
    old = FILE_WORK / WORK_OLD
    old.write_text("notes\n")
    now = time.time()
    os.utime(fresh, (now, now))
    os.utime(old, (now - 86400, now - 86400))


# A hand-written index, deliberately not a built one. This file tests *shell*
# behaviour — key bindings, the READLINE_LINE/READLINE_POINT contract, ZLE's
# redraw, prompt-time recording — so the ranking engine is out of scope. What
# the assertions need is a tiny candidate set whose best answer per prefix is
# unambiguous. Building the fixture with `tai refresh` would make every
# expectation depend on ranking internals *and* on whatever commands the
# developer's own history happens to contain.
#
# Order matters: _tai_best takes the highest _TAI_SCORE, so the first entry is
# the winner for any prefix it matches. `ls -la` before `ls -l` mirrors the real
# generator, where the conventional completion outranks the bare flag.
# The `cd …` lines are for the menu. Every one of them offers its *next* word as a
# completion of the word being typed, so `cd ..` offers `..` and `cd -` offers
# `-` — neither of which starts with `t`, and offering them for `cd t` was a
# reported bug. `cd tzz_dir` is here for the other half: a learned name and the
# directory of the same name are one completion said twice, not a choice between
# a name and itself.
COMMANDS = ["git pull --rebase", "git status", "docker ps", "ls -la", "ls -l",
            "cd tzz_dir", "cd /tmp", "cd ..", "cd -", "chmod +x script",
            "cat tzz_d"]
# The generator's own limits, imported rather than restated. The fixture writes
# the file every plugin test reads, so a limit copied here is a second copy that
# drifts — and when it drifted, in the direction of *more* permissive than the
# product, every plugin test passed against an index nothing ever loads.
from tai.index import WORD_CANDIDATE_CAP, WORD_KEY_MAX_DEPTH  # noqa: E402
# A command that exists on any Linux and appears in no fixture entry, so the
# "installed but unknown" path can be exercised without inventing a binary.
INSTALLED = "true"
NOT_INSTALLED = "zzznotarealcommand"
# No _TAI_SEQ entries on purpose. An empty prompt predicts the command that
# followed the last one; with no sequence data that prediction is empty, which
# is what makes the empty-prompt assertions exact instead of dependent on the
# commands this harness happens to run.
SEQUENCES: dict[str, list[str]] = {}

RIGHT = "\x1b[C"      # right arrow, normal cursor-key mode
RIGHT_APP = "\x1bOC"  # right arrow, application cursor-key mode: what konsole and
                      # every xterm-compatible terminal send once ZLE has enabled
                      # it with terminfo's smkx at line-init
TAB = "\t"
ENTER = "\r"         # what a terminal sends for Enter; zsh reads it as ^M
CTRL_F = "\x06"
# The key that opens the list on purpose, whatever the hint says. Tab takes the
# hint when there is one, so a test that wants a menu has to ask for one.
LIST = "\x14"           # Ctrl-T: the list, asked for directly
CTRL_SPACE = "\x00"     # Ctrl-Space: the same widget, and the key to press
CTRL_RIGHT = "\x1b[1;5C"  # Ctrl-Right: one word of the hint
CTRL_A = "\x01"
CTRL_E = "\x05"
CTRL_K = "\x0b"
CTRL_U = "\x15"      # clear the line
CTRL_X_CTRL_E = "\x18\x05"
# Not ^X: that is a prefix in zsh's emacs keymap, so a widget bound to it waits
# out KEYTIMEOUT (0.4s) on every press. See _setup_lines.
DUMP_KEY_ZSH = "\x19"
CTRL_C = "\x03"
ALT_F = "\x1bf"

ANSI = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]")
NOISE = re.compile(r"bad array subscript|unbound variable|No such file|"
                   r"command not found|: line \d+:|function definition file not found|"
                   r"No such widget|nested function level")
failures: list[str] = []


def _zq(value: str) -> str:
    """POSIX single-quote a value for a generated index line."""
    if "'" in value:
        raise ValueError(f"index fixture cannot quote {value!r}")
    return "'" + value + "'"


def _index_rows() -> list[tuple[str, str, str]]:
    """Build the (key-kind, key, newline-joined values) fixture rows."""
    score = {cmd: len(COMMANDS) - i for i, cmd in enumerate(COMMANDS)}
    rows: list[tuple[str, str, str]] = []
    for cmd in COMMANDS:
        rows.append(("SCORE", cmd, str(score[cmd])))
    first: dict[str, list[str]] = {}
    words: dict[str, list[str]] = {}
    for cmd in COMMANDS:
        parts = cmd.split()
        first.setdefault(parts[0], []).append(cmd)
        # Mirror the real generator exactly, because this file *is* the index
        # every plugin test reads. It used to write a key per prefix starting at
        # one word, plus the same key with a trailing space, under a comment
        # claiming it mirrored `tai/index.py` — which writes neither. The index
        # has no single-word `_TAI_WORD` key (the command-name list lives once,
        # in `_TAI_FIRST`) and no trailing-space key (every lookup is
        # `${prefix% *}`, which never has one). Those two extra families
        # manufactured exactly the lookup wrapper transparency depends on, so
        # `sudo git ` passed here against a file shape the product never loads
        # and answered nothing against a real index.
        #
        # A test fixture that is a plausible *stand-in* is the trap; the only
        # safe stand-in is the generator. `test_fixture_matches_generator`
        # below asserts the two agree, so this cannot drift again.
        for i in range(2, min(len(parts), WORD_KEY_MAX_DEPTH) + 1):
            words.setdefault(" ".join(parts[:i]), []).append(cmd)
        # …and the same candidate cap, from the same constant. A key holding
        # forty commands is a file the product never writes, and the plugins
        # read the cap as the number of answers a key has.
    for key, values in sorted(first.items()):
        rows.append(("FIRST", key, "\n".join(values)))
    for key, values in sorted(words.items()):
        rows.append(("WORD", key, "\n".join(values[:WORD_CANDIDATE_CAP])))
    for key, values in sorted(SEQUENCES.items()):
        rows.append(("SEQ", key, "\n".join(values)))
    # The file-argument marks, from the same `takes_file` the generator uses, so
    # the fixture cannot claim a line ends in a file when the builder would not.
    from tai.paths import takes_file
    for cmd in COMMANDS:
        if takes_file(cmd):
            head = cmd.rsplit(" ", 1)[0]
            if head:
                rows.append(("FILE", head, "1"))
    return rows


def write_index(commands: list[str] | None = None,
                new_enough_than: pathlib.Path | None = None) -> None:
    """Write both plugin index snapshots from the fixed fixture.

    `new_enough_than` back-dates a stamp file instead of the test sleeping. The
    plugins compare mtimes with `-nt`, which bash and zsh both resolve in whole
    seconds, so "the file changed" has to be more than a second apart to be
    visible — and a test should pay that in a timestamp it controls, not in a
    second of wall clock.
    """
    global COMMANDS
    if commands is not None:
        COMMANDS = commands
    INDEX_DIR.mkdir(parents=True, exist_ok=True)
    rows = _index_rows()
    # The zsh snapshot is written in the same shape the real generator uses — a
    # pair appended per entry — so the tests load the format that ships rather
    # than a fixture-only one. A key arriving as an argument to a helper function
    # is safe but costs a shell function call per entry, which on a real history
    # was 572ms of shell startup.
    zsh = ["# tai zsh index; test fixture, not generated",
           "typeset -gA _TAI_SCORE _TAI_FIRST _TAI_WORD _TAI_SEQ _TAI_FILE"]
    for kind, key, value in rows:
        zsh.append(f"_TAI_{kind}+=({_zq(key)} {_zq(value)})")
    ZSH_INDEX.write_text("\n".join(zsh) + "\n", encoding="utf-8")

    bash = ["# tai bash index; test fixture, not generated",
            "declare -gA _TAI_SCORE _TAI_FIRST _TAI_WORD _TAI_SEQ _TAI_FILE"]
    for kind, key, value in rows:
        bash.append(f"_TAI_{kind}[{_zq(key)}]={_zq(value)}")
    BASH_INDEX.write_text("\n".join(bash) + "\n", encoding="utf-8")

    if new_enough_than is not None:
        # `-nt` compares whole seconds in both shells, so "the file moved on" has
        # to be more than a second apart to be visible. A test should pay that in a
        # timestamp it controls, not in a second of wall clock: the index keeps its
        # natural "just written" mtime and the stamp is moved into the past. The
        # plugin appends `.stamp` to the index path — with_suffix would replace
        # `.zsh` instead, which is a different file and a test that waits forever.
        old = time.time() - 30
        new_enough_than.parent.mkdir(parents=True, exist_ok=True)
        if not new_enough_than.exists():
            new_enough_than.write_text("")
        os.utime(new_enough_than, (old, old))


def setup() -> None:
    for suffix in ("", "-wal", "-shm"):
        pathlib.Path(str(DB) + suffix).unlink(missing_ok=True)
    write_index()
    shutil.rmtree(MENU_DIR, ignore_errors=True)
    MENU_DIR.mkdir(parents=True)
    for name in ("tzz_a", "tzz_b", "tzz_c", "tzz_solo"):
        script = MENU_DIR / name
        script.write_text(f"#!/bin/sh\necho 'RAN {name}'\n")
        script.chmod(0o755)
    (MENU_DIR / "tzz_d").write_text("")
    (MENU_DIR / "tzz_dir").mkdir()
    STEM_ROOT.mkdir()
    for name in STEM_NAMES:
        if name.endswith("/"):
            (STEM_ROOT / name).mkdir()
        else:
            (STEM_ROOT / name).write_text("")
    for name in STEM_FILES:
        (MENU_DIR / name).write_text("")
    shutil.rmtree(EMPTY_DIR, ignore_errors=True)
    EMPTY_DIR.mkdir(parents=True)
    shutil.rmtree(MENU_BIN, ignore_errors=True)
    MENU_BIN.mkdir(parents=True)
    other = MENU_BIN / "tzz_other"
    other.write_text("#!/bin/sh\necho 'RAN tzz_other'\n")
    other.chmod(0o755)
    far = MENU_BIN / MENU_FAR
    far.write_text(f"#!/bin/sh\necho 'RAN {MENU_FAR}'\n")
    far.chmod(0o755)
    (MENU_BIN / "tzz_nope").write_text("")
    (MENU_BIN / "tzz_nodir").mkdir()
    # A scratch HISTFILE is not optional. bash and zsh both append every line
    # they read to $HISTFILE, including lines the test typed and then abandoned,
    # and both write it out when the shell exits. Without this the harness
    # appends its own keystrokes to the developer's real history, which then
    # leaks into `tai refresh` and the built index — a test run that
    # silently rewrites the user's data and changes later results.
    HISTFILE.unlink(missing_ok=True)
    # C-x C-e hands the editing buffer to $EDITOR. Copying it out and exiting
    # non-zero is the only reliable way to observe what a key sequence left in
    # the line, and the non-zero exit stops the command from running.
    #
    # The destination is $TAI_TEST_DUMP, which every session sets to a file of
    # its own. A single shared path is a shared mailbox: one shell's leftover
    # write — from a killed run, or from a session the harness has already
    # closed — is then the next session's answer, and the failure reads as
    # `sudo git` in a buffer that only ever held `tzz_a`, or a Tab that
    # mysteriously chose `--help`. Both were reported; neither was the plugin.
    EDITOR.write_text('#!/bin/sh\ncp "$1" "$TAI_TEST_DUMP" 2>/dev/null\nexit 1\n')
    EDITOR.chmod(0o755)


