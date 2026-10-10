"""The spool: the shells' batched write side of the store.

One user command used to be one python process — `tai record` forked after
every line for the life of an install. The spool replaced that with a builtin
append and a batched `tai flush`, so these tests hold the two halves of that
promise: the frame survives everything a person can type, and nothing reaches
the store without passing the same `is_recordable` gate `tai record` applies.
The plugin half of the path — the appends the shells actually make — is
asserted where it runs, in tests/test_plugins_index.py.

    python3 tests/test_spool.py
"""
import os
import pathlib
import sqlite3
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
os.chdir(REPO)

# An isolated world: this suite's database, spool and index are named files
# under /tmp/tai, set before tai is imported, so a developer's exported
# TAI_DB cannot turn an assertion about a three-row store into a question
# about their real history.
DB = "/tmp/tai/tai_test_spool.db"
SPOOL = "/tmp/tai/tai_test_spool.log"
INDEX = "/tmp/tai/tai_test_spool_index.zsh"
os.environ["TAI_DB"] = DB
os.environ["TAI_SPOOL"] = SPOOL
os.environ["TAI_INDEX"] = INDEX
os.environ["TAI_NO_LEARN"] = "1"   # a flush must not probe --help inside a test
os.environ["TAI_NO_AUTO_RECORD"] = "1"
for _suffix in ("", "-wal", "-shm"):
    pathlib.Path(DB + _suffix).unlink(missing_ok=True)
pathlib.Path(SPOOL).unlink(missing_ok=True)
pathlib.Path(INDEX).unlink(missing_ok=True)

from tai.spool import RS, US, drain, flush, spool_append, spool_path  # noqa: E402
from tai.store import MAX_CMD_LEN, count, is_recordable  # noqa: E402

failures: list[str] = []


def check(what: str, got, want) -> None:
    if got == want:
        print(f"  ok   {what}: {got!r}")
    else:
        print(f"  FAIL {what}:\n         got  {got!r}\n         want {want!r}")
        failures.append(what)


def db_rows() -> list[tuple]:
    if not pathlib.Path(DB).exists():
        return []
    con = sqlite3.connect(DB)
    try:
        return con.execute(
            "SELECT cmd,cwd,repo,branch,exit_code,ts FROM commands ORDER BY id"
        ).fetchall()
    except sqlite3.Error:
        return []
    finally:
        con.close()


def main() -> int:
    print("the spool path")
    check("TAI_SPOOL names the spool outright", spool_path(), SPOOL)

    print("an empty or missing spool")
    check("draining nothing is three zeroes and a zero",
          drain(), (0, 0, 0, 0))
    check("asking must not create the spool", pathlib.Path(SPOOL).exists(), False)

    print("one append, one drain, one row")
    spool_append("echo hello", cwd="/tmp", exit_code=0, ts=1700000000)
    check("the frame is on disk before any flush",
          pathlib.Path(SPOOL).exists(), True)
    check("drain reports one row with the newest ts",
          drain(), (1, 0, 1, 1700000000))
    check("the row is what was appended, in the frame's own order",
          db_rows(), [("echo hello", "/tmp", "", "", 0, 1700000000)])
    check("the spool is gone after the drain",
          pathlib.Path(SPOOL).exists(), False)
    check("draining again is a clean no-op", drain(), (0, 0, 0, 0))

    print("the gate is is_recordable, the same one tai record applies")
    spool_append("y", cwd="/tmp", ts=1700000010)
    spool_append("git commit -m \"a\nb\"", cwd="/tmp", ts=1700000011)
    spool_append("curl -H 'token: abc123' https://x", cwd="/tmp", ts=1700000012)
    ingested, skipped, total, _ = drain()
    check("one-key, multiline and secret frames are all skipped",
          (ingested, skipped, total), (0, 3, 0))
    check("and none of them reached the store", db_rows()[-3:] != [
        ("y", "/tmp", "", "", 0, 1700000010)], True)

    print("frames a person can produce")
    os.makedirs("/tmp/tai/tai_test_spool_git", exist_ok=True)
    repo_cwd = str(REPO)          # a real git checkout
    spool_append("taï اَلْعَرَبِيَّة -- verbose ünïcode", cwd=repo_cwd,
                 exit_code=3, ts=1700000020)
    # The store learns at most MAX_CMD_LEN characters: a longer frame is a
    # paste, not vocabulary, and is skipped at the same gate everything else
    # passes. Right at the bound is still a person's command.
    spool_append("ls " + "x" * 995, cwd="/tmp", ts=1700000021)
    spool_append("ls " + "x" * 4000, cwd="/tmp", ts=1700000024)
    # The separators are the write side's own framing, and both transports
    # strip them at the boundary — a command that carries them would tear the
    # frame, and the flush would read half of it as a command never typed.
    spool_append("echo framing\x1fmiddle\x1fend", cwd="/tmp", ts=1700000022)
    spool_append("echo torn\x1e frame", cwd="/tmp", ts=1700000023)
    ingested, skipped, _, _ = drain()
    check("everything recordable survives its own separators",
          (ingested, skipped), (4, 1))
    got = db_rows()[-4:]
    check("unicode round-trips byte for byte",
          got[0][0], "taï اَلْعَرَبِيَّة -- verbose ünïcode")
    check("a command at the bound round-trips whole", len(got[1][0]), 998)
    check("the over-bound paste is not in the store",
          any(r[0].startswith("ls ") and len(r[0]) > MAX_CMD_LEN
              for r in db_rows()), False)
    check("the repo context is resolved from the cwd, in the flush",
          (got[0][2] != "", got[0][3] != ""), (True, True))
    check("a directory without git carries no labels",
          (got[1][2], got[1][3]), ("", ""))
    check("the exit code rode along", got[0][4], 3)
    check("the separators did not ride along",
          (got[2][0], got[3][0]), ("echo framingmiddleend", "echo torn frame"))

    print("a frame corrupted on disk is skipped, never stored half-parsed")
    # Written raw, past the write side's own stripping: what a concurrent
    # writer mid-teardown, a truncated file, or a foreign tool would leave.
    with open(SPOOL, "ab") as f:
        f.write(f"1700000030{US}0{US}/tmp{US}echo clean{RS}".encode())
        f.write(f"1700000031{US}0{US}/tmp{US}echo torn{US}apart{RS}".encode())
        f.write(b"garbage-with-no-separators" + RS.encode())
    ingested, skipped, _, _ = drain()
    check("the clean frame lands; the torn and shapeless ones are skipped",
          (ingested, skipped), (1, 2))
    check("the torn frame's half-command was never stored",
          "echo torn" in [r[0] for r in db_rows()], False)

    print("order and timestamps are the frame's, not the drain's")
    spool_append("echo first", cwd="/tmp", ts=1700000030)
    spool_append("echo second", cwd="/tmp", ts=1700000005)
    drain()
    got = [r[0] for r in db_rows()[-2:]]
    check("insert order is append order, whatever the timestamps say",
          got, ["echo first", "echo second"])
    check("the record's own ts is kept",
          [r[5] for r in db_rows()[-2:]], [1700000030, 1700000005])

    print("concurrent flushes: shells flushing at once lose nothing")
    # The docstring's second fact, held executable: every flush rotates the
    # spool into a rotation file of its own, so overlapping flushes each land
    # the records they rotated and none is lost. A shared rotation name was a
    # lost-record race — os.replace overwrites silently, so the flush that
    # renamed second clobbered the file the first had rotated but not yet
    # read, and CI caught it in the wild: two commands typed back-to-back,
    # and the first was simply gone. Real processes, not threads: the race
    # lives between a rename and an open across processes, which is exactly
    # how the product's shells meet it — several terminals, each asking for a
    # flush at once.
    for round_no in range(4):
        frames = [f"echo race-{round_no}-{i}" for i in range(16)]
        for i, cmd in enumerate(frames):
            spool_append(cmd, cwd="", exit_code=0,
                         ts=1700000100 + round_no * 100 + i)
        procs = [subprocess.Popen(
            [sys.executable, "-S", "-E", "-c",
             "import sys; sys.path.insert(0, '.'); "
             "from tai.spool import drain; print(drain()[0])"],
            stdout=subprocess.PIPE, env=dict(os.environ))
            for _ in range(8)]
        outs = [p.communicate()[0].decode().strip() for p in procs]
        check(f"round {round_no}: every flush process exited clean",
              [p.returncode for p in procs], [0] * len(procs))
        check(f"round {round_no}: the ingested counts sum to the batch",
              sum(int(o) if o.isdigit() else 0 for o in outs), len(frames))
        stored = sorted(r[0] for r in db_rows()
                        if r[0].startswith(f"echo race-{round_no}-"))
        check(f"round {round_no}: every frame landed exactly once",
              stored, sorted(frames))

    print("the CLI verbs")
    env = dict(os.environ)
    r = subprocess.run([sys.executable, "-S", "-E", "tai/cli.py", "flush"],
                       capture_output=True, text=True, env=env)
    check("a flush with nothing pending says so and succeeds",
          (r.returncode, r.stdout.strip()), (0, "✓ nothing pending"))
    spool_append("echo cli-flush-me", cwd="/tmp", ts=1700000040)
    r = subprocess.run([sys.executable, "-S", "-E", "tai/cli.py", "version"],
                       capture_output=True, text=True, env=env)
    check("version does not drain — the spool waits for a flush that wants it",
          pathlib.Path(SPOOL).exists(), True)
    r = subprocess.run([sys.executable, "-S", "-E", "tai/cli.py", "flush"],
                       capture_output=True, text=True, env=env)
    check("the flush ingests and says what it did",
          (r.returncode, r.stdout.strip()), (0, "✓ ingested 1"))
    check("and the row landed", "echo cli-flush-me" in [r0[0] for r0 in db_rows()],
          True)
    spool_append("echo suggest-drains-too", cwd="/tmp", ts=1700000041)
    r = subprocess.run([sys.executable, "-S", "-E", "tai/cli.py", "suggest", "echo"],
                       capture_output=True, text=True, env=env)
    check("any tai command drains first, so the answer is never behind the spool",
          pathlib.Path(SPOOL).exists(), False)
    check("and the drained row is in the store",
          "echo suggest-drains-too" in [r0[0] for r0 in db_rows()], True)

    print("flush() carries the record path's maintenance")
    spool_append("echo maintained", cwd="/tmp", ts=1700000050)
    out = flush()
    check("flush reports what it ingested",
          (out["ingested"], out["learned"] >= 0), (1, True))
    check("the store is where the flush left it",
          count()[0], len(db_rows()))

    print("appends that must refuse")
    check("an empty command is not spooled", spool_append("   "), False)
    check("an empty command wrote nothing",
          pathlib.Path(SPOOL).exists(), False)

    print("where the spool lives when TAI_SPOOL is not set")
    # spool_path reads its answer at call time, so the env can be swapped for
    # one question and put back. The rule under test: the spool follows the
    # database, because a relocated store must relocate its pending records
    # with it, without being told twice.
    _saved_spool = os.environ.get("TAI_SPOOL")
    _saved_db = os.environ.get("TAI_DB")
    try:
        os.environ.pop("TAI_SPOOL", None)
        os.environ["TAI_DB"] = "/tmp/tai/somewhere/else.db"
        check("a TAI_DB with a directory keeps the spool beside it",
              spool_path(), "/tmp/tai/somewhere/spool.log")
        os.environ["TAI_DB"] = "plain-name.db"
        check("a relative TAI_DB makes the spool a relative neighbour",
              spool_path(), "spool.log")
        os.environ.pop("TAI_DB", None)
        from tai.paths import data_dir
        check("and with neither variable it is beside the data directory",
              spool_path(),
              os.path.join(str(data_dir()), "spool.log"))
    finally:
        if _saved_spool is not None:
            os.environ["TAI_SPOOL"] = _saved_spool
        else:
            os.environ.pop("TAI_SPOOL", None)
        os.environ["TAI_DB"] = _saved_db

    print("an append that cannot write refuses, and refuses quietly")
    # A directory named as the spool file: open() raises IsADirectoryError,
    # deterministic on every machine, which is the same OSError a read-only
    # directory or a vanished parent produces.
    os.environ["TAI_SPOOL"] = "/tmp"
    try:
        check("a spool that cannot be opened answers False",
              spool_append("echo nowhere", cwd="/tmp"), False)
    finally:
        os.environ["TAI_SPOOL"] = SPOOL
    check("and the refusal left no spool behind",
          pathlib.Path(SPOOL).exists(), False)

    print("frames that fail before the store ever opens")
    # Both are written raw, past the write side's own guards: what a truncated
    # write, a foreign tool or a runaway paste would leave on disk. The store
    # must never see them.
    with open(SPOOL, "ab") as f:
        f.write(f"1700000060{US}0{US}/tmp{US}echo {'x' * 13000}{RS}".encode())
        f.write(f"not-a-number{US}0{US}/tmp{US}echo late{RS}".encode())
        f.write(f"1700000061{US}zero{US}/tmp{US}echo badcode{RS}".encode())
    ingested, skipped, total, _ = drain()
    check("an oversized frame, a bad timestamp and a bad exit code are skipped",
          (ingested, skipped, total), (0, 3, 0))
    check("none of them reached the store",
          any("echo late" in r[0] or "echo badcode" in r[0] or r[0].startswith("echo x")
              for r in db_rows()), False)
    check("and the spool file itself was consumed", pathlib.Path(SPOOL).exists(), False)

    print("flush() answers before it does any store work")
    out = flush()
    check("a flush with nothing pending is all zeroes and no learning",
          out, {"ingested": 0, "skipped": 0, "total": 0, "newest": 0,
                "learned": 0})

    print("the learning step is bounded and filtered")
    # The real learn() runs --help discovery; the stub records what it was
    # asked, so the assertion is about the batch's own rules: which names are
    # candidates, how many may be asked at once, and when the rebuild fires.
    import tai.cli as spool_cli
    import tai.knowledge as spool_knowledge
    import tai.maintenance as spool_maintenance
    saved = (spool_cli._which, spool_knowledge.learn, spool_knowledge.is_known,
             spool_maintenance._rebuild_quietly, os.environ.get("TAI_NO_LEARN"))
    asked: list[list[str]] = []
    rebuilds: list[int] = []
    try:
        spool_cli._which = lambda name: f"/usr/bin/{name}"
        spool_knowledge.learn = lambda tools=None: (asked.append(list(tools or [])),
                                                    len(tools or []))[1]
        spool_knowledge.is_known = lambda name: name == "known-tool"
        spool_maintenance._rebuild_quietly = lambda: rebuilds.append(1)
        from tai.spool import _learn_from_batch

        os.environ["TAI_NO_LEARN"] = "1"
        check("TAI_NO_LEARN=1 turns the batch learning off",
              _learn_from_batch(["echo hello"]), 0)
        check("and nothing was probed", asked, [])
        os.environ.pop("TAI_NO_LEARN", None)

        check("flags, paths and over-long names are never candidates",
              _learn_from_batch(["-x", "a/b", "n" * 65, "known-tool",
                                 "realtool --now"]),
              1)
        check("only the one candidate that can exist was probed",
              asked, [["realtool"]])

        asked.clear()
        rebuilds.clear()
        n = _learn_from_batch([f"tool{i:02d} --run" for i in range(12)])
        check("a batch pays for at most eight probes", n, 8)
        check("and one rebuild covers them all", rebuilds, [1])

        spool_knowledge.learn = lambda tools=None: (asked.append(list(tools or [])), 0)[1]
        rebuilds.clear()
        check("a probe that teaches nothing rebuilds nothing",
              _learn_from_batch(["realtool --now"]), 0)
        check("no rebuild on a learned-nothing batch", rebuilds, [])
    finally:
        (spool_cli._which, spool_knowledge.learn, spool_knowledge.is_known,
         spool_maintenance._rebuild_quietly, _nl) = saved
        if _nl is not None:
            os.environ["TAI_NO_LEARN"] = _nl
        else:
            os.environ.pop("TAI_NO_LEARN", None)

    print("a git question that cannot even be asked")
    from tai.spool import _git_context
    check("a cwd with a NUL byte answers no labels",
          _git_context("bad\x00dir"), ("", ""))

    print("\nOK — the spool is transport; the store's gate is the only gate."
          if not failures else f"\nFAILED ({len(failures)}): {', '.join(failures)}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
