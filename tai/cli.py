#!/usr/bin/env python3
"""tai CLI — embedded index builder + direct one-shot inference. Stdlib only.

Normal shell autocomplete uses generated zsh/bash indexes and never invokes
this CLI during typing. These commands are for learning, refreshing,
updating, and diagnostics.

    tai suggest "<prefix>" [--cwd X --git Y --branch B --last "a;b" --limit N --json --jev]
    tai jev "<prefix>" --candidate "cmd" [--candidate "cmd" ...]
    tai record "<cmd>" [--cwd X --git Y --branch B --exit 0]
    tai flush              ingest the records the plugins spooled, then maintain
    tai refresh [--quiet]
    tai update | upgrade   pull the latest version and reinstall — from GitHub
                           for a checkout install, from npm for an npm one
    tai version            print the release this install is running
    tai discover [tool ...]
    tai purge [--stale] [--rebuild/--no-rebuild]
    tai uninstall
    tai bench [--limit N]
    tai tune [--limit N --trials N]
    tai web [--port N] [--no-browser]     (alias: tai dashboard)
    tai doctor
    tai eval
    tai eval-jev
"""
import os
import sys

REPO_URL = "https://github.com/mlibre/terminal-ai-helper"

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Nothing heavy is imported at module level, and the reason is the CLI's own
# startup: `tai flush` runs once per batch of records, `tai record` is the
# fallback write path, and every command here is one process the user waits
# on. argparse — about 5ms — is imported inside main(), after the two
# hand-parsed fast paths (record, version) have had their chance; pathlib is
# imported by the four commands that touch paths; json, shutil and subprocess
# are each needed by one command, and maintenance, knowledge and jev by a
# few — all of them load inside the functions that use them, so a record or a
# flush pays for none of them.


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


def _repo_dir() -> "Path":
    """The checkout this `tai` was installed from.

    The wrapper is `exec python3 <checkout>/tai/cli.py`, so the checkout is
    always this file's grandparent. No state file, no configuration, and it
    still works on a machine that has never been online.
    """
    from pathlib import Path
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


def _install_method(repo: "Path") -> str:
    """'npm', 'git', or 'none' — decided by where the running code lives.

    The wrapper execs one cli.py, and that file's location is the copy an
    update has to replace, so the method is a fact about the path, not about
    the user's memory of how they installed: npm keeps every global package
    under a node_modules directory, a checkout keeps its .git, and anything
    else (the .deb, a hand-copied tree) is 'none' — nothing here can pull it
    forward, and the answer says so instead of guessing.
    """
    if "node_modules" in repo.parts:
        return "npm"
    if (repo / ".git").exists():
        return "git"
    return "none"


def _npm_configured() -> bool:
    """True when npm's postinstall hook already ran the installer from the tree.

    The hook runs install.sh inside the npm install itself, and rerunning it
    from here would print the welcome twice and rebuild an index that is
    current. The check is the installer's own two products — the wrapper
    naming this tree and one rc line per shell sourcing it — asked of the text
    npm's layout guarantees is unquoted through the middle: a prefix path with
    a space in it comes out of printf %q escaped, but everything from
    node_modules down survives intact.
    """
    from pathlib import Path

    tail = "node_modules/terminal-ai-helper"
    wrapper = (Path(os.environ.get("BIN_DIR") or Path.home() / ".local" / "bin")
               / "tai")
    try:
        text = wrapper.read_text(encoding="utf-8", errors="surrogateescape")
    except OSError:
        return False
    if f"{tail}/tai/cli.py" not in text:
        return False
    home = Path.home()
    for rc, plugin in ((".zshrc", "tai.zsh"), (".bashrc", "tai.bash")):
        try:
            lines = (home / rc).read_text(
                encoding="utf-8", errors="surrogateescape").splitlines()
        except OSError:
            return False
        if not any("source" in line and f"{tail}/plugins/{plugin}" in line
                   for line in lines):
            return False
    return True


def _update_npm(repo: "Path") -> int:
    """The npm door of `tai update`: let the registry replace the package.

    npm's own output streams through — the install can take several seconds
    and its progress is the only thing the user sees while it runs — and the
    postinstall hook finishes the reinstall from the new tree unless scripts
    were skipped (--ignore-scripts) or it failed, in which case the installer
    runs here instead: idempotent, and the same report as every other door.
    """
    import shutil
    import subprocess

    npm = shutil.which("npm")
    if not npm:
        print("tai: npm is not installed, so there is nothing to update from")
        print("     reinstall with: npm i -g terminal-ai-helper@latest")
        return 1
    before = _read_version()
    pulled = subprocess.run([npm, "install", "-g", "terminal-ai-helper@latest"])
    if pulled.returncode != 0:
        print("tai: npm install failed — the previous version is still installed")
        return 1
    after = _read_version()
    # Flushed before anything else prints: npm's own output above is the
    # progress, and this line is the verdict.
    print("✓ already up to date" if after == before else f"✓ updated to {after}",
          flush=True)
    if after == before or _npm_configured():
        return 0
    installer = repo / "install.sh"
    if not installer.exists():
        print(f"tai: {installer} is missing, so the new code was not installed")
        return 1
    # A subprocess, not a re-entrant call: the install just replaced the files
    # this process was loaded from, so anything it calls now is the old code.
    return subprocess.run(["bash", str(installer)]).returncode


def cmd_update(a) -> int:
    """Pull the latest version and reinstall it, from wherever tai came from.

    The user should never have to remember which folder the project lives in —
    or which package manager put it there — so this is driven from where tai
    already is rather than from a path they have to type.
    """
    repo = _repo_dir()
    if _install_method(repo) == "npm":
        return _update_npm(repo)

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


# The two knobs of the record path's self-refresh, named where the web page
# can quote them: a rebuild at most once per this many seconds (bursts of
# records coalesce into one background build), and one unconditional build
# every this many recorded commands — TAI_REBUILD_EVERY moves the hundred.
_REBUILD_MIN_INTERVAL = 5.0


def _env_count(name: str, default: int) -> int:
    """One knob read as a count; a broken or zero value keeps the default."""
    try:
        return max(1, int(os.environ.get(name, "") or default))
    except ValueError:
        return default


AUTO_REBUILD_EVERY = _env_count("TAI_REBUILD_EVERY", 100)


def _auto_maintain(total: int, newest_ts: int = 0) -> None:
    """Rebuild at every-Nth-record boundaries without blocking the shell."""
    if total % AUTO_REBUILD_EVERY:
        _rebuild_when_stale(newest_ts)
        return
    from tai.maintenance import _rebuild_quietly
    _rebuild_quietly()


# A record may not wait a hundred records to be worth suggesting. The index is
# what the shells read, and a rebuild every 100th command meant a command typed
# now could take the next ninety-nine to reach it — long enough that the user
# stopped believing the suggestions were learning anything. So every record
# also asks one cheap question (one stat, one indexed MAX), and when the index
# on disk is older than the newest row by more than the debounce window, the
# background rebuild runs. The window keeps a burst of records from stacking
# rebuilds on top of each other; the rebuild itself still takes the maintenance
# lock, so two shells recording at once produce at most one build.


def _rebuild_when_stale(newest_ts: int | None = None) -> None:
    import time

    from tai.index import index_path
    from tai.maintenance import _rebuild_quietly

    idx = index_path()
    if not idx.exists():
        _rebuild_quietly()
        return
    mtime = idx.stat().st_mtime
    if time.time() - mtime < _REBUILD_MIN_INTERVAL:
        return
    # The newest row's timestamp arrives from the same connection that did the
    # insert; asking the database again here would be a whole second connection
    # per recorded command. The one caller that has no timestamp is the
    # missing-index case above, which rebuilds before ever reading it.
    newest = newest_ts
    if newest is None:
        import sqlite3

        from tai.store import db_path
        try:
            con = sqlite3.connect(str(db_path()))
            try:
                row = con.execute("SELECT MAX(ts) FROM commands").fetchone()
            finally:
                con.close()
        except Exception:
            return
        newest = row[0] if row and row[0] else 0
    if newest and newest > mtime:
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


def cmd_flush(a) -> int:
    """Ingest the records the plugins spooled, then do the record path's chores.

    The plugins append every command to the spool as one builtin write and ask
    for this when the batch is worth a process — one interpreter per batch, not
    one per command. Draining first and maintaining after is the same order
    `tai record` keeps, so the index never rebuilds over rows it has not seen.
    """
    from tai.spool import flush
    r = flush()
    if r["ingested"]:
        parts = [f"ingested {r['ingested']}"]
        if r["skipped"]:
            parts.append(f"skipped {r['skipped']} (secrets, control bytes, or not one line)")
        if r["learned"]:
            parts.append(f"learned {r['learned']} new tools")
        print(f"✓ {', '.join(parts)}")
    elif r["skipped"]:
        print(f"✓ nothing ingested; skipped {r['skipped']} unusable records")
    else:
        print("✓ nothing pending")
    return 0


def cmd_record(a) -> int:
    """Store one command. Says whether it was stored, and says why if not.

    The plugins no longer call this per command — they spool and the batch is
    flushed — so this is the manual write path and the plugin's fallback when
    the spool file cannot be written. The news is for the person who typed it:
    `tai record` on the command line used to print nothing at all, and a
    command rejected as a secret looked exactly like one that had been stored.
    Which is the wrong way round — the rejection is the one outcome worth
    explaining, because the rule is deliberate and the row is absent on
    purpose.
    """
    # Spooled records land first, so a record typed by hand is the newest row,
    # not a row competing with a pending batch for the same moment.
    from tai.spool import drain
    drain()
    from tai.store import append_and_count
    ok, total, newest = append_and_count(a.command, cwd=a.cwd, repo=a.git,
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
    _auto_maintain(total, newest)
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


def cmd_web(a) -> int:
    """A read-only localhost page of what tai learned, with a live try box.

    Kept out of the module imports like every other command's dependency: none
    of http.server lands in `tai record`'s process.
    """
    from tai.web import serve
    return serve(port=a.port, open_browser=not a.no_browser)


def cmd_eval(a) -> int:
    from tai.evaluate import main
    return main()


def cmd_eval_jev(a) -> int:
    from tai.evaluate_jev import main
    return main()


def cmd_uninstall(a) -> int:
    import shutil
    from pathlib import Path

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


def cmd_forget(a) -> int:
    """Drop every stored row for the named commands, then rebuild.

    The one command that speaks to the store in the user's own words: a typo
    that an older install recorded as a success sits in the ranking forever —
    no rule can tell it from a command that worked — and this is how it leaves.
    The names are exact, arguments included; the count is printed so a name
    that matched nothing is visible rather than silent.
    """
    names = [n.strip() for n in (a.commands or []) if n.strip()]
    if not names:
        print("usage: tai forget <command> [more commands...]")
        print("       the names are exact — 'tai forget opencoe git sta' forgets "
              "those lines, not their prefixes")
        return 2
    from tai.store import forget_commands
    try:
        n = forget_commands(names)
    except Exception as e:
        print(f"tai forget: could not read the history store: {e}")
        return 1
    shown = ", ".join(f"`{x}`" for x in names[:6]) + ("…" if len(names) > 6 else "")
    if not n:
        print(f"nothing forgotten — no stored row matches {shown}")
        return 0
    if a.rebuild:
        from tai.maintenance import _rebuild_quietly
        _rebuild_quietly()
    row_word = "row" if n == 1 else "rows"
    cmd_word = "command" if len(names) == 1 else "commands"
    print(f"forgot {n} {row_word} for {len(names)} {cmd_word}: {shown}"
          + ("; index rebuilt" if a.rebuild else ""))
    if not a.rebuild:
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


def cmd_version(a) -> int:
    """Say which release this is."""
    version = _read_version()
    print(f"tai {version}")
    return 0


def _read_version() -> str:
    """The release version, from the VERSION file beside the checkout.

    Plain open, not pathlib: `tai version` is a fast path, and importing
    pathlib costs about 9ms for a file read.
    """
    try:
        here = os.path.dirname(os.path.abspath(__file__))
        with open(os.path.join(here, os.pardir, "VERSION"), encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return "(unknown)"


_COMMANDS = {
    "suggest": cmd_suggest,
    "jev": cmd_jev,
    "record": cmd_record,
    "flush": cmd_flush,
    "refresh": cmd_refresh,
    "update": cmd_update,
    "upgrade": cmd_update,
    "version": cmd_version,
    "discover": cmd_discover,
    "purge": cmd_purge,
    "forget": cmd_forget,
    "uninstall": cmd_uninstall,
    "bench": cmd_bench,
    "tune": cmd_tune,
    "doctor": cmd_doctor,
    # Both spellings, because the name the user typed is the name argparse
    # reports back, and a KeyError under an alias is a broken alias.
    "web": cmd_web,
    "dashboard": cmd_web,
    "eval": cmd_eval,
    "eval-jev": cmd_eval_jev,
}


def _fast_suggest(argv: list[str]):
    """Hand-parse `tai suggest …`'s seven flags, or return None to say no.

    suggest is the one command a curious person runs first and the one the
    dashboard's try box re-runs, and argparse is about 6ms of its 45 — the
    same deal `tai record` got, with one extra rule: any token this parser
    cannot name (--help, -h, a misspelled flag, an abbreviation like --lim,
    a bare `--`, a second positional) returns None and argparse reports it
    properly. The = form is accepted because it is the one shell scripts
    actually write.
    """
    class _A: pass
    a = _A()
    a.prefix = ""
    a.cwd = a.git = a.branch = ""
    a.last = ""
    a.limit = 1
    a.json = a.jev = False
    positional: list[str] = []
    it = iter(argv)
    for tok in it:
        if tok == "--":
            return None
        if tok.startswith("--"):
            name, eq, value = tok[2:].partition("=")
            if name == "json" and not eq:
                a.json = True
                continue
            if name == "jev" and not eq:
                a.jev = True
                continue
            if name in ("cwd", "git", "branch", "last"):
                if not eq:
                    value = next(it, None)
                    if value is None:
                        return None
                setattr(a, name, value)
                continue
            if name == "limit":
                if not eq:
                    value = next(it, None)
                    if value is None:
                        return None
                try:
                    a.limit = int(value)
                except ValueError:
                    return None
                continue
            return None              # --help, -h, a typo, an abbreviation
        if tok.startswith("-"):
            return None              # short flags belong to argparse
        positional.append(tok)
    if len(positional) > 1:
        return None
    a.prefix = positional[0] if positional else ""
    return a


def _fast_record(argv: list[str]):
    """Hand-parse `tai record …`'s five flags, so record skips argparse.

    record runs once per command the user types for the life of an install, and
    importing argparse costs about as much as the store write itself. This is
    the same subset of parsing the record subparser accepts — anything else
    (``--help``, a misspelled flag) falls through to argparse, which reports it
    properly.
    """
    class _A: pass
    a = _A()
    a.command = ""
    a.cwd = a.git = a.branch = ""
    a.exit = 0
    words: list[str] = []
    it = iter(argv)
    for tok in it:
        if tok == "--cwd":
            a.cwd = next(it, "")
        elif tok == "--git":
            a.git = next(it, "")
        elif tok == "--branch":
            a.branch = next(it, "")
        elif tok == "--exit":
            try:
                a.exit = int(next(it, "0"))
            except ValueError:
                a.exit = 0
        else:
            words.append(tok)
    a.command = " ".join(words)
    return a


def main() -> int:
    argv = sys.argv[1:]
    # `record` is the fallback write path the plugins call by proxy, and
    # `version` is the one a person types to see what they are running — both
    # answer before argparse is even imported, because argparse is about 5ms
    # and both commands are shorter than that. A word either does not
    # recognise (--help, a typo) drops through to the real parser, which
    # names the problem.
    if argv and argv[0] == "record" and "--help" not in argv and "-h" not in argv:
        return cmd_record(_fast_record(argv[1:]))
    if argv == ["version"]:
        print(f"tai {_read_version()}")
        return 0
    # Any other command is a tai process the user asked for, and a non-empty
    # spool is records waiting for one: drain it here, so `tai suggest`,
    # `tai refresh` or the dashboard never answer from a store that is a few
    # records behind what the shells just did. `record` drained above, `flush`
    # is the drain, and `version` stays lean; `uninstall` is about to delete
    # the store either way. The fast suggest path below returns from inside
    # main(), so the drain has to have happened by then — it is ordered first
    # on purpose, and tests/test_cli.py pins the order.
    if argv and argv[0] not in ("record", "flush", "version", "uninstall"):
        try:
            from tai.spool import drain
            drain()
        except Exception:
            pass
    if argv and argv[0] == "suggest":
        fast = _fast_suggest(argv[1:])
        if fast is not None:
            return cmd_suggest(fast)
    import argparse
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

    sub.add_parser("flush", help="ingest the records the plugins spooled")
    sub.add_parser("uninstall", help="remove the plugins and the stored history")
    pg = sub.add_parser("purge", help="drop unusable stored history rows")
    pg.add_argument("--rebuild", action=argparse.BooleanOptionalAction, default=True,
                    help="rebuild the shell indexes afterwards (default: yes)")
    pg.add_argument("--stale", action="store_true",
                    help="also drop commands whose paths no longer exist")
    fg = sub.add_parser("forget",
                        help="drop every stored row for the named commands")
    fg.add_argument("commands", nargs="+",
                    help="commands to forget, exactly as they were run")
    fg.add_argument("--rebuild", action=argparse.BooleanOptionalAction, default=True,
                    help="rebuild the shell indexes afterwards (default: yes)")
    rf = sub.add_parser("refresh",
                        help="import new history rows and rebuild the indexes")
    rf.add_argument("--quiet", action="store_true", help="print nothing on success")
    sub.add_parser("update", aliases=["upgrade"],
                   help="pull the latest version and reinstall")
    d = sub.add_parser("discover", help="read a tool's --help and cache its vocabulary")
    d.add_argument("tools", nargs="*")

    b = sub.add_parser("bench", help="report load time, latency and memory")
    b.add_argument("--limit", type=int, default=5000)
    t = sub.add_parser("tune", help="search the ranking weights against the history")
    t.add_argument("--limit", type=int, default=10000)
    t.add_argument("--trials", type=int, default=60)
    sub.add_parser("doctor", help="say what is indexed and what is held back")
    w = sub.add_parser("web", aliases=["dashboard"],
                       help="serve a read-only localhost dashboard of what tai learned")
    # The default is tai.web's own, written here as a literal because main()
    # also builds the parser for `tai record`, and that process pays for none
    # of http.server. tests/test_web.py reads both spellings and asserts they
    # agree, so the two copies cannot drift apart in silence.
    w.add_argument("--port", type=int, default=8247,
                   help="port to bind on 127.0.0.1 (default 8247)")
    w.add_argument("--no-browser", action="store_true",
                   help="print the URL instead of opening the browser")
    sub.add_parser("eval", help="score the ranker against held-out history")
    sub.add_parser("eval-jev", help="the same, against a hosted model")
    sub.add_parser("version", help="print the release this install is running")

    a = p.parse_args()
    # A subcommand the table does not know is a KeyError at the worst possible
    # moment, so the two lists are checked against each other here instead:
    # argparse and the table are the same decision written twice.
    missing = set(sub.choices) - set(_COMMANDS)
    assert not missing, f"no handler for: {sorted(missing)}"
    return _COMMANDS[a.cmd](a)


if __name__ == "__main__":
    raise SystemExit(main() or 0)
