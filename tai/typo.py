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

    Three cuts keep the walk small, and all three are exact — they never change
    a distance that survives them:

      * the longer and shorter are swapped, so the band below covers the wider
        side;
      * the shared prefix and suffix are peeled first — the edits of every
        near-duplicate live in the middle, and the peel is a slice, not a
        cell;
      * the DP walks only the diagonal band `|i - j| <= max_dist`. Every edit
        moves one step off the diagonal, so a path leaving the band is longer
        than `max_dist` edits long — those cells are a sentinel, not a number,
        and the row's minimum over the band alone is still the row's minimum.

    Each row is then a few dozen cells rather than `len(a) * len(b)` of them,
    and the three-way `min()` call — a Python function call per cell, 9.1
    million of them on one real rebuild — became three comparisons. The
    `shadow_map` scan that owns most of those calls dropped from 2.6s to
    0.55s on a 20k-row history, with the map it returns unchanged.
    """
    if a == b:
        return 0
    la, lb = len(a), len(b)
    if la > lb:
        a, b, la, lb = b, a, lb, la
    if lb - la > max_dist:
        return max_dist + 1
    # Peel the shared head and tail. `p < la` keeps the peel from consuming
    # everything: equal strings were answered above, and one side reaching
    # empty here is what the la==0 / lb==0 answers below are for.
    p = 0
    while p < la and a[p] == b[p]:
        p += 1
    s = 0
    while s < la - p and a[la - 1 - s] == b[lb - 1 - s]:
        s += 1
    if p:
        a = a[p:]
        b = b[p:]
        la -= p
        lb -= p
    if s:
        a = a[: la - s]
        b = b[: lb - s]
        la -= s
        lb -= s
    if la == 0:
        return lb
    if lb == 0:
        return la
    cap = max_dist + 1
    prev = list(range(lb + 1))
    for i in range(1, la + 1):
        ca = a[i - 1]
        lo = i - max_dist
        if lo < 1:
            lo = 1
        hi = i + max_dist
        if hi > lb:
            hi = lb
        # Cells off the band are the cap itself: a number that means "past
        # caring". `cur[0]` is column zero, which the band may exclude — the
        # sentinel stands in for it too, and only the j==1 branch reads a
        # real left neighbour (`cur[0]` when the band opens at 1).
        cur = [cap] * (lb + 1)
        cur[0] = i
        row_min = cur[lo - 1] if lo == 1 else cap
        if lo == 1:
            cost = 0 if ca == b[0] else 1
            v = prev[0] + cost
            w = prev[1] + 1
            if w < v:
                v = w
            if cur[0] + 1 < v:
                v = cur[0] + 1
            cur[1] = v
            if v < row_min:
                row_min = v
        for j in range(lo if lo > 1 else 2, hi + 1):
            cost = 0 if ca == b[j - 1] else 1
            v = prev[j - 1] + cost
            w = prev[j] + 1
            if w < v:
                v = w
            w = cur[j - 1] + 1
            if w < v:
                v = w
            cur[j] = v
            if v < row_min:
                row_min = v
        if row_min > max_dist:
            return cap
        prev = cur
    d = prev[lb]
    return d if d <= max_dist else cap


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
    `docker` command. The word comparison is case-insensitive — `Dokcer` is
    `docker`'s typo no less than `dokcer` — while the rest-of-line test runs on
    lowered copies of both sides, and the slice that separates a candidate from
    its first word stays positional: a lowered first word has the same length
    as the original, so the remainder past it is the same remainder either way.
    """
    words = prefix.split()
    first = words[0] if words else prefix
    if len(first) < 3:
        return []
    first_l = first.lower()
    cands = []
    max_d = 1 if len(first) <= 4 else 2
    for w in _first_words(eng):
        w_l = w.lower()
        if w_l.startswith(first_l) or first_l.startswith(w_l):
            continue
        if lev1(first_l, w_l, max_d) <= max_d:
            cands.append(w)
            if len(cands) >= 10:
                break
    rest_l = prefix[len(first):].lower()
    out: list[str] = []
    for w in cands:
        w_l = w.lower()
        for c in eng._prefix_range(w):
            if len(c.split(None, 1)) > 1 or prefix.strip() == first:
                # only keep commands consistent with rest of prefix
                if c[len(w):].lower().startswith(rest_l):
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
    # Per-line letter sets and counts: the two cheap gates before the edit
    # distance. Two lines within two edits can differ by at most two distinct
    # letters per side (the set gate) and by at most two in character surplus
    # per side (the count gate — every edit changes one character's count by
    # one, so a distance of two can move a letter's multiplicity no further).
    # Both answer at C speed, and both are sound: a pair either gate rejects
    # is a pair the distance would reject too. The counts also answer the
    # deficit for free — surplus minus the length difference is deficit, by
    # conservation of characters — so one walk per pair gates both sides.
    sets = {c: set(c) for c in names}
    counts = {c: {} for c in names}
    for c, bag in counts.items():
        for ch in c:
            bag[ch] = bag.get(ch, 0) + 1
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
        s_bag = counts[cmd]
        ev = (st.success, st.freq, st.last_ts)
        best: tuple[tuple[int, int, int], str] | None = None
        for ln in range(len(cmd) - 2, len(cmd) + 3):
            for other in buckets.get(ln, ()):
                if other == cmd:
                    continue
                o_set = sets[other]
                if len(o_set - s_set) > 2 or len(s_set - o_set) > 2:
                    continue
                # Character surplus of `cmd` over `other`; deficit follows
                # from the length difference the bucket already guarantees
                # is at most two.
                o_bag = counts[other]
                surplus = 0
                for ch, have in s_bag.items():
                    extra = have - o_bag.get(ch, 0)
                    if extra > 0:
                        surplus += extra
                        if surplus > 2:
                            break
                if surplus > 2 or surplus - (len(cmd) - len(other)) > 2:
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