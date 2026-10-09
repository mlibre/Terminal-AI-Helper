"""Build the embedded zsh and bash indexes.

This is deliberately not a service. `tai refresh` writes two small
shell-sourceable snapshots; the plugins load them into associative arrays and
perform suggestions entirely inside the shell. No socket, no daemon, and no
Python process during a keystroke.

Each snapshot stores:
  _TAI_SCORE[command]       base rank
  _TAI_FIRST[first-word]    newline-separated candidates
  _TAI_WORD[word-prefix]    newline-separated candidates
  _TAI_SEQ[previous]        newline-separated candidates
  _TAI_FILE[line-without-its-last-word]  1, when that word is a file

Commands are escaped as POSIX single-quoted strings, which zsh accepts.
"""
from __future__ import annotations

import math
import os
import time
from collections import defaultdict
from pathlib import Path

from tai.engine import (Engine, LEN_PENALTY_DIV, MAX_LEN_PEN, W_FREQ,
                        W_RECENCY, W_SUCCESS)
from tai.knowledge import load_all as load_tool_knowledge
from tai.paths import destinations_first, takes_file
from tai.seed import SEED_COMMANDS
from tai.store import load_rows
from tai.typo import shadow_map

# Where a convention sits. A seed the user has never run is a convention and not
# an observation, so it has no evidence at all and its score is *only* its place
# in the corpus — a band below anything the history actually saw.
#
# The band is the corpus length times the step, so 60 seeds span 0.003..0.18,
# and the weakest real command scores 0.233 (one run, long ago, failed, and
# long enough to pay the full length penalty below). That gap is the whole
# invariant: generated vocabulary ranks below observed usage.
#
# This used to be a flat SEED_BONUS of 1500 added on top of a seed's real score.
# That is not a tiebreak, it is a promotion, and it was large enough to invert
# the rule it was meant to express — on a real history, `cd ..` (4 runs) outranked
# a project directory (10 runs) 6226 to 4963, and 15 seeds sat above the best
# real command in the whole index. A convention is what to say when the history
# is silent; it is not an opinion about a command the history has an opinion
# about.
#
# A seed the user *has* run is not a convention at all — it is history, and it is
# scored as history with no bonus. The corpus orders the corpus; it never
# outranks evidence.
#
# The step has to be big enough to survive ordinary noise, or the ordering it is
# meant to express stops being real: one hour of recency decay is worth ~9 milli,
# so a 1-milli step meant "the user ran `ls -la` an hour ago" silently demoted
# `ls -la` below `ls -l`. It also has to stay *below* the weakest real command:
# with the engine's length penalty baked in (below), a one-run, long-ago, failed
# line scores as little as 0.833 − 0.6 = 0.233, so 60 seeds spaced at 10 milli
# once reached 0.60 — above the floor, and the convention outranked the
# observation it exists to stand in for. 3 keeps every seed below 0.18, real
# commands above the band on every history, and the seeds themselves strictly
# ordered by a step that no longer has to fight anything.
SEED_RANK_STEP = 3

# Vocabulary generated from a tool's `--help` is a guess until the history says
# otherwise, so it ranks below what the same words would score had the history
# actually run them: its base score is multiplied by COLD_FACTOR, one and the
# same down-scaling for every generated line. The history is the only thing
# that turns a guess into a rank of its own.
COLD_FACTOR = 0.6
# A tool that appears in the history is worth its full verb and flag dump; one
# that does not is worth two lines, because the name alone is an echo of what is
# already typed and `--help` is the only honest thing to say about a command
# the history has never seen.
TOOL_CANDIDATE_CAP = 32
# How many `tool --flag` lines one tool may contribute. The help of a large CLI
# documents hundreds of options, and every one of them becomes an index entry and
# a shell-startup cost for a flag the user has probably never typed. Only tools
# that appear in the history reach this cap at all.
TOOL_FLAG_CAP = 12
# How many subcommands of one tool get their flags indexed, and how many flags one
# of those subcommands contributes. Both are small on purpose, and the reason is
# the one above: these are the entries that are paid for at every shell startup.
# The discovery side already chose *which* subcommands by how often the history
# runs them, so this cap only has to stop one heavily-used tool from taking the
# index on its own — `docker run` alone would otherwise add 104 lines.
TOOL_SUB_CAP = 4
TOOL_SUBFLAG_CAP = 10
# How many sub-subcommands one subcommand contributes, such as the twenty-odd
# verbs under `gh pr`. Lower than the flag cap because a verb is something you
# type and an option is something you scan past: eight names is a menu a person
# chooses from, and it is still a shortlist rather than a transcript of the tool.
TOOL_SUBSUB_CAP = 8
# How many commands one `_TAI_SEQ` key keeps. The key answers one question — what
# did I run after this — and the hint takes the best of them, so forty was a
# shortlist nobody could read and, on a real history, a quarter of the index.
SEQ_CANDIDATE_CAP = 15
# How many candidates one `_TAI_WORD` key holds. The short keys are the expensive
# ones — `ls` holds every `ls …` the shell has ever run — and the plugin asks a key
# for the best few, never for all of them.
WORD_CANDIDATE_CAP = 20
# How many words deep a `_TAI_WORD` key goes. Not a guess about how long your
# commands are: the keys exist so `ls -l` can find `ls -la`, and after a handful
# of words the answer is the file you are typing, which zsh's own completion and
# tai's filesystem argument both do better. One pasted 90-word command was writing
# 90 keys, each holding the whole line — on a real history that tail was most of
# the index and most of the startup cost.
WORD_KEY_MAX_DEPTH = 8

# A one-off that nearly duplicates a stronger line is a typo of it, and it
# ranks just below that line instead of beside it — `tai un` must offer
# `tai uninstall`, not the `tai unsintall` recorded beside it, however the
# three rows' frequencies, timestamps and exit codes happen to tie. The rule
# and its evidence order live in tai/typo.py (shadow_map, imported above):
# one rule, applied here to the integer scores the plugins read and in the
# engine to the floats `tai suggest` answers with, so the two rankers cannot
# drift apart.


def index_path() -> Path:
    from tai.paths import data_dir
    return Path(os.environ.get("TAI_INDEX") or data_dir() / "zsh-index.zsh")


def bash_index_path() -> Path:
    """The bash index, which is one file name away from the zsh one.

    `TAI_INDEX` names the zsh file, so the bash file is derived from it rather
    than from a second rule: the two names have to stay one decision, and
    `tai doctor` reads both.
    """
    return index_path().with_name("bash-index.bash")


def _zq(value: str) -> str:
    return "'" + str(value).replace("'", "'\\''") + "'"


def _word_value(values: list[str]) -> str:
    """One `_TAI_WORD` entry: the key's candidates, capped, newline-joined.

    Both writers call this, which is the point. The cap was applied to the zsh
    index and not the bash one — two lines apart in the same function — so bash
    kept the full candidate list for every word key, which is the 3.9MB of a
    4.1MB index that the cap exists to avoid, and the two plugins read a
    different number of candidates for the same key.

    `cmds` is ranked best-first, so the head of each list is the best answer for
    that key. Twenty is well past what _tai_best or the menu can show, and the
    complete list for a command name is still in _TAI_FIRST, so nothing becomes
    unreachable: `9router --p` asks for `9router`, not for a word key.
    """
    return chr(10).join(values[:WORD_CANDIDATE_CAP])


def build(max_commands: int = 20000) -> int:
    """Write both shell indexes atomically and return the command count.

    The count is the only thing a caller needs: the file paths are already
    known from index_path(), and a diagnostic run that wants row and stale
    counts reads them from the database and the path check directly.

    A store that cannot be read aborts the build rather than producing one. An
    empty history and an unreadable one used to look identical here, so a single
    corrupt or locked database replaced a real index with the seed corpus and
    the caller reported success — the one failure that destroys the last good
    state and says nothing while doing it.
    """
    eng = Engine()
    rows = load_rows(50000)
    if not rows and index_path().exists():
        from tai.store import schema_note
        note = schema_note()
        if note:
            raise RuntimeError(f"cannot read the history database: {note}")
    eng.build_from_rows(rows)
    from_history = set(eng.cmds)

    # Universal cold-start vocabulary. These are suggestions, never actions.
    for cmd in SEED_COMMANDS:
        if cmd not in eng.cmds:
            eng.add(cmd, _prev="")
            eng.cmds[cmd].last_ts = int(time.time())

    generated: set[str] = set()
    typed = {cmd.split()[0] for cmd in eng.cmds if cmd.split()}

    # Add conservative knowledge candidates for installed CLI tools. These
    # are generated from --help/man only; no suggested command is executed.
    # Vocabulary for the tools whose first word appears in the history: their real
    # subcommands and flags. A tool that is installed but never used is
    # deliberately *not* indexed. The plugins already answer `tool --help` for any
    # name on PATH using a builtin hash lookup, so an index copy of that answer
    # would buy nothing and would cost two entries and four word keys per unused
    # tool — hundreds of keys on a machine with a few dozen such tools, paid at
    # every shell startup, for tools the user has never shown any sign of using.
    from tai.knowledge import candidates_for_prefix
    for tool in load_tool_knowledge():
        if tool.name not in typed:
            continue
        cands = candidates_for_prefix(tool.name, [tool], cap=TOOL_CANDIDATE_CAP)
        # A second pass with a flag-shaped prefix, because the generator only
        # offers flags when the prefix it is given already extends past the
        # command name. Without this the flags in the cached help never reach
        # the shell, and `tool -` can only ever complete to `tool --help`.
        cands += candidates_for_prefix(f"{tool.name} -", [tool], cap=TOOL_FLAG_CAP)
        # The flags of the subcommands this history actually runs, for a tool that
        # answers for itself. Without them `docker run --` can only complete to
        # `docker run --help`, which teaches nothing, and the 104 options `docker
        # run` genuinely accepts are invisible — while `docker --blkio-weight`,
        # which docker rejects, would be the sort of thing a flat flag list offers.
        for sub in list(tool.subs)[:TOOL_SUB_CAP]:
            cands += candidates_for_prefix(f"{tool.name} {sub} -", [tool],
                                           cap=TOOL_SUBFLAG_CAP)
        # …and the subcommands those subcommands have, which is where `gh pr
        # checkout` and `gh repo view` live. Capped lower than the flags above
        # because these are verbs a user actually types rather than options they
        # scan: eight of them is a menu, forty is a list nobody reads.
        for sub in list(tool.subverbs)[:TOOL_SUB_CAP]:
            cands += candidates_for_prefix(f"{tool.name} {sub} ", [tool],
                                           cap=TOOL_SUBSUB_CAP)
        for cmd in cands:
            generated.add(cmd)
            if cmd not in eng.cmds:
                eng.add(cmd, _prev="")
                eng.cmds[cmd].last_ts = int(time.time())

    # A command whose paths are gone is not a suggestion. It stays in SQLite,
    # so it comes back on its own if the directory returns; it just never wins.
    # Recorded evidence alone — no fallback to the directory the rebuild ran
    # in. The snapshot is read in every directory, so judging a relative path
    # from the rebuild's own directory strips commands that are real wherever
    # they were actually run: `cd tmp/fun-game/`, recorded before cwd capture
    # existed, vanished from the index because the rebuild fired outside ~.
    from tai.paths import stale_in
    stale = stale_in(eng)
    if stale:
        for cmd in stale:
            eng.cmds.pop(cmd, None)
        eng.sorted_cmds = [c for c in eng.sorted_cmds if c not in stale]
        # The filter is order-preserving, but the engine's sorted-list contract
        # is "sorted by the time it is read, not before": mark it so any later
        # reader re-sorts rather than trusting the filter to have kept order.
        eng._cmds_dirty = True
        # The sequence table as well, and it is the one that leaks. A stale
        # command dropped from `cmds` is gone from _TAI_SCORE, _TAI_FIRST and
        # _TAI_WORD — and still sat in `eng.seq`, which is written verbatim, so
        # it came back on an *empty* prompt, where the hint is whatever followed
        # the last command. A command that is not a suggestion anywhere else was
        # therefore still a suggestion here, and it scored 0 rather than being
        # absent. Both directions matter: a stale command must not *follow* one
        # either, or `git checkout` offers `make ~build` — a path already purged.
        # And a stale command must not be a *key*, which is the direction that is
        # easier to miss: `_TAI_SEQ['cd /nowhere/gone']` is read on an empty
        # prompt right after the user ran that very command, which is exactly
        # when it is most tempting to answer with that line's successors.
        eng.seq = {
            prev: {cmd: n for cmd, n in counter.items() if cmd not in stale}
            for prev, counter in eng.seq.items() if prev not in stale
        }
    if rows and not (from_history - stale):
        # Every command the user has actually run is judged missing. Publishing an
        # index here would replace everything they learned with the seed corpus —
        # the same destruction as a corrupt store, arrived at honestly rather than
        # by accident, and `tai purge --stale` is the way out.
        #
        # `from_history - stale`, not `eng.cmds`: the seed corpus and the generated
        # tool vocabulary were added above, so `eng.cmds` is never empty here and
        # asking about it could never fire. That is what made this guard dead: the
        # case it exists for reached `build()` and was answered with 69 commands,
        # every one of them a convention, and a green ✓.
        #
        # An exception, not sys.exit: a library that ends the process cannot be
        # called from the background `record` process, and cannot be tested. The
        # message says what to do, because "failed" with no next step is the same
        # silence as a green ✓ for a store that learned nothing.
        raise RuntimeError("every stored command names a path that is gone — "
                           "run 'tai purge --stale' to drop them, then refresh")

    # Keep the most useful distinct commands. The three signals the index can
    # evaluate without a process are the same three the engine weights, and they
    # are read from the engine rather than repeated: `tai tune` searches these
    # constants and writes them back into engine.py, and a hardcoded copy here
    # meant every tuned run reported success and changed nothing a user sees.
    now = int(time.time())
    # Membership is asked twice per command below, and `SEED_COMMANDS` is a
    # list: `cmd in SEED_COMMANDS` scanned it for every command in the store.
    # A set built once is the same question without the scan; `.index` is only
    # asked of the handful of commands that are actually seeds.
    seed_set = frozenset(SEED_COMMANDS)
    n_seeds = len(SEED_COMMANDS)
    scored: list[tuple[int, str]] = []
    for cmd, st in eng.cmds.items():
        # A seed the user has never run is a convention, not an observation. It
        # gets no recency credit for a timestamp it never had, so its score is
        # decided by where it sits in the corpus — which is the whole point of
        # having an ordered corpus. A seed the user *has* run is scored as
        # history, with nothing added: the history is the evidence, and the
        # corpus has no vote about a command the user has already answered.
        pure = cmd in seed_set and cmd not in from_history
        if pure:
            seed_rank = SEED_COMMANDS.index(cmd)
            scored.append((int((n_seeds - seed_rank) * SEED_RANK_STEP), cmd))
            continue
        age_days = max(0, (now - (st.last_ts or now)) / 86400)
        recency = math.exp(-age_days / 14.0)
        freq = min(math.log1p(st.freq) / math.log1p(20), 1.0)
        success = max(-0.5, min(1.0, (st.success - st.fail * 0.5) / max(st.freq, 1))) * 0.5 + 0.5
        score = W_FREQ * freq + W_RECENCY * recency + W_SUCCESS * success
        if cmd in generated and cmd not in seed_set:
            score *= COLD_FACTOR
        # The engine's length penalty, baked into the scores the plugins read —
        # without it the two rankers disagree about exactly the lines a
        # first-word ghost has to choose between: a three-run `go mod download`
        # outranked a two-run `go mod vendor` here while `tai suggest` and the
        # dashboard put the shorter, fresher line first, so the hint offered
        # the download and the panel said vendor. The penalty is linear in the
        # line's length and its *difference* between two candidates is
        # (len(a) − len(b)) / LEN_PENALTY_DIV whatever prefix the engine is
        # asked, so subtracting it once here — against a one-character query,
        # the shortest there is — reproduces the engine's per-query ordering,
        # not merely its formula. The constants are the engine's own (tai tune
        # writes them back into engine.py, not here), and pure seeds keep
        # their corpus band: a convention has no length, only a place.
        score -= min(max(0, len(cmd) - 1) / LEN_PENALTY_DIV, MAX_LEN_PEN)
        # Integer milli-score keeps comparisons native and process-free in zsh/bash.
        scored.append((int(round(score * 1000)), cmd))
    # One-off typos rank just below the line they shadow — tai/typo.py owns
    # the rule; here it lands on the integer scores. Two passes, so a typo of
    # a typo lands below its own target whichever order the map iterates in.
    score_of = {cmd: s for s, cmd in scored}
    shadows = shadow_map(eng, from_history)
    for _ in range(2):
        for typo, partner in shadows.items():
            cap = score_of.get(partner)
            if cap is not None and score_of[typo] > cap - 1:
                score_of[typo] = cap - 1
    scored = [(score_of[cmd], cmd) for _, cmd in scored]
    ranked = destinations_first(scored)
    ranked.sort(reverse=True)
    ranked = ranked[:max_commands]
    cmds = [cmd for _, cmd in ranked]
    scores = {cmd: score for score, cmd in ranked}

    first: dict[str, list[str]] = defaultdict(list)
    words: dict[str, list[str]] = defaultdict(list)
    # `cmds` is already best-first, so every list built here is too, and that is
    # the ranking the zsh menu shows. `_tai_best` re-ranks by _TAI_SCORE anyway
    # to pick a single winner, so nothing depends on this — it is only the order
    # the menu reads.
    for cmd in cmds:
        parts = cmd.split()
        if parts:
            first[parts[0]].append(cmd)
        # Word-boundary prefixes: "docker ", "docker compose ", etc. Two entries
        # deliberately *not* written, both of which used to double the size of the
        # largest map in the index:
        #
        #   * the same key again with a trailing space. No lookup can ask for one —
        #     every key in both plugins is `${prefix% *}`, which has no trailing
        #     space — so they were a copy of the file that nothing ever read.
        #   * the single-word prefix, which holds exactly what `_TAI_FIRST` holds
        #     for that command. `ls -` looks the key `ls` up, so it still needs an
        #     answer; it gets it from `_TAI_FIRST` instead, which is the same list.
        for i in range(2, min(len(parts), WORD_KEY_MAX_DEPTH) + 1):
            words[" ".join(parts[:i])].append(cmd)

    seq: dict[str, list[str]] = defaultdict(list)
    for prev, counter in eng.seq.items():
        # Sorted here, best first, because a shell now reads the order as the
        # ranking and not only as a tiebreak: the zsh menu shows a key's values in
        # the order they are stored, while _tai_best still re-ranks by
        # _TAI_SCORE to pick one. It used to add `count * 2.5` to a milli-score,
        # which is 0.08% of the value it was added to: the list looked
        # sequence-ranked and was really just base-score ranked with extra steps.
        values = [cmd for cmd, _ in sorted(
            ((cmd, scores.get(cmd, 0)) for cmd in counter),
            key=lambda x: (-x[1], x[0]))[:SEQ_CANDIDATE_CAP]]
        # A key whose every follower was stale has no answer left, and writing
        # it as an empty string puts `_TAI_SEQ[key] = ''` in the file. The zsh
        # plugin splits that into one empty line and the bash one reads it as an
        # empty candidate, so a key that can answer nothing is stored as though
        # it had answered nothing — for every key the stale filter emptied.
        if values:
            seq[prev] = values

    # Which lines end in a file, keyed the way every other key here is: the line's
    # own words without the last one, which is exactly the key a shell looks up
    # when the cursor is on that last word. `_TAI_FILE[chmod +x]` is what tells the
    # plugin that the word being typed after `chmod +x` is a path, and so that the
    # filesystem — not the history — is what answers it.
    #
    # Only the head of the key is written, never the whole line: the mark is about
    # the *next* word, so `chmod +x` marks the file after it and `chmod +x script`
    # itself is marked by nothing, which is right, because its last word is a file
    # that has already been consumed.
    file_keys: set[str] = set()
    for cmd in cmds:
        if takes_file(cmd):
            head = cmd.rsplit(" ", 1)[0]
            if head:
                file_keys.add(head)

    zsh_file = index_path()
    zsh_file.parent.mkdir(parents=True, exist_ok=True)
    bash_file = bash_index_path()
    tmp = zsh_file.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        f.write("# tai embedded zsh index; generated, do not edit\n")
        f.write("typeset -gA _TAI_SCORE _TAI_FIRST _TAI_WORD _TAI_SEQ _TAI_FILE\n")
        # `_TAI_X+=(key value)` — a pair appended to an associative array, one
        # builtin call per entry and no function call at all.
        #
        # Two earlier shapes were measured on a real 38k-entry index. Assigning
        # with a literal subscript (`_TAI_WORD['k']='v'`) is a syntax error for a
        # key like `']'`, because zsh expands inside a subscript — 572ms and a
        # broken load. Passing the key to a helper function as `$2` is safe but
        # costs a shell function call per entry, and 572ms. Appending a pair
        # takes the key as an ordinary word, which is never re-parsed as code, and
        # the same 38k entries load in ~15ms. Half a second of every shell's
        # startup is not a price worth paying to save a quoting worry.
        for score, cmd in ranked:
            f.write(f"_TAI_SCORE+=({_zq(cmd)} {score})\n")
        for key, values in sorted(first.items()):
            f.write(f"_TAI_FIRST+=({_zq(key)} {_zq(chr(10).join(values))})\n")
        for key, values in sorted(words.items()):
            f.write(f"_TAI_WORD+=({_zq(key)} {_zq(_word_value(values))})\n")
        for key, values in sorted(seq.items()):
            f.write(f"_TAI_SEQ+=({_zq(key)} {_zq(chr(10).join(values))})\n")
        for key in sorted(file_keys):
            f.write(f"_TAI_FILE+=({_zq(key)} 1)\n")
    os.replace(tmp, zsh_file)

    bash_tmp = bash_file.with_suffix(".tmp")
    with bash_tmp.open("w", encoding="utf-8") as f:
        f.write("# tai embedded bash index; generated, do not edit\n")
        f.write("declare -gA _TAI_SCORE _TAI_FIRST _TAI_WORD _TAI_SEQ _TAI_FILE\n")
        for score, cmd in ranked:
            f.write(f"_TAI_SCORE[{_zq(cmd)}]={score}\n")
        for key, values in sorted(first.items()):
            f.write(f"_TAI_FIRST[{_zq(key)}]={_zq(chr(10).join(values))}\n")
        for key, values in sorted(words.items()):
            f.write(f"_TAI_WORD[{_zq(key)}]={_zq(_word_value(values))}\n")
        for key, values in sorted(seq.items()):
            f.write(f"_TAI_SEQ[{_zq(key)}]={_zq(chr(10).join(values))}\n")
        for key in sorted(file_keys):
            f.write(f"_TAI_FILE[{_zq(key)}]=1\n")
    os.replace(bash_tmp, bash_file)
    _compile_zsh_index()
    return len(cmds)


def _compile_zsh_index() -> None:
    """Compile the zsh index next to itself — the source-time cost halves.

    `zsh-index.zsh.zwc` beside a zsh script is used by zsh whenever its
    recorded source timestamp matches the script's mtime, and zsh falls back
    to sourcing the script itself when there is no match (e.g. a torn
    compilation was killed and sent nowhere). Bytecode goes through the same
    variable assignments, so the loaded maps are unchanged — only the per-
    entry tokenization and quote-parsing of a 3MB file is skipped, measured
    at ~2× faster on a real install.

    An absent zcompile, a failed compile, or no zsh at all is not an error:
    the text index alone is always the whole story the tests read, and the
    plugin performs every check against the file.
    """
    import shutil
    import subprocess

    zsh_file = index_path()
    zsh = shutil.which("zsh")
    if zsh is None or not zsh_file.exists():
        return
    zwc = pathlib_path = zsh_file.with_suffix(zsh_file.suffix + ".zwc")
    try:
        got = subprocess.run([zsh, "-fc", f"zcompile {zsh_file!s}"],
                             capture_output=True, timeout=120)
        if got.returncode != 0 or not zwc.exists():
            zwc.unlink(missing_ok=True)
    except (OSError, subprocess.SubprocessError):
        zwc.unlink(missing_ok=True)
