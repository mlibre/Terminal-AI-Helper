"""The CLI verbs, asked as a user asks them: one process per question.

    python3 tests/test_cli.py

`test_smoke_cli.py` holds the learning side — discovery, wrappers, the index
scores. This suite holds the *conversation*: what each verb prints, what it
costs the store, and what it refuses to do. Every command here runs as a real
subprocess on its own scratch world, because the words a user reads are the
product too — a rejection that printed nothing would look exactly like a
success, and that was a reported failure once already.
"""
import json
import os
import pathlib
import sqlite3
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
CLI = str(REPO / "tai" / "cli.py")
TAI = [sys.executable, "-S", "-E", CLI]

failures: list[str] = []


def check(what: str, got, want) -> None:
    if got == want:
        print(f"  ok   {what}: {got!r}")
    else:
        print(f"  FAIL {what}:\n         got  {got!r}\n         want {want!r}")
        failures.append(what)


def run(*args, env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(TAI + list(args), env=env, capture_output=True,
                          text=True, timeout=120)


def world(name: str) -> dict:
    """A scratch store, spool and index for one section, freshly empty."""
    base = pathlib.Path("/tmp/tai/tai_cli_" + name)
    db = str(base / "history.db")
    for suffix in ("", "-wal", "-shm"):
        pathlib.Path(db + suffix).unlink(missing_ok=True)
    spool = str(base / "spool.log")
    pathlib.Path(spool).unlink(missing_ok=True)
    index = str(base / "index.zsh")
    pathlib.Path(index).unlink(missing_ok=True)
    base.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, TAI_DB=db, TAI_SPOOL=spool, TAI_INDEX=index,
               TAI_NO_LEARN="1")
    # The hosted rerank is nobody's test subject here: with a key present the
    # jev flag would leave the machine, and the assertion is about the words
    # printed when it cannot run.
    env.pop("TYPESAFE_API_KEY", None)
    return env


def spool_path(env: dict) -> str:
    return env["TAI_SPOOL"]


def append_frame(env: dict, ts: str, code: str, cwd: str, cmd: str) -> None:
    """One framed record, the way the shell writes it."""
    US, RS = "\x1f", "\x1e"
    with open(spool_path(env), "ab") as f:
        f.write(f"{ts}{US}{code}{US}{cwd}{US}{cmd}{RS}".encode())


def db_rows(env: dict) -> list[tuple]:
    p = pathlib.Path(env["TAI_DB"])
    if not p.exists():
        return []
    con = sqlite3.connect(str(p))
    try:
        return con.execute(
            "SELECT cmd,cwd,exit_code FROM commands ORDER BY id").fetchall()
    except sqlite3.Error:
        return []
    finally:
        con.close()


def seed_rows(env: dict, rows: list[tuple]) -> None:
    """Seed the store directly, the way an older install would have left it."""
    con = sqlite3.connect(env["TAI_DB"])
    con.execute(
        "CREATE TABLE IF NOT EXISTS commands(id INTEGER PRIMARY KEY, "
        "cmd TEXT NOT NULL, cwd TEXT DEFAULT '', repo TEXT DEFAULT '', "
        "branch TEXT DEFAULT '', exit_code INTEGER DEFAULT 0, ts INTEGER DEFAULT 0)")
    con.executemany(
        "INSERT INTO commands(cmd,cwd,repo,branch,exit_code,ts) VALUES(?,?,?,?,?,?)",
        rows)
    con.commit()
    con.close()


def main() -> int:
    print("tai record: the write path and every way it says no")
    env = world("record")
    r = run("record", "echo kept", "--cwd", "/tmp", "--exit", "0", env=env)
    check("a kept command answers and lands in the store",
          (r.returncode, r.stdout.strip()), (0, "✓ recorded"))
    check("the row is whole",
          db_rows(env), [("echo kept", "/tmp", 0)])

    r = run("record", "y", env=env)
    check("a one-key Enter is refused with its reason",
          (r.returncode, "not recorded" in r.stdout), (1, True))
    r = run("record", "", env=env)
    check("an empty line says there was nothing to record",
          (r.returncode, r.stdout.strip()),
          (1, "tai: nothing to record — no command was given"))
    r = run("record",
            "curl -H 'token: aaaabbbbccccddddeeeeffffgggghhhh1111' https://x",
            env=env)
    check("a secret is refused, named and cut to fit one line",
          (r.returncode, "looks like a secret" in r.stdout, "…" in r.stdout),
          (1, True, True))
    check("none of the refusals wrote a row",
          len(db_rows(env)), 1)

    r = run("record", "echo coded", "--exit", "garbage", env=env)
    check("a garbage --exit still records — the fast parser degrades to zero",
          (r.returncode, r.stdout.strip()), (0, "✓ recorded"))
    check("and the row carries exit 0",
          db_rows(env)[-1], ("echo coded", "", 0))

    r = run("record", "echo trailing", "--cwd", env=env)
    check("a flag at the end with no value is not a crash",
          (r.returncode, r.stdout.strip()), (0, "✓ recorded"))

    append_frame(env, "1700000100", "0", "/tmp", "echo spooled")
    r = run("record", "echo manual", env=env)
    check("record drains the pending spool before writing its own row",
          (r.returncode, len(db_rows(env))), (0, 5))
    check("the spooled row landed ahead of the manual one",
          [row[0] for row in db_rows(env)],
          ["echo kept", "echo coded", "echo trailing", "echo spooled",
           "echo manual"])
    check("and the spool is gone",
          pathlib.Path(spool_path(env)).exists(), False)

    print("tai flush: the counts it prints")
    env = world("flush")
    r = run("flush", env=env)
    check("nothing pending says so",
          (r.returncode, r.stdout.strip()), (0, "✓ nothing pending"))
    append_frame(env, "1700000200", "0", "/tmp", "echo good")
    append_frame(env, "1700000201", "0", "/tmp", "y")
    append_frame(env, "1700000202", "0", "/tmp", "curl -H 'token: x' https://s")
    r = run("flush", env=env)
    check("the batch reports ingested and skipped, and names the skips",
          (r.returncode, r.stdout.strip()),
          (0, "✓ ingested 1, skipped 2 (secrets, control bytes, or not one line)"))
    append_frame(env, "1700000203", "0", "/tmp", "y")
    r = run("flush", env=env)
    check("a batch that teaches nothing is honest about it",
          (r.returncode, r.stdout.strip()),
          (0, "✓ nothing ingested; skipped 1 unusable records"))

    print("tai suggest: the shapes it prints")
    env = world("suggest")
    seed_rows(env, [("git status", "/w", "", "", 0, 1728500000),
                    ("git status", "/w", "", "", 0, 1728500100),
                    ("git status", "/w", "", "", 0, 1728500200),
                    ("git status -sb", "/w", "", "", 0, 1728500150)])
    r = run("suggest", "git ", env=env)
    check("the plain answer is one line",
          (r.returncode, r.stdout.strip().splitlines()[0]), (0, "git status"))
    r = run("suggest", "git ", "--limit", "2", env=env)
    check("--limit prints that many lines",
          (r.returncode, len(r.stdout.strip().splitlines())), (0, 2))
    r = run("suggest", "git ", "--json", env=env)
    payload = json.loads(r.stdout)
    check("--json is the shape the plugins and the panel read",
          sorted(k for k in payload if k in
                 ("choice", "choices", "probabilities", "confidence",
                  "latency_ms", "count")),
          ["choice", "choices", "confidence", "count", "latency_ms",
           "probabilities"])
    r = run("suggest", "zzz-nothing", env=env)
    check("a prefix the store never saw answers an empty line, not an error",
          (r.returncode, r.stdout), (0, "\n"))
    r = run("suggest", "git ", "--last", "echo a;echo b", env=env)
    check("--last parses its semicolon list",
          (r.returncode, r.stdout.strip().splitlines()[0]), (0, "git status"))
    r = run("suggest", "git ", "--jev", env=env)
    check("a rerank that cannot run is said out loud, on stderr",
          (r.returncode, "semantic rerank unavailable" in r.stderr), (0, True))
    check("and the local ranking still answers",
          r.stdout.strip().splitlines()[0], "git status")

    print("tai forget: the usage line")
    env = world("forget")
    r = run("forget", "   ", env=env)
    check("names that are only whitespace is a usage error, not a rebuild",
          (r.returncode, "usage: tai forget" in r.stdout), (2, True))

    print("tai purge: what it says about what it did")
    env = world("purge")
    r = run("purge", env=env)
    check("an empty store is nothing to purge, with the stale hint",
          (r.returncode, r.stdout.strip().splitlines()),
          (0, ["nothing to purge",
               "run 'tai purge --stale' to also drop commands whose paths are gone"]))
    seed_rows(env, [("git status", "/w", "", "", 0, 1728500000),
                    ("y", "/w", "", "", 0, 1728500100),
                    ('git commit -m "a\nb"', "/w", "", "", 0, 1728500200)])
    r = run("purge", env=env)
    check("unusable rows are purged and the index rebuilt",
          (r.returncode, r.stdout.strip()),
          (0, "purged 2 unusable history rows and rebuilt the index"))
    seed_rows(env, [("cat /nonexistent-tai-cli-dir/notes.txt", "/w", "", "", 0,
                     1728500300)])
    r = run("purge", "--stale", env=env)
    check("--stale names the kind it purged, and the rebuild it did",
          (r.returncode, r.stdout.strip()),
          (0, "purged 1 commands whose paths no longer exist and rebuilt the index"))
    env = world("purge2")
    seed_rows(env, [("y", "/w", "", "", 0, 1728500000)])
    r = run("purge", "--no-rebuild", env=env)
    check("a purge that skipped the rebuild says where to get it",
          (r.returncode, r.stdout.strip()),
          (0, "purged 1 unusable history rows\n"
              "run 'tai refresh' to rebuild the shell indexes"))

    print("tai refresh: what it reports")
    env = world("refresh")
    hist = pathlib.Path(env["TAI_DB"]).parent / "hist"
    hist.write_text("echo one\necho two\n: 1728500000:0;git status\n")
    # A scratch HOME too: import_shell_history also reads the well-known names
    # under Path.home(), and a developer's real history must never decide what
    # this assertion counts.
    env = dict(env, HISTFILE=str(hist), HOME=str(hist.parent))
    r = run("refresh", env=env)
    check("the count and the news are on one line",
          (r.returncode, r.stdout.strip().splitlines()[0].startswith("✓ ")
           and ", 3 new" in r.stdout), (0, True))
    r2 = run("refresh", "--quiet", env=env)
    check("--quiet prints nothing at all",
          (r2.returncode, r2.stdout), (0, ""))
    r = run("refresh", env=env)
    check("a second refresh imports nothing new",
          (r.returncode, "new" in r.stdout), (0, False))

    print("tai doctor: the first thing a new user runs")
    env = world("doctor")
    r = run("doctor", env=env)
    check("a healthy scratch store reports its rows",
          (r.returncode, f"rows=0" in r.stdout and "run: tai refresh" in r.stdout),
          (0, True))
    con = sqlite3.connect(env["TAI_DB"])
    con.execute("DROP TABLE IF EXISTS commands")
    con.execute("CREATE TABLE commands(id INTEGER PRIMARY KEY, cmd TEXT, cwd TEXT)")
    con.commit()
    con.close()
    r = run("doctor", env=env)
    check("an old schema is named before the row count that lies",
          ("out of date" in r.stdout, "every command is being read as zero rows"
           in r.stdout), (True, True))

    print("tai uninstall: the answer when there is nothing to remove")
    env = world("uninstall")
    env = dict(env, HOME=str(pathlib.Path(env["TAI_DB"]).parent),
               TAI_DATA_DIR=str(pathlib.Path(env["TAI_DB"]).parent / "data"),
               BIN_DIR=str(pathlib.Path(env["TAI_DB"]).parent / "bin"))
    r = run("uninstall", env=env)
    check("nothing installed is said plainly and succeeds",
          (r.returncode, r.stdout.strip()), (0, "tai was not installed"))

    print("the argument wall")
    env = world("args")
    r = run(env=env)
    check("bare tai is a usage error",
          r.returncode, 2)
    r = run("nonsense", env=env)
    check("an unknown verb is argparse's error, not a traceback",
          (r.returncode, "invalid choice" in r.stderr), (2, True))

    print("\nOK — every verb answers in words a user can act on."
          if not failures else f"\nFAILED ({len(failures)}): {', '.join(failures)}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
