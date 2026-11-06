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
from tai.paths import (MISSING, alive_in, command_verdict, db_location,
                       enabled, takes_file)

_E: Engine | None = None
_STALE: "LazyStale | frozenset" = frozenset()

# Is this command's first word installed? Asked once per word per process and
# kept here, because PATH does not change under a running process in any way
# worth re-statting per question. The engine itself never touches the
# filesystem — it is a sub-millisecond ranker — so the installed question is
# answered here, at the one place that can afford a `which`, and handed to the
# engine as a callable. A cold answer stats every PATH directory for the word;
# a warm one is a dict lookup, which is what the scored loop pays.
_WHICH: dict[str, bool] = {}


def _path_lookup(word: str) -> bool:
    """`shutil.which(word) is not None`, answered without importing shutil.

    shutil was 5ms of a warm one-shot suggest — and it dragged re, fnmatch,
    bz2 and lzma in behind it — for a PATH scan this module can do with os
    alone. The walk is shutil.which's own, POSIX branch, line for line: a
    word with a directory part is looked up directly; an empty PATH element
    means the current directory; a hit must exist, be executable, and not be
    a directory. Only the Windows PATHEXT machinery is absent — the plugin
    and the installer are Linux-only anyway.
    """
    dirname, name = os.path.split(word)
    if dirname:
        dirs = [dirname]
    else:
        path = os.environ.get("PATH")
        if path is None:
            try:
                path = os.confstr("CS_PATH")
            except (AttributeError, ValueError):
                path = os.defpath
        if not path:
            return False
        dirs = os.fsdecode(path).split(os.pathsep)
    seen: set[str] = set()
    for d in dirs:
        norm = os.path.normcase(d)
        if norm in seen:
            continue
        seen.add(norm)
        cand = os.path.join(d, name)
        if (os.path.exists(cand) and os.access(cand, os.F_OK | os.X_OK)
                and not os.path.isdir(cand)):
            return True
    return False


def _on_path(word: str) -> bool:
    """Whether `word` resolves to an executable on this machine's PATH."""
    hit = _WHICH.get(word)
    if hit is None:
        hit = _WHICH[word] = _path_lookup(word)
    return hit


class LazyStale:
    """The engine's stale set, asked lazily and remembered per command.

    `paths.stale_in` walks every distinct command's every token up front, and
    the one-shot suggest paid that whole walk — measured at more than half of
    its process on a 20k-row history — before ranking a handful of candidates
    for one prefix. The verdict for one command is a pure function of the
    command, the directories it was recorded in, and the directory the answer
    is shown in, so it can be asked per candidate instead: the engine consults
    `exclude` for exactly the names it would have filtered, and only those
    verdicts are ever computed. For `tai suggest git ` that is the ~300
    candidates behind the prefix instead of every command in the store; for an
    empty prefix it is the same walk, deferred.

    The full walk is still available — `materialize`, for the one reader that
    counts the set, the dashboard's state panel. Behaves like a set for every
    question the engine and the web page ask: `in`, `len`, truthiness.

    The evidence rule is `stale_commands`' own, verbatim: a command is stale
    when any of its path-like tokens is MISSING, judged from the directories
    it was recorded in plus the one fallback directory (the cwd the answer is
    shown in, appended only when the records do not already have it), and
    `TAI_SKIP_PATH_CHECK=1` turns the whole question off.
    """

    __slots__ = ("_eng", "_cwd", "_memo")

    def __init__(self, eng: Engine, cwd: str = ""):
        self._eng = eng
        self._cwd = cwd or ""
        self._memo: dict[str, bool] = {}

    def _ask(self, cmd: str) -> bool:
        hit = self._memo.get(cmd)
        if hit is None:
            if not enabled():
                return False
            st = self._eng.cmds.get(cmd)
            cwds = list(st.cwd) if st is not None else []
            if self._cwd and self._cwd not in cwds:
                cwds.append(self._cwd)
            hit = self._memo[cmd] = command_verdict(cmd, cwds) == MISSING
        return hit

    def __call__(self, cmd: str) -> bool:
        return self._ask(cmd)

    def __contains__(self, cmd: str) -> bool:
        return self._ask(cmd)

    def __bool__(self) -> bool:
        # Truthy so the engine always consults the verdicts; an all-alive
        # history filters to exactly what it filtered when the walk was eager.
        return True

    def __len__(self) -> int:
        self.materialize()
        return sum(1 for v in self._memo.values() if v)

    def materialize(self) -> "LazyStale":
        """Ask every command in the engine once, so `len` is the full count."""
        for cmd in self._eng.cmds:
            self._ask(cmd)
        return self


def _stale_for(eng: Engine) -> LazyStale:
    """The stale question for this engine, asked lazily.

    The one-shot suggestion is shown in the directory the suggest process runs
    in, so the fallback cwd is passed explicitly here — and only here. The
    index builder uses recorded evidence alone, because its snapshot is shown
    in every directory, not just this one.
    """
    return LazyStale(eng, os.getcwd())


def _build_engine() -> Engine:
    """Build the engine from the store, or the seed vocabulary on a broken one.

    A broken store must not become an empty ranking here either, but this path
    answers a question and cannot exit: it says what it could not do and falls
    through to the seed vocabulary, which is the honest answer for a history
    tai has not been able to read.
    """
    eng = Engine()
    try:
        # Imported here, not at the top: a warm engine cache answers without
        # the store, and this import is sqlite3 and re for every process that
        # never reads either.
        from tai.store import load_rows
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
    if temp is None:
        temp = TEMP_DEFAULT
    if _E is None:
        # A fresh process rebuilds the engine from SQLite unless a disk cache
        # taken by an earlier process is still current — see tai/engcache.py.
        # The path-liveness set is deliberately NOT cached across processes: it
        # answers against the directory this process runs in, which changes
        # call to call. The store is named as a plain string so a warm cache
        # imports no pathlib, no sqlite3 and no re — see tai.paths.db_location.
        from tai.engcache import get
        _E = get(db_location(), _build_engine)
        _STALE = _stale_for(_E)
    res = _E.suggest(prefix=prefix or "", cwd=cwd, repo=repo, branch=branch,
                     last_commands=last_commands or [], limit=limit, temp=temp,
                     exclude=_STALE, on_path=_on_path)
    # The stale set above never hides a relative path that was true where it
    # was recorded — which is most of them, and that is right for a snapshot
    # shown in every directory. This answer is shown in one directory, so its
    # lines are judged there too: a line whose relative path does not resolve
    # where the answer will be taken is not an answer, and Enter on it is the
    # error the user reads. The same rule the index writes into _TAI_LIVE for
    # the shells, from the same module, so the rooms cannot disagree about
    # what works where the user stands.
    _where = cwd or os.getcwd()
    _all = res.get("choices", [])
    _live = [c for c in _all if alive_in(c["cmd"], _where)]
    if len(_live) != len(_all):
        res["choices"] = _live
        res["probabilities"] = {c["cmd"]: c["prob"] for c in _live}
        _best = _live[0]["cmd"] if _live else ""
        res["choice"] = _best
        res["completion"] = (_best[len(prefix or ""):]
                             if _best.lower().startswith((prefix or "").lower())
                             else "")
    res = _file_answer(prefix or "", res)
    # The engine measures its own ranking; a caller that only cares about the
    # whole answer wants the process time, and a sub-millisecond rank has no
    # measurable latency of its own.
    if res.get("latency_ms", 0) == 0:
        res["latency_ms"] = round((time.time() - t0) * 1000, 2)
    return res


def explain(prefix: str, cmd: str, cwd: str = "", repo: str = "",
            branch: str = "", last_commands: list | None = None) -> dict | None:
    """Why `cmd` ranks what it ranks for `prefix` — the dashboard's why.

    Same engine, same path-liveness policy: a command hidden as stale is
    reported as hidden rather than scored, because the honest answer to "why
    don't I see it" is not its arithmetic. repo and branch ride along like
    suggest's do — the shell asks every question with the git context of the
    directory it runs in, and an explain that dropped them would score the
    repository and branch factors at zero for a question the prompt would
    have scored. Returns None for a line the store never recorded.
    """
    global _E, _STALE
    if _E is None:
        from tai.engcache import get
        _E = get(db_location(), _build_engine)
        _STALE = _stale_for(_E)
    out = _E.explain(prefix or "", cmd, cwd=cwd, repo=repo, branch=branch,
                     last_commands=last_commands or [], on_path=_on_path)
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
    # the paths drift apart. The typed form is kept beside the stripped one,
    # because the engine's own answer for a wrapped line carries the wrapper
    # back on it — the learned answer is read against the line as the user
    # typed it, or `sudo cat notes.txt` would never start with the bare `cat`
    # the lookup ran behind, and a live learned file would lose to whatever
    # touched the directory last.
    typed = key
    head = key.split(" ", 1)[0]
    if head in WRAPPERS:
        key = key.split(" ", 1)[1] if " " in key else ""
        typed = f"{head} {key}" if key else ""
    if not key:
        return res
    evidence = any(takes_file(cmd) for cmd in _E.cmds
                   if cmd.startswith(key + " "))
    if not evidence:
        return res
    learned = res.get("choice") or ""
    last = learned[len(typed) + 1:] if learned.startswith(typed + " ") else ""
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
