"""Ctrl-R is the shell's search, and for its every keystroke tai's no-op.

The reported hang: the user searches history with ^R and presses ^R again to
step to the older match, and on each press the shell visibly stalls — tai
"starting to work", they guessed, maybe for the menu. The guess is the
mechanism: tai's ghost rides the `line-pre-redraw` hook, and ZLE fires that
hook for the search's redraws too — measured on the bundled zsh, every step
of a search ran the hook with the matched line in the buffer, and each one
paid for a full `_tai_query_do`: the index scan, the file liveness stats,
the PATH probes, the loose glimpse, all for a line nobody is editing. The
loose glimpse could even open its rows over the `bck-i-search` prompt,
because `_TAI_TYPED` was still set from whatever typing preceded the search
and a matched history line is a fine-looking prefix to glance at.

The guard in the plugin stands down while the search owns the line, and the
state it reads is the isearch hook pair: `zle-isearch-update` fires when the
search starts and at every step, `zle-isearch-exit` when it ends, and the
flag they bracket is what the redraw hook checks (ZLE_STATE, whose isearch
token this zsh does not set, is the second answer). This test instruments
the same two points the guard does: every `_tai_update` call is logged with
the flag it saw, and every `_tai_query_do` — a miss, real work — is logged
with the buffer it answered for.

That log is also how a query is *attributed* to the search. The seed history
holds `lsof -i`, and nothing in this test ever types it: the only way that
line can stand in the buffer is as a search match. If tai's query log shows
it, tai went to work inside the search — the report, reproduced. The found
line itself (`ls /tmp`, left in the buffer by the arrow that exits the
search) is the opposite case: editing it is ordinary editing, and its first
draw after the search is tai working again, once, at a prompt.

Every read of the log happens after the session is closed, because the log
is written from inside the very hooks the keystrokes drive.

    python3 tests/test_plugins_isearch.py
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from plugin_env import *  # noqa: F401,F403  (constants, fixture writers, setup)
from plugin_screen import *  # noqa: F401,F403  (Screen and the styled-menu helpers)
from plugin_pty import *  # noqa: F401,F403  (Session, check, probe, failures)

LOG = pathlib.Path("/tmp/tai/tai_isearch_log.txt")
ISEARCH = "\x12"      # ^R: zsh's history-incremental-search-backward, and again
RIGHT = "\x1b[C"      # exits the search, keeping the line it found
# The history the search looks through. `l` matches `lsof -i` and `s` narrows
# to `ls /tmp`; the further ^R presses re-search on the same line. `lsof -i`
# is the discriminator: typed nowhere in this test, it can only reach the
# buffer as a match found by the search.
HISTORY_SEED = ["ls -la", "ls -l", "ls /tmp", "lsof -i",
                "docker ps", "git status", "cd tzz_dir"]

# The instruments, installed where the plugin's own hooks are: a log line per
# redraw hook call, carrying the isearch flag the call ran under, and a log
# line per query miss, carrying the buffer it worked on. The flag is the
# plugin's own `_TAI_ISEARCH` — the exact value the guard reads.
SETUP = (
    "functions[_tai_update_orig]=${functions[_tai_update]}; "
    f"_tai_update() {{ print -r -- \"U ${{_TAI_ISEARCH:-0}}\" >> {LOG}; "
    "_tai_update_orig \"$@\" }; "
    "functions[_tai_qdo_orig]=${functions[_tai_query_do]}; "
    f"_tai_query_do() {{ print -r -- \"Q $BUFFER\" >> {LOG}; "
    "_tai_qdo_orig \"$@\" }; "
    "fc -R $HISTFILE; print seeded"
)


def test_isearch_costs_nothing() -> None:
    if not SHELLS["zsh"]:
        return
    print("the ^R search costs tai nothing (zsh)")
    HISTFILE.write_text("\n".join(HISTORY_SEED) + "\n")
    s = Session("zsh")
    s.run(SETUP)
    s.run(f": > {LOG}")     # the setup's own typing has queried; start clean
    # The line the user had typed when ^R arrived — left in the buffer, ghost
    # drawn on it, exactly the state the report starts from.
    s.send("ls -l")
    mark = len(s.seen)
    # The search itself: ^R, two pattern characters, ^R twice to step through
    # the matches — the presses the report hangs on — then the right arrow,
    # which ends the search and leaves the found line on the prompt to edit.
    # The wait after the arrow is one quiet window wider than usual, because
    # the arrow's first byte is the byte meta sequences start with, and a
    # shell that waits to hear more of the chord must be waited out too.
    s.write(ISEARCH)
    s.settle()
    s.write("l")
    s.settle()
    s.write("s")
    s.settle()
    s.write(ISEARCH)
    s.settle()
    s.write(ISEARCH)
    s.settle()
    s.write(RIGHT)
    s.settle(0.5)
    # One deleted character on the found line — the user's own words for when
    # tai *should* work again.
    s.write(BACKSPACE)
    s.settle()
    found = s.line()
    s.close()
    log = LOG.read_text().splitlines()
    hook_flags = [line[2:] for line in log if line.startswith("U ")]
    queried = [line[2:] for line in log if line.startswith("Q ")]
    check("the search ran (zsh's own prompt came up)",
          "bck-i-search" in s.raw()[mark:], True)
    check("typing reached tai (the instruments are alive)",
          "ls -l" in queried, True)
    check("the search's steps reached tai's hook", "1" in hook_flags, True)
    check("tai answered none of the search's found lines",
          "lsof -i" in queried, False)
    check("the found line's first draw asked tai once",
          queried.count("ls /tmp"), 1)
    check("the found line was still there to edit", "/tmp" in found, True)
    check("the ^R session clean", s.noise(), [])


def main() -> int:
    setup()
    test_isearch_costs_nothing()
    check_fixture_intact("the ^R test")
    print("\nOK — the search is the shell's, and editing the found line is tai's."
          if not failures else f"\nFAILED ({len(failures)}): {', '.join(failures)}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
