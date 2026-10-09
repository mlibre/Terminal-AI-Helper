"""The index lifecycle: a rebuild the running shell picks up, and recording.

The index is written by a process the shell does not wait for, so these are the
tests for the two moments where that shows up — a shell that was already open
when the index changed, and a command that has not been recorded yet.

    python3 tests/test_plugins_index.py
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from plugin_env import *  # noqa: F401,F403  (constants, fixture writers, setup)
from plugin_screen import *  # noqa: F401,F403  (Screen and the styled-menu helpers)
from plugin_pty import *  # noqa: F401,F403  (Session, Ghosts, check, probe, failures)


def test_live_reload() -> None:
    """A shell that is already open must pick up a rebuilt index.

    This is the difference between learning something and seeing it. tai reads a
    new tool's `--help` the first time you run it and rewrites the index file, but
    the shell already holds the old arrays in memory — so without a reload the
    subcommands you just paid for appear only in the *next* shell, which reads as
    "it did not learn anything". The check is `index -nt stamp` at every prompt:
    a builtin comparison, no process, and atomic replace means it sees the old
    file or the new one and never a partial one.
    """
    if not SHELLS["zsh"]:
        return
    print("live index reload (zsh)")
    base = list(COMMANDS)
    write_index(base)
    # A shell that is already running when the index changes underneath it.
    write_index(["docker ps -a"] + base)
    s = Session("zsh")
    s.send("docker")
    check("a new shell sees the new index", s.suggestion().split(" PD=")[0],
          "SUG=[ ps -a]")
    s.close()

    write_index(base)
    s = Session("zsh")
    s.send("docker")
    check("before the rebuild", s.suggestion().split(" PD=")[0], "SUG=[ ps]")
    write_index(["docker compose up -d"] + base,
                new_enough_than=pathlib.Path(str(ZSH_INDEX) + ".stamp"))
    s.send("docker")
    check("still the old index, no prompt yet", s.suggestion().split(" PD=")[0],
          "SUG=[ ps]")
    s.send("\n")         # run an empty line, get a fresh prompt
    s.send("docker")
    check("the same shell sees it after one prompt",
          s.suggestion().split(" PD=")[0], "SUG=[ compose up -d]")
    check("reload is clean", s.noise(), [])

    # The other half, and the one that was broken: a rebuild that *removes* a
    # command. Re-sourcing overwrote the value of a key the new index still had,
    # but a key the new index no longer mentions was simply left where it was — so
    # `tai purge --stale` dropped a suggestion from the store, the next prompt
    # re-read the file, and the stale entry stayed installed for the life of the
    # shell. A shell that can only learn and never forget is not reading the file,
    # it is accumulating it.
    #
    # `git status` would not catch it: `git` is still in the reduced index, so its
    # key is overwritten and the lookup lands on the surviving candidate. What has
    # to go is a command whose *own* word key disappears with it — `docker ps`,
    # which nothing else in the fixture shares a key with and which no glob on
    # this machine can answer.
    write_index([c for c in COMMANDS if not c.startswith("docker ")],
                new_enough_than=pathlib.Path(str(ZSH_INDEX) + ".stamp"))
    s.send("\n")
    s.send("docker p")
    check("and it forgets what the rebuild removed",
          s.suggestion().split(" PD=")[0], "SUG=[]")
    s.close()
    write_index(base)

    # The same rule for a cached answer. The loose glimpse for `gst` is cached,
    # because a redraw without a keystroke must not re-scan every learned line —
    # so the cache has to be keyed on the index as well as on the prefix. A cache
    # that forgets keeps offering `git status` for a typo long after `git status`
    # was purged from the store: a suggestion the store no longer holds and the
    # file on disk no longer mentions.
    s = Session("zsh")
    s.send("gst")
    check("a typo reaches the learned line", s._dump(clear=False)[2],
          "MENU=[\\n  git status] N=[1] IDX=[1]")
    write_index([c for c in COMMANDS if c != "git status"],
                new_enough_than=pathlib.Path(str(ZSH_INDEX) + ".stamp"))
    s.send("\n")             # a fresh prompt is what reads the new file
    s.send("gst")
    check("and a rebuild retires the cached answer",
          s._dump(clear=False)[2], "MENU=[] N=[0] IDX=[0]")
    s.close()
    write_index(base)


def test_live_reload_bash() -> None:
    """The live-reload rule in bash — "keep a case in both suites".

    The zsh half above is the one the rule was born from; this is the same
    question asked of the other implementation, because a rule that works in
    one shell and not the other is how a split starts. Bash delivers its hint
    by rewriting the readline buffer on the accept key, so each answer here is
    read by pressing Ctrl-F and reading the line: an empty hint accepts
    nothing, and a stale one accepts the stale line.
    """
    if not SHELLS["bash"]:
        return
    print("live index reload (bash)")
    base = list(COMMANDS)
    write_index(["docker ps -a"] + base)
    s = Session("bash")
    s.send("docker")
    s.write(CTRL_F)
    s.settle()
    check("a new shell sees the new index", s.line(), "docker ps -a")
    s.close()

    write_index(base)
    s = Session("bash")
    s.send("docker")
    s.write(CTRL_F)
    s.settle()
    check("before the rebuild", s.line(), "docker ps")
    write_index(["docker compose up -d"] + base,
                new_enough_than=pathlib.Path(str(BASH_INDEX) + ".stamp"))
    s.send("docker")
    s.write(CTRL_F)
    s.settle()
    check("still the old index, no prompt yet", s.line(), "docker ps")
    s.send("\n")         # run an empty line, get a fresh prompt
    s.send("docker")
    s.write(CTRL_F)
    s.settle()
    check("the same shell sees it after one prompt", s.line(),
          "docker compose up -d")
    check("reload is clean", s.noise(), [])

    # The forgetting half: a rebuild that removes a command must remove it
    # from an open shell too. `docker ps` is the command whose own word key
    # disappears with the rebuild — nothing else in the fixture shares it.
    write_index([c for c in COMMANDS if not c.startswith("docker ")],
                new_enough_than=pathlib.Path(str(BASH_INDEX) + ".stamp"))
    s.send("\n")
    s.send("docker p")
    s.write(CTRL_F)
    s.settle()
    check("and it forgets what the rebuild removed", s.line(), "docker p")
    s.close()
    write_index(base)


def test_recording() -> None:
    print("recording honours TAI_NO_AUTO_RECORD")
    typed = ["echo alpha-one", "echo beta-two"]
    for shell in PLUGINS:
        if not SHELLS[shell]:
            continue
        for disabled in (False, True):
            # A database of its own per iteration, named from RECORD_DB above. The
            # disabled case asserts the *absence* of a write and the writes are
            # detached, so a shared file made it a question about the previous
            # session: its record processes were still in flight when this
            # iteration deleted the file, and a straggler landed in the fresh one.
            # A path no other shell was ever given is what makes "wrote nothing"
            # mean what it says.
            db = RECORD_DB.with_name(f"{RECORD_DB.stem}-{shell}-"
                                     f"{'off' if disabled else 'on'}"
                                     f"{RECORD_DB.suffix}")
            # The spool, named just as singly: it is the shells' write side now,
            # and "wrote nothing" has to mean the spool never grew either.
            spool = RECORD_DB.with_name(f"{db.stem}-spool{db.suffix}")
            for suffix in ("", "-wal", "-shm"):
                pathlib.Path(str(db) + suffix).unlink(missing_ok=True)
            spool.unlink(missing_ok=True)
            # The only test that records, so it also gets a throwaway index path:
            # a stored command can trigger the 100-command maintenance rebuild in
            # a detached process, and that process writes TAI_INDEX.
            # TAI_SPOOL_MAX=1 asks for the flush after every command — the same
            # one-record-at-a-time cadence the tests can wait on; the product's
            # default batches eight.
            env = {"TAI_NO_AUTO_RECORD": "1" if disabled else "0",
                   "TAI_INDEX": str(RECORD_INDEX), "TAI_DB": str(db),
                   "TAI_SPOOL": str(spool),
                   "TAI_SPOOL_MAX": "1", "TAI_SPOOL_SECONDS": "1"}
            s = Session(shell, env)
            for cmd in typed:
                s.send(cmd + "\n")   # newline submits, so it really runs
            # Recording runs detached, so the rows land some time after the
            # prompt returns — and the detached process is a child of this
            # session's terminal. Waiting for the rows while the session is
            # still open is what makes the assertion about recording and not
            # about the race between a python startup and the pty going away:
            # close() tears down the master, and the kernel's SIGHUP to the
            # session's group is then free to kill a flush that has not
            # exec'd yet. A session that exits NORMALLY never sends that
            # SIGHUP, so the product never sees this race — only this
            # harness could lose it.
            if disabled:
                s.close()
                # Recording is off, so nothing will ever land: the absence
                # is checked after the session is gone, on the final state.
                check(f"{shell} writes nothing when disabled", rows(db), [])
                check(f"{shell} spools nothing when disabled",
                      spool.exists(), False)
            else:
                missing = await_rows(db, typed)
                s.close()
                check(f"{shell} records when enabled", missing, [])
                # Every record went through the spool, and every flush drains
                # it whole: a spool still holding frames at this point means a
                # record bypassed the gate or a flush lost one.
                check(f"{shell} drained its spool",
                      not spool.exists() or spool.read_text().strip() == "",
                      True)




def main() -> int:
    setup()
    test_live_reload()
    check_fixture_intact("the run")
    test_live_reload_bash()
    check_fixture_intact("the bash run")
    test_recording()
    check_fixture_intact("the recording test")
    print("\nOK — a rebuild picked up by an open shell, and recording."
          if not failures else f"\nFAILED ({len(failures)}): {', '.join(failures)}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
