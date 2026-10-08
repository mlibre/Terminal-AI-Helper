"""Evaluate autocomplete candidates against labeled terminal examples.

Keep this small and deterministic: it is an application-level benchmark, not
an LLM benchmark. Add examples for new CLI tools and use it to tune ranking,
thresholds, and knowledge extraction.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

CASES = [
    {"prefix": "git st", "expect": "git status"},
    {"prefix": "git sw", "expect": "git switch"},
    {"prefix": "docker co", "expect": "docker compose up -d"},
    {"prefix": "python3 -m py", "expect": "python3 -m pytest"},
    # A prefix that is already a whole command must not "complete" to itself,
    # and must not be answered with a command whose paths are gone. Both of
    # these were reported from real use: `ls -l` suggested nothing, and a
    # stale `cd ~/projects/myapp` was the top suggestion for `cd `.
    {"prefix": "ls -l", "expect": "ls -la"},
    {"prefix": "cd ~/", "reject": ["cd ~/projects/myapp"]},
]


def main() -> int:
    """Evaluate the engine. Returns a shell exit code, so a red run is red."""
    from tai.predictor import suggest
    from tai.knowledge import candidates_for_prefix, load_all
    passed = 0
    for case in CASES:
        history = suggest(case["prefix"], limit=8)
        candidates = [c["cmd"] for c in history.get("choices", [])]
        candidates += candidates_for_prefix(case["prefix"], load_all(), cap=8)
        if "reject" in case:
            bad = [c for c in case["reject"] if c in candidates]
            passed += not bad
            print(("PASS" if not bad else "FAIL"), case["prefix"], "-> must not offer",
                  case["reject"], "got=", candidates[:5])
            continue
        found = case["expect"] in candidates
        passed += found
        print(("PASS" if found else "FAIL"), case["prefix"], "->", case["expect"],
              "got=", candidates[:5])
    print(f"eval: {passed}/{len(CASES)} exact-candidate coverage")
    return 0 if passed == len(CASES) else 1


if __name__ == "__main__":
    main()
