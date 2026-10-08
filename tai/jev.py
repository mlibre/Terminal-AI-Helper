"""Optional Jev System One adapter for bounded terminal decisions.

Jev is used as a semantic judge/reranker, never as a command generator.
The normal embedded autocomplete remains local and sub-millisecond. Call this
module only for ambiguous/missing-candidate cases or explicitly with
`tai suggest --jev`.

Environment:
    TYPESAFE_API_KEY       required for hosted Jev
    TYPESAFE_BASE_URL      default https://api.typesafe.ai
    TYPESAFE_MODEL         default jev-latest
    TAI_JEV_TIMEOUT        default 1.5 seconds
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any


BASE_URL = os.environ.get("TYPESAFE_BASE_URL", "https://api.typesafe.ai").rstrip("/")
MODEL = os.environ.get("TYPESAFE_MODEL", "jev-latest")
TIMEOUT = float(os.environ.get("TAI_JEV_TIMEOUT", "1.5"))


@dataclass
class JevResult:
    choice: str
    probabilities: dict[str, float]
    confidence: float
    show_now: float
    destructive: float
    model: str
    usage: dict[str, Any]
    raw: dict[str, Any]


def _question_payload(candidates: list[str]) -> dict[str, Any]:
    # Keep the rubric in one place so it is reviewable and versionable.
    # Candidate descriptions are intentionally short: the model judges the
    # current terminal intent, not shell syntax or command execution.
    criteria = {c: f"Terminal completion candidate: {c}" for c in candidates}
    return {
        "best_candidate": {
            "type": "choice",
            "instructions": (
                "Which terminal command candidate best matches what the user is "
                "likely trying to do? Use only the candidates. If none is "
                "appropriate, choose NONE."
            ),
            "criteria": {**criteria, "NONE": "No listed candidate fits the context."},
        },
        "show_now": {
            "type": "noul",
            "instructions": (
                "Is the selected candidate useful and sufficiently certain to "
                "show as an immediate ghost-text completion?"
            ),
            "criteria": {
                "true": "The candidate is relevant, plausible, and likely to help.",
                "false": "The candidate is weak, ambiguous, unsupported, or too uncertain.",
            },
        },
        "destructive": {
            "type": "noul",
            "instructions": (
                "Does the selected candidate risk destructive effects such as "
                "deleting data, resetting state, terminating processes, or "
                "destroying infrastructure?"
            ),
            "criteria": {
                "true": "The candidate may delete, destroy, reset, terminate, or overwrite important state.",
                "false": "The candidate is read-only or has no material destructive effect.",
            },
        },
    }


def _answer_value(answer: dict[str, Any], key: str, default: Any = None) -> Any:
    if not isinstance(answer, dict):
        return default
    return answer.get(key, default)


def decide(prefix: str, candidates: list[str], *, cwd: str = "",
           repo: str = "", branch: str = "", previous: str = "") -> JevResult:
    """Run one batched Jev request containing all bounded decisions."""
    candidates = [c for c in dict.fromkeys(candidates) if c][:64]
    if not candidates:
        return JevResult("", {}, 0.0, 0.0, 0.0, "", {}, {})

    state = {
        "typed_prefix": prefix,
        "current_directory": cwd,
        "repository": repo,
        "branch": branch,
        "previous_command": previous,
        "candidate_commands": candidates,
    }
    payload = {
        "state": state,
        "model": MODEL,
        "questions": _question_payload(candidates),
    }
    key = os.environ.get("TYPESAFE_API_KEY", "")
    if not key:
        raise RuntimeError("TYPESAFE_API_KEY is not set")
    req = urllib.request.Request(
        f"{BASE_URL}/v1/systemone",
        data=json.dumps(payload, separators=(",", ":")).encode(),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as response:
            raw = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300]
        raise RuntimeError(f"Jev HTTP {e.code}: {detail}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"Jev unavailable: {e.reason}") from e

    answers = raw.get("answers", {})
    best = answers.get("best_candidate", {})
    choice = _answer_value(best, "choice", "NONE")
    probs = _answer_value(best, "probabilities", {}) or {}
    conf = float(_answer_value(best, "confidence", 0.0) or 0.0)
    show = float(_answer_value(answers.get("show_now", {}), "noul", 0.0) or 0.0)
    destructive = float(_answer_value(answers.get("destructive", {}), "noul", 0.0) or 0.0)
    if choice == "NONE":
        choice = ""
        conf = 0.0
        show = 0.0
    elif choice not in candidates:
        # The model may only choose from the candidates it was shown. Anything
        # else is invented text, and it is discarded here rather than at each
        # caller, because every caller would otherwise have to remember.
        choice = ""
        probs = {}
        conf = 0.0
        show = 0.0
    return JevResult(choice, probs, conf, show, destructive,
                     raw.get("model", MODEL), raw.get("usage", {}), raw)
