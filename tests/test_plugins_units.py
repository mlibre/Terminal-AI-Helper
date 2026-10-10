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
from plugin_screen import menu_entries  # noqa: F401  (the drawn rows, split)

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

    # The shared head is written before the list draws, on this vocabulary like
    # on every other: `her` is answered by two units that agree up to `herm`,
    # so the line takes `herm` and the list stands under it. Enter then lays
    # the whole unit over the span the head fills — the `.service` written
    # once, the head not doubled.
    s.send("sudo systemctl restart her")
    s.write(TAB)
    s.settle()
    line, drawn, size, idx = s.menu(clear=False)
    check("the units menu writes the head both units share",
          line, "sudo systemctl restart herm")
    check("and still lists both units",
          (menu_entries(drawn), size),
          (["herm.service", "herm2.service"], 2))
    s.write(ENTER)
    s.settle()
    line, drawn, size, idx = s.menu(clear=False)
    check("Enter takes a unit over the advanced line",
          (line, size), ("sudo systemctl restart herm.service", 0))
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


def test_preload_makes_no_job_noise() -> None:
    """The prompt hook's preload is nobody's job, so nothing announces it.

    A plain `&` in the prompt hook made the loader a job of the interactive
    shell, and the shell reported it: bash printed `[N] pid` at spawn and
    `[N]+ Done` before the next prompt, zsh the same in its own words — the
    user saw both around every systemctl command. The parenthesised fork
    (the rule the flush and the record fallback always followed) makes the
    loader the grandchild of a subshell that exits at once. The work still
    happens — both cache files land — and the terminal stays silent.
    """
    for shell in ("bash", "zsh"):
        if not SHELLS[shell]:
            continue
        print(f"{shell}: the systemctl preload works and stays quiet")
        s = Session(shell, env_extra=units_env())
        mark = len(s.seen)
        # A command that mentions systemctl, at the prompt: the prompt hook
        # answers it with the background preload. Both cache files appearing
        # is the observable end of that work.
        s.run("systemctl --user disable nothing.service")
        end = time.monotonic() + 15.0
        while not (cache_text("system") and cache_text("user")) \
                and time.monotonic() < end:
            time.sleep(0.01)
        check(f"{shell} preload wrote the system cache",
              "herm.service" in cache_text("system"), True)
        check(f"{shell} preload wrote the user cache",
              "user-a.service" in cache_text("user"), True)
        # Job-state reports are printed before the next prompt, so give the
        # shell one more prompt to speak in.
        s.run("true")
        s.close()
        seen = s.seen[mark:].replace("\r", "")
        notes = sorted(set(re.findall(r"\[\d+\]\s*(?:\d+|\+)", seen)))
        check(f"{shell} preload leaves no job announcements", notes, [])
        if notes:
            print(f"       terminal tail:\n{indent(tail(s.raw(), 400))}")


def test_units_cache_survives_corruption() -> None:
    """A torn, stale-in-the-future, or gutted cache refetches, never sticks.

    The cache is one file a shell reads back; anything can happen to it — a
    truncated write, a clock that jumped, a stamp with no units under it. The
    loader's answer in every one of those cases has to be the same: fork the
    one `systemctl` call, rewrite the file, answer with the real units. A
    cache that could answer "no units" for a day because of one bad write
    would be a completion that lies politely.
    """
    for shell in ("bash", "zsh"):
        if not SHELLS[shell]:
            continue
        print(f"{shell}: the unit cache refetches instead of sticking")
        cache_dir = UNITS_CACHE / "tai"
        cache_dir.mkdir(parents=True, exist_ok=True)
        s = Session(shell, env_extra=units_env())
        for name, body in (
                ("a garbage stamp", "not-a-number\njunk\n"),
                ("a stamp with no units", f"{int(time.time()) - 10}\n"),
                ("a stamp from the future", f"{int(time.time()) + 90_000}\n"
                                            "herm.service\n"),
        ):
            (cache_dir / "units-system.txt").write_text(body)
            s.run("_tai_units_load 0 >/dev/null 2>&1")
            # The accept key, not the ghost: bash paints no hint while a line
            # is being typed — its suggestion arrives when a key takes it —
            # so the answer is read the way a user reads it, in both shells.
            s.send("sudo systemctl restart her")
            s.write(RIGHT)
            s.settle()
            check(f"{shell}: {name} refetches rather than answering",
                  s.line(), "sudo systemctl restart herm.service")
            s.write("\x15")
            body_now = cache_text("system")
            check(f"{shell}: {name} left a whole cache behind",
                  body_now.splitlines()[0].isdigit()
                  and "herm.service" in body_now, True)
        s.close()
        check(f"{shell} corrupt-cache runs clean", s.noise(), [])


def main() -> int:
    setup()
    setup_units()
    test_zsh_units()
    test_bash_units()
    test_units_cache_is_reused()
    test_units_cache_survives_corruption()
    test_preload_makes_no_job_noise()
    print("\nOK — systemctl units complete from a cached list, in both shells."
          if not failures else f"\nFAILED ({len(failures)}): {', '.join(failures)}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
