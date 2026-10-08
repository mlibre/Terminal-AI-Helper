#!/usr/bin/env python3
"""tai CLI — embedded index builder + direct one-shot inference. Stdlib only.

Normal shell autocomplete uses generated zsh/bash indexes and never invokes
this CLI during typing. These commands are for learning, refreshing,
updating, and diagnostics.

    tai suggest "<prefix>" [--cwd X --git Y --branch B --last "a;b" --limit N --json --jev]
    tai jev "<prefix>" --candidate "cmd" [--candidate "cmd" ...]
    tai record "<cmd>" [--cwd X --git Y --branch B --exit 0]
    tai refresh [--quiet]
    tai update
    tai discover [tool ...]
    tai purge [--stale] [--rebuild/--no-rebuild]
    tai uninstall
    tai bench [--limit N]
    tai tune [--limit N --trials N]
    tai doctor
    tai eval
    tai eval-jev
"""
import argparse
import os
import sys
from pathlib import Path

REPO_URL = "https://github.com/mlibre/terminal-ai-helper"

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Nothing heavy is imported at module level, and the reason is `tai record`: it
# runs in a fresh process after every single command the user types, so every
# import here is paid once per command for the life of an install. argparse is
# unavoidable (every path parses arguments) and the store is what record opens;
# json, shutil and subprocess are each needed by one command, and maintenance,
# knowledge and jev by a few — all of them load inside the functions that use
# them, so a record pays for none of them.


def _which(name: str) -> str | None:
    """The PATH lookup `shutil.which` performs, without importing shutil.

    The one question `tai record` asks of the filesystem — is this tool
    installed — used to drag the whole `shutil` module into a process that runs
    once per command. os is already loaded; this is the same answer for the one
    case that matters: an exact name, no Windows extensions, executable and a
    file rather than a directory.
    """
    for d in os.environ.get("PATH", "").split(os.pathsep):
        if not d:
            d = os.curdir
        cand = os.path.join(d, name)
        if os.path.isfile(cand) and os.access(cand, os.X_OK):
            return cand
    return None


def cmd_suggest(a) -> int:
    from tai.predictor import suggest
    last = [s.strip() for s in a.last.split(";") if s.strip()] if a.last else []
    cwd, repo, branch = a.cwd, a.git, a.branch
    res = suggest(prefix=a.prefix or "", cwd=cwd, repo=repo,
                  branch=branch, last_commands=last, limit=a.limit)
    if a.jev:
        import json
        try:
            from tai.decisions import unsafe_score
            from tai.jev import decide
            candidates = [c["cmd"] for c in res.get("choices", [])]
            candidates += [a.prefix] if a.prefix and a.prefix not in candidates else []
            j = decide(a.prefix or "", candidates, cwd=cwd, repo=repo,
                       branch=branch, previous=last[-1] if last else "")
            res["jev"] = {"choice": j.choice, "probabilities": j.probabilities,
                          "confidence": j.confidence, "show_now": j.show_now,
                          "destructive": j.destructive, "model": j.model}
            # The gate is deterministic and in code. `j.destructive` is the
            # model's own opinion of its own answer and is not trusted: a model
            # that answers `{"choice": "rm -rf /", "destructive": 0.1}` would
            # otherwise walk it straight into the prompt. And the echo rule still
            # applies, so a choice equal to the prefix is not a completion.
            echo = j.choice == (a.prefix or "")
            unsafe = unsafe_score(j.choice) if j.choice else 0.0
            if (j.choice and not echo and j.show_now >= 0.5
                    and j.destructive < 0.5 and unsafe < 0.5):
                res["choice"] = j.choice
                res["completion"] = (j.choice[len(a.prefix or ""):]
                                     if j.choice.startswith(a.prefix or "") else "")
            elif j.choice and (echo or unsafe >= 0.5):
                res["jev_rejected"] = f"not offering {j.choice!r}"
        except Exception as e:
            # Said out loud, because a rerank that never ran must not look like
            # one that ran and agreed.
            res["jev_error"] = str(e)
            print(f"tai: semantic rerank unavailable ({e}); using the local ranking",
                  file=sys.stderr)
    if a.json:
        import json
        print(json.dumps(res))
    elif a.limit > 1:
        for c in res.get("choices", []):
            print(c["cmd"])
    else:
        # With a newline, and this used to print none: the answer landed on the
        # same line as the prompt, so `tai suggest git` read as `godot .user@host
        # %`. A command substitution strips the newline anyway, so nothing was
        # being gained by omitting it — and nothing documents it, so the only
        # thing the omission ever did was break the terminal it prints to.
        print(res.get("choice", ""))
    return 0


def _repo_dir() -> Path:
    """The checkout this `tai` was installed from.

    The wrapper is `exec python3 <checkout>/tai/cli.py`, so the checkout is
    always this file's grandparent. No state file, no configuration, and it
    still works on a machine that has never been online.
    """
    return Path(__file__).resolve().parent.parent


def cmd_refresh(a) -> int:
    """Learn new history and rebuild the embedded indexes.

    The one command that makes new history visible to the shell. The index is a
    file and the plugin re-reads it at the next prompt when it changes, so
    nothing has to be restarted.
    """
    from tai.maintenance import _rebuild_index
    from tai.store import import_shell_history
    imported = import_shell_history()
    try:
        count = _rebuild_index()
    except Exception as e:
        # A person is watching this one, so the reason is worth the words. The
        # previous index is still in place, which is what "failed" means here.
        print(f"tai: index rebuild failed: {e}")
        return 1
    if count is None:
        print("tai: another index rebuild holds the lock; try again in a moment")
        return 1
    if not a.quiet:
        print(f"✓ {count} commands indexed"
              + (f", {imported} new" if imported else ""))
    return 0


def cmd_update(a) -> int:
    """Pull the latest version of the checkout and reinstall it.

    The user should never have to remember which folder the project lives in to
    get a new version, so this is driven from where tai already is rather than
    from a path they have to type.
    """
    repo = _repo_dir()
    import shutil
    import subprocess

    git = shutil.which("git")
    if not git:
        print("tai: git is not installed, so there is nothing to update from")
        print(f"     reinstall from {REPO_URL}")
        return 1
    if not (repo / ".git").exists():
        print(f"tai: {repo} is not a git checkout, so there is nothing to pull")
        print(f"     reinstall from {REPO_URL} to get the latest version")
        return 1

    def head() -> str:
        return subprocess.run([git, "-C", str(repo), "rev-parse", "--short", "HEAD"],
                              capture_output=True, text=True).stdout.strip()

    before = head()
    pull = subprocess.run([git, "-C", str(repo), "pull", "--ff-only"],
                          capture_output=True, text=True)
    if pull.returncode != 0:
        # A local edit in the checkout is the usual cause, and git's own message
        # says exactly what to do about it, so pass it through rather than guess.
        print("tai: git pull failed:")
        print((pull.stderr or pull.stdout).strip())
        return 1
    after = head()
    # Flushed, because the installer below writes to the same terminal from a
    # child process and would otherwise appear ahead of this line.
    print("✓ already up to date" if after == before else f"✓ updated to {after}",
          flush=True)

    installer = repo / "install.sh"
    if not installer.exists():
        print(f"tai: {installer} is missing, so the new code was not installed")
        return 1
    # A subprocess, not a re-entrant call: the pull just replaced the files this
    # process was loaded from, so anything it calls now is the old code.
    return subprocess.run(["bash", str(installer)]).returncode


def _auto_maintain(total: int) -> None:
    """Rebuild at 100-command boundaries without blocking the shell."""
    if total % 100:
        return
    from tai.maintenance import _rebuild_quietly
    _rebuild_quietly()


def _learn_on_use(command: str) -> None:
    """Learn a tool's help the first time the user actually runs it.

    This is how a tool nobody has used yet gets real subcommands: run it once,
    and from then on `tool <tab>` knows its verbs and flags. The whole call
    already happens inside the backgrounded `record` process the plugin spawns,
    so nothing here touches the prompt, and it fires at most once per tool
    because the result is cached in cli_tools.

    TAI_NO_LEARN=1 turns it off. It has to be a separate switch from
    TAI_NO_AUTO_RECORD: the test harness asserts that recording happens, and a
    rebuild triggered here would land on top of the index fixture it is
    asserting on.
    """
    if os.environ.get("TAI_NO_LEARN") == "1":
        return
    words = (command or "").split()
    name = words[0] if words else ""
    if not name or name.startswith("-") or "/" in name or len(name) > 64:
        return
    if not _which(name):
        return
    from tai.knowledge import is_known
    if is_known(name):
        return
    from tai.knowledge import learn
    from tai.maintenance import _rebuild_quietly
    if learn([name]):
        _rebuild_quietly()


def cmd_record(a) -> int:
    """Store one command. Says whether it was stored, and says why if not.

    The plugins call this detached with the output discarded, so the news is for
    the person who typed it: `tai record` on the command line used to print
    nothing at all, and a command rejected as a secret looked exactly like one
    that had been stored. Which is the wrong way round — the rejection is the one
    outcome worth explaining, because the rule is deliberate and the row is
    absent on purpose.
    """
    from tai.store import append_and_count
    ok, total = append_and_count(a.command, cwd=a.cwd, repo=a.git,
                                 branch=a.branch, exit_code=a.exit)
    if not ok:
        if not (a.command or "").strip():
            print("tai: nothing to record — no command was given")
        else:
            shown = a.command.strip()
            if len(shown) > 40:
                shown = shown[:39] + "…"
            print(f"tai: not recorded — {shown!r} looks like a secret or a "
                  "session-harness wrapper")
        return 1
    _auto_maintain(total)
    _learn_on_use(a.command)
    print("✓ recorded")
    return 0


def cmd_discover(a) -> int:
    from tai.knowledge import learn
    print(f"discovered {learn(a.tools or None)} installed tools")
    return 0


def cmd_bench(a) -> int:
    from tai.bench import main
    main(a.limit)
    return 0


def cmd_tune(a) -> int:
    from tai.tune import main
    main(a.limit, a.trials)
    return 0


def cmd_doctor(a) -> int:
    from tai.bench import doctor
    doctor()
    return 0


def cmd_eval(a) -> int:
    from tai.evaluate import main
    return main()


def cmd_eval_jev(a) -> int:
    from tai.evaluate_jev import main
    return main()


def cmd_uninstall(a) -> int:
    import shutil

    from tai.paths import data_dir
    home = Path.home()
    # `TAI_DATA_DIR` overrides only *this* command's idea of where the data is.
    # It is a seam for the install tests, which must be able to uninstall without
    # touching the developer's own database — the same discipline as the
    # subprocesses they run in — and not a knob: `TAI_DB` names the database and
    # `TAI_INDEX` the index, which is how everything else finds them.
    data = Path(os.environ.get("TAI_DATA_DIR") or data_dir())
    # The same rule the installer uses, so a wrapper installed into a custom
    # BIN_DIR is the one that gets removed.
    wrapper = Path(os.environ.get("BIN_DIR") or home / ".local" / "bin") / "tai"
    removed = []
    if wrapper.exists() or wrapper.is_symlink():
        wrapper.unlink()
        removed.append(str(wrapper))
    if data.exists():
        shutil.rmtree(data)
        removed.append(str(data))
    for rc in (home / ".zshrc", home / ".bashrc"):
        if not rc.exists():
            continue
        # surrogateescape round-trips bytes that are not valid UTF-8. errors=
        # "replace" would read them as U+FFFD and then write those replacement
        # characters back into the user's dotfile, corrupting it.
        text = rc.read_text(encoding="utf-8", errors="surrogateescape")
        # Two managed blocks, each identified by its marker and its own payload
        # lines rather than by position, so a user's own edits between them cannot
        # leave half a block behind. The second is the zsh-autosuggestions pause
        # the installer adds, and it has to come back out: an uninstall that left a
        # user's shell configured differently from how they found it would not be an
        # uninstall.
        kept: list[str] = []
        src = text.splitlines(keepends=True)
        i = 0
        while i < len(src):
            line = src[i]
            if line.strip() == "# tai autocomplete (installed by ./install.sh)":
                i += 1
                while i < len(src) and "plugins/" in src[i] and "source" in src[i]:
                    i += 1
                continue
            if line.startswith("# tai: zsh-autosuggestions paused"):
                i += 1
                while i < len(src) and ("_zsh_autosuggest_start" in src[i]
                                        or src[i].startswith("# ")):
                    i += 1
                continue
            if "plugins/tai." in line:
                i += 1
                continue
            kept.append(line)
            i += 1
        new = "".join(kept)
        if new != text:
            tmp = rc.with_name(rc.name + ".tai-uninstall")
            tmp.write_text(new, encoding="utf-8", errors="surrogateescape")
            os.replace(tmp, rc)
            removed.append(str(rc))
    print("tai removed" if removed else "tai was not installed")
    for path in removed:
        print(f"  removed {path}")
    return 0


def cmd_purge(a) -> int:
    from tai.maintenance import _rebuild_quietly
    from tai.store import purge_stale, purge_unrecordable
    n = purge_unrecordable()
    m = purge_stale() if a.stale else 0
    if (n or m) and a.rebuild:
        _rebuild_quietly()
    parts = []
    if n:
        parts.append(f"purged {n} unusable history rows")
    if m:
        parts.append(f"purged {m} commands whose paths no longer exist")
    if parts:
        print("; ".join(parts) + (" and rebuilt the index" if a.rebuild else ""))
    else:
        print("nothing to purge")
        if not a.stale:
            print("run 'tai purge --stale' to also drop commands whose paths are gone")
    if n and not a.rebuild:
        print("run 'tai refresh' to rebuild the shell indexes")
    return 0


def cmd_jev(a) -> int:
    import json
    from tai.jev import decide
    result = decide(a.prefix, a.candidate, cwd=a.cwd, repo=a.git,
                    branch=a.branch, previous=a.previous)
    value = {"choice": result.choice, "probabilities": result.probabilities,
             "confidence": result.confidence, "show_now": result.show_now,
             "destructive": result.destructive, "model": result.model,
             "usage": result.usage}
    print(json.dumps(value, indent=2) if a.json else value["choice"])
    return 0


_COMMANDS = {
    "suggest": cmd_suggest,
    "jev": cmd_jev,
    "record": cmd_record,
    "refresh": cmd_refresh,
    "update": cmd_update,
    "discover": cmd_discover,
    "purge": cmd_purge,
    "uninstall": cmd_uninstall,
    "bench": cmd_bench,
    "tune": cmd_tune,
    "doctor": cmd_doctor,
    "eval": cmd_eval,
    "eval-jev": cmd_eval_jev,
}


def main() -> int:
    p = argparse.ArgumentParser(
        prog="tai",
        description="learn the commands you run and suggest them as you type")
    sub = p.add_subparsers(dest="cmd", required=True, metavar="command")

    # Every subcommand says what it is in one line. `tai --help` listed three of
    # thirteen, because argparse only shows the ones that were given a `help=`
    # and the rest were added without it — so the first thing a new user ran
    # could not tell them what the tool could do.
    s = sub.add_parser("suggest", help="print what tai would suggest for a prefix")
    s.add_argument("prefix", nargs="?", default="")
    s.add_argument("--cwd", default="")
    s.add_argument("--git", default="")
    s.add_argument("--branch", default="")
    s.add_argument("--last", default="")
    s.add_argument("--limit", type=int, default=1)
    s.add_argument("--json", action="store_true")
    s.add_argument("--jev", action="store_true",
                   help="ask a hosted model to rerank the candidates")

    j = sub.add_parser("jev", help="ask the hosted model directly")
    j.add_argument("prefix")
    j.add_argument("--candidate", action="append", default=[])
    j.add_argument("--cwd", default="")
    j.add_argument("--git", default="")
    j.add_argument("--branch", default="")
    j.add_argument("--previous", default="")
    j.add_argument("--json", action="store_true")

    r = sub.add_parser("record", help="store one command in the history")
    r.add_argument("command", default="", nargs="?")
    r.add_argument("--cwd", default="")
    r.add_argument("--git", default="")
    r.add_argument("--branch", default="")
    r.add_argument("--exit", type=int, default=0)

    sub.add_parser("uninstall", help="remove the plugins and the stored history")
    pg = sub.add_parser("purge", help="drop unusable stored history rows")
    pg.add_argument("--rebuild", action=argparse.BooleanOptionalAction, default=True,
                    help="rebuild the shell indexes afterwards (default: yes)")
    pg.add_argument("--stale", action="store_true",
                    help="also drop commands whose paths no longer exist")
    rf = sub.add_parser("refresh",
                        help="import new history rows and rebuild the indexes")
    rf.add_argument("--quiet", action="store_true", help="print nothing on success")
    sub.add_parser("update", help="pull the latest version and reinstall")
    d = sub.add_parser("discover", help="read a tool's --help and cache its vocabulary")
    d.add_argument("tools", nargs="*")

    b = sub.add_parser("bench", help="report load time, latency and memory")
    b.add_argument("--limit", type=int, default=5000)
    t = sub.add_parser("tune", help="search the ranking weights against the history")
    t.add_argument("--limit", type=int, default=10000)
    t.add_argument("--trials", type=int, default=60)
    sub.add_parser("doctor", help="say what is indexed and what is held back")
    sub.add_parser("eval", help="score the ranker against held-out history")
    sub.add_parser("eval-jev", help="the same, against a hosted model")

    a = p.parse_args()
    # A subcommand the table does not know is a KeyError at the worst possible
    # moment, so the two lists are checked against each other here instead:
    # argparse and the table are the same decision written twice.
    missing = set(sub.choices) - set(_COMMANDS)
    assert not missing, f"no handler for: {sorted(missing)}"
    return _COMMANDS[a.cmd](a)


if __name__ == "__main__":
    raise SystemExit(main() or 0)
