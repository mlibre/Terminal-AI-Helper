"""The case a command was recorded with is not a spelling the user remembers.

`ls down` must reach `ls Downloads` — as a ghost hint, as an accepted line, as
a menu row, and as a file answer from the disk itself. Every half that used to
compare bytes is pinned here end to end, through a real bash and zsh: the
plugin lookups, the ghost's paint-and-strip gate, the menu's word filter, the
file answer's snapshot filter and its fallback glob, and the systemctl unit
match (`Net` reaching NetworkManager.service). The ghost's own length cap is
pinned here too, because it guards the same redraw.

    python3 tests/test_plugins_case.py
"""
import pathlib
import re
import shutil
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import plugin_env  # noqa: E402  (its COMMANDS list is snapshotted for the restore)
from plugin_env import *  # noqa: F401,F403  (constants, fixture writers, setup)
from plugin_pty import *  # noqa: F401,F403  (Session, check, probe, failures)

DEFAULT_COMMANDS = list(plugin_env.COMMANDS)

CASE_DIR = pathlib.Path("/tmp/tai/tai_case_dir")
CASE_CD_DIR = pathlib.Path("/tmp/tai/tai_case_cd")
CASE_UNITS_BIN = pathlib.Path("/tmp/tai/tai_case_units_bin")
CASE_UNITS_CACHE = pathlib.Path("/tmp/tai/tai_case_units_cache")


def setup_case() -> None:
    CASE_DIR.mkdir(parents=True, exist_ok=True)
    (CASE_DIR / "Downloads").write_text("x\n")


def case_units_env() -> dict:
    shutil.rmtree(CASE_UNITS_BIN, ignore_errors=True)
    shutil.rmtree(CASE_UNITS_CACHE, ignore_errors=True)
    CASE_UNITS_BIN.mkdir(parents=True)
    CASE_UNITS_CACHE.mkdir(parents=True)
    ctl = CASE_UNITS_BIN / "systemctl"
    # A mixed-case unit name is the whole point: `Net` has to reach it.
    ctl.write_text("#!/bin/sh\nprintf '%s enabled\\n' NetworkManager.service sshd.service\n")
    ctl.chmod(0o755)
    return {"PATH": f"{CASE_UNITS_BIN}:{pathlib.Path(__file__).parent / 'bin'}:"
                    f"{pathlib.Path(__file__).parent / 'bin' / 'zsh-functions'}:"
                    f"{__import__('os').environ['PATH']}",
            "XDG_CACHE_HOME": str(CASE_UNITS_CACHE)}


def sug_of(dump: str) -> str:
    """The stored suggestion (the whole answer, taken by the accept keys)."""
    return dump.split(" PD=")[0]


def sug(s) -> str:
    """The stored suggestion, off one dump."""
    return sug_of(s.suggestion())


def pd_of(dump: str) -> str:
    """What is actually PAINTED: the dump's PD=[${(V)POSTDISPLAY}] field.

    SUG= is _TAI_SUGGESTION — the whole answer, held back for the key that
    takes it — while PD= is the ellipsised text on screen. The cap is a rule
    about the paint, so the paint is what this reads.
    """
    m = re.search(r" PD=\[(.*)\] RH=", dump)
    got = m.group(1) if m else ""
    if len(got) >= 2 and got[0] == "'" and got[-1] == "'":
        got = got[1:-1]
    return got


def test_zsh_ghost_case() -> None:
    if not SHELLS["zsh"]:
        return
    print("zsh ghost folds case")
    write_index(["ls Downloads", "git status"])
    s = Session("zsh")
    s.send("ls down")
    check("zsh ghost folds case", sug(s), "SUG=[loads]")
    s.send("ls D")
    check("and the exact case still answers", sug(s), "SUG=[ownloads]")
    s.close()
    check("zsh ghost clean", s.noise(), [])


def test_bash_accept_case() -> None:
    if not SHELLS["bash"]:
        return
    print("bash accept folds case")
    write_index(["ls Downloads", "git status"])
    probe("bash", RIGHT, "ls down", "ls Downloads")
    probe("bash", CTRL_F, "ls down", "ls Downloads")


def test_zsh_menu_case() -> None:
    if not SHELLS["zsh"]:
        return
    print("zsh menu folds case")
    write_index(["cd Downloads", "cd Desktop"])
    # A `cd` destination is judged for liveness from where the user stands —
    # the rule that keeps a learned directory two directories away from
    # answering — so the destinations have to exist from here.
    shutil.rmtree(CASE_CD_DIR, ignore_errors=True)
    CASE_CD_DIR.mkdir(parents=True)
    (CASE_CD_DIR / "Downloads").mkdir()
    (CASE_CD_DIR / "Desktop").mkdir()
    s = Session("zsh")
    s.run(f"cd {CASE_CD_DIR}")
    s.send("cd D")
    s.write(TAB)
    s.settle()
    line, rows, n, _ = s.menu()
    check("zsh menu line untouched", line, "cd D")
    check("zsh menu folds the word's case",
          n >= 1 and "Downloads" in rows and "Desktop" in rows, True)
    s.close()
    check("zsh menu clean", s.noise(), [])


def test_file_case() -> None:
    if not SHELLS["zsh"] and not SHELLS["bash"]:
        return
    print("file answers fold case")
    setup_case()
    # A FILE named Downloads: the fresh-file answer reads files (`-f`), and the
    # learned-argument gate below needs the name to exist too.
    # Learned case: the history says `cat Downloads`, the user typed `down`.
    # The learned argument that still exists keeps the hint, case folded.
    write_index(["cat Downloads"])
    s = Session("zsh")
    s.run(f"cd {CASE_DIR}")
    s.send("cat down")
    check("zsh learned file folds case", sug(s), "SUG=[loads]")
    s.close()
    check("zsh learned-file clean", s.noise(), [])
    # Disk case: nothing learned that extends `down`, the file is simply there
    # — and `cat` is a known file-argument line, so tai answers, not readline.
    write_index(["cat notes.txt"])
    s = Session("zsh")
    s.run(f"cd {CASE_DIR}")
    s.send("cat down")
    check("zsh disk answer folds case", sug(s), "SUG=[loads]")
    s.close()
    check("zsh disk-file clean", s.noise(), [])
    if SHELLS["bash"]:
        # TAI_COMPLETE_ALL is what puts tai's own completion behind Tab for
        # every line; without it readline's own (case-sensitive) completion
        # answers instead — which is exactly the behaviour this file exists to
        # fold away.
        s = Session("bash", env_extra={"TAI_COMPLETE_ALL": "1"})
        s.run(f"cd {CASE_DIR}")
        s.send("cat down")
        s.write(TAB)
        s.settle()
        check("bash disk answer folds case", s.line(), "cat Downloads")
        s.close()
        check("bash disk-file clean", s.noise(), [])


def test_units_case() -> None:
    if not SHELLS["zsh"] and not SHELLS["bash"]:
        return
    print("systemctl units fold case")
    write_index(["git status"])
    env = case_units_env()
    if SHELLS["zsh"]:
        s = Session("zsh", env_extra=env)
        # The ghost must not fork: the cache is preloaded the way the prompt
        # hook preloads it in real use, minus the timing race.
        s.run("_tai_units_load 0 >/dev/null 2>&1")
        s.send("systemctl restart Net")
        check("zsh unit ghost folds case", sug(s), "SUG=[workManager.service]")
        s.close()
        check("zsh units clean", s.noise(), [])
    if SHELLS["bash"]:
        s = Session("bash", env_extra=env)
        s.run("_tai_units_load 0 >/dev/null 2>&1")
        # Read COMPREPLY back the bash way — a completion press in the line
        # editor would list, and a listing is not what this check is about.
        s.run("COMP_LINE='systemctl restart Net'; COMP_POINT=${#COMP_LINE}; "
              "_tai_complete; printf 'UNITS=[%s]\\n' \"${COMPREPLY[*]}\"")
        raw = s.raw().replace("\r", "")
        got = "\n".join(l for l in raw.splitlines() if "UNITS=[" in l)
        check("bash unit completion folds case", "NetworkManager.service" in got, True)
        s.close()
        check("bash units clean", s.noise(), [])


def test_ghost_cap() -> None:
    """A long hint paints 200 characters and an ellipsis — and only paints.

    The store refuses lines over MAX_CMD_LEN now; this line is under it, the
    way a person's worst command is, and the cap is what keeps even that from
    wrapping the prompt. Painted short, taken whole: the painted field holds
    the ellipsised glance, and the stored suggestion behind it stays whole,
    because the key that takes the hint appends all of it.
    """
    if not SHELLS["zsh"]:
        return
    print("zsh ghost cap")
    write_index(["ls " + "x" * 900])
    s = Session("zsh")
    s.send("ls ")
    dump = s.suggestion()          # one dump: a second one reads a cleared line
    check("zsh ghost paints short", pd_of(dump), "x" * 200 + "…")
    check("and is still taken whole", sug_of(dump), "SUG=[" + "x" * 900 + "]")
    s.close()
    check("zsh cap clean", s.noise(), [])


def main() -> int:
    setup()
    setup_case()
    test_zsh_ghost_case()
    test_bash_accept_case()
    test_zsh_menu_case()
    test_file_case()
    test_units_case()
    test_ghost_cap()
    write_index(DEFAULT_COMMANDS)   # the other suites meet the default fixture
    print("\nOK — case-insensitive answers, ghost cap, end to end."
          if not failures else f"\nFAILED ({len(failures)}): {', '.join(failures)}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
