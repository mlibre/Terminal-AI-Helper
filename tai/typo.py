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


# A one-off that nearly duplicates a stronger line is a typo of it, and it is
# demoted just below that line instead of ranking beside it. `TYPO_FREQ_MAX`
# bounds how rare the shadow may be: a line the user has run three times is
# a habit, even when a twin runs more often, and habits are not demoted.
TYPO_FREQ_MAX = 2


def shadow_map(eng, lines=None) -> dict[str, str]:
    """Rare lines that are two edits from a stronger line, mapped to it.

    The report that shaped this: `tai unsintall`, `tai unisntall` and
    `tai uninstall` each recorded once, within the same second, every row
    exit 0 — frequency, recency and success all tied, the score tie fell to
    lexical order, and the ghost offered `tai unsintall` for `tai un`. The
    old rule (one Damerau edit, target three times more frequent) covered
    none of it. This one covers all of it:

      * the shadow ran at most `TYPO_FREQ_MAX` times — rarer than its target;
      * the two share their first word and are within two edits whole-line,
        which is the shape a mistyped word takes (`unsintall`/`uninstall` is
        two adjacent swaps, invisible to a one-edit rule);
      * the target is the *stronger* spelling: more successes, then more
        runs, then more recent — and when the evidence ties outright, the
        lexically smallest of the near-duplicates stands, which is the one
        honest way to break a tie the store carries no other evidence for;
      * the population both sides come from is `lines`: the index passes the
        lines the user actually ran (a corpus convention is not a typo and
        not a habit one shadows), the engine passes the candidates it is
        ranking, which keeps the pairwise search proportional to the
        question instead of to the whole history.

    The result feeds both rankers — the index applies it to the integer
    scores the plugins read, the engine to the floats `tai suggest` answers
    with — so neither can disagree with the other about which spelling is
    the real one.
    """
    names = list(eng.cmds) if lines is None else \
        [c for c in lines if c in eng.cmds]
    # Per-line letter sets: the cheap gate before the edit distance. Two
    # lines within two edits can differ by at most two distinct letters per
    # side, and two set differences answer that at C speed — which is what
    # keeps a pairwise search over a real history from turning into a walk.
    # Sound, not just fast: every edit touches at most one letter, so a pair
    # the gate rejects is a pair the distance would reject too.
    sets = {c: set(c) for c in names}
    groups: dict[str, dict[int, list[str]]] = {}
    for c in names:
        groups.setdefault(c.split(" ", 1)[0], {}).setdefault(len(c), []).append(c)
    out: dict[str, str] = {}
    for cmd in names:
        st = eng.cmds[cmd]
        if st.freq > TYPO_FREQ_MAX:
            continue
        buckets = groups.get(cmd.split(" ", 1)[0])
        if not buckets:
            continue
        s_set = sets[cmd]
        ev = (st.success, st.freq, st.last_ts)
        best: tuple[tuple[int, int, int], str] | None = None
        for ln in range(len(cmd) - 2, len(cmd) + 3):
            for other in buckets.get(ln, ()):
                if other == cmd:
                    continue
                o_set = sets[other]
                if len(o_set - s_set) > 2 or len(s_set - o_set) > 2:
                    continue
                ost = eng.cmds[other]
                oev = (ost.success, ost.freq, ost.last_ts)
                # The target must be the stronger spelling — or, on a full
                # tie, the smaller one. Anything weaker is somebody else's
                # shadow, not this line's habit.
                if oev < ev or (oev == ev and other > cmd):
                    continue
                if lev1(cmd, other, 2) > 2:
                    continue
                if best is None or oev > best[0] or \
                        (oev == best[0] and other < best[1]):
                    best = (oev, other)
        if best is not None:
            out[cmd] = best[1]
    return out