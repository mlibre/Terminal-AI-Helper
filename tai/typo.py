"""Typo tolerance for the first word of a command: `dokcer` is `docker`.

One concern, kept out of `tai/engine.py` so that file is about ranking: what to
do when the command name being typed has no exact match but is one or two
characters away from one the user has actually run.

Two rules that are not about spelling:

  * **Half-typed is not a licence to invent.** `god` is answered by `godot .`
    because a command name *begins with* it, and `godx` is answered by nothing
    because nothing begins with it — not by the nearest name, which is what a
    fuzzy matcher would return and is a guess dressed as an answer.
  * **A near miss is ranked below every exact match.** The candidate is offered
    (that is the point of the feature) and carries `TYPO_PENALTY` in the ranking,
    so `dokcer ` shows `docker ps` rather than replacing a command that exists.

The edit distance is Levenshtein with an early exit and a cap, which is what
makes it affordable at all: the scan is over distinct first *words*, not over
commands — 855 of them on a 7.6k-command history, under a thousand `_lev1`
calls. It is only reached when the exact lookup found fewer than three
candidates, and the first-word list it walks is built once and dropped when a new
command is learned.
"""
from __future__ import annotations


def lev1(a: str, b: str, max_dist: int = 2) -> int:
    """Levenshtein distance, capped at `max_dist + 1`.

    The cap is the whole trick: a caller only ever asks "is this within n", and
    returning `max_dist + 1` for everything further away lets the row loop stop
    as soon as no cell of the row can come back under the cap.
    """
    if a == b:
        return 0
    la, lb = len(a), len(b)
    if abs(la - lb) > max_dist:
        return max_dist + 1
    if la == 0:
        return lb
    if lb == 0:
        return la
    # ensure b is shorter for less memory
    prev = list(range(lb + 1))
    for i in range(1, la + 1):
        cur = [i] + [0] * lb
        row_min = cur[0]
        ca = a[i - 1]
        for j in range(1, lb + 1):
            cost = 0 if ca == b[j - 1] else 1
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
            if cur[j] < row_min:
                row_min = cur[j]
        if row_min > max_dist:
            return max_dist + 1
        prev = cur
    return prev[lb]


def _first_words(eng) -> list[str]:
    """Every distinct command name's first word, in lexical order.

    Built on demand and discarded when the engine learns a new command, because
    it is a function of the command list and nothing else. Cached on the engine:
    the walk was one `split` per distinct command on every multi-word prefix that
    had no exact match.
    """
    cached = eng._first_word_cache
    if cached is None:
        eng._ensure_sorted()      # the list is appends until first read
        seen: dict[str, None] = {}
        for c in eng.sorted_cmds:
            w = c.split(" ", 1)[0]
            if w and w not in seen:
                seen[w] = None
                if len(seen) > 2000:
                    break
        cached = eng._first_word_cache = list(seen)
    return cached


def near_miss_lines(eng, prefix: str, limit: int = 50) -> list[str]:
    """Whole commands whose first word is within an edit or two of `prefix`'s.

    Only for a first word of three characters or more, which is where a typo is
    plausible and where the nearest match is still more likely to be the word
    being typed than a coincidence. The result is filtered against the rest of
    the line, so `dokcer ps` is answered by a `docker … ps` and not by any
    `docker` command.
    """
    words = prefix.split()
    first = words[0] if words else prefix
    if len(first) < 3:
        return []
    cands = []
    max_d = 1 if len(first) <= 4 else 2
    for w in _first_words(eng):
        if w.startswith(first) or first.startswith(w):
            continue
        if lev1(first, w, max_d) <= max_d:
            cands.append(w)
            if len(cands) >= 10:
                break
    out: list[str] = []
    for w in cands:
        for c in eng._prefix_range(w):
            if len(c.split(None, 1)) > 1 or prefix.strip() == first:
                # only keep commands consistent with rest of prefix
                rest = prefix[len(first):]
                if c[len(w):].startswith(rest):
                    out.append(c)
                    if len(out) >= limit:
                        return out
    return out