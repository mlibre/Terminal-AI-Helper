"""tai bench — embedded index + direct engine diagnostics. Stdlib only."""
from __future__ import annotations

import shutil
import time


def _rss_kb() -> int | None:
    """Resident memory in kB, or None where /proc does not say.

    A diagnostic that prints 0 when it cannot read the number is worse than one
    that prints nothing: 0kB is a measurement, and it is wrong.
    """
    try:
        with open("/proc/self/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1])
    except (OSError, ValueError):
        return None
    return None


def _load(limit: int):
    from tai.engine import Engine
    from tai.store import load_rows, count, schema_note
    t0 = time.time()
    # A store that cannot be read is a fact to report, not a reason to measure
    # an empty engine: `tai bench` exists to tell the user what is going on.
    try:
        rows = load_rows(limit)
    except Exception as e:
        note = schema_note() or str(e)
        print(f"cannot read the history database: {note}")
        raise SystemExit(1) from e
    eng = Engine()
    eng.build_from_rows(rows)
    return eng, rows, (time.time() - t0) * 1000, *count()


def main(limit: int = 5000) -> None:
    from tai.store import db_path
    from tai.index import bash_index_path, index_path
    print(f"db: {db_path()}")
    eng, rows, load_ms, n, d = _load(limit)
    rss = _rss_kb()
    print(f"rows={n} distinct={d} loaded={len(rows)} in {load_ms:.0f}ms"
          f" rss={rss if rss else '?'}kB")
    prefixes = ["", "g", "gi", "git ", "git p", "docker ", "docker c", "d", "npm ", "python3 "]
    lat = []
    for _ in range(5):
        for prefix in prefixes:
            t0 = time.time()
            eng.suggest(prefix=prefix, limit=1)
            lat.append((time.time() - t0) * 1000)
    lat.sort()
    print(f"engine suggest: p50={lat[len(lat)//2]:.2f}ms p95={lat[int(len(lat)*.95)-1]:.2f}ms")
    print(f"embedded indexes: zsh={index_path().exists()} "
          f"bash={bash_index_path().exists()}")
    _report_source_time(index_path(), bash_index_path())


def _report_source_time(zsh_index: Path, bash_index: Path) -> None:
    """How long each shell takes to read the index — the startup cost itself.

    Every other number here is Python: the load, the build, a suggest. Those
    answer "is the engine slow", and the engine is not on the keystroke path —
    the shell is, and it reads this file once per shell start, which is why its
    *size* is the cost that matters. So the one measurement the design turns on
    is the one this could not make: a shell started for the purpose, sourcing
    its own index and nothing else, timed with the start-up inside the number so
    it cannot be read as cheaper than it is.
    """
    import subprocess

    for label, path, argv in (("zsh", zsh_index, ["zsh", "-f", "-c"]),
                              ("bash", bash_index, ["bash", "--noprofile", "--norc",
                                                    "-c"])):
        if not path.exists() or not shutil.which(argv[0]):
            continue
        best = None
        for _ in range(3):        # the first start pays for the page cache
            t0 = time.time()
            got = subprocess.run([*argv, f"source {path}"], capture_output=True,
                                 timeout=60)
            took = (time.time() - t0) * 1000
            if got.returncode != 0:
                print(f"{label} index: not readable — "
                      f"{got.stderr.decode(errors='replace').strip()[:80]}")
                best = None
                break
            best = took if best is None else min(best, took)
        if best is not None:
            print(f"{label} index source: {best:.0f}ms "
                  f"({path.stat().st_size // 1024}kB, per shell start)")


def doctor() -> None:
    from tai.store import db_path, count, schema_note
    from tai.index import bash_index_path, index_path
    from tai.predictor import suggest
    # First, because "rows=0" is what every reader reports for a broken store as
    # well as for an empty one, and the two need opposite advice.
    broken = schema_note()
    if broken:
        print(f"tai: {broken} — every command is being read as zero rows")
    n, d = count()
    z = index_path()
    print(f"db: {db_path()} rows={n} distinct={d}")
    print(f"embedded indexes: zsh={z.exists()} bash={bash_index_path().exists()}")
    if not z.exists():
        print("run: tai refresh")
    _report_stale(d)
    t0 = time.time()
    r = suggest(prefix="docker ", limit=1)
    print(f"suggest e2e={(time.time()-t0)*1000:.1f}ms -> {r.get('choice','')!r}")


def _report_stale(distinct: int) -> None:
    """Say how many stored commands point at paths that are gone.

    Silence here is the failure mode this guards: an index that quietly omits
    half the history looks identical to a working one until you notice the
    suggestion you expected is not there. The verdict itself is `stale_in`, the
    one place the policy lives — a second copy of that walk is how the report and
    the index would come to disagree about which commands are hidden.
    """
    import os

    from tai.engine import Engine
    from tai.paths import enabled, stale_in
    from tai.store import load_rows
    if not enabled():
        print("path check: disabled by TAI_SKIP_PATH_CHECK=1")
        return
    try:
        eng = Engine()
        eng.build_from_rows(load_rows(20000))
        stale = stale_in(eng, os.getcwd())
    except Exception as e:
        # A store that cannot be read is reported, not counted as "no stale
        # paths" — the same empty result that means a healthy install.
        print(f"stale paths: not checked ({e})")
        return
    if stale:
        sample = " ".join(_preview(cmd) for cmd in sorted(stale)[:3])
        print(f"stale paths: {len(stale)} of {distinct} commands are not suggested "
              f"({sample}) — run 'tai purge --stale' to drop them")
        if len(stale) >= distinct:
            # All of them. The index builder refuses in this state rather than
            # replacing what the user learned with the seed corpus, so `tai
            # refresh` is about to start failing and the reason has to be here,
            # where somebody is already asking what is wrong.
            print("…which is all of them: nothing is indexable until those are "
                  "dropped")
    else:
        print("stale paths: none")


def _preview(cmd: str, width: int = 40) -> str:
    """One bounded line for a command, whatever it is.

    A stored command is whatever the user pasted, and a paste can be a
    kilobyte of prose on several lines: printing three of those in full put a
    wall of text in the middle of a diagnostic whose whole job is to be read at
    a glance. Newlines become spaces — a row of this report has to stay a row —
    and anything past `width` is dropped, with the ellipsis saying so.
    """
    flat = " ".join((cmd or "").split())
    if len(flat) > width:
        flat = flat[:width - 1].rstrip() + "…"
    return f'"{flat}"' if flat else '""'
