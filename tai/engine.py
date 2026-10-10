"""tai engine — in-memory fast ranker. Stdlib only.

Goals: <2ms suggest on 10k distinct, <10MB RAM, no deps.
Signals: freq + recency + cwd + repo + branch + hour + exit + sequence
         + token n-gram + typo-tolerant prefix.
Output: Jev-Choice-compatible {choice, probabilities, confidence}.
"""
from __future__ import annotations

import bisect
import math
import time

from tai.paths import destinations_first
from tai.typo import near_miss_lines, shadow_map


# Tunable weights (see tai tune). Defaults from smoke + manual tuning.
W_FREQ = 3.0
W_RECENCY = 2.0
W_CWD = 2.2
W_REPO = 1.2
W_BRANCH = 1.0
W_SUCCESS = 0.6
W_INSTALLED = 0.5
W_SEQ = 2.5
W_HOUR = 0.4
W_TOKEN = 0.8
LEN_PENALTY_DIV = 60.0
MAX_LEN_PEN = 0.6
EXACT_CTX_BONUS = 0.5
TYPO_PENALTY = 1.0
TEMP_DEFAULT = 0.9

# The freq normaliser, hoisted: it is a constant asked once per candidate per
# suggest, and `math.log1p(20)` was being recomputed for every one of them.
_LOG1P20 = math.log1p(20)
# Half-life of a command's recency, in days, as `exp(-age / TAU)`.
_TAU = 14.0


def hour_bucket(ts: int | None = None) -> int:
    try:
        t = time.localtime(ts) if ts else time.localtime()
    except (OverflowError, OSError, ValueError):
        return 0            # a timestamp we cannot read has no hour
    h = t.tm_hour
    if h < 6:
        return 0
    if h < 12:
        return 1
    if h < 18:
        return 2
    return 3


# Hour buckets are asked for once per stored row, and every row in a session
# carries the same few timestamps. Keyed by the ten-minute window rather than by
# the second, so the table is bounded by the history's span rather than by its
# length, and so a window that straddles a DST change still answers from its own
# timestamp. 20k rows of `localtime` cost 43ms of a 139ms build; this is ~1ms.
_HOURS: dict[int, int] = {}


def _hour_of(ts: int) -> int:
    window = ts // 600
    bucket = _HOURS.get(window)
    if bucket is None:
        bucket = _HOURS[window] = hour_bucket(ts)
    return bucket


def _softmax(scores: list[float], temp: float = 1.0) -> list[float]:
    if not scores:
        return []
    m = max(scores)
    exps = [math.exp((s - m) / max(temp, 1e-6)) for s in scores]
    tot = sum(exps) or 1.0
    return [e / tot for e in exps]


class CmdStats:
    """What the ranker knows about one command.

    `__slots__` and plain dicts, because there is one of these per distinct
    command and the memory budget is a few MB: a 20k-command history has to fit
    in a sub-millisecond suggest. Written as a plain class rather than a
    dataclass because the defaults are what the whole codebase mutates in place.

    Dicts, not Counters. A Counter is a dict that costs a Python-level
    `__init__` and `__missing__` on every construction and every miss, and this
    class builds three per command while the n-gram tables below build ~120k
    more: measured on a 20k-row history, swapping them for dicts took
    `build_from_rows` from 397ms to under 150ms. Nothing here used a Counter
    feature — it is read with `.get`, incremented, and iterated.
    """

    __slots__ = ("freq", "last_ts", "cwd", "repo", "branch", "hour",
                 "success", "fail", "nf")

    def __init__(self):
        self.freq = 0
        self.last_ts = 0
        self.cwd: dict[str, int] = {}
        self.repo: dict[str, int] = {}
        self.branch: dict[str, int] = {}
        self.hour = [0, 0, 0, 0]
        self.success = 0
        self.fail = 0
        # Runs the shell refused outright: exit 127 is "command not found",
        # which is not this line failing, it is this line not existing. Counted
        # apart from `fail` because the two carry different news — a failing
        # command ran and can be fixed; a not-found one never ran at all.
        self.nf = 0


class Engine:
    def __init__(self):
        self.cmds: dict[str, CmdStats] = {}
        self.seq: dict[str, dict[str, int]] = {}
        self.token_bigram: dict[str, dict[str, int]] = {}
        self.token_trigram: dict[tuple[str, str], dict[str, int]] = {}
        self.sorted_cmds: list[str] = []
        # The lowercase mirror the case-insensitive prefix walk runs over:
        # (lowered, original) pairs, sorted by the lowered form, so the pairs
        # whose lowered text begins with a lowered prefix are one contiguous
        # bisect range. Built beside sorted_cmds in _ensure_sorted and never
        # read before it.
        self.sorted_lower: list[tuple[str, str]] = []
        self._prev_cmd: str | None = None  # for building seq incrementally
        self._first_word_cache: list[str] | None = None  # see tai/typo.py
        self._cmds_dirty = False         # sorted_cmds holds appends, not order

    def _ensure_sorted(self) -> None:
        """Sort the command list if anything was appended since the last sort.

        `add` used to `bisect.insort` every new command, which kept the list
        sorted at all times at an O(n) memmove per *new distinct* command — on a
        50k-row history with thousands of distinct names, that memmove work was
        an order of magnitude more than one final sort. Nothing can read the
        list before asking here: `_prefix_range` needs the lexical order to walk
        a bisect range, and `_first_words` in tai/typo.py keeps the same
        contract. One sort at first use, then appends only mark it dirty.
        """
        if self._cmds_dirty:
            self.sorted_cmds.sort()
            self.sorted_lower = sorted((c.lower(), c) for c in self.sorted_cmds)
            self._cmds_dirty = False

    # -- learning ---------------------------------------------------------
    def add(self, cmd: str, cwd: str = "", repo: str = "",
            branch: str = "", exit_code: int = 0,
            ts: int | None = None,
            _prev: str | None = "__USE_LAST__") -> None:
        cmd = cmd.strip()
        if not cmd:
            return
        # The same bound is_recordable applies — imported here, where a row this
        # long is rare, rather than at module load, which every fast process pays.
        from tai.store import MAX_CMD_LEN
        if len(cmd) > MAX_CMD_LEN:
            return
        ts = ts if ts is not None else int(time.time())
        st = self.cmds.get(cmd)
        if st is None:
            st = self.cmds[cmd] = CmdStats()
            self.sorted_cmds.append(cmd)
            self._cmds_dirty = True     # one sort at first read, not insort per add
            self._first_word_cache = None   # the derived list is now stale
        st.freq += 1
        if ts >= st.last_ts:
            st.last_ts = ts
        if cwd:
            st.cwd[cwd] = st.cwd.get(cwd, 0) + 1
        if repo:
            st.repo[repo] = st.repo.get(repo, 0) + 1
        if branch:
            st.branch[branch] = st.branch.get(branch, 0) + 1
        st.hour[_hour_of(ts)] += 1
        if (exit_code or 0) == 0:
            st.success += 1
        else:
            st.fail += 1
            if exit_code == 127:
                st.nf += 1
        # token n-grams within command
        toks = cmd.split()
        bigram = self.token_bigram
        trigram = self.token_trigram
        for i, tok in enumerate(toks[1:], 1):
            row = bigram.get(toks[i - 1])
            if row is None:
                row = bigram[toks[i - 1]] = {}
            row[tok] = row.get(tok, 0) + 1
            if i >= 2:
                key = (toks[i - 2], toks[i - 1])
                row3 = trigram.get(key)
                if row3 is None:
                    row3 = trigram[key] = {}
                row3[tok] = row3.get(tok, 0) + 1
        # sequence
        prev = self._prev_cmd if _prev == "__USE_LAST__" else _prev
        if prev:
            row = self.seq.get(prev)
            if row is None:
                row = self.seq[prev] = {}
            row[cmd] = row.get(cmd, 0) + 1
        self._prev_cmd = cmd

    def build_from_rows(self, rows) -> None:
        """rows: iterable of (cmd, cwd, repo, branch, exit_code, ts) in
        chronological order (oldest first) for correct seq.

        Rows the recorder itself would reject are dropped here as well: older
        databases hold rows written before the filter existed, and every
        consumer of the engine — the index the plugins source, `tai suggest`,
        the predictor — reads through this one list.
        """
        from tai.store import is_recordable
        for cmd, cwd, repo, branch, exit_code, ts in rows:
            cmd = (cmd or "").strip()
            if not is_recordable(cmd):
                continue
            self.add(cmd, cwd or "", repo or "", branch or "",
                     exit_code or 0, ts or int(time.time()))

    # -- prefix lookup ----------------------------------------------------
    def _prefix_range(self, prefix: str) -> list[str]:
        """The commands whose *lowercased* text begins with `prefix`, best case
        aside — `ls down` reaches `ls Downloads` here, which is the point: the
        case a command was recorded with is the filesystem's business, not a
        spelling the user must remember.

        The walk is over the lowered mirror, so the sort is still what makes it
        a walk rather than a scan: every lowered string beginning with the
        lowered prefix is >= it and lies before any that does not, so the first
        entry that stops matching ends the range. `cap` bounds the walk for a
        prefix as broad as a single letter, where the range is the whole history
        and ranking every entry is not worth it. Order inside the answer is
        lowered-lexical; every caller ranks after this, none reads order from it.
        """
        if not prefix:
            return []
        lp = prefix.lower()
        if not lp:
            return []
        self._ensure_sorted()
        pairs = self.sorted_lower
        out: list[str] = []
        i = bisect.bisect_left(pairs, (lp,))
        n = len(pairs)
        while i < n and len(out) < 2000:
            lc, c = pairs[i]
            if not lc.startswith(lp):
                break
            out.append(c)
            i += 1
        return out

    # -- suggest ----------------------------------------------------------
    def suggest(self, prefix: str = "", cwd: str = "", repo: str = "",
                branch: str = "", last_commands: list | None = None,
                limit: int = 1, temp: float = TEMP_DEFAULT,
                now_ts: int | None = None,
                exclude: set | None = None,
                on_path=None) -> dict:
        """Rank candidates for `prefix`.

        `exclude` drops candidates before ranking. The engine itself never
        touches the filesystem — it has to stay a sub-millisecond in-memory
        ranker — so the caller decides policy (currently: commands whose paths
        no longer exist) and passes the result in.

        `on_path` is the caller's answer to one question — is this command's
        first word installed? — asked at most once per candidate, and only for
        the candidates actually scored. The engine supplies the question's
        shape, the caller the filesystem: an installed command is worth
        W_INSTALLED over an uninstalled word, all evidence equal, because the
        user's own report is exactly that intuition — a tool they have must
        outrank the typo of its name that never once ran.
        """
        # `choices[:limit]` and `[...][:max(limit, 1) * 3]` disagree about a
        # limit of zero or less: one asks for nothing and the other asks for
        # three, so a negative limit returned a list nobody asked for. One
        # answer, here, rather than a policy spread across two slices.
        limit = max(1, limit)
        t0 = time.time()
        prefix = prefix or ""
        last_commands = last_commands or []
        now = now_ts or int(time.time())
        hb = hour_bucket(now)
        prev = last_commands[-1].strip() if last_commands else ""

        prefix_stripped = prefix.strip()
        cand_names: list[str] = []
        typo_set: set[str] = set()
        if prefix_stripped:
            cand_names = self._prefix_range(prefix)
            if prefix != prefix_stripped:
                extra = self._prefix_range(prefix_stripped)
                if extra:
                    seen = set(cand_names)
                    cand_names += [c for c in extra if c not in seen]
            if len(cand_names) < 3:
                typo = near_miss_lines(self, prefix_stripped)
                if typo:
                    seen = set(cand_names)
                    for c in typo:
                        if c not in seen:
                            cand_names.append(c)
                            typo_set.add(c)
        else:
            # empty prefix: rank all distinct (two-stage filter inside scoring)
            cand_names = list(self.cmds.keys())

        if exclude:
            cand_names = [c for c in cand_names if c not in exclude]
        # A line the shell never ran is not a command. Exit 127 is not this
        # line failing — it is the shell refusing the word — so a line whose
        # every recorded run was command-not-found was never a tool on this
        # machine, and ranking it as though it were one is how the typo of a
        # name outranks the name (the user's report: `opencoe` above
        # `opencode`, every signal otherwise tied). Such a line is dropped
        # here and from the index the plugins read — one rule, two rankers —
        # and its rows stay in the store: install the tool later and its
        # first success returns it on its own. The near-miss vocabulary still
        # answers a *typed* phantom with the real spelling beside it, which
        # is the direction typo tolerance exists for.
        if cand_names:
            phantom = {c for c in cand_names
                       if (stc := self.cmds.get(c)) is not None
                       and stc.nf and stc.nf == stc.freq}
            if phantom:
                cand_names = [c for c in cand_names if c not in phantom]
        if not cand_names:
            wrapped = split_wrapper(prefix)
            if wrapped:
                # Nothing for `sudo git st` in this history, so rank the line as
                # if the wrapper were not there and put it back. The suggestion
                # is a command the user really ran, one word apart.
                head, rest = wrapped
                inner = self.suggest(prefix=rest, cwd=cwd, repo=repo,
                                      branch=branch, last_commands=last_commands,
                                      limit=limit, temp=temp, now_ts=now_ts,
                                      exclude=exclude, on_path=on_path)
                return _rewrap(inner, head, prefix)
            return _fallback(prefix, limit)

        # two-stage: if huge, pre-filter by cheap freq+recency to 600
        # The pre-filter's per-command arithmetic is the main loop's arithmetic
        # for the same names — the recency `exp` and the `log1p` of the same
        # frequency — so it is cached here and reused below rather than paid
        # twice for the 600 that survive.
        pre: dict[str, tuple[float, float]] | None = None
        if len(cand_names) > 600:
            pre = {}
            scored_pre = []
            exp, log1p = math.exp, math.log1p
            for name in cand_names:
                st = self.cmds[name]
                age_days = max(0, (now - (st.last_ts or now)) / 86400)
                rec = exp(-age_days / _TAU)
                fq = log1p(st.freq)
                pre[name] = (fq, rec)
                scored_pre.append((fq + rec, name))
            scored_pre.sort(reverse=True)
            cand_names = [n for _, n in scored_pre[:600]]

        follow = self.seq.get(prev, None) if prev else None
        max_follow = max(follow.values()) if follow else 0

        # token context for next-token bonus. The rows and their totals are
        # taken once, here, rather than per candidate: the old loop summed
        # the same row again for every candidate the prefix had, which on a
        # broad prefix was the same walk paid 600 times for one row.
        prefix_l = prefix.lower()
        ptoks = prefix.split()
        tri_ctx = tuple(ptoks[-2:]) if len(ptoks) >= 2 else None
        bi_ctx = ptoks[-1] if ptoks else None
        tri_row = self.token_trigram.get(tri_ctx) if tri_ctx else None
        tri_total = sum(tri_row.values()) if tri_row else 0
        bi_row = self.token_bigram.get(bi_ctx) if bi_ctx else None
        bi_total = sum(bi_row.values()) if bi_row else 0

        scored: list[tuple[float, str]] = []
        exp = math.exp
        for name in cand_names:
            st = self.cmds[name]
            if pre is not None:
                fq, recency = pre[name]
                freq_s = min(fq / _LOG1P20, 1.0)
            else:
                age_days = max(0, (now - (st.last_ts or now)) / 86400)
                recency = exp(-age_days / _TAU)
                freq_s = min(math.log1p(st.freq) / _LOG1P20, 1.0)
            cwd_s = min((st.cwd.get(cwd, 0) if cwd else 0) / max(st.freq, 1), 1.0)
            # basename fallback: half credit
            if cwd and cwd_s == 0 and st.cwd:
                base = cwd.rstrip("/").split("/")[-1]
                for k, v in st.cwd.items():
                    if k.rstrip("/").split("/")[-1] == base:
                        cwd_s = min(0.5 * v / max(st.freq, 1), 0.5)
                        break
            repo_s = min((st.repo.get(repo, 0) if repo else 0) / max(st.freq, 1), 1.0)
            branch_s = min((st.branch.get(branch, 0) if branch else 0) / max(st.freq, 1), 1.0)
            success_s = max(-0.5, min(1.0, (st.success - st.fail * 0.5) / max(st.freq, 1))) * 0.5 + 0.5
            hour_s = (st.hour[hb] / max(st.freq, 1)) if st.freq else 0.0
            seq_s = (follow.get(name, 0) / max_follow) if (follow and max_follow) else 0.0

            # token n-gram bonus: P(next token | context). The row and its
            # total come from above; `or 1` is the loop's old `max(sum, 1)`.
            tok_s = 0.0
            if prefix and name.lower().startswith(prefix_l):
                rest = name[len(prefix):].lstrip()
                if rest:
                    nxt = rest.split()[0]
                    if tri_row is not None:
                        tok_s = max(tok_s, tri_row.get(nxt, 0) / (tri_total or 1))
                    if bi_row is not None:
                        tok_s = max(tok_s,
                                    0.7 * bi_row.get(nxt, 0) / (bi_total or 1))

            extra_len = max(0, len(name) - len(prefix))
            len_pen = min(extra_len / LEN_PENALTY_DIV, MAX_LEN_PEN)

            score = (
                W_FREQ * freq_s
                + W_RECENCY * recency
                + W_CWD * cwd_s
                + W_REPO * repo_s
                + W_BRANCH * branch_s
                + W_SUCCESS * success_s
                + W_HOUR * hour_s
                + W_SEQ * seq_s
                + W_TOKEN * tok_s
                - len_pen
            )
            if cwd and repo and st.cwd.get(cwd, 0) and st.repo.get(repo, 0):
                score += EXACT_CTX_BONUS
            if on_path is not None and on_path(name.split(" ", 1)[0]):
                score += W_INSTALLED
            if name in typo_set:
                score -= TYPO_PENALTY
            # A phantom (every run exit 127) was dropped above; a line that
            # sometimes ran carries its failures softly via success_s. A hard
            # top-1 veto stays out, for recall's sake.
            scored.append((score, name))

        # A one-off that nearly duplicates a stronger line ranks just below it
        # (tai/typo.py, which owns the rule and its evidence order). The index
        # applies the same map to the scores the shell plugins read — one
        # rule, two rankers — so `tai un` offers `tai uninstall` here and in
        # the prompt, never the `unsintall` recorded beside it. Computed over
        # the candidates, because the cap only means something when both
        # spellings are on offer, and a candidate set this narrow is a
        # handful of lines rather than a walk over the history. Wide answers
        # skip it: a demotion at rank 400 of 20000 is not a thing anyone sees.
        if len(cand_names) <= 64:
            shadows = shadow_map(self, cand_names)
            if shadows:
                by_name = {n: s for s, n in scored}
                demoted = False
                # Two passes, so a shadow of a shadow lands below its own
                # target whichever order the map iterates in.
                for _ in range(2):
                    for typo, canon in shadows.items():
                        cs = by_name.get(canon)
                        cs_ts = by_name.get(typo)
                        if cs is not None and cs_ts is not None \
                                and cs_ts > cs - 0.01:
                            by_name[typo] = cs - 0.01
                            demoted = True
                if demoted:
                    scored = [(s, n) for n, s in by_name.items()]
        # `cd ..` and `cd -` are true in every directory there is, so how often
        # they were typed is not evidence about where the user wants to be. They
        # are ranked below every real destination and never removed, and the
        # index applies the same rule to the scores the shell plugins read — one
        # rule, two rankers, and `tai suggest` cannot disagree with the hint.
        scored = destinations_first(scored)
        scored.sort(reverse=True)
        # A suggestion has to extend the line. A candidate identical to what is
        # already typed cannot be rendered as a hint and accepting it changes
        # nothing, so it must not win: `ls -l` would otherwise hide `ls -la`. A
        # candidate that does not *start with* the prefix is not an answer
        # either — the rule the shell plugins enforce on their side of the
        # index, and the one `completion` below silently assumes when it slices
        # the choice by `len(prefix)`.
        #
        # "Extends" is the test, not "differs from": the filter was `n !=
        # prefix`, which dropped the echo and nothing else, so a prefix that
        # differs from the command by more than that — `'  git'`, `'git  '`, a
        # tab — was answered with `'git stash'`, which extends nothing and has
        # an empty completion. `typo_set` is the exception and says so: a
        # near-miss first word is the whole point of typo tolerance, so those are
        # kept, and they are already ranked below every exact match by
        # `TYPO_PENALTY`.
        #
        # Filter first, then cap, then split. Taking the names from the filtered
        # list and the scores from the head of the unfiltered one paired every
        # command with the *next* command's score, and shortened the candidate
        # list to whatever survived — so the softmax, the probabilities and the
        # confidence all described commands that were not on offer.
        kept = [(s, n) for s, n in scored
                if n.lower().startswith(prefix_l) and n != prefix or n in typo_set
                ][: limit * 3][:200]
        top_scores = [s for s, _ in kept]
        top_names = [n for _, n in kept]
        probs = _softmax(top_scores, temp=temp)

        conf = 0.0
        if len(probs) == 1:
            conf = min(0.55 + probs[0] * 0.3, 0.95)
        elif probs:
            conf = max(0.0, min(1.0, (probs[0] - probs[1]) * 2.2 + 0.35))

        elapsed_ms = (time.time() - t0) * 1000
        choices = [
            {"cmd": n, "prob": round(p, 4), "score": round(s, 3)}
            for n, p, s in zip(top_names, probs, top_scores)
        ][:limit]
        # No candidate means no answer. Echoing the prefix back would read as
        # "here is your suggestion" to every caller, which is the one thing a
        # suggestion must never be; the shell plugins return "" for the same
        # case, and the two paths have to agree.
        best = top_names[0] if top_names else ""
        return {
            "type": "choice",
            "choice": best,
            # The candidate was picked under the case-insensitive prefix, so
            # the slice test folds too — and slices positionally, the same way
            # the plugin strips: the head that matched is the prefix's own
            # length, in the candidate's case.
            "completion": best[len(prefix):] if best.lower().startswith(prefix.lower()) else "",
            "probabilities": {c["cmd"]: c["prob"] for c in choices},
            "choices": choices,
            "confidence": round(conf, 3),
            "latency_ms": round(elapsed_ms, 2),
            "count": len(cand_names),
        }

    # -- explain ----------------------------------------------------------
    def explain(self, prefix: str, cmd: str, cwd: str = "", repo: str = "",
                branch: str = "", last_commands: list | None = None,
                now_ts: int | None = None, on_path=None) -> dict | None:
        """Why `cmd` scores what it scores for `prefix` — the factors, named.

        The dashboard's question: the panel says `got log` is 7.4% and the
        user asks why. Every contribution the scoring loop above adds is
        listed here with its weight, its value and the evidence it came from,
        in the loop's own order and from the same module constants, so `tai
        tune` moves both at once.

        A twin of the loop, not a shared function: the loop is the hot path
        (hundreds of candidates per suggest, two-stage prefilter and cap
        around the arithmetic), while explain answers for one candidate and
        can afford to speak. What keeps the twins honest where a shared
        function would keep them identical is the test that pins explain's
        total against suggest's score for the same candidate on a seeded
        engine (tests/test_web.py).

        Returns None for a command the engine never saw. A candidate that
        does not start with the prefix is reported as the near-miss it can
        only have been, with the typo penalty applied.
        """
        st = self.cmds.get(cmd)
        if st is None:
            return None
        now = now_ts or int(time.time())
        hb = hour_bucket(now)
        last_commands = last_commands or []
        prev = last_commands[-1].strip() if last_commands else ""
        prefix = prefix or ""

        factors: list[dict] = []

        def add(label: str, weight: float, value: float, detail: str) -> None:
            factors.append({"label": label, "weight": weight, "value": round(value, 4),
                            "contrib": round(weight * value, 4), "detail": detail})

        age_days = max(0, (now - (st.last_ts or now)) / 86400)
        recency = math.exp(-age_days / _TAU)
        freq_s = min(math.log1p(st.freq) / _LOG1P20, 1.0)
        add("frequency", W_FREQ, freq_s, f"run {st.freq}× (log1p scaled at 20)")
        add("recency", W_RECENCY, recency,
            f"last run {age_days:.1f}d ago (half-life 14d)")

        cwd_s = min((st.cwd.get(cwd, 0) if cwd else 0) / max(st.freq, 1), 1.0)
        if cwd:
            cwd_detail = f"{st.cwd.get(cwd, 0)} of {st.freq} runs in this directory"
            if cwd_s == 0 and st.cwd:
                base = cwd.rstrip("/").split("/")[-1]
                for k, v in st.cwd.items():
                    if k.rstrip("/").split("/")[-1] == base:
                        cwd_s = min(0.5 * v / max(st.freq, 1), 0.5)
                        cwd_detail = (f"half credit: {v} of {st.freq} runs "
                                      f"in a directory also named {base}/")
                        break
        else:
            cwd_detail = f"{st.freq} runs recorded, no directory asked"
        add("this directory", W_CWD, cwd_s, cwd_detail)

        repo_s = min((st.repo.get(repo, 0) if repo else 0) / max(st.freq, 1), 1.0)
        add("repository", W_REPO, repo_s,
            (f"{st.repo.get(repo, 0)} of {st.freq} runs in this repository"
             if repo else f"{st.freq} runs recorded, no repository asked"))

        branch_s = min((st.branch.get(branch, 0) if branch else 0) / max(st.freq, 1), 1.0)
        add("git branch", W_BRANCH, branch_s,
            (f"{st.branch.get(branch, 0)} of {st.freq} runs on this branch"
             if branch else f"{st.freq} runs recorded, no branch asked"))

        success_s = max(-0.5, min(1.0, (st.success - st.fail * 0.5) / max(st.freq, 1))) * 0.5 + 0.5
        add("success", W_SUCCESS, success_s,
            f"{st.success} ok / {st.fail} failed")

        # The loop's own question, asked of the caller's filesystem: the panel
        # explains the installed bonus with the word it was decided on. A twin
        # that skipped the factor would total a number suggest never made.
        if on_path is not None:
            fw = cmd.split(" ", 1)[0]
            hit = bool(on_path(fw))
            add("installed command", W_INSTALLED, 1.0 if hit else 0.0,
                f"`{fw}` is" + ("" if hit else " not") + " on PATH")

        hour_s = (st.hour[hb] / max(st.freq, 1)) if st.freq else 0.0
        add("hour of day", W_HOUR, hour_s,
            f"{st.hour[hb]} of {st.freq} runs in this hour of the day")

        follow = self.seq.get(prev, None) if prev else None
        max_follow = max(follow.values()) if follow else 0
        seq_s = (follow.get(cmd, 0) / max_follow) if (follow and max_follow) else 0.0
        seq_n = follow.get(cmd, 0) if follow else 0
        if prev:
            add("follows the last command", W_SEQ, seq_s,
                f"followed `{prev}` {seq_n}×"
                + ("" if seq_s else "; never this command"))
        else:
            add("follows the last command", W_SEQ, 0.0, "no last command in the question")

        # token n-gram bonus: P(next token | context) — the loop's own test,
        # including the partial-token shape a mid-word prefix produces.
        tok_s = 0.0
        tok_detail = "no next word behind the prefix"
        prefix_l = prefix.lower()
        ptoks = prefix.split()
        tri_ctx = tuple(ptoks[-2:]) if len(ptoks) >= 2 else None
        bi_ctx = ptoks[-1] if ptoks else None
        if prefix and cmd.lower().startswith(prefix_l):
            rest = cmd[len(prefix):].lstrip()
            if rest:
                nxt = rest.split()[0]
                tok_detail = f"`{nxt}` has never followed the typed words"
                if tri_ctx and tri_ctx in self.token_trigram:
                    tc = self.token_trigram[tri_ctx]
                    tok_s = max(tok_s, tc.get(nxt, 0) / max(sum(tc.values()), 1))
                    tok_detail = (f"`{nxt}` followed `{' '.join(tri_ctx)}` "
                                  f"{tc.get(nxt, 0)} of {sum(tc.values())}×")
                if bi_ctx and bi_ctx in self.token_bigram:
                    bc = self.token_bigram[bi_ctx]
                    cand = 0.7 * bc.get(nxt, 0) / max(sum(bc.values()), 1)
                    if cand > tok_s:
                        tok_s = cand
                        tok_detail = (f"`{nxt}` followed `{bi_ctx}` "
                                      f"{bc.get(nxt, 0)} of {sum(bc.values())}×")
        add("next-word evidence", W_TOKEN, tok_s, tok_detail)

        extra_len = max(0, len(cmd) - len(prefix))
        len_pen = min(extra_len / LEN_PENALTY_DIV, MAX_LEN_PEN)
        factors.append({"label": "length", "weight": -1.0, "value": round(len_pen, 4),
                        "contrib": -round(len_pen, 4),
                        "detail": f"{extra_len} characters past the prefix"
                                  + (", at the penalty cap" if len_pen >= MAX_LEN_PEN else "")})

        score = sum(f["contrib"] for f in factors)
        if cwd and repo and st.cwd.get(cwd, 0) and st.repo.get(repo, 0):
            score += EXACT_CTX_BONUS
            factors.append({"label": "directory and repository together", "weight": 1.0,
                            "value": EXACT_CTX_BONUS, "contrib": EXACT_CTX_BONUS,
                            "detail": "run here, in this repository"})
        near_miss = not cmd.lower().startswith(prefix_l)
        if near_miss:
            score -= TYPO_PENALTY
            factors.append({"label": "near-miss", "weight": -1.0, "value": TYPO_PENALTY,
                            "contrib": -TYPO_PENALTY,
                            "detail": f"does not start with `{prefix}`; offered as a typo of it"})
        phantom = bool(st.nf and st.nf == st.freq)
        if phantom:
            # Zero-weight row: the arithmetic above is what the line would
            # score, and the row says why it scores nowhere — suggest drops a
            # phantom before ranking, and the panel should say so rather than
            # leave a number the reader cannot reconcile with an absent row.
            factors.append({"label": "command not found", "weight": 0.0, "value": 0.0,
                            "contrib": 0.0,
                            "detail": f"all {st.nf} runs were exit 127 — the shell "
                                      f"never found it; hidden from suggestions"})

        return {"cmd": cmd, "prefix": prefix, "score": round(score, 3),
                "freq": st.freq, "last_ts": st.last_ts,
                "age_days": round(age_days, 2), "near_miss": near_miss,
                "phantom": phantom,
                "factors": factors}


def _rewrap(result: dict, head: str, prefix: str) -> dict:
    """Put the wrapper back on a suggestion made for the line behind it."""
    choice = result.get("choice") or ""
    if not choice:
        return result
    full = f"{head} {choice}"
    out = dict(result)
    out["choice"] = full
    out["completion"] = full[len(prefix):] if full.lower().startswith(prefix.lower()) else ""
    out["probabilities"] = {f"{head} {c}": p for c, p in result.get("probabilities", {}).items()}
    out["choices"] = [dict(c, cmd=f"{head} {c['cmd']}") for c in result.get("choices", [])]
    out["wrapped"] = head
    return out


def _fallback(prefix: str, limit: int) -> dict:
    """Seed vocabulary and the installed-command answer, when ranking cannot.

    The same rule as the shell plugins applies: a candidate identical to what is
    already typed is not an answer, so the seed corpus must not reintroduce the
    echo that ranking just rejected.

    A tool that is installed but has never been run gets `tool --help`. It is the
    only honest thing to say about a command the history has never seen, and the
    plugins already answer it, so `tai suggest` has to answer it too — otherwise
    the two paths disagree about the same question, which is the one thing they
    are not allowed to do.
    """
    from tai.seed import SEED_COMMANDS
    defaults = list(SEED_COMMANDS)
    prefix_l = prefix.lower() if prefix else ""
    cands = [d for d in defaults if d.lower().startswith(prefix_l) and d != prefix] if prefix \
        else defaults[:3]
    if not cands and prefix and not any(ch.isspace() for ch in prefix):
        import shutil
        if shutil.which(prefix):
            cands = [f"{prefix} --help"]
    cands = cands[:limit]
    probs = _softmax([1.0 - i * 0.2 for i in range(len(cands))]) if cands else []
    return {
        "type": "choice",
        "choice": cands[0] if cands else "",
        "completion": cands[0][len(prefix):] if cands and cands[0].lower().startswith(prefix_l) else "",
        "probabilities": {c: round(p, 4) for c, p in zip(cands, probs)},
        "choices": [{"cmd": c, "prob": round(p, 4), "score": 0.0} for c, p in zip(cands, probs)],
        "confidence": 0.25 if cands else 0.0,
        "latency_ms": 0.05,
        "count": 0,
    }


# Words that run the command after them rather than being the command. The
# history is full of `git ...` and the shell autocomplete is asked for
# `sudo git ...`, so without treating the wrapper as transparent the whole
# vocabulary behind it is invisible. Deliberately a closed list, and deliberately
# excluding `env` and `xargs`: both change what follows them enough that
# re-attaching the wrapper would be a lie.
WRAPPERS = ("sudo", "doas", "nohup", "time", "nice", "ionice", "stdbuf", "command")


def split_wrapper(prefix: str) -> tuple[str, str] | None:
    """("sudo", "git st") when `prefix` runs another command behind a wrapper.

    A wrapper carrying its own options (`sudo -u root git st`) is left alone:
    the rest of the line is not the wrapped command, and guessing where the
    options end is not worth a wrong completion.
    """
    head, sep, rest = (prefix or "").partition(" ")
    if not sep or head not in WRAPPERS or not rest or "=" in rest:
        return None
    return head, rest

