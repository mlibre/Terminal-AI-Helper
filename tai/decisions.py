"""Jev-style bounded decision layer for terminal candidates.

This module does not call an LLM. It exposes the same contract we will use
for a local or hosted System One model: candidate Choice + safety Noul +
show-now Noul. Deterministic code owns safety-critical checks; a model may
rerank candidates later.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass


@dataclass
class Decision:
    type: str
    choice: str
    probabilities: dict[str, float]
    confidence: float
    show_now: float
    destructive: float


def unsafe_score(cmd: str) -> float:
    """1.0 when `cmd` is destructive, else 0.0. Deterministic, in code, on purpose.

    A model is allowed to rerank candidates. It is not allowed to decide what is
    dangerous, because that decision is only as trustworthy as the answer, and
    the answer is text off a network. Every path that can show a suggestion runs
    this first.

    **A false positive is cheap here and a false negative is not.** This is a
    substring match over the whole line, so `echo 'rm -rf /'` scores 1.0 — and
    that is the right answer, because the only thing it costs is that the line is
    not *offered*; the user who typed it can still run it. The other direction
    offers `rm -rf /` as a grey hint. So the rules below are deliberately blunt
    and a pattern is not tightened for a case it over-matches.

    What that means in practice is that a pattern has to cover the *spellings* of
    what it already covers, not only the one it was written for: `rm -rf` matched
    and `rm -fr` — the same command, the flags in the other order, which is how
    people write it when they think of the force first — did not, and neither did
    `rm --recursive --force`. Long options and combined short ones are the two
    spellings, and a gate that knows one of them knows the intent.
    """
    s = cmd.lower()
    patterns = (
        # Any short-flag group holding r/R: `-r`, `-rf`, `-fr`, `-rfz`. The
        # original was `-rf?`, which matched `-r` and `-rf` and nothing else.
        r"\brm\s+(-{1,2}[^\s]+\s+)*-{1,2}[^\s]*[rR][^\s]*\b",
        # …and the long forms, which read as *safer* and are the same command.
        r"\brm\s+(-{1,2}[^\s]+\s+)*--\s*(recursive|force|no-preserve-root)\b",
        r"\bgit\s+reset\s+--hard\b",
        r"\bgit\s+clean\b.*-[^\s]*f", r"\bmkfs\b", r"\bdd\s+if=",
        r":\(\)\s*\{.*\};?", r"\bshutdown\b", r"\breboot\b",
        # `kill -9 1`, `kill -TERM 1` and `kill 1234` are the same act with and
        # without the signal; the original matched only the first two, and the
        # literal word "kill9", which it used to match and which never appears in
        # a command.
        r"\bkill(all|\s+(-[a-zA-Z0-9]+|\d+))\b", r"\bterraform\s+destroy\b",
        r"\bkubectl\s+delete\b", r"\bhelm\s+uninstall\b",
        # Docker deletes things too, and `docker system prune -af` is the same
        # class of act as `kubectl delete`: everything unused, irreversibly. It sat
        # next to `helm uninstall` in the list of what a model must not be trusted
        # to decide, and was missing from it.
        r"\bdocker\s+(system|volume|image|container|network)\s+prune\b",
        r"\bdocker\s+(rm|rmi)\s+(-[^\s]+\s+)*-[a-zA-Z]*f",
    )
    return 1.0 if any(re.search(p, s) for p in patterns) else 0.0


def decide(candidates: list[str], scores: dict[str, float] | None = None,
           typed_prefix: str = "", typed_seconds: float = 0.0) -> Decision:
    """Bounded reranker. `candidates` must already be generated locally."""
    candidates = list(dict.fromkeys(candidates))[:32]
    if not candidates:
        return Decision("choice", "", {}, 0.0, 0.0, 0.0)
    scores = scores or {}
    vals = [float(scores.get(c, 0.0)) for c in candidates]
    m = max(vals) if vals else 0.0
    # min(20, ...) keeps one runaway score from overflowing; the shift by the
    # maximum is what makes the result a distribution rather than a magnitude.
    exps = [math.exp(min(20.0, v - m)) for v in vals]
    total = sum(exps) or 1.0
    probs = {c: e / total for c, e in zip(candidates, exps)}
    best = max(probs, key=probs.get)
    second = sorted(probs.values(), reverse=True)[1] if len(probs) > 1 else 0.0
    conf = min(1.0, 0.35 + (probs[best] - second) * 2.2)
    destructive = unsafe_score(best)
    show = 1.0 if typed_prefix and typed_seconds >= 0.08 and conf >= 0.5 else 0.0
    if destructive:
        show *= 0.25
    return Decision("choice", best, probs, round(conf, 3), show, destructive)
