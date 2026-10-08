"""Plugin lookup behaviour, driven through a real pty.

Unit-testing the shell plugins by calling their functions in a non-interactive
shell misses the parts that actually break: key bindings, readline's
READLINE_LINE/READLINE_POINT contract, PROMPT_COMMAND recording, and terminal
stderr. So drive a real interactive bash and zsh and assert on what the
terminal shows.

Three rules keep these tests honest and fast:

  * every run uses a scratch TAI_DB, so a test can never write into the
    developer's real history;
  * the default session does not record, so no background process can rebuild
    the index fixture these tests assert on;
  * every wait ends on something observable rather than on a stopwatch — see
    `plugin_pty`.

This file covers the lookups themselves: what a typed word completes to, what
wrapping a command does to it, and that each shell reads its own index. The
menu, the files-on-the-end-of-a-line, and the index lifecycle have their own
entry points:

    python3 tests/test_plugins.py
    python3 tests/test_plugins_menu.py
    python3 tests/test_plugins_draw.py
    python3 tests/test_plugins_files.py
    python3 tests/test_plugins_index.py
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from plugin_env import *  # noqa: F401,F403  (constants, fixture writers, setup)
from plugin_env import _index_rows  # underscore names are not re-exported by *
from plugin_screen import *  # noqa: F401,F403  (Screen and the styled-menu helpers)
from plugin_pty import *  # noqa: F401,F403  (Session, Ghosts, check, probe, failures)


def test_bash() -> None:
    print("bash keys")
    # Right arrow with the cursor mid-line must move, not accept.
    probe("bash", CTRL_A + RIGHT + "X", "git status", "gXit status")
    # Right arrow at end of line accepts the suggestion.
    probe("bash", RIGHT * 12 + "X", "git status", "git statusX")
    # Ctrl-F accepts.
    probe("bash", CTRL_E + CTRL_F + "X", "git status", "git statusX")
    # Nothing to suggest, cursor at end: stays put, no crash.
    probe("bash", RIGHT * 3 + "X", "zzzz", "zzzzX")
    # An empty prompt must not raise "bad array subscript". It stays empty here
    # because the fixture defines no _TAI_SEQ entries: with sequence data the
    # empty prompt would legitimately predict the next command, so this asserts
    # the no-crash invariant, not the absence of a prediction.
    probe("bash", RIGHT * 4, "", "")
    # Mid-line with no suggestion still moves exactly one character.
    probe("bash", CTRL_A + RIGHT * 2 + "X", "abcd", "abXcd")
    # Same arrow, application cursor-key mode: ESC O C, not ESC [ C. zsh had
    # this miss; bash now binds both byte strings, and both must move mid-line.
    probe("bash", RIGHT_APP, "docker", "docker ps")
    probe("bash", CTRL_A + RIGHT_APP * 2 + "X", "abcd", "abXcd")
    # One word of the suggestion, on the key the hand already makes for "next
    # word". `git ` hints `git pull --rebase`, so one word is `git pull` — the
    # same answer zsh gives, and the only way the two shells can be said to agree
    # about it.
    probe("bash", CTRL_A + CTRL_RIGHT, "git ", "git pull")
    # With no suggestion, bash's `bind -x` cannot move the cursor on its own —
    # readline ignores a bare READLINE_POINT from a bound function — so the key
    # rewrites nothing and Alt-F, which is readline's own `forward-word`, is the
    # one that moves. Asserted as what it is: the key does not invent an answer.
    probe("bash", CTRL_A + CTRL_RIGHT + "X", "abcd", "aXbcd")


def test_zsh() -> None:
    print("zsh ghost text and keys")
    # Read ZLE's own view of the suggestion from inside a widget. Asserting on
    # the bytes ZLE paints is unreliable, because how the post-display region
    # is drawn depends on the terminal.
    s = Session("zsh")
    s.send("docker")
    # "docker" is 6 characters and the hint is " ps", so the styled region is
    # 6..9 and covers the hint exactly.
    check("zsh styles the ghost via region_highlight", s.suggestion(),
          "SUG=[ ps] PD=[ ps] RH=[6 9 fg=8,bold]")
    check("zsh has no hook errors", s.noise(), [])
    s.close()

    probe("zsh", CTRL_E + CTRL_F + "X", "docker", "docker psX")
    probe("zsh", CTRL_E + CTRL_F, "docker", "docker ps")
    probe("zsh", RIGHT * 3, "", "")
    # Right arrow takes the whole hint at end of line, and steps one character
    # when the cursor is not at the end. The dump reads the buffer, not the
    # cursor, so a cursor move shows up as the text being left alone — which is
    # the assertion: with the arrow bound straight to accept, these two would
    # have pulled " ps" into the line.
    probe("zsh", RIGHT, "docker", "docker ps")
    probe("zsh", CTRL_A + RIGHT, "docker", "docker")
    probe("zsh", CTRL_A + RIGHT * 2, "docker", "docker")
    # The same arrow as a terminal actually sends it. ZLE switches the terminal
    # into application-cursor-key mode at line-init (`smkx`), and from then on the
    # arrow is ESC O C, not ESC [ C — a widget bound to the normal-mode form only
    # is never reached, and the key that takes the hint does nothing at the end of
    # the line. Sending the bytes a terminal sends is the only way to see this:
    # every other test in this file writes the normal-mode sequence and so passes
    # a plugin that no user could use.
    probe("zsh", RIGHT_APP, "docker", "docker ps")
    probe("zsh", CTRL_A + RIGHT_APP, "docker", "docker")
    # A fresh line must not carry a stale ghost.
    probe("zsh", CTRL_E, "docker", "docker")
    # Alt-F accepts a single word, matching the documented behaviour: the full
    # suggestion for "git " is "git pull --rebase", but only "git pull" lands.
    probe("zsh", CTRL_E + ALT_F, "git ", "git pull")
    # Nothing to accept leaves the line alone.
    probe("zsh", CTRL_E + ALT_F, "git status", "git status")


def test_prefix_lookups() -> None:
    """A line prefix has to resolve to something that *extends* it.

    Three failures hide behind one line of lookup code, and all three came from
    real use rather than from a test:

      * `ls -l` suggested nothing, because `_TAI_WORD["ls -l"]` is a key in its
        own right holding only `ls -l`. The key that also holds `ls -la` is
        `ls`, so the lookup has to start from the last *complete* word.
      * an installed command the history has never seen (`9router`) suggested
        nothing at all, and `--help` is the only honest thing to offer.
      * a name that is not installed must still suggest nothing, rather than
        inventing something to look capable.
    """
    if not SHELLS["zsh"]:
        return
    print("prefix lookups (zsh ghost text)")
    g = Ghosts()
    check("`ls -l` extends to `ls -la`", g.of("ls -l"), "a")
    check("`ls -` extends to `ls -la`", g.of("ls -"), "la")
    check("`ls -la` has nothing left to add", g.of("ls -la"), "")
    check(f"installed `{INSTALLED}` offers --help", g.of(INSTALLED), " --help")
    check(f"installed `{INSTALLED} ` offers --help", g.of(INSTALLED + " "), "--help")
    check("uninstalled name stays empty", g.of(NOT_INSTALLED), "")
    check("uninstalled name with space stays empty", g.of(NOT_INSTALLED + " "), "")
    check("partial unknown name stays empty", g.of(NOT_INSTALLED[:9]), "")
    # A line of nothing but spaces has no complete word to look up, and bash
    # treats an empty associative-array subscript as a hard error. This is the
    # input that would print "bad array subscript" on every single prompt.
    check("whitespace-only line is clean", g.of("   "), "")
    g.close()

    if not SHELLS["bash"]:
        return
    print("prefix lookups (bash accept)")
    probe("bash", CTRL_E + CTRL_F, "ls -l", "ls -la")
    probe("bash", CTRL_E + CTRL_F, INSTALLED, INSTALLED + " --help")
    probe("bash", CTRL_E + CTRL_F, INSTALLED + " ", INSTALLED + " --help")
    probe("bash", CTRL_E + CTRL_F + "X", NOT_INSTALLED + " ", NOT_INSTALLED + " X")
    probe("bash", CTRL_E + CTRL_F + "X", "   ", "   X")


def test_half_typed_command() -> None:
    """A command name is a word being written, so it has to answer unfinished.

    The reported case: `god`, and `godot .` never appearing. The index keys
    `_TAI_FIRST` by the *whole* command name, so `godot` is a key and `god` is not
    — and a half-typed command is the ordinary state of a line, not an edge case.
    `tai suggest god` answered `godot .` through the whole time, because the engine
    walks a prefix range over its sorted command names while the plugins asked for
    one exact key. Two implementations of one rule set, disagreeing, which is the
    one thing they may not do.

    So the lookup takes the exact name first — the fast path, and what every
    complete command uses — and on a miss the names that *begin* with it. What must
    not come with it: a word nothing begins with has to stay silent rather than
    reaching for the nearest name, and a glob character must not become a pattern,
    because `c*t` reaching `cat` is the index answering a question nobody asked.
    """
    if not SHELLS["zsh"]:
        return
    print("a command name that is still being typed (zsh ghost text)")
    s = Session("zsh")
    # An empty directory, so nothing can answer from the filesystem and every
    # answer below is the index's or nothing's.
    s.run(f"cd {EMPTY_DIR}")
    for prefix, want in (("doc", "ker ps"),        # the `god` case
                         ("gi", "t pull --rebase"),
                         ("ca", "t tzz_d"),
                         ("l", "s -la"),
                         ("cat", " tzz_d"),        # the exact name still wins
                         ("cdt", ""),              # nothing begins with it
                         (NOT_INSTALLED[:9], ""),  # and it stays a non-answer
                         ("c*t", "")):             # a glob is not a prefix
        s.send(prefix)
        check(f"zsh {prefix!r} hints", s.suggestion().split(" PD=")[0],
              f"SUG=[{want}]")
    # The arrow takes what the hint says, which is the point of it being a hint.
    s.send("doc")
    s.write(RIGHT)
    s.settle()
    check("and the arrow takes it", s.line(), "docker ps")
    s.close()

    if not SHELLS["bash"]:
        return
    print("a command name that is still being typed (bash completion)")
    # No trailing space: tai's own completion is registered with `-o nospace`, and
    # the shell's, which does add one, is only reached when tai has no answer —
    # which is the next case.
    probe("bash", TAB, "doc", "docker")
    probe("bash", CTRL_E + CTRL_F, "doc", "docker ps")
    probe("bash", CTRL_E + CTRL_F, "cdt", "cdt")


def test_fixture_matches_generator() -> None:
    """The fixture must be the index the product writes, not a plausible one.

    Every plugin test reads the index `plugin_env.py` builds. When the fixture
    is a *stand-in* rather than a copy, every one of those tests is measuring
    the stand-in, and the gap between the two is invisible exactly where it
    matters: the fixture wrote a single-word `_TAI_WORD` key, which
    `tai/index.py` does not, and wrapper transparency reads precisely that key —
    so `sudo git ` was green here and answered nothing on a real install.

    The assertion is deliberately not "the tests pass". It is that the two
    writers agree on which keys exist, which is the only property that makes the
    rest of the suite mean anything.
    """
    print("the fixture is the generator")
    from tai.index import WORD_KEY_MAX_DEPTH as DEPTH

    real_words: dict[str, list[str]] = {}
    for cmd in COMMANDS:
        parts = cmd.split()
        for i in range(2, min(len(parts), DEPTH) + 1):
            real_words.setdefault(" ".join(parts[:i]), []).append(cmd)

    rows = _index_rows()
    fixture_words = {r[1]: r[2].split("\n") for r in rows if r[0] == "WORD"}
    check("the fixture's word keys are the generator's", sorted(fixture_words),
          sorted(real_words))
    check("and so are their candidate lists", fixture_words, real_words)

    # The specific shape the wrapper rule depends on, asserted directly so the
    # failure names the cause rather than "the fixture drifted".
    for key in ("git", "git "):
        check(f"no {key!r} word key, exactly as the generator writes it",
              key in fixture_words, False)


def test_wrappers() -> None:
    """A wrapper runs the command after it, so it must not hide the history.

    The index is keyed on the whole line, so `sudo git ` found nothing at all on
    a history full of `git ...`. Treating a closed list of wrappers as
    transparent fixes that, and the suggestion stays a command the user really
    ran — one word apart.
    """
    if not SHELLS["zsh"]:
        return
    print("wrapper transparency (zsh ghost text)")
    g = Ghosts()
    check("`sudo git ` completes from `git ...`", g.of("sudo git "),
          "pull --rebase")
    check("`sudo git s` completes too", g.of("sudo git s"), "tatus")
    check("`sudo ls -l` still extends", g.of("sudo ls -l"), "a")
    check("`doas docker ` works", g.of("doas docker "), "ps")
    check("`time git ` works", g.of("time git "), "pull --rebase")
    # `env` and `xargs` change what follows them enough that re-attaching the
    # wrapper would be a lie, and a wrapper carrying assignments is not a
    # transparent prefix at all.
    check("`env git ` stays empty", g.of("env git "), "")
    check("`xargs git ` stays empty", g.of("xargs git "), "")
    check("`sudo FOO=1 git ` stays empty", g.of("sudo FOO=1 git "), "")
    check("an unknown wrapped command stays empty", g.of("sudo zzznope "), "")
    # With no `sudo` history there is nothing behind the wrapper to rank, and the
    # honest answer for a name that exists is its own --help. What must never
    # happen is an invented continuation.
    check("`sudo ` with no history offers --help", g.of("sudo "), "--help")
    g.close()

    if not SHELLS["bash"]:
        return
    print("wrapper transparency (bash accept)")
    probe("bash", CTRL_E + CTRL_F, "sudo git ", "sudo git pull --rebase")
    probe("bash", CTRL_E + CTRL_F, "sudo ls -l", "sudo ls -la")
    probe("bash", CTRL_E + CTRL_F + "X", "env git ", "env git X")


def test_each_shell_reads_its_own_index() -> None:
    """The bash plugin must source bash-index.bash, and zsh the zsh one.

    `TAI_INDEX` names the zsh file — one decision about where the indexes live,
    shared with tai/index.py and plugins/tai.zsh. The bash plugin used to read the
    same variable as if it named *its* file, so with TAI_INDEX set it sourced the
    zsh snapshot and `bash-index.bash` was written on every rebuild and read by
    nobody. Nothing caught it because the harness sets TAI_INDEX to the zsh path for
    both shells, so every bash assertion was reading the zsh fixture and passing.

    The two files are formatted differently — `typeset -gA` against `declare -gA`,
    and a pair-append against a subscript assignment — so reading the wrong one is
    observable rather than merely wrong on paper.
    """
    for shell, want in (("zsh", ZSH_INDEX), ("bash", BASH_INDEX)):
        # A live session, and the file it actually resolved. Read out of the
        # running shell rather than re-deriving it here, so a plugin that
        # resolved the wrong path cannot agree with this test by construction.
        s = Session(shell)
        try:
            scratch = s.buf.with_name("tai_index_name.txt")
            scratch.unlink(missing_ok=True)
            s.run(f"basename \"$_TAI_INDEX_FILE\" > {scratch}")
            check(f"{shell} reads its own index",
                  scratch.read_text().strip(), want.name)
            # And it holds the answers, not merely the name. Each shell delivers
            # its hint its own way — zsh as POSTDISPLAY, which is only the text
            # that extends the line, and bash by rewriting the readline buffer on
            # the accept key — so each is read the way that shell gives it.
            if shell == "zsh":
                s.send("cd ")
                check(f"{shell} answers from that file",
                      s.suggestion().split(" PD=")[0], "SUG=[tzz_dir]")
            else:
                s.send("cd ")
                s.write(CTRL_F)
                s.settle()
                check(f"{shell} answers from that file", s.line(), "cd tzz_dir")
        finally:
            s.close()
    # The two files really are different files, so the check above has teeth.
    check("the two fixtures are not the same file",
          ZSH_INDEX.read_text() != BASH_INDEX.read_text(), True)


def test_a_foreign_index_is_not_executed() -> None:
    """A file the plugin did not write is refused, not sourced.

    The index is sourced, so its contents are code, and in bash the damage from
    a file that is not one does not stop at that file: an unterminated `(`
    leaves the parser mid-construct, so the plugin's *next* line was read as part
    of it and run as a command name — the shell printed
    `bash: [[ -n /home/u/.bash_history && -z ]]: No such file or directory` on
    every start, from a file the user never touched.

    The check is the writer's own first line plus the shape of the first data
    line. It does not need to catch a torn file: `tai/index.py` writes a
    temporary file and `os.replace`s it, so a half-written index cannot exist.
    What it must catch is everything else — the wrong `TAI_INDEX`, a file that is
    not an index, a truncated download — and the shell has to stay usable when it
    does: no noise, and the installed-command fallback still answers.
    """
    good_zsh = ZSH_INDEX.read_text()
    good_bash = BASH_INDEX.read_text()
    for shell, path, good in (("zsh", ZSH_INDEX, good_zsh),
                              ("bash", BASH_INDEX, good_bash)):
        for label, text in (
            ("the wrong file entirely", "hello world\n_TAI_SCORE=()\n"),
            ("a header with something else under it",
             good.splitlines()[0] + "\nnot an index at all\n"),
            ("a file that is empty", ""),
        ):
            path.write_text(text)
            s = Session(shell)
            try:
                s.run("true")
                said = "".join(s.seen)
                check(f"{shell} refuses {label}", "tai refresh" in said, True)
                check(f"{shell} is not left printing errors after {label}",
                      s.noise(), [])
                # …and it still works: the installed-command fallback answers, which needs no
                # index at all. zsh delivers it as ghost text and bash by
                # rewriting the line on the accept key, so each is read its way.
                s.send("git")
                if shell == "zsh":
                    check(f"{shell} still answers after {label}",
                          s.suggestion().split(" PD=")[0], "SUG=[ --help]")
                else:
                    s.write(CTRL_F)
                    s.settle()
                    check(f"{shell} still answers after {label}",
                          s.line(), "git --help")
            finally:
                s.close()
        path.write_text(good)




def main() -> int:
    setup()
    # Before anything reads the fixture: every other test here is only measuring
    # something if the file it loads is the one the product writes.
    test_fixture_matches_generator()
    for shell, fn in (("bash", test_bash), ("zsh", test_zsh)):
        if SHELLS[shell]:
            fn()
    test_prefix_lookups()
    test_half_typed_command()
    test_wrappers()
    test_each_shell_reads_its_own_index()
    test_a_foreign_index_is_not_executed()
    check_fixture_intact("the run")
    print("\nOK — lookups, prefix completion, wrappers, and per-shell index files."
          if not failures else f"\nFAILED ({len(failures)}): {', '.join(failures)}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
