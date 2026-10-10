"""The rapid-edit storm: a word typed and taken apart letter by letter.

The report: typing a nonsense word and then removing or changing characters
quickly hung the prompt and took /bin/zsh down. The mechanics: every delete
key lands on a prefix the typing already answered, and the loose scan's
parent shortcut only serves a prefix that *extends* its parent — a deletion
never does — so every delete key paid the full three-tier scan again.
Measured on a 7,600-line index: 171ms per delete key, single keys past a
second, and an auto-repeat delete run outlives the user's patience. The fix
is the memo (a bounded ring keyed on prefix and index generation) plus a
flood bound on the two tier loops; this suite pins the mechanism and the
behaviour, not the clock — a clock here would flake on a loaded CI runner
and pass on a regression small enough to still sting.

The state reader is a widget, not a probe command typed at the prompt:
typing anything at the prompt is itself a storm — every character is a
prefix the hook scans — and a probe would both smash `_TAI_LOOSE_PAR` with
its own lowercased line and clear the buffer it meant to inspect. Its output
is one field per line, because every other shape — one line, space-separated
— shatters on the first value that holds a space, and every interesting
value here is a typed line.
"""
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from plugin_env import *  # noqa: F401,F403  (constants, fixture writers, setup)
from plugin_pty import *  # noqa: F401,F403  (Session, check, probe, failures)

STATE = pathlib.Path("/tmp/tai/tai_storm_state.txt")
BS = "\x7f"
STATE_KEY = "\x0f"  # ^O: the stock accept-and-hold, rebound here to the reader

# The widget definition runs once per session, before any storm; after that
# one keypress writes the memo's state without moving the buffer. The two
# membership probes are this suite's own keys, compared exactly — the ring
# stores the user's text as data, and this is how it is asked about.
STATE_WIDGET = (
    'tai_storm_state() {'
    ' local k has_full=0 has_del=0;'
    ' for k in "${_TAI_LOOSE_MEM_KEYS[@]}"; do'
    '  [[ "$k" == "pull --qqzzx|$_TAI_LOOSE_GEN" ]] && has_full=1;'
    '  [[ "$k" == "pull --qq|$_TAI_LOOSE_GEN" ]] && has_del=1;'
    ' done;'
    f' print -r -- "GEN=[$_TAI_LOOSE_GEN]" > "{STATE}";'
    f' print -r -- "PAR=[$_TAI_LOOSE_PAR]" >> "{STATE}";'
    f' print -r -- "MEM=[${{_TAI_LOOSE_MEM_N}}]" >> "{STATE}";'
    f' print -r -- "HASFULL=[$has_full]" >> "{STATE}";'
    f' print -r -- "HASDEL=[$has_del]" >> "{STATE}"'
    ' }; '
    'zle -N tai_storm_state; bindkey "^O" tai_storm_state')


def _state(s: Session) -> dict:
    STATE.unlink(missing_ok=True)
    s.write(STATE_KEY)
    s.settle()
    out = {}
    for line in STATE.read_text().splitlines():
        found = re.match(r"^([A-Z]+)=\[(.*)\]$", line)
        if found:
            out[found.group(1)] = found.group(2)
    return out


def test_delete_storm_answers_from_the_memo() -> None:
    if not SHELLS["zsh"]:
        return
    print("zsh: the delete storm lands on memo answers")
    s = Session("zsh")
    s.run(STATE_WIDGET)
    st = _state(s)
    # A word that is dead at every prefix: no learned line holds its tail or
    # anything one edit away from it, so the glimpse answers nothing and the
    # memo fills on every keystroke. This is the reported shape — the
    # nonsense word, then the backspace run back down it.
    s.send("pull --qqzzx")
    got = s._dump(clear=False)
    check("dead word previews nothing", got[2], "MENU=[] N=[0] IDX=[0]")
    st = _state(s)
    check("the storm's last prefix is in the memo", st["HASFULL"], "1")
    for _ in range(3):
        s.write(BS)
        s.settle()
    got = s._dump(clear=False)
    check("line after the storm", got[0], "pull --qq")
    check("still no preview on the way back", got[2],
          "MENU=[] N=[0] IDX=[0]")
    # The memo hit restored the parent state: only a hit sets PAR to the
    # prefix it answered, and the delete keys asked for prefixes the typing
    # had already scanned.
    st = _state(s)
    check("the memo restored the parent state", st["PAR"], "pull --qq")
    check("the storm left no shell noise", s.noise(), [])
    s.close()

    # The alive storm: the word is answered on the way up and on the way
    # back down, and every delete key is a revisit of a scanned prefix.
    s = Session("zsh")
    s.run(STATE_WIDGET)
    s.send("pull --rebasex")
    for _ in range(3):
        s.write(BS)
        s.settle()
    got = s._dump(clear=False)
    check("line after the alive storm", got[0], "pull --reba")
    check("memo-hit answer equals the fresh-scan answer",
          got[2], "MENU=[\\n  git pull --rebase] N=[1] IDX=[1]")
    check("the memo restored the parent state",
          _state(s)["PAR"], "pull --reba")
    s.close()

    # The same prefix typed cold, in a shell whose memo is empty: the two
    # roads must agree, or a revisit would answer differently from a first
    # ask — the drift the one-deep cache existed to prevent, now bounded.
    s2 = Session("zsh")
    s2.send("pull --reba")
    got2 = s2._dump(clear=False)
    check("fresh-typed prefix previews the same", got2[2], got[2])
    s2.close()


def test_memo_is_bounded() -> None:
    if not SHELLS["zsh"]:
        return
    print("zsh: the memo ring overwrites its oldest answer")
    s = Session("zsh")
    # Direct scans, three prefixes against a cap of two: the first answer is
    # overwritten, the two latest stand. A memo that only ever grew would
    # hold every prefix a session ever typed, which is the index's job, not
    # the memo's. Membership, not position: which slot a survivor landed in
    # is the ring's business.
    s.run(
        '_TAI_LOOSE_MEM_MAX=2; _tai_loose "aaa-xx"; _tai_loose "bbb-xx"; '
        '_tai_loose "ccc-xx";'
        ' local k has_a=0 has_b=0 has_c=0;'
        ' for k in "${_TAI_LOOSE_MEM_KEYS[@]}"; do'
        '  [[ "$k" == "aaa-xx|"* ]] && has_a=1;'
        '  [[ "$k" == "bbb-xx|"* ]] && has_b=1;'
        '  [[ "$k" == "ccc-xx|"* ]] && has_c=1;'
        f' done; print -r -- "MEM=[${{_TAI_LOOSE_MEM_N}}] '
        f'HASA=[$has_a] HASB=[$has_b] HAS=[$has_c]" > "{STATE}"')
    text = STATE.read_text()
    mem = re.search(r"MEM=\[(\d+)\]", text).group(1)
    check("bounded", int(mem), 2)
    check("oldest overwritten", re.search(r"HASA=\[(\d)\]", text).group(1), "0")
    check("the two latest stand",
          (re.search(r"HASB=\[(\d)\]", text).group(1),
           re.search(r"HAS=\[(\d)\]", text).group(1)), ("1", "1"))
    s.close()


def test_flood_bound_keeps_the_answer() -> None:
    if not SHELLS["zsh"]:
        return
    print("zsh: the flood bound answers when the survivor matches")
    s = Session("zsh")
    # `git stats` matches nothing verbatim; its one-edit spelling `git stat`
    # occurs in `git status`. With the loop's input capped at one line, the
    # one survivor is still tested and the answer stands — the cap bounds the
    # flood, it does not gate the tier.
    s.run("_TAI_LOOSE_FLOOD=1")
    s.send("git stats")
    got = s._dump(clear=False)
    check("one-edit answer survives the cap",
          got[2], "MENU=[\\n  git status] N=[1] IDX=[1]")
    check("the storm left no shell noise", s.noise(), [])
    s.close()


def main() -> int:
    setup()
    test_delete_storm_answers_from_the_memo()
    test_memo_is_bounded()
    test_flood_bound_keeps_the_answer()
    check_fixture_intact("the run")
    print("\nOK — the rapid-edit storm: memo hits, bounded ring, flood bound."
          if not failures else f"\nFAILED ({len(failures)}): {', '.join(failures)}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
