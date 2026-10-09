"""tai predictor — the one-shot suggest path, over engine + store.

This is where the path-liveness policy is applied to the one-shot path. The
index's stale set is computed the same way from the same engine — recorded
cwd only, because the snapshot is shown in every directory — so the one-shot
path never *offers* what the index already dropped. It may hide one or two
more (judged against the directory it is run in), which is the safe direction
to disagree.
"""
from __future__ import annotations

import os
import time

from tai.engine import WRAPPERS, Engine
from tai.fresh import files
from tai.paths import takes_file
from tai.store import load_rows

_E: Engine | None = None
_STALE: frozenset = frozenset()


def _stale_for(eng: Engine) -> set:
    """Commands whose paths no longer exist, per the engine's own cwd records.

    The one-shot suggestion is shown in the directory the suggest process runs
    in, so the fallback cwd is passed explicitly here — and only here. The
    index builder uses recorded evidence alone, because its snapshot is shown
    in every directory, not just this one.
    """
    from tai.paths import stale_in
    return stale_in(eng, os.getcwd())


def _build_engine() -> Engine:
    """Build the engine from the store, or the seed vocabulary on a broken one.

    A broken store must not become an empty ranking here either, but this path
    answers a question and cannot exit: it says what it could not do and falls
    through to the seed vocabulary, which is the honest answer for a history
    tai has not been able to read.
    """
    eng = Engine()
    try:
        eng.build_from_rows(load_rows(20000))
    except Exception as e:
        from tai.store import schema_note
        import sys
        print(f"tai: cannot read the history database: "
              f"{schema_note() or e}", file=sys.stderr)
    return eng


def suggest(prefix: str = "", cwd: str = "", repo: str = "",
            last_commands: list | None = None, limit: int = 1,
            temp: float | None = None, branch: str = "") -> dict:
    global _E, _STALE
    t0 = time.time()
    # The named constant rather than a copy of its value: `tai tune` searches
    # these, and a literal here meant a tuned temperature changed the engine and
    # not the one-shot path that calls it.
    from tai.engine import TEMP_DEFAULT
    from tai.store import db_path
    if temp is None:
        temp = TEMP_DEFAULT
    if _E is None:
        # A fresh process rebuilds the engine from SQLite unless a disk cache
        # taken by an earlier process is still current — see tai/engcache.py.
        # The path-liveness set is deliberately NOT cached: it answers against
        # the directory this process runs in, which changes call to call.
        from tai.engcache import get
        _E = get(db_path(), _build_engine)
        _STALE = _stale_for(_E)
    res = _E.suggest(prefix=prefix or "", cwd=cwd, repo=repo, branch=branch,
                     last_commands=last_commands or [], limit=limit, temp=temp,
                     exclude=_STALE)
    res = _file_answer(prefix or "", res)
    # The engine measures its own ranking; a caller that only cares about the
    # whole answer wants the process time, and a sub-millisecond rank has no
    # measurable latency of its own.
    if res.get("latency_ms", 0) == 0:
        res["latency_ms"] = round((time.time() - t0) * 1000, 2)
    return res


def explain(prefix: str, cmd: str, cwd: str = "",
            last_commands: list | None = None) -> dict | None:
    """Why `cmd` ranks what it ranks for `prefix` — the dashboard's why.

    Same engine, same path-liveness policy: a command hidden as stale is
    reported as hidden rather than scored, because the honest answer to "why
    don't I see it" is not its arithmetic. Returns None for a line the store
    never recorded.
    """
    global _E, _STALE
    if _E is None:
        from tai.engcache import get
        from tai.store import db_path
        _E = get(db_path(), _build_engine)
        _STALE = _stale_for(_E)
    out = _E.explain(prefix or "", cmd, cwd=cwd,
                     last_commands=last_commands or [])
    if out is None:
        return None
    out["stale"] = cmd in _STALE
    return out


def _file_answer(prefix: str, res: dict) -> dict:
    """A path argument is answered by the filesystem, not by the history.

    The same rule the two shell plugins apply through `tai.fresh`, in the one
    place the one-shot path can afford to stat files. `chmod +x ` on a history
    holding only `chmod +x script` is the case it exists for: `script` is not a
    file here, so it is not an answer, and the file that was just downloaded is.

    Three questions, and every "no" leaves the ranking exactly as it was:

      * is the word being typed a file? The evidence is the history — some line
        the user actually ran has this line's words in front of it and a file
        after them, which is `takes_file` over the engine's own commands. A line
        nothing has been run after is not answered with what happens to be on
        disk, because that is a guess with a file name on it.
      * is what tai learned still a file here? If it is, it wins: it was asked
        for before and it exists, so `cat ~/notes/todo.txt` is not displaced by
        whatever was touched last.
      * is there a file at all? Only then does the filesystem answer.
    """
    if _E is None or not prefix or " " not in prefix:
        return res
    before, _, word = prefix.rpartition(" ")
    key = " ".join(before.split())
    # A wrapper runs the command behind it, so it is transparent to the file too:
    # `sudo cat ` is a path argument exactly as `cat ` is. Both plugins strip it
    # before the lookup, and a rule that changed in one and not the others is how
    # the paths drift apart.
    head = key.split(" ", 1)[0]
    if head in WRAPPERS:
        key = key.split(" ", 1)[1] if " " in key else ""
    if not key:
        return res
    evidence = any(takes_file(cmd) for cmd in _E.cmds
                   if cmd.startswith(key + " "))
    if not evidence:
        return res
    learned = res.get("choice") or ""
    last = learned[len(key) + 1:] if learned.startswith(key + " ") else ""
    if last.startswith(word) and last and os.path.exists(
            os.path.expanduser(last)):
        return res
    # A completion has to extend the word being typed. A glob match does not
    # always: `chmod +x freeb` matches `~/Downloads/freebuff…`, but that name
    # does not start with the word, so it cannot replace the letters typed.
    found = [name for name in files(word) if name.startswith(word)]
    if not found:
        return res
    # A file is a *word*, so the answer replaces the word being typed rather than
    # being appended after it — appending is right for `chmod +x ` and wrong for
    # `chmod +x ~/Downloads/f`, which would come out as `…/f~/Downloads/freebuff…`.
    join = prefix if prefix.endswith(" ") else before + " "
    choice = f"{join}{found[0]}"
    out = dict(res)
    out["choice"] = choice
    out["completion"] = choice[len(prefix):]
    out["probabilities"] = {choice: 1.0}
    out["choices"] = [{"cmd": choice, "prob": 1.0, "score": 0.0}]
    out["confidence"] = max(out.get("confidence", 0.0), 0.5)
    out["from_disk"] = found[:8]
    return out
