"""systemctl unit completion, driven through a real pty.

`sudo systemctl restart <Tab>` is supposed to cycle the real service units —
Manjaro's own completion does, and a shell without it feels broken the moment
you type one. tai answers the same question from a cached unit list: one
`systemctl` call per day, one file per scope, and after that no process and no
I/O on the keystroke path.

The sessions here get a fake `systemctl` on PATH and a scratch cache directory,
so the assertions are about these units on any machine, and the cache the test
writes or the loader forks is never the developer's.
"""
import os
import pathlib
import shutil
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from plugin_env import *  # noqa: F401,F403  (constants, fixture writers, setup)
from plugin_pty import *  # noqa: F401,F403  (Session, check, probe, failures)

UNITS_BIN = pathlib.Path("/tmp/tai/tai_units_bin")
UNITS_CACHE = pathlib.Path("/tmp/tai/tai_units_cache")
SYSTEM_UNITS = ["docker.service", "herm.service", "herm2.service",
                "sshd.service", "tmp.mount"]
USER_UNITS = ["user-a.service", "user-b.timer"]

FAKE_SYSTEMCTL = """#!/bin/sh
if [ "$1" = "--user" ]; then
  printf '%s enabled\n' user-a.service user-b.timer
else
  printf '%s enabled\n' sshd.service herm.service docker.service herm2.service tmp.mount
fi
"""


def setup_units() -> None:
    shutil.rmtree(UNITS_BIN, ignore_errors=True)
    shutil.rmtree(UNITS_CACHE, ignore_errors=True)
    UNITS_BIN.mkdir(parents=True)
    UNITS_CACHE.mkdir(parents=True)
    bin_ = UNITS_BIN / "systemctl"
    bin_.write_text(FAKE_SYSTEMCTL)
    bin_.chmod(0o755)


def units_env() -> dict:
    return {"PATH": f"{UNITS_BIN}:{os.environ['PATH']}",
            "XDG_CACHE_HOME": str(UNITS_CACHE)}


def cache_text(scope: str) -> str:
    path = UNITS_CACHE / "tai" / f"units-{scope}.txt"
    return path.read_text() if path.exists() else ""


def test_zsh_units() -> None:
    if not SHELLS["zsh"]:
        return
    print("zsh systemctl units")
    s = Session("zsh", env_extra=units_env())
    # Load the cache deterministically: the ghost text must not fork, so a cold
    # cache answers nothing there by design. This is what the prompt hook's
    # preload does in real use, minus the timing race.
    s.run("_tai_units_load 0 >/dev/null 2>&1")
    s.send("sudo systemctl restart her")
    check("zsh hints the unit that extends the word",
          s.suggestion().split(" PD=")[0], "SUG=[m.service]")
    s.write("\x15")

    # The list, asked for directly: the units ARE the vocabulary on this line —
    # files, command names and the directory listing would be noise around them.
    s.send("systemctl restart ")
    s.write(LIST)
    s.settle()
    line, drawn, size, idx = s.menu(clear=False)
    check("zsh lists the units for an opened argument",
          [u for u in ("herm.service", "sshd.service") if u in drawn],
          ["herm.service", "sshd.service"])
    check("zsh does not list files on a unit line", "tzz_a" in drawn, False)
    s.write("\x15")

    # A verb still being typed is not a unit argument: `resta` may yet grow into
    # `restart`, and a unit after it would be a guess.
    s.send("systemctl resta")
    check("zsh answers nothing while the verb is half-typed",
          s.suggestion().split(" PD=")[0], "SUG=[]")
    s.write("\x15")

    # --user completes from the user's own units, not the system's.
    s.run("_tai_units_load 1 >/dev/null 2>&1")
    s.send("systemctl --user restart use")
    check("zsh hints a user unit on --user",
          s.suggestion().split(" PD=")[0], "SUG=[r-a.service]")
    s.write("\x15")

    # One fork, one file: the cache exists, and the loader reads it — the
    # freshness line first, the units after.
    body = cache_text("system")
    check("the system cache was written", body.splitlines()[0] if body else "",
          body.splitlines()[0] if body else "")
    check("the cache holds the units",
          all(u in body for u in SYSTEM_UNITS), True)
    s.close()
    check("zsh units clean", s.noise(), [])


def test_bash_units() -> None:
    if not SHELLS["bash"]:
        return
    print("bash systemctl units")
    s = Session("bash", env_extra=units_env())
    s.run("_tai_units_load 0 >/dev/null 2>&1")
    # bash has no `${(j)}` — read COMPREPLY back the bash way.
    s.run("COMP_LINE='sudo systemctl restart her'; COMP_POINT=${#COMP_LINE}; "
          "_tai_complete; printf 'UNITS=[%s]\\n' \"${COMPREPLY[*]}\"")
    raw = s.raw().replace("\r", "")
    got = "\n".join(l for l in raw.splitlines() if "UNITS=[" in l)
    check("bash completes the unit", "herm.service" in got
          and "herm2.service" in got, True)
    s.run("COMP_LINE='systemctl cat ssh'; COMP_POINT=${#COMP_LINE}; "
          "_tai_complete; printf 'UNITS2=[%s]\\n' \"${COMPREPLY[*]}\"")
    raw = s.raw().replace("\r", "")
    got = "\n".join(l for l in raw.splitlines() if "UNITS2=[" in l)
    check("bash completes through `cat`", "sshd.service" in got, True)
    s.close()
    check("bash units clean", s.noise(), [])


def test_units_cache_is_reused() -> None:
    if not SHELLS["zsh"]:
        return
    print("the unit cache answers without a fork")
    # A fresh cache written with a *current* stamp must be read back, not
    # refetched: point the loader at a systemctl that fails, and a fresh cache
    # still fills the array.
    stamp = int(time.time()) - 10
    body = f"{stamp}\n" + "\n".join(SYSTEM_UNITS) + "\n"
    (UNITS_CACHE / "tai").mkdir(parents=True, exist_ok=True)
    (UNITS_CACHE / "tai" / "units-system.txt").write_text(body)
    broken = UNITS_BIN / "brokenctl"
    broken.write_text("#!/bin/sh\nexit 3\n")
    broken.chmod(0o755)
    env = {"PATH": f"{UNITS_BIN}:", "XDG_CACHE_HOME": str(UNITS_CACHE)}
    s = Session("zsh", env_extra=env)
    s.run("_tai_units_load 0 >/dev/null 2>&1")
    s.send("sudo systemctl restart her")
    check("a fresh cache answers with the units it holds",
          s.suggestion().split(" PD=")[0], "SUG=[m.service]")
    s.close()
    check("cache reuse clean", s.noise(), [])


def main() -> int:
    setup()
    setup_units()
    test_zsh_units()
    test_bash_units()
    test_units_cache_is_reused()
    print("\nOK — systemctl units complete from a cached list, in both shells."
          if not failures else f"\nFAILED ({len(failures)}): {', '.join(failures)}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
