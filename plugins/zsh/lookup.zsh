# Turning a line into candidates: the index lookup, the ranking, and the one
# answer the ghost text shows.

# Every line that shares this line's longest complete word, wrapper transparency
# included, best first — the index stores each key's values in descending
# _TAI_SCORE order, so order is the ranking.
#
# A global rather than a return value, and that is the whole reason: the ghost
# text asks for the best of these on every keystroke and the menu asks for all
# of them on a Tab, and neither can afford a subshell. Both read this one
# lookup, which is also what stops them drifting apart: a word the ghost text
# would suggest is a word the menu can offer.
typeset -ga _TAI_LINES
# The wrapper, if any, that has to go back in front of every line. It is kept
# apart from the lines because a score is the score of the command the user ran,
# not of the wrapper in front of it.
typeset -g _TAI_LINES_LEAD=""
# The winner of the last _tai_best, for the same reason.
typeset -g _TAI_BEST=""
# 1 when that winner is the installed-command `--help` fallback rather than
# something the user has run. Tab reads it, because that one hint is not a
# completion of the word being typed: `docker ` asks which container, and the
# list is the answer while `docker --help` is a guess at what you meant.
typeset -gi _TAI_BEST_HELP=0
# What a path argument could be: the files that are here, newest first, and the
# one tai learned for this line if it is still one of them.
typeset -ga _TAI_FRESH _TAI_FILES
# Beside _TAI_FILES: 1 when that entry is a name the filesystem gave us and has
# to be quoted before a shell will pass it as one word, 0 when it is the word of
# a line the user ran and is already the text they typed. The menu writes entries
# from three sources — this answer, `$commands`, and a glob of the current
# directory — and only the last two are names, so the flag travels with the
# candidate rather than being guessed at the moment it is written.
typeset -ga _TAI_FILES_Q
# The part of the line in front of the word these files complete. A file is a
# *word*, so a whole-line answer is this plus one of them — not the line plus one,
# which would repeat the word that was already typed.
typeset -g _TAI_FILE_JOIN=""
# 1 when the current _TAI_BEST is an entry _TAI_FILES_Q says needs quoting.
typeset -gi _TAI_BEST_Q=0

# A stored command that carries raw control bytes can never be painted: the
# ghost and the loose menu write candidates into POSTDISPLAY, and to this
# terminal those bytes are escape sequences, not text. Such rows used to land
# in the index from paste markers that made it into the recorded buffer, and
# an ESC at the head of a ghost line went to the screen raw. The store refuses
# them at record time now, but a shell sources the index it *finds* — this is
# the last line of the defence, where candidates are about to become bytes on
# screen. Any C0 control or DEL: ESC is one of them, a tab would smear a
# menu row, and a newline would turn one candidate into two cells.
_tai_clean() {
  # One pattern test, not one per character: this runs once per candidate per
  # keystroke, and a per-char loop over every candidate measurably cost more
  # than the whole rest of the lookup. ${~_TAI_CTRL} forces pattern reading:
  # the raw value would be matched as a literal string.
  [[ "$1" == *${~_TAI_CTRL}* ]] && return 1
  return 0
}
_TAI_CTRL=$'[\x01-\x1f\x7f]'

# Command names considered for a half-typed command name, in one lookup. A key
# holds at most WORD_CANDIDATE_CAP lines, so this bounds the lines too. The widest
# range on a real 6k-command history was 46 keys; it bites only on a first letter
# shared by hundreds of commands, where the answer is a guess either way.
typeset -gi _TAI_PREFIX_KEYS=64

# The candidate lines for a command name — the word before the first space — into
# _TAI_VALUES, newline-joined the way the index stores them.
#
# The index keys _TAI_FIRST by the *whole* name, so `godot` is a key and `god` is
# not, and a half-typed command is the ordinary state of a line being written. The
# exact name is the fast path; on a miss, the names that *begin* with it. That is
# the same prefix range `tai suggest` walks over its sorted command names, and
# the two answering differently is the bug this closes: `tai suggest god` said
# `godot .` while the plugin said nothing at all.
#
# `(I)` is zsh's C-level pattern match on associative keys — 0.07ms across 754
# keys, where a shell loop over them costs more than the redraw this sits in front
# of. The order it returns is the order the keys were inserted, and the generator
# writes them sorted, so the candidates are the same on every run and the same as
# bash's, which reaches the same names in a different order and lets the scores
# decide between them.
#
# No fork, deliberately: this runs on the ghost-text path, where a subshell is the
# one thing that cannot be afforded, so it answers in a global like _TAI_LINES.
_tai_first_values() {
  local word="$1" values="" k n=0
  # An empty key is not a command name, and an empty subscript is an error rather
  # than a miss in bash, so the lookup does not happen at all here.
  [[ -n "$word" ]] || { _TAI_VALUES=""; return }
  values="${_TAI_FIRST[$word]:-}"
  # A glob character in the word would make the pattern mean something other than
  # "begins with the word" — zsh's `(I)` does not honour a backslash escape here —
  # and no command is spelled with one. Answer nothing rather than something else.
  if [[ -z "$values" && "$word" != *[\*\?\[]* ]]; then
    for k in "${(@k)_TAI_FIRST[(I)${word}*]}"; do
      values+="${_TAI_FIRST[$k]}"$'\n'
      (( ++n >= _TAI_PREFIX_KEYS )) && break
    done
    values="${values%$'\n'}"
  fi
  _TAI_VALUES="$values"
}
typeset -g _TAI_VALUES=""

_tai_lines() {
  local prefix="$1" word rest head key values c
  _TAI_LINES=()
  _TAI_LINES_LEAD=""
  if [[ "$prefix" == *' '* ]]; then
    # Use the last complete word as the key. The generator writes one key per
    # cumulative word boundary, so the shorter key always holds a superset of
    # the candidates the exact-prefix key holds — and the superset is what makes
    # `ls -l` extend to `ls -la`.
    word="${prefix% *}"
    # A key with no space in it is the command name itself, and the index keeps
    # that list once, in _TAI_FIRST rather than twice. `ls -` looks `ls` up here,
    # so this branch is what answers it — without a second copy of every command
    # a shell has ever run, which on a real history was the largest thing in the
    # index and the reason sourcing it took a fifth of a second.
    if [[ "$word" == *" "* ]]; then
      [[ -n "$word" ]] && values="${_TAI_WORD[$word]:-}"
    else
      _tai_first_values "$word"
      values="$_TAI_VALUES"
    fi
  else
    _tai_first_values "$prefix"
    values="$_TAI_VALUES"
  fi
  [[ -n "$values" ]] && { for c in "${(@f)values}"; do _tai_clean "$c" && _TAI_LINES+=( "$c" ); done } && return
  [[ "$prefix" == *' '* ]] || return
  # Nothing for the whole line: rank the line behind the wrapper, then put the
  # wrapper back. `sudo git ` completes from the `git ...` the user has run.
  # Before the `--help` fallback, or that answers `sudo git ` with
  # `sudo --help` and the wrapper is never even looked at.
  head="${prefix%% *}"
  rest="${prefix#* }"
  (( ${_TAI_WRAPPERS[(Ie)$head]} )) || return
  [[ -n "$rest" && "$rest" != *=* ]] || return
  key="${rest%% *}"
  [[ -n "$key" ]] || return
  # The same lookup the branch above makes, and for the same reason. A key with
  # no space in it is the command name itself, and the index keeps that list
  # once, in _TAI_FIRST — `tai/index.py` writes _TAI_WORD keys from two words on,
  # so reading a one-word key there is a miss against every real index and
  # wrapper transparency silently answered nothing at all.
  if [[ "$key" == *" "* ]]; then
    values="${_TAI_WORD[$key]:-}"
  else
    _tai_first_values "$key"
    values="$_TAI_VALUES"
  fi
  [[ -n "$values" ]] && _TAI_LINES_LEAD="$head "
  for c in "${(@f)values}"; do _tai_clean "$c" && _TAI_LINES+=( "$c" ); done
}

# The best line in _TAI_LINES that extends the prefix, into _TAI_BEST.
#
# A candidate identical to what is already typed is skipped. It can never be
# rendered as ghost text, so letting it win only hides the candidate that could
# have extended the line: without this, `ls -l` beats `ls -la` and the hint for
# `ls -l` is nothing at all.
_tai_best() {
  local prefix="$1" c line best="" best_score=-1 s
  _TAI_BEST=""
  for c in "${_TAI_LINES[@]}"; do
    line="$_TAI_LINES_LEAD$c"
    [[ -z "$c" || "$line" == "$prefix" || "$line" != "$prefix"* ]] && continue
    s="${_TAI_SCORE[$c]:-0}"
    if (( s > best_score )); then best="$c"; best_score="$s"; fi
  done
  [[ -n "$best" ]] && _TAI_BEST="$_TAI_LINES_LEAD$best"
}

# An installed command the history has never seen. `--help` is the only thing
# worth offering: the bare name is an echo of what is already typed, and any
# flag would be a guess. The check is a shell builtin hash lookup, so it costs
# nothing and forks nothing — and it only runs when the index has no answer, so
# a tool with learned knowledge never reaches it.
_tai_installed() { (( $+commands[$1] )) }

# Words that run the command after them rather than being the command. The
# history is full of `git ...` and the shell is asked for `sudo git ...`, so
# without treating the wrapper as transparent the whole vocabulary behind it is
# invisible. A closed list, and deliberately without `env` or `xargs`: both change
# what follows them enough that putting the wrapper back would be a lie.
_TAI_WRAPPERS=(sudo doas nohup time nice ionice stdbuf command)

# Aline that is not a prefix of any command left this slot with only the
# installed-command guess, while there may still be something the history knows
# where every word of the line appears. The fallback below is the loose half of
# the same job: it answers nothing directly, only lists what mentions the words.
typeset -ga _TAI_LOOSE_LINES=()
# The least that qualifies the line as having been tapped out: a shorter prefix
# is more likely an unfinished thought than a query.
typeset -gi _TAI_LOOSE_MIN_PREFIX=3
# Enough for a glance, not so many that a screen of them outlives the line.
typeset -gi _TAI_LOOSE_CAP=16

# The one line tai would complete, into _TAI_BEST. The menu reads the same
# candidates through _tai_lines and takes all of them; this takes the winner.
_tai_query() {
  local prefix="$1" last="$2" first
  _TAI_BEST_HELP=0; _TAI_BEST_Q=0
  _TAI_LOOSE_LINES=()
  if [[ -z "$prefix" ]]; then
    # An empty prompt predicts the command that followed the last one.
    _TAI_LINES=()
    _TAI_LINES_LEAD=""
    local c
    [[ -n "$last" ]] && for c in "${(@f)_TAI_SEQ[$last]}"; do _tai_clean "$c" && _TAI_LINES+=( "$c" ); done
    _tai_best ""
    return
  fi
  _tai_lines "$prefix"
  _tai_best "$prefix"
  # A path argument is answered by the filesystem, not by the history. The
  # reported case: `chmod +x ` on a history holding only `chmod +x script`, where
  # `script` is not a file here and the file you just downloaded is the one you
  # want. Read through the same lookup the menu makes, so the two cannot answer
  # different questions about the same word.
  # The hint is the whole line, so the file — a *word* — has to replace the word
  # being typed rather than be appended after it. Appending is right for `chmod +x `
  # (nothing typed yet) and wrong for `chmod +x ~/Downloads/f`, where it produced
  # `chmod +x ~/Downloads/f~/Downloads/freebuff…`. _TAI_FILE_JOIN is what was
  # already typed, so the line is that plus the answer.
  if _tai_file_answer "$prefix" && [[ -n "${_TAI_FILES[1]}" ]]; then
    _TAI_BEST="$_TAI_FILE_JOIN${_TAI_FILES[1]}"
    _TAI_BEST_Q=${_TAI_FILES_Q[1]:-0}
  fi
  if [[ -z "$_TAI_BEST" ]]; then
    first="${prefix%% *}"
    # Last resort, and only while the line is still just the command name or has
    # just opened its first argument. `9router --p` is not answered with
    # `9router --help`; that would be a value the plugin then has to discard as
    # "not an extension".
    if [[ "$prefix" == "$first" || "$prefix" == *' ' ]] && _tai_installed "$first"; then
      _TAI_BEST="$first --help"
      _TAI_BEST_HELP=1
    fi
  fi
  if [[ -z "$_TAI_BEST" && "$prefix" != *$'\n'* && ${#prefix} -ge $_TAI_LOOSE_MIN_PREFIX ]]; then
    _tai_loose "$prefix"
  fi
}

# The commands from the history that mention every word typed, case
# insensitively and in any order: the answer to "I remember some of the words"
# when nothing extends the line exactly. Prefix lookup answers what fits the
# letters typed; this answers what they might have once remembered. Both come
# from the same learned commands, so there is no second vocabulary.
#
# Typo tolerance: the user does not always spell the name they mean — "gst" is
# a tap away from `git status`. A typed word therefore matches a line when the
# word occurs verbatim anywhere in it, or when *every letter of the word, in
# order*, is somewhere in the line — the letters may have anything between them,
# but none of them may be absent. That is the whole of the rule, and each clause
# is a rule of its own:
#
#   * "every letter, in order" is what keeps a typo a typo: `gst` reads across
#     `git status` because g, then s, then t all sit in it.
#   * "none of them absent" is what keeps it from becoming noise. A matcher that
#     may *drop* a letter of the word answers `tzz_dir/` to `cd tzz_dir`, then
#     opens a menu under a directory the user is already standing in, and lets
#     every half-typed word reach every line with the letters — the two bugs
#     this rule was written after.
#   * a word of two letters is not fuzzy at all. `cd` reaching every line with
#     a c then a d is not a memory of the user's, it is arithmetic.
#
# The scan is written as a regex built once per typed word, so the engine runs
# it in C instead of a zsh loop over the letters of every learned line: 7k lines
# cost milliseconds per redraw instead of most of a second.

# What this scan refuses to be asked. Both bounds are about cost. The figures
# are one redraw on a real history — 7668 commands, 3.2MB of index, ~110ms to
# source — so they are scan time with that load already paid.
#
# The line: longer than this is not a fragment of something remembered, it is a
# command being pasted or edited, and the prefix lookup that ran before this one
# already answers what can be extended. Unbounded, a 6KB pasted line cost a
# second of scan per redraw to answer "what mentions `curl` and
# `--compressed`", which is not a question a prompt should ever ask.
typeset -gi _TAI_LOOSE_MAX_LINE=200
# The word: the pattern below joins every letter with `*`, so a word of N letters
# costs the matcher N passes over each learned line's characters. Measured, one
# redraw at 1.7s for a 500-character word and 3.9s at 1100 — the cost is not
# linear in anything a person can see. A word past this is the signature of a
# paste, so it is matched as a plain substring instead of becoming a pattern:
# the same visible answer for exact text, 85ms rather than 34s on a 6KB token.
# It is matched, not dropped — see the note where `rxs` is built.
typeset -gi _TAI_LOOSE_MAX_WORD=32

# The in-order check for one word, as a regex: its letters joined by `.*`, each
# escaped so a word holding `-`, `/` or `[` is letters and not syntax. Measured:
# the same check through the letters-as-a-glob pattern is not merely slower —
# on an adversarial spelling (`npm` gives it `n…p…m` shapes across every
# `--header` of a 1KB URL, and an `aaaa…` word *thirty-two* rounds past the
# escaped letter count) the glob engine does not come back on a keystroke at
# all, while the same text answers as a regex in ~0 s. There is no operator
# for it in zsh that is both this fast and safe, so it is spelled as one.
_tai_word_re() {
  local w="$1" re="" c
  local -i i
  for (( i = 1; i <= ${#w}; i++ )); do
    c="${w[i]}"
    case "$c" in
      [a-z0-9_-]) ;;
      *) c="\\$c" ;;
    esac
    re+="$c"
    [[ $i -lt ${#w} ]] && re+=".*"
  done
  print -r -- "$re"
}


_tai_loose() {
  local prefix="${1:l}"
  local -a want ranked rxs exact fuzzy ranked_f
  local w line ll ok s t rx c
  local -i i k
  # A pasted command is not a half-remembered one. Checked before the cache key
  # and before any line is read, so the answer is nothing and the cost is one
  # comparison.
  if (( ${#prefix} > _TAI_LOOSE_MAX_LINE )); then
    _TAI_LOOSE_LINES=(); _TAI_LOOSE_CACHED=()
    _TAI_LOOSE_FOR="$prefix|$_TAI_LOOSE_GEN"
    return
  fi
  # The same question twice has the same answer: ZLE redraws without the buffer
  # changing when the cursor moves, the window is resized or a menu is painted,
  # and each of those used to pay for a full pass over every learned line. The
  # answer is *restored*, not just skipped — `_tai_query` clears
  # `_TAI_LOOSE_LINES` before it calls, so returning early would hand back an
  # empty list and the glimpse would vanish on the second redraw. The generation
  # is part of the key because a reloaded index is a different index with the
  # same prefix: _tai_load_index bumps it, so a cached answer can never speak for
  # lines that are no longer installed.
  if [[ "$prefix|$_TAI_LOOSE_GEN" == "${_TAI_LOOSE_FOR-}" ]]; then
    _TAI_LOOSE_LINES=( "${_TAI_LOOSE_CACHED[@]}" )
    return
  fi
  for w in "${(@s: :)prefix}"; do
    [[ -n "$w" ]] && want+=("$w")
  done
  (( ${#want} )) || return
  # One pattern per typed word, not one per line per word: rebuilding it was
  # the larger half of what this function used to cost. A word too short for a
  # pattern, or too long to be one, is matched as a plain substring instead — and
  # it still has to be *found*, because a word that drops out of the question
  # takes its veto with it: `cd <pasted-path>` would then only ask whether the
  # line mentions `cd`, and every learned `cd` command would answer.
  # Substring words go first, so the cheap vetoes of the whole question veto
  # every line before a matcher is ever run: for `npm i repomix@latest -g`
  # the two-letter `-g` is the one that throws almost every line out.
  local -a want_plain want_glob
  for w in "${want[@]}"; do
    if (( ${#w} < 3 || ${#w} > _TAI_LOOSE_MAX_WORD )); then
      want_plain+=("$w")
    else
      want_glob+=("$w")
    fi
  done
  want=( "${want_plain[@]}" "${want_glob[@]}" )
  rxs=()
  for w in "${want[@]}"; do
    if (( ${#w} < 3 || ${#w} > _TAI_LOOSE_MAX_WORD )); then
      rxs+=( "" )
    else
      rxs+=( "$(_tai_word_re "$w")" )
    fi
  done
  # Lowercase of every learned line, built once per index load and not once per
  # keystroke: measured, it was the bulk of each loose pass itself. The two
  # arrays stay parallel so the scan visits both by the same position.
  if (( _TAI_LOOSE_SNAP != _TAI_LOOSE_GEN )); then
    _TAI_LOOSE_KEYS=( "${(@k)_TAI_SCORE[@]}" )
    _TAI_LOOSE_LOW=()
    local line
    for line in "${_TAI_LOOSE_KEYS[@]}"; do
      _TAI_LOOSE_LOW+=( "${line:l}" )
    done
    _TAI_LOOSE_SNAP=$_TAI_LOOSE_GEN
  fi
  local -i n_k=${#_TAI_LOOSE_KEYS[@]}
  integer -i i_l
  for (( i_l = 1; i_l <= n_k; i_l++ )); do
    line=${_TAI_LOOSE_KEYS[i_l]}
    ll=${_TAI_LOOSE_LOW[i_l]}
    ok=1; fuzzy_used=0
    for (( k = 1; k <= ${#want}; k++ )); do
      w=$want[k]; rx=$rxs[k]
      # A word cannot occur in a line shorter than itself, and the test for that
      # is arithmetic. It is also the whole cost of a long token: asked about
      # 7k lines one at a time, the substring comparison of a 6KB word is
      # skipped for every line that could not possibly hold it.
      (( ${#w} > ${#ll} )) && { ok=0; break }
      [[ "$ll" == *"$w"* ]] && continue
      [[ -n "$rx" ]] || { ok=0; break }
      # The in-order witness, straight: a letters-as-a-glob pattern was
      # exponential on this index's long URL lines, and the per-letter
      # strstr prefilter it replaced spent more time screening than the
      # pattern spends deciding, measured on every keystroke bug it is.
      [[ "$ll" =~ $rx ]] || { ok=0; break }
      fuzzy_used=1
    done
    (( ok )) || continue
    if (( fuzzy_used )); then
      fuzzy+=("$line")
    else
      exact+=("$line")
    fi
  done
  # Two tiers: a line holding every typed word verbatim outranks one that only
  # holds their letters in order. Without the tiers, `forest` was answered by
  # whichever high-scoring line happened to have an f … o … r … e … s … t in it,
  # and the line the user meant — the one with `forest` in it — ranked below
  # the junk on score. Typo tolerance still applies, it just comes second.
  # Same ranking the rest of the index already uses, per tier: highest score
  # first, capped. Insertion into a list that is bounded, so the cost stays
  # constant. Exact lines fill the list first; fuzzy lines only ever reach the
  # tail.
  for line in "${exact[@]}"; do
    s=${_TAI_SCORE[$line]:-0}
    if (( ${#ranked} >= $_TAI_LOOSE_CAP )); then
      t=${_TAI_SCORE[${ranked[-1]}]:-0}
      (( s <= t )) && continue
    fi
    for (( i = 1; i <= ${#ranked}; i++ )); do
      t=${_TAI_SCORE[${ranked[i]}]:-0}
      (( s > t )) && break
    done
    if (( i > ${#ranked} )); then
      ranked+=("$line")
    else
      local -a tail_=( "${ranked[@]:$i-1}" )
      local -a head_=( )
      (( i > 1 )) && head_=( "${ranked[@]:0:$i-1}" )
      ranked=( "${head_[@]}" "$line" "${tail_[@]}" )
    fi
    (( ${#ranked} > $_TAI_LOOSE_CAP )) && \
      ranked=( "${ranked[@]:0:$_TAI_LOOSE_CAP}" )
  done
  for line in "${fuzzy[@]}"; do
    s=${_TAI_SCORE[$line]:-0}
    if (( ${#ranked_f} >= $_TAI_LOOSE_CAP )); then
      t=${_TAI_SCORE[${ranked_f[-1]}]:-0}
      (( s <= t )) && continue
    fi
    for (( i = 1; i <= ${#ranked_f}; i++ )); do
      t=${_TAI_SCORE[${ranked_f[i]}]:-0}
      (( s > t )) && break
    done
    if (( i > ${#ranked_f} )); then
      ranked_f+=("$line")
    else
      local -a tail_=( "${ranked_f[@]:$i-1}" )
      local -a head_=( )
      (( i > 1 )) && head_=( "${ranked_f[@]:0:$i-1}" )
      ranked_f=( "${head_[@]}" "$line" "${tail_[@]}" )
    fi
    (( ${#ranked_f} > $_TAI_LOOSE_CAP )) && \
      ranked_f=( "${ranked_f[@]:0:$_TAI_LOOSE_CAP}" )
  done
  ranked=( "${ranked[@]}" "${ranked_f[@]}" )
  (( ${#ranked} > $_TAI_LOOSE_CAP )) && \
    ranked=( "${ranked[@]:0:$_TAI_LOOSE_CAP}" )
  _TAI_LOOSE_LINES=()
  for c in "${ranked[@]}"; do _tai_clean "$c" && _TAI_LOOSE_LINES+=( "$c" ); done
  _TAI_LOOSE_CACHED=( "${_TAI_LOOSE_LINES[@]}" )
  _TAI_LOOSE_FOR="$prefix|$_TAI_LOOSE_GEN"
}
# The generation the cache above is keyed on, and the key itself. A reload
# bumps the generation because the answer for a prefix is an answer about the
# lines that were installed when it was asked.
typeset -gi _TAI_LOOSE_GEN=0
typeset -g _TAI_LOOSE_FOR=""
typeset -ga _TAI_LOOSE_CACHED=()
# The lowercase scan copy is rebuilt when the generation moves on, and not
# before: the first redraw after a reload pays for it.
typeset -gi _TAI_LOOSE_SNAP=-1
typeset -ga _TAI_LOOSE_KEYS=()

