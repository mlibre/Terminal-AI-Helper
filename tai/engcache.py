"""A disk cache of a built engine, keyed on the store it was built from.

`tai suggest`, `tai jev` and `tai web` each start a fresh process, and until
now each one rebuilt the whole engine from SQLite before it could answer — a
hundred milliseconds or more of pure startup on a real history, paid on every
question. The engine is plain data, so it caches: the first process after a
change builds and saves, every process after that loads in a few milliseconds.

The format is `marshal`, not pickle, and the reason is measured, not
taste: the engine is thousands of small dicts and short strings, and unpickling
them ran 23ms on a 20k-row history where marshal of the same shape runs 13ms —
marshal's C loader skips pickle's per-object constructor dispatch and its memo
bookkeeping, which is exactly the work a dict-of-dicts makes it do most. What
is stored is the engine's own state, in its own shapes: `cmds` as two parallel
lists (names, and one flat row of fields per name, because a tuple per name
unpacks faster than a dict per name reads), the three learned tables as the
dicts they are, and the sorted command list with its dirty flag already
squared away, so a loaded engine reads as sorted without re-sorting.

The key is the database file's own (size, mtime_ns) plus a format version, so a
new row invalidates the cache within the same nanosecond the write lands, and a
shape change in engine.py invalidates it for every install at once. Everything
is best-effort: an unreadable cache, a read-only data directory or a mid-write
crash all fall back to building, which is the answer the cache exists to
accelerate, never to replace. TAI_NO_ENGINE_CACHE=1 turns it off.
"""
from __future__ import annotations

import marshal
import os

# `pathlib` is deliberately not imported at module level: the one-shot suggest
# loads this module on its warm path, and a warm cache reads no `Path` — only
# `_cache_path`'s Path-in-Path-out contract for tests and tools that pass one
# needs the type, and that import happens on the first call that wants it.

# Bump when the cached shape changes (new fields on CmdStats, new maps, a new
# serialization). 2: CmdStats gained `nf`. 3: pickle gave way to marshal, and
# a pickle cache of any version is simply a miss to the marshal reader.
# 4: the engine gained the lowered mirror the case-insensitive prefix walk
# runs over (engine.sorted_lower); a version-3 cache would hand back an engine
# without it, and the first prefix query would AttributeError.
VERSION = 4

_MAGIC = "tai-engine-m"


def _cache_path(db) -> "Path | str":
    # Beside the database, named for it: a scratch TAI_DB in a test cannot
    # stamp a cache over the developer's real one, and `tai uninstall` (which
    # removes the whole data directory) removes the cache with everything else.
    # A `Path` in, a `Path` out; a string in, a string out — the one-shot path
    # passes the string form so a warm process imports no pathlib at all.
    import hashlib

    dsp = os.fspath(db)
    name = hashlib.sha1(dsp.encode("utf-8", "replace")).hexdigest()[:16]
    out = os.path.join(os.path.dirname(dsp), f"engine-{name}.cache")
    if type(db) is str:
        return out
    from pathlib import Path
    return Path(out)


def _sibling(cp, name: str):
    """`name` in the directory `cp` lives in, in `cp`'s own kind."""
    out = os.path.join(os.path.dirname(os.fspath(cp)), name)
    if type(cp) is str:
        return out
    from pathlib import Path
    return Path(out)


def get(db_path, build) -> object:
    """The built engine for `db_path`, loading a cached one when it is current.

    `db_path` may be a `Path` or a plain string — the one-shot path passes the
    string form (`tai.paths.db_location`) so a warm process never imports
    pathlib. `build` is called with no arguments and must return the engine;
    it runs at most once per process, and only when the cache is missing or
    stale.
    """
    if os.environ.get("TAI_NO_ENGINE_CACHE") == "1":
        return build()
    dsp = os.fspath(db_path)
    try:
        st = os.stat(dsp)
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
                return _load(f.read())
    except Exception:
        pass                    # torn, stale, or foreign: build instead
    eng = build()
    try:
        tmp = _sibling(cp, (cp.name if type(cp) is not str
                            else os.path.basename(cp)) + f".tmp{os.getpid()}")
        with open(tmp, "wb") as f:
            f.write(f"{_MAGIC} {VERSION} {key[0]} {key[1]}\n".encode("ascii"))
            marshal.dump(_snapshot(eng), f, 4)
        os.replace(os.fspath(tmp), os.fspath(cp))
    except Exception:
        pass                    # a read-only or vanished directory is fine
    return eng


# -- the snapshot ---------------------------------------------------------
#
# Two functions, and the round trip through them has to be invisible: the
# fuzz in tests/test_engcache.py answers the same question of the loaded
# engine that the built one answers, for every prefix shape the suggest
# path is asked, on a seeded history. The shapes below are the engine's,
# documented here once:
#
#   cmds   → two parallel lists: the names, and per name one flat row
#            (freq, last_ts, cwd, repo, branch, hour, success, fail, nf).
#            A flat row keeps the reader to one tuple unpack per command;
#            the dicts inside stay themselves, because they *are* the
#            engine's own and marshal moves them at C speed.
#   seq / token_bigram / token_trigram → as they are.
#   sorted_cmds → as it is; the snapshot is taken after `_ensure_sorted`,
#            so the flag comes back False and nothing re-sorts.
#   sorted_lower → as it is, for the same reason: it is built in
#            _ensure_sorted beside sorted_cmds, and a loaded engine that had
#            to rebuild it would pay the sort twice.

# CmdStats' slots, in the order the row above packs them.
_FIELDS = ("freq", "last_ts", "cwd", "repo", "branch", "hour",
           "success", "fail", "nf")


def _snapshot(eng) -> dict:
    eng._ensure_sorted()
    names = list(eng.cmds)
    fields = []
    append = fields.append
    for c in names:
        st = eng.cmds[c]
        append((st.freq, st.last_ts, st.cwd, st.repo, st.branch, st.hour,
                st.success, st.fail, st.nf))
    return {"cmds": (names, fields), "seq": eng.seq,
            "big": eng.token_bigram, "tri": eng.token_trigram,
            "sorted": eng.sorted_cmds, "lower": eng.sorted_lower}


def _load(blob: bytes):
    from tai.engine import CmdStats, Engine

    d = marshal.loads(blob)
    names, fields = d["cmds"]
    eng = Engine.__new__(Engine)
    cmds = {}
    setitem = cmds.__setitem__
    mk = CmdStats
    for c, row in zip(names, fields):
        st = mk.__new__(mk)
        (st.freq, st.last_ts, st.cwd, st.repo, st.branch, st.hour,
         st.success, st.fail, st.nf) = row
        setitem(c, st)
    eng.cmds = cmds
    eng.seq = d["seq"]
    eng.token_bigram = d["big"]
    eng.token_trigram = d["tri"]
    eng.sorted_cmds = d["sorted"]
    eng.sorted_lower = d["lower"]
    eng._prev_cmd = None
    eng._first_word_cache = None
    eng._cmds_dirty = False
    return eng
