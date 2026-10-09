"""A disk cache of a built engine, keyed on the store it was built from.

`tai suggest`, `tai jev` and `tai web` each start a fresh process, and until
now each one rebuilt the whole engine from SQLite before it could answer — a
hundred milliseconds or more of pure startup on a real history, paid on every
question. The engine is plain data, so it pickles: the first process after a
change builds and saves, every process after that loads in a few milliseconds.

The key is the database file's own (size, mtime_ns) plus a format version, so a
new row invalidates the cache within the same nanosecond the write lands, and a
shape change in engine.py invalidates it for every install at once. Everything
is best-effort: an unreadable cache, a read-only data directory or a mid-write
crash all fall back to building, which is the answer the cache exists to
accelerate, never to replace. TAI_NO_ENGINE_CACHE=1 turns it off.
"""
from __future__ import annotations

import os
import pickle
from pathlib import Path

# Bump when Engine's pickled shape changes (new fields on CmdStats, new maps).
# 2: CmdStats gained `nf` (command-not-found runs) — an engine unpickled from
# a version-1 cache has no `.nf` and every ranking that asks would raise.
VERSION = 2

_MAGIC = "tai-engine"


def _cache_path(db: Path) -> Path:
    # Beside the database, named for it: a scratch TAI_DB in a test cannot
    # stamp a cache over the developer's real one, and `tai uninstall` (which
    # removes the whole data directory) removes the cache with everything else.
    import hashlib

    name = hashlib.sha1(str(db).encode("utf-8", "replace")).hexdigest()[:16]
    return db.parent / f"engine-{name}.cache"


def get(db_path: Path, build) -> object:
    """The built engine for `db_path`, loading a cached one when it is current.

    `build` is called with no arguments and must return the engine; it runs at
    most once per process, and only when the cache is missing or stale.
    """
    if os.environ.get("TAI_NO_ENGINE_CACHE") == "1":
        return build()
    try:
        st = db_path.stat()
    except OSError:
        return build()          # no store yet: nothing to cache against
    key = (st.st_size, st.st_mtime_ns)
    cp = _cache_path(db_path)
    try:
        with open(cp, "rb") as f:
            header = f.readline().decode("ascii", "replace").split()
            if (len(header) == 4 and header[0] == _MAGIC
                    and int(header[1]) == VERSION
                    and (int(header[2]), int(header[3])) == key):
                return pickle.load(f)
    except Exception:
        pass                    # torn, stale, or foreign: build instead
    eng = build()
    try:
        tmp = cp.with_name(cp.name + f".tmp{os.getpid()}")
        with open(tmp, "wb") as f:
            f.write(f"{_MAGIC} {VERSION} {key[0]} {key[1]}\n".encode("ascii"))
            pickle.dump(eng, f, protocol=4)
        os.replace(tmp, cp)
    except Exception:
        pass                    # a read-only or vanished directory is fine
    return eng
