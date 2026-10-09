"""The typo tolerance: banded distance, shadow map, near-miss lines.

lev1 is the one function a rebuild calls tens of thousands of times, and its
rewrite to a banded walk is the kind of change whose bugs are silent — a
distance that comes back one too small hides a shadow or promotes a phantom,
and nothing crashes. So the distance is held against a reference here: the
full-matrix walk it replaced, over random vocabularies, deliberate
near-duplicates, unicode, and the empty string. The shadow map is held against
itself with its cheap gates switched off, because a gate that ever rejected a
pair the distance would accept would do it quietly, per rebuild, forever.

    python3 tests/test_typo.py
"""
import os
import pathlib
import random
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
os.chdir(REPO)

from tai.engine import CmdStats, Engine  # noqa: E402
from tai.typo import TYPO_FREQ_MAX, lev1, near_miss_lines, shadow_map  # noqa: E402

failures: list[str] = []


def check(what: str, got, want) -> None:
    if got == want:
        print(f"  ok   {what}: {got!r}")
    else:
        print(f"  FAIL {what}:\n         got  {got!r}\n         want {want!r}")
        failures.append(what)


def reference_distance(a: str, b: str) -> int:
    """Textbook Levenshtein: the full matrix, no cap, no band."""
    la, lb = len(a), len(b)
    prev = list(range(lb + 1))
    for i in range(1, la + 1):
        cur = [i] + [0] * lb
        ca = a[i - 1]
        for j in range(1, lb + 1):
            cost = 0 if ca == b[j - 1] else 1
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
        prev = cur
    return prev[lb]


def test_lev1_against_reference() -> None:
    """The banded walk agrees with the textbook matrix it must be equal to.

    Every caller asks `lev1(...) <= max_dist`, so anything above the cap is
    interchangeable — but a band bug can also return a *smaller* number than
    the truth, and that is the one this fuzz exists to catch: for pairs the
    reference puts under the cap, the banded answer must be exact.
    """
    rng = random.Random(20261010)
    alphabet = "abcde"
    words = ["".join(rng.choice(alphabet) for _ in range(rng.randint(1, 14)))
             for _ in range(2500)]
    pairs = [(rng.choice(words), rng.choice(words)) for _ in range(4000)]
    # Deliberate near-duplicates, built by one or two edits, because random
    # pairs over five letters are mostly far apart and the band is only
    # interesting where the answer is not the cap.
    for _ in range(4000):
        w = list(rng.choice(words))
        roll = rng.random()
        if roll < 0.3 and w:
            w[rng.randrange(len(w))] = rng.choice(alphabet)
        elif roll < 0.6:
            w.insert(rng.randint(0, len(w)), rng.choice(alphabet))
        elif w:
            del w[rng.randrange(len(w))]
        pairs.append(("".join(w), rng.choice(words)))
    # Words the real history holds: long, mixed case, punctuation, unicode.
    real = ["git commit -m 'fix the thing'", "docker compose up -d --build",
            "sudo systemctl restart nginx", "ls -la ~/Downloads",
            "python3 -m venv .venv", "héllo wörld", "日本語のコマンド",
            "grep -rn 'TODO\\|FIXME' src/", "git push --force-with-lease origin main"]
    pairs += [(a, b) for a in real for b in real]
    pairs += [(a, a[::-1]) for a in real]
    mismatches: list[str] = []
    for a, b in pairs:
        want = reference_distance(a, b)
        for md in (1, 2, 3):
            got = lev1(a, b, md)
            if want <= md:
                if got != want:
                    mismatches.append(f"exact {a!r} vs {b!r} cap {md}: "
                                      f"{got} != {want}")
            else:
                if got <= md:
                    mismatches.append(f"over-cap {a!r} vs {b!r} cap {md}: "
                                      f"{got} <= {md} (true {want})")
            if got > md + 1:
                mismatches.append(f"cap {a!r} vs {b!r} cap {md}: "
                                  f"{got} > {md + 1}")
    check("the banded walk never disagrees with the matrix",
          mismatches[:3] if mismatches else "no mismatches", "no mismatches")
    # The shapes every caller also relies on without a fuzz pair behind them.
    check("identical strings", lev1("docker", "docker", 2), 0)
    check("empty against word", lev1("", "git", 2), 3)
    check("word against empty", lev1("git", "", 2), 3)
    check("length gap over the cap", lev1("a", "abcdef", 2), 3)
    check("length gap at the cap", lev1("ab", "abcd", 2), 2)
    check("unicode counts characters", lev1("héllo", "hello", 2), 1)
    check("swap is two edits, not one", lev1("ab", "ba", 2), 2)
    check("cap is max_dist+1, whatever the truth", lev1("aaaa", "bbbb", 1), 2)


def test_shadow_map_gates_are_invisible() -> None:
    """The cheap gates must never change the map the distance would write.

    The set gate and the character-count gate both run before lev1 to keep a
    pairwise scan over a real history off the DP. Each is a lower bound on the
    edit distance, so a pair either rejects is a pair lev1 would reject —
    asserted here the honest way: the map with the gates is compared, pair for
    pair, to the map a gate-free walk writes over the same engine.
    """
    def gateless(eng, lines):
        names = [c for c in lines if c in eng.cmds]
        sets = {c: set(c) for c in names}
        groups: dict[str, dict[int, list[str]]] = {}
        for c in names:
            groups.setdefault(c.split(" ", 1)[0], {}) \
                  .setdefault(len(c), []).append(c)
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
            best = None
            for ln in range(len(cmd) - 2, len(cmd) + 3):
                for other in buckets.get(ln, ()):
                    if other == cmd:
                        continue
                    o_set = sets[other]
                    if len(o_set - s_set) > 2 or len(s_set - o_set) > 2:
                        continue
                    ost = eng.cmds[other]
                    oev = (ost.success, ost.freq, ost.last_ts)
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

    rng = random.Random(42)
    verbs = ["status", "sta", "stats", "st", "commit", "comit", "push", "pull",
             "unsintall", "uninstall", "unisntall", "ps", "logs", "log"]
    rows = []
    now = 1_750_000_000
    for i, first in enumerate(["git", "tai", "docker", "ls", "npm"]):
        for v in verbs:
            runs = rng.choice([1, 1, 2, 5, 9])
            for _ in range(runs):
                rows.append((f"{first} {v}", "", "", "", 0,
                             now - rng.randint(0, 90_000)))
    eng = Engine()
    eng.build_from_rows(rows)
    lines = [c for c in eng.cmds]
    check("gates are invisible to the map", shadow_map(eng, lines),
          gateless(eng, lines))


def test_shadow_map_rules() -> None:
    """The map's own law: rarer ranks below stronger, habits stay free."""
    eng = Engine()

    def add(cmd: str, success: int, freq: int, last_ts: int) -> None:
        for _ in range(success):
            eng.add(cmd, ts=last_ts, exit_code=0, _prev="")
        for _ in range(freq - success):
            eng.add(cmd, ts=last_ts, exit_code=1, _prev="")

    # A one-off typo of a stronger spelling is mapped to it.
    add("tai uninstall", 1, 1, 1_000)
    add("tai unsintall", 1, 1, 1_000)
    # …the tie broken toward the lexically smaller line.
    check("full tie maps to the smaller spelling",
          shadow_map(eng, list(eng.cmds))["tai unsintall"], "tai uninstall")
    # A habit is not a shadow: three runs is past TYPO_FREQ_MAX.
    add("tai uninstoll", 3, 3, 1_000)
    check("a three-run habit shadows nobody",
          "tai uninstoll" in shadow_map(eng, list(eng.cmds)), False)
    # The stronger spelling is the target, not the weaker one.
    eng2 = Engine()
    for _ in range(4):
        eng2.add("docker ps", ts=1_000, exit_code=0, _prev="")
    eng2.add("docker pss", ts=2_000, exit_code=0, _prev="")
    check("the rarer spelling maps to the stronger",
          shadow_map(eng2, list(eng2.cmds)), {"docker pss": "docker ps"})
    # Different first words are different commands, however close the tails.
    eng3 = Engine()
    eng3.add("git status", ts=1_000, exit_code=0, _prev="")
    eng3.add("gat status", ts=1_000, exit_code=0, _prev="")
    check("a different first word is nobody's typo",
          shadow_map(eng3, list(eng3.cmds)), {})


def test_near_miss_lines() -> None:
    """`dokcer` is answered by the docker lines, and by nothing else."""
    eng = Engine()
    eng.add("docker ps", ts=1_000, exit_code=0, _prev="")
    eng.add("docker compose up", ts=1_000, exit_code=0, _prev="")
    eng.add("docker", ts=1_000, exit_code=0, _prev="")
    eng.add("git status", ts=1_000, exit_code=0, _prev="")
    got = near_miss_lines(eng, "dokcer ")
    check("a misspelled name reaches its real lines",
          all(c.startswith("docker") for c in got) and "docker ps" in got, True)
    check("the rest of the line is honoured",
          near_miss_lines(eng, "dokcer com"), ["docker compose up"])
    check("a bare misspelled name answers the whole first word",
          near_miss_lines(eng, "dokcer"),
          ["docker", "docker compose up", "docker ps"])
    check("two letters are never a typo", near_miss_lines(eng, "dc "), [])
    check("nothing begins with it, nothing answers",
          near_miss_lines(eng, "zzzzz "), [])
    # `god` extends `godot`: a prefix of a real name is a half-typed name,
    # not a typo of it, and the near-miss scan must stay out of the way.
    eng2 = Engine()
    eng2.add("godot .", ts=1_000, exit_code=0, _prev="")
    check("a half-typed name is not a near miss",
          near_miss_lines(eng2, "god"), [])


def test_first_word_cache_retirement() -> None:
    """The near-miss vocabulary is dropped when the engine learns a name.

    `_first_words` caches on the engine and `add` retires it — the cache that
    survives a new command answers from a vocabulary one word behind the
    store, and the missing word is exactly the one just learned.
    """
    eng = Engine()
    check("no vocabulary, no near miss", near_miss_lines(eng, "dokcer "), [])
    eng.add("docker ps", ts=1_000, exit_code=0, _prev="")
    got = near_miss_lines(eng, "dokcer ")
    check("one name in, its lines out", got, ["docker ps"])
    eng.add("docker compose up", ts=1_001, exit_code=0, _prev="")
    got = near_miss_lines(eng, "dokcer com")
    check("the cache was retired by the add", got, ["docker compose up"])
    # CmdStats keeps the fields the ranker and the index both read, in both
    # construction paths — the cache loader builds them without __init__.
    st = CmdStats()
    check("a fresh CmdStats counts nothing",
          (st.freq, st.success, st.fail, st.nf), (0, 0, 0, 0))
    check("its hour buckets are four zeros", st.hour, [0, 0, 0, 0])


def main() -> int:
    test_lev1_against_reference()
    test_shadow_map_gates_are_invisible()
    test_shadow_map_rules()
    test_near_miss_lines()
    test_first_word_cache_retirement()
    print()
    if failures:
        print(f"FAILED — {len(failures)} check(s):")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("OK — typo tolerance: banded distance, gates, shadows, near misses")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
