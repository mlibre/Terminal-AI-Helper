"""Offline Jev decision tests: no API key or network required."""
import pathlib
import sys

# The repository root, so `tai` is importable wherever this is run from.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from tai.decisions import decide


def test_bounded_choice():
    result = decide(
        ["git restore", "git reset --hard", "git status"],
        {"git restore": 4.0, "git status": 2.0, "git reset --hard": 1.0},
        "git re", typed_seconds=0.1,
    )
    assert result.choice == "git restore"
    assert 0 <= result.confidence <= 1
    assert result.show_now == 1.0
    assert result.destructive == 0.0


def test_destructive_gate():
    result = decide(
        ["terraform destroy", "terraform plan"],
        {"terraform destroy": 4.0, "terraform plan": 1.0},
        "terraform ", typed_seconds=0.1,
    )
    assert result.destructive == 1.0
    assert result.show_now < 1.0


if __name__ == "__main__":
    test_bounded_choice()
    test_destructive_gate()
    print("OK — Jev decision contract")
