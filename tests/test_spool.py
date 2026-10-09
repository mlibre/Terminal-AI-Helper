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
from tai.store import count, is_recordable  # noqa: E402

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
    spool_append("ls " + "x" * 1500, cwd="/tmp", ts=1700000021)
    # The separators are the write side's own framing, and both transports
    # strip them at the boundary — a command that carries them would tear the
    # frame, and the flush would read half of it as a command never typed.
    spool_append("echo framing\x1fmiddle\x1fend", cwd="/tmp", ts=1700000022)
    spool_append("echo torn\x1e frame", cwd="/tmp", ts=1700000023)
    ingested, skipped, _, _ = drain()
    check("everything recordable survives its own separators",
          (ingested, skipped), (4, 0))
    got = db_rows()[-4:]
    check("unicode round-trips byte for byte",
          got[0][0], "taï اَلْعَرَبِيَّة -- verbose ünïcode")
    check("the long command round-trips", len(got[1][0]), 1503)
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

    print("\nOK — the spool is transport; the store's gate is the only gate."
          if not failures else f"\nFAILED ({len(failures)}): {', '.join(failures)}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
