"""The engine disk cache: marshal round trip, staleness, and every fall-back.

The cache exists so a one-shot process loads instead of rebuilding. Its danger
is exactly that: a loaded engine that answers *differently* from a built one
would change what every user of the predictor says, quietly, forever. So the
round trip is held to answer-identity — the same question, asked of the built
engine and of the loaded one, must produce the same bytes — and every way the
cache can be wrong (stale, torn, foreign, switched off, no store at all) must
land on the built engine rather than on an error.

    python3 tests/test_engcache.py
"""
import marshal
import os
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
os.chdir(REPO)

DB = "/tmp/tai/tai_test_engcache.db"
os.environ["TAI_DB"] = DB
os.environ["TAI_NO_LEARN"] = "1"
os.environ["TAI_SKIP_PATH_CHECK"] = "1"   # liveness is predictor's policy, not the cache's
for _suffix in ("", "-wal", "-shm"):
    pathlib.Path(DB + _suffix).unlink(missing_ok=True)
for _stale in pathlib.Path("/tmp/tai").glob("engine-*.cache"):
    _stale.unlink()

from tai import engcache  # noqa: E402
from tai.engine import Engine  # noqa: E402
from tai.paths import db_path  # noqa: E402
from tai.store import append_and_count  # noqa: E402

failures: list[str] = []


def check(what: str, got, want) -> None:
    if got == want:
        print(f"  ok   {what}: {got!r}")
    else:
        print(f"  FAIL {what}:\n         got  {got!r}\n         want {want!r}")
        failures.append(what)


ROWS = [
    ("git status", "/tmp", "", "", 0, 1_750_000_000),
    ("git status", "/tmp", "", "", 0, 1_750_000_100),
    ("git push --force-with-lease origin main", "/tmp", "", "", 0, 1_750_000_200),
    ("docker compose up -d", "/tmp", "", "", 0, 1_750_000_300),
    ("docker compose down", "/tmp", "", "", 1, 1_750_000_400),
    ("ls -la", "/tmp", "", "", 0, 1_750_000_500),
    ("dokcer ps", "/tmp", "", "", 127, 1_750_000_600),
    ("héllo wörld --flag", "/tmp", "", "", 0, 1_750_000_700),
]
for row in ROWS:
    append_and_count(row[0], cwd=row[1], exit_code=row[4])


def built() -> Engine:
    eng = Engine()
    eng.build_from_rows(__import__("tai.store", fromlist=["load_rows"])
                        .load_rows(20000))
    return eng


QUESTIONS = ["", "g", "gi", "git ", "git s", "git status", "docker ",
             "docker compose ", "docker compose u", "ls -", "l", "héllo ",
             "zzz-nothing", "dokcer ", "git push --force"]


def answers(eng: Engine) -> dict:
    now = 1_760_000_000
    out = {}
    for prefix in QUESTIONS:
        r = eng.suggest(prefix=prefix, limit=4, now_ts=now,
                        last_commands=["ls -la"])
        out[prefix] = (r["choice"], r["completion"], r["choices"],
                       r["probabilities"], r["confidence"], r["count"])
    x = eng.explain("git ", "git status", cwd="/tmp", repo="", branch="",
                    last_commands=["ls -la"], now_ts=now)
    out["explain"] = x
    return out


def test_round_trip_identity() -> None:
    """A loaded engine answers what the built engine answers. Byte for byte."""
    eng = built()
    snap = engcache._snapshot(eng)
    loaded = engcache._load(marshal.dumps(snap, 4))
    check("the loaded engine answers identically",
          answers(loaded), answers(built()))
    # And the shapes the ranker reads between answers are themselves too.
    check("sorted list survives", loaded.sorted_cmds, eng.sorted_cmds)
    check("sequence table survives", loaded.seq, eng.seq)
    check("bigram table survives", loaded.token_bigram, eng.token_bigram)
    check("trigram table survives", loaded.token_trigram, eng.token_trigram)
    check("the loaded engine believes it is sorted", loaded._cmds_dirty, False)
    # A loaded engine still learns, and learning retires its sorted cache.
    loaded.add("git stash list", ts=1_760_000_001, _prev="")
    check("a loaded engine accepts new rows",
          "git stash list" in loaded.cmds, True)
    check("and marks its list dirty", loaded._cmds_dirty, True)


def test_get_paths() -> None:
    """Every way the cache can be wrong lands on the built engine."""
    # Prime it the way a one-shot process does.
    predictor_ns = __import__("tai.predictor", fromlist=["suggest"])
    predictor_ns._E = None
    r = predictor_ns.suggest("git ")
    check("a primed run answers", r["choice"].startswith("git"), True)
    cp = engcache._cache_path(db_path())
    check("the cache is beside the store", cp.parent, db_path().parent)
    check("the cache exists", cp.exists(), True)

    # A current cache answers without a build.
    import tai.predictor as predictor

    def must_not_build():
        raise AssertionError("built with a current cache present")

    predictor._E = None
    real_build = predictor._build_engine
    predictor._build_engine = must_not_build
    try:
        check("a current cache is used, not rebuilt",
              predictor.suggest("git ")["choice"],
              r["choice"])
    finally:
        predictor._build_engine = real_build

    # A torn cache is a miss, not an error — and it is rewritten whole.
    predictor._E = None
    good = cp.read_bytes()
    cp.write_bytes(good[: len(good) // 3])
    rebuilt = predictor.suggest("git ")
    check("a torn cache falls back to the build",
          rebuilt["choice"], r["choice"])
    check("and the torn cache was rewritten", cp.read_bytes(), good)

    # A foreign format (the pickle of an older release) is a miss too.
    predictor._E = None
    cp.write_bytes(b"tai-engine 1 1 1\nnot a marshal payload at all")
    check("a foreign cache falls back", predictor.suggest("git ")["choice"],
          r["choice"])

    # A stale key (the store moved on) is a miss.
    predictor._E = None
    append_and_count("git stash list", cwd="/tmp")
    check("a store that gained a row invalidates the cache",
          predictor.suggest("git stash")["choice"], "git stash list")

    # Opted out, the build runs every time.
    os.environ["TAI_NO_ENGINE_CACHE"] = "1"
    predictor._E = None
    try:
        called = []
        predictor._build_engine = lambda: (called.append(1), real_build())[1]
        predictor.suggest("git ")
        check("TAI_NO_ENGINE_CACHE=1 builds by hand", len(called), 1)
    finally:
        os.environ.pop("TAI_NO_ENGINE_CACHE")
        predictor._build_engine = real_build
        predictor._E = None

    # No store at all: the build still runs, and no cache is written for it.
    os.environ["TAI_DB"] = "/tmp/tai/tai_test_engcache_absent.db"
    predictor._E = None
    cp2 = engcache._cache_path(db_path())
    check("no store, no cache file before",
          cp2.exists(), False)
    predictor.suggest("git ")
    check("no store, no cache file after", cp2.exists(), False)
    os.environ["TAI_DB"] = DB
    predictor._E = None


def test_snapshot_is_plain_data() -> None:
    """The snapshot marshal writes holds no custom objects of its own."""
    eng = built()
    snap = engcache._snapshot(eng)
    names, fields = snap["cmds"]
    check("names are the engine's own", list(names), list(eng.cmds))
    first = eng.cmds[names[0]]
    row = fields[0]
    check("the row is one flat tuple per command", len(row), 9)
    check("the row's fields are the slots, in order",
          (first.freq, first.last_ts, first.cwd, first.repo, first.branch,
           first.hour, first.success, first.fail, first.nf), row)


def main() -> int:
    test_round_trip_identity()
    test_get_paths()
    test_snapshot_is_plain_data()
    print()
    if failures:
        print(f"FAILED — {len(failures)} check(s):")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("OK — engine cache: round-trip identity, staleness, torn, foreign, off")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
