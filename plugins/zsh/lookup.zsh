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
# _TAI_LINES, newline-joined the way the index stores them.
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
#
# The answer is one of two shapes, and which one is recorded in
# _TAI_VALUES_ONE because the ranking that consumes it depends on the shape:
#
#   * ONE (the exact key): the key's whole list, which the generator wrote in
#     score order — so the best candidate that extends the typed line is the
#     *head* of the list, and no ranking loop is needed at all. This is the shape
#     on every ordinary keystroke past a complete command name.
#   * MANY (a half-typed name): the keys that begin with it, and one candidate
#     per key — each key's own head, which is that key's best line. No key's
#     list is scanned here: `${values%%\n*}` peels the first line off the string
#     in one expansion, where splitting 2,600 lines to read one of them was most
#     of a millisecond per keystroke on a real history. The winner is the best of
#     the heads, by score, in _tai_best — provably the same winner a scan of
#     every line would find, because each key's head is the best of that key.
_tai_first_values() {
  local word="$1" values="" k head n=0
  _TAI_VALUES_ONE=0
  # An empty key is not a command name, and an empty subscript is an error rather
  # than a miss in bash, so the lookup does not happen at all here.
  [[ -n "$word" ]] || { _TAI_VALUES=""; return }
  values="${_TAI_FIRST[$word]:-}"
  if [[ -n "$values" ]]; then
    _TAI_VALUES_ONE=1
    _TAI_VALUES="$values"
    return
  fi
  # A glob character in the word would make the pattern mean something other than
  # "begins with the word" — zsh's `(I)` does not honour a backslash escape here —
  # and no command is spelled with one. Answer nothing rather than something else.
  [[ "$word" != *[\*\?\[]* ]] || { _TAI_VALUES=""; return }
  # Half-typed name: the keys that begin with it, and each key's best line. The
  # head of a key's list is that best line — the generator wrote every list in
  # score order — and `${values%%\n*}` reads it without splitting the string.
  # A key whose head carries a raw control byte cannot be painted, so its next
  # line is its best usable one; the peel repeats, bounded, rather than skipping
  # the key and its real answer with it.
  values=""
  for k in "${(@k)_TAI_FIRST[(I)${word}*]}"; do
    head="${_TAI_FIRST[$k]}"
    while [[ -n "$head" ]] && ! _tai_clean "${head%%$'\n'*}"; do
      # A single-line value peels to nothing — the guard below, not the
      # expansion, ends the walk: `${head#*\n}` on a line with no newline
      # returns it unchanged, and a loop that depended on it would never
      # come back.
      [[ "$head" == *$'\n'* ]] || { head=""; break; }
      head="${head#*$'\n'}"
    done
    [[ -n "$head" ]] || continue
    values+="${head%%$'\n'*}"$'\n'
    (( ++n >= _TAI_PREFIX_KEYS )) && break
  done
  _TAI_VALUES="${values%$'\n'}"
}
typeset -g _TAI_VALUES=""
# 1 when _TAI_VALUES is one key's own score-ordered list (the head of it is the
# winner), 0 when it is one head per key and _tai_best must rank by score.
typeset -gi _TAI_VALUES_ONE=0

# The lines a prefix extends, into _TAI_LINES.
#
# The whole working set here is C-level parameter expansion, and that is the
# difference between a redraw that costs 0.2ms and one that costs 2ms on a real
# index. The old shape split the key's full candidate list and walked it line by
# line — a pattern test per line — where `_TAI_FIRST[git]` alone held 2,600
# lines and every one of them paid the walk on every keystroke, when only the
# handful that extend the line were ever wanted. Now the split is one expansion,
# the "starts with the line" test is one `(M)` filter over the array, and the
# per-line `_tai_clean` walk runs over what survived — usually less than twenty
# lines, never more than the cap.
#
# The pattern is the typed line with every glob character escaped (`${(b)…}`),
# so `foo[bar` is a literal prefix and not a broken class: the same refusal the
# file answer makes, made unnecessary.
_tai_lines() {
  local prefix="$1" word rest head key values match
  local -a lines
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
      [[ -n "$word" ]] && values="${_TAI_WORD[$word]:-}" && _TAI_VALUES_ONE=1
    else
      _tai_first_values "$word"
      values="$_TAI_VALUES"
    fi
  else
    _tai_first_values "$prefix"
    values="$_TAI_VALUES"
  fi
  if [[ -n "$values" ]]; then
    lines=( "${(@f)values}" )
    lines=( "${(@M)lines:#${(b)prefix}*}" )
    (( ${#lines} > _TAI_PREFIX_KEYS )) && lines=( "${lines[@]:0:_TAI_PREFIX_KEYS}" )
    for c in "${lines[@]}"; do _tai_clean "$c" && _TAI_LINES+=( "$c" ); done
    (( ${#_TAI_LINES} )) && return
  fi
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
    values="${_TAI_WORD[$key]:-}" && _TAI_VALUES_ONE=1
  else
    _tai_first_values "$key"
    values="$_TAI_VALUES"
  fi
  [[ -n "$values" ]] || return
  _TAI_LINES_LEAD="$head "
  # The wrapped candidates are the lines behind the wrapper, so the prefix they
  # have to extend is the line *without* it — `sudo git st` is answered by the
  # `git st…` lines, and the wrapper goes back on in _tai_best.
  match="$prefix"
  [[ -n "$_TAI_LINES_LEAD" ]] && match="${prefix#"$head" }"
  lines=( "${(@f)values}" )
  lines=( "${(@M)lines:#${(b)match}*}" )
  (( ${#lines} > _TAI_PREFIX_KEYS )) && lines=( "${lines[@]:0:_TAI_PREFIX_KEYS}" )
  for c in "${lines[@]}"; do _tai_clean "$c" && _TAI_LINES+=( "$c" ); done
}

# The best line in _TAI_LINES that extends the prefix, into _TAI_BEST.
#
# A candidate identical to what is already typed is skipped. It can never be
# rendered as ghost text, so letting it win only hides the candidate that could
# have extended the line: without this, `ls -l` beats `ls -la` and the hint for
# `ls -l` is nothing at all.
#
# When _TAI_VALUES_ONE is set, _TAI_LINES came from one key's own list in the
# score order the generator wrote — so the first line that extends the prefix is
# the winner, and the ranking loop does not run at all. When it is clear, the
# lines are one head per key and the highest score wins, over at most
# _TAI_PREFIX_KEYS of them.
_tai_best() {
  local prefix="$1" c line best="" best_score=-1 s
  _TAI_BEST=""
  if (( _TAI_VALUES_ONE )); then
    for c in "${_TAI_LINES[@]}"; do
      line="$_TAI_LINES_LEAD$c"
      [[ -z "$c" || "$line" == "$prefix" ]] && continue
      best="$line"
      break
    done
  else
    for c in "${_TAI_LINES[@]}"; do
      line="$_TAI_LINES_LEAD$c"
      [[ -z "$c" || "$line" == "$prefix" || "$line" != "$prefix"* ]] && continue
      s="${_TAI_SCORE[$c]:-0}"
      if (( s > best_score )); then best="$c"; best_score="$s"; fi
    done
    [[ -n "$best" ]] && best="$_TAI_LINES_LEAD$best"
  fi
  _TAI_BEST="$best"
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
#
# Answered once per question, not once per redraw. ZLE redraws far more often
# than a line changes — the cursor moves, a menu paints, the window resizes,
# another plugin touches the screen — and every one of those used to pay for
# the whole lookup again for an answer nothing could have changed. So the
# question ("prefix, last command, index generation") is remembered beside the
# answer (the globals the ghost, the menu and the keys read), and an unchanged
# question restores the answer instead of recomputing it. The generation is
# part of the key because a reloaded index is a different index with the same
# line in front of it: _tai_load_index bumps it, so a cached answer can never
# speak for candidates that are no longer installed.
typeset -g _TAI_Q_FOR=""
typeset -ga _TAI_Q_SNAP=()

# Splitting a newline-joined answer back into lines. `${(@f)""}` is one empty
# element, not none — an @-quoted empty result keeps a word — and an empty
# candidate is not a line, so an empty answer splits to an empty array.
_tai_q_split() {
  REPLY=()
  [[ -n "$1" ]] && REPLY=( "${(@f)1}" )
}

_tai_query() {
  local key="$1"$'\n'"$2"$'\n'"$_TAI_LOOSE_GEN"
  if [[ "$key" == "$_TAI_Q_FOR" ]]; then
    _tai_q_split "$_TAI_Q_SNAP[1]"; _TAI_LINES=( "${REPLY[@]}" )
    _TAI_LINES_LEAD=$_TAI_Q_SNAP[2]
    _TAI_BEST=$_TAI_Q_SNAP[3]
    _TAI_BEST_HELP=$_TAI_Q_SNAP[4]
    _TAI_BEST_Q=$_TAI_Q_SNAP[5]
    _tai_q_split "$_TAI_Q_SNAP[6]"; _TAI_LOOSE_LINES=( "${REPLY[@]}" )
    _tai_q_split "$_TAI_Q_SNAP[7]"; _TAI_FILES=( "${REPLY[@]}" )
    _tai_q_split "$_TAI_Q_SNAP[8]"; _TAI_FILES_Q=( "${REPLY[@]}" )
    _TAI_FILE_JOIN=$_TAI_Q_SNAP[9]
    return
  fi
  _tai_query_do "$1" "$2"
  _TAI_Q_SNAP=( "${(pj:\n:)_TAI_LINES}" "$_TAI_LINES_LEAD" "$_TAI_BEST"
                "$_TAI_BEST_HELP" "$_TAI_BEST_Q"
                "${(pj:\n:)_TAI_LOOSE_LINES}" "${(pj:\n:)_TAI_FILES}"
                "${(pj:\n:)_TAI_FILES_Q}" "$_TAI_FILE_JOIN" )
  _TAI_Q_FOR="$key"
}

_tai_query_do() {
  local prefix="$1" last="$2" first
  _TAI_BEST_HELP=0; _TAI_BEST_Q=0
  _TAI_LOOSE_LINES=()
  if [[ -z "$prefix" ]]; then
    # An empty prompt predicts the command that followed the last one.
    _TAI_LINES=()
    _TAI_LINES_LEAD=""
    local c
    [[ -n "$last" ]] && for c in "${(@f)_TAI_SEQ[$last]}"; do _tai_clean "$c" && _TAI_LINES+=( "$c" ); done
    # The sequence key's values are score-ordered like every other list the
    # generator writes, so the head of it is the prediction — the same shape the
    # exact-key lookup answers in.
    _TAI_VALUES_ONE=1
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
  # A systemctl line is answered by its units — `sudo systemctl restart her`
  # hints `herm.service`, from the cached unit list, with no process. The hook
  # is before the loose glimpse because the units are the *answer* to a unit
  # argument, and three remembered lines that merely mention the word are not.
  # It never forks: an empty cache answers nothing here and lets the Tab
  # preload it.
  if [[ -z "$_TAI_BEST" ]] && _tai_unit_best "$prefix"; then
    _TAI_BEST="$_TAI_UNIT_BEST"
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

# The in-order check for one word, as a regex: its letters joined by a bounded
# gap, each escaped so a word holding `-`, `/` or `[` is letters and not syntax.
# Measured: the same check through the letters-as-a-glob pattern is not merely
# slower — on an adversarial spelling (`npm` gives it `n…p…m` shapes across every
# `--header` of a 1KB URL, and an `aaaa…` word *thirty-two* rounds past the
# escaped letter count) the glob engine does not come back on a keystroke at
# all, while the same text answers as a regex in ~0 s. There is no operator
# for it in zsh that is both this fast and safe, so it is spelled as one.
#
# The gap is bounded (`.{0,N}`), where it used to be `.*`, and the bound is what
# makes the fuzzy tier honest. `forestt` typed into a history of long URLs used
# to come back with aria2c lines, because f, o, r, e, s, t and t really are all
# *somewhere* in a 200-character URL, in order — a fact about arithmetic, not
# about anything the user remembered. Letters that are typed within a few
# characters of each other are one misspelled word; letters scattered across a
# line are a coincidence, and the matcher no longer votes for coincidences.
# `gst` still reads across `git status` (gaps 0, 0, 0), and a transposition
# still matches (`uninsall` reads across `uninstall` across a gap of two).
#
# Both builders answer in _TAI_RE_OUT rather than on stdout: they run inside the
# scan, where `$()` around a call was a fork per word per query for a string a
# variable already carries.
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
    [[ $i -lt ${#w} ]] && re+=".{0,$_TAI_LOOSE_MAX_GAP}"
  done
  _TAI_RE_OUT="$re"
}

# The one-edit check for a word, as one regex: every spelling the word has with
# a single character removed, alternated. A line holding any of them verbatim is
# one keystroke away from the word typed — `forestt` holds `forest`, and the
# lines that mention `forest` are the answer the user meant, ranked ahead of
# anything the gap matcher can argue for. Deletion only, because that is the
# typo a glance cannot see: an extra letter in the middle of a word the user
# otherwise spelled exactly. Transpositions are the gap matcher's job (they pass
# it at a gap of two); substitutions usually leave the rest of the word
# verbatim, and one of the deletions above lands in it.
#
# A variant is only a witness when it is still a word: four characters or more.
# `forestt` loses its doubled t and reads across every line that mentions
# `forest`; a three-letter witness matches half a history, and a match that
# wide says nothing.
_tai_word_variants_re() {
  local w="$1" re="" v c esc
  local -i i j
  local -A seen
  for (( i = 1; i <= ${#w}; i++ )); do
    v="${w[1,i-1]}${w[i+1,-1]}"
    [[ -n "$v" && ${#v} -ge 4 && -z "${seen[$v]}" ]] || continue
    seen[$v]=1
    esc=""
    for (( j = 1; j <= ${#v}; j++ )); do
      c="${v[j]}"
      case "$c" in
        [a-z0-9_-]) ;;
        *) c="\\$c" ;;
      esac
      esc+="$c"
    done
    [[ -n "$re" ]] && re+="|"
    re+="$esc"
  done
  _TAI_RE_OUT="$re"
}


_tai_loose() {
  local prefix="${1:l}"
  local -a want rxs_c exact_lw var_lw fuzz_lw rest3 hits_lw scan_ll
  local w line ll rxc rxb ok
  local -i k n_exact=0
  # A pasted command is not a half-remembered one. Checked before the cache key
  # and before any line is read, so the answer is nothing and the cost is one
  # comparison.
  if (( ${#prefix} > _TAI_LOOSE_MAX_LINE )); then
    _TAI_LOOSE_LINES=(); _TAI_LOOSE_CACHED=()
    _TAI_LOOSE_FOR="$prefix|$_TAI_LOOSE_GEN"
    _TAI_LOOSE_PAR=""; _TAI_LOOSE_PAR_IDX=()
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
  rxs_c=()
  for w in "${want[@]}"; do
    if (( ${#w} < 3 || ${#w} > _TAI_LOOSE_MAX_WORD )); then
      rxs_c+=( "" )
    else
      _tai_word_re "$w"
      rxs_c+=( "$_TAI_RE_OUT" )
    fi
  done
  # The one-edit patterns are built for the second pass only, and only when that
  # pass runs — see below. A word qualifies for one when it is long enough that
  # a deletion of it is still a witness (five characters, so the deleted spelling
  # is four or more — `forestt` loses a t and reads `forest`, while `tzzx` losing
  # its last letter would read across half a history of `tzz…` lines), and when
  # it ends like a word: a query that ends in `/` or `-` is a path being
  # completed, not a word being mistyped, and `tzz_dir/` is not one letter away
  # from `tzz_dir` in any way that means something.
  local -a rxs_b
  local -i want_b=0
  rxs_b=()
  for (( k = 1; k <= ${#want}; k++ )); do
    w=${want[k]}
    if (( ${#w} >= 5 && ${#w} <= _TAI_LOOSE_MAX_WORD )) && [[ "$w" == *[a-z0-9] ]]; then
      rxs_b+=( "?" )
      want_b=1
    else
      rxs_b+=( "" )
    fi
  done
  # Lowercase of every learned line, built once per index load and not once per
  # keystroke: measured, it was the bulk of each loose pass itself. The scan
  # runs on the lowercase copy — the query is lowercased to meet it — and the
  # map back to the line the user actually ran is built beside it, once, for
  # the ranked answer to read.
  if (( _TAI_LOOSE_SNAP != _TAI_LOOSE_GEN )); then
    _TAI_LOOSE_KEYS=( "${(@k)_TAI_SCORE[@]}" )
    _TAI_LOOSE_LOW=()
    local line2
    for line2 in "${_TAI_LOOSE_KEYS[@]}"; do
      _TAI_LOOSE_LOW+=( "${line2:l}" )
    done
    _TAI_LOOSE_LINE_OF=()
    local -i _i
    for (( _i = 1; _i <= ${#_TAI_LOOSE_KEYS}; _i++ )); do
      _TAI_LOOSE_LINE_OF[${_TAI_LOOSE_LOW[_i]}]=${_TAI_LOOSE_KEYS[_i]}
    done
    _TAI_LOOSE_SNAP=$_TAI_LOOSE_GEN
    _TAI_LOOSE_PAR=""
    _TAI_LOOSE_PAR_LOW=()
  fi
  # What the scan walks: every learned line, or — when this prefix *extends* the
  # prefix the last scan answered — only the lines that last scan matched. Every
  # tier is monotone under adding letters to the last word: a line that holds
  # the longer word verbatim holds the shorter; a line holding a one-letter
  # deletion of the longer word holds one of the shorter; and the shorter
  # word's letters sit between the longer word's letters, so an in-order match
  # survives too. A line the parent rejected therefore cannot match the child,
  # and the scan may skip it. Typing a word out ends up paying for one full pass
  # — the word's first letter — and a bounded pass over that pass's few matches
  # for every letter after it.
  local -a scan_ll
  if [[ -n "$_TAI_LOOSE_PAR" && "$_TAI_LOOSE_PAR_GEN" == "$_TAI_LOOSE_GEN" \
        && "$prefix" != "$_TAI_LOOSE_PAR" && "$prefix" == "$_TAI_LOOSE_PAR"* ]]; then
    scan_ll=( "${_TAI_LOOSE_PAR_LOW[@]}" )
  else
    scan_ll=( "${_TAI_LOOSE_LOW[@]}" )
  fi
  # Pass one — verbatim, one C-level filter per word over the whole set, and no
  # shell loop at all. This is the tier the glimpse answers from on an ordinary
  # prefix, and where the old code walked every line with a pattern test per
  # word, the filter does the same work inside parameter expansion. The words
  # are pattern-escaped, so a typed `foo[bar` is a literal substring and not a
  # broken class.
  local -a exact_lw var_lw fuzz_lw rest3
  exact_lw=( "${scan_ll[@]}" )
  for w in "${want[@]}"; do
    (( ${#exact_lw[@]} )) || break
    exact_lw=( "${(@M)exact_lw:#*${(b)w}*}" )
  done
  n_exact=${#exact_lw[@]}
  # Pass two — the one-edit tier, over the lines pass one rejected, and only
  # when verbatim answered nothing at all: a word the history holds verbatim is
  # the answer, and a second tier of guesses under it is noise, not memory.
  # `forestt` lands here — nothing holds it verbatim, and the gap matcher is
  # not allowed to argue — and reads across every line that mentions `forest`,
  # one deletion away. One regex per word, hot in zsh's compile cache because
  # the pass runs it alone.
  if (( want_b && n_exact == 0 && ${#scan_ll[@]} > 0 )); then
    for (( k = 1; k <= ${#want}; k++ )); do
      [[ "${rxs_b[k]}" == "?" ]] || continue
      _tai_word_variants_re "${want[k]}"
      rxs_b[k]="$_TAI_RE_OUT"
    done
    for ll in "${scan_ll[@]}"; do
      ok=1
      for (( k = 1; k <= ${#want}; k++ )); do
        w=$want[k]; rxb=$rxs_b[k]
        (( ${#w} > ${#ll} )) && { ok=0; break }
        [[ "$ll" == *"$w"* ]] && continue
        [[ -n "$rxb" && "$ll" =~ $rxb ]] && continue
        ok=0; break
      done
      if (( ok )); then
        var_lw+=( "$ll" )
      else
        rest3+=( "$ll" )
      fi
    done
  else
    rest3=( "${scan_ll[@]}" )
  fi
  # Pass three — the bounded-gap tier. It only speaks when nothing else did:
  # a line the verbatim or one-edit tier answered is the answer, and lines of
  # letters-scattered arithmetic under it were the junk this tier existed to
  # serve. With the tiers above it silent, what it finds is the closest thing
  # to a memory the typed letters have — and with them silent, the pass has
  # nothing to be ranked under, so nothing is lost by its caution.
  if (( n_exact == 0 && ${#var_lw[@]} == 0 && ${#rest3[@]} > 0 )); then
    typeset -gA _TAI_LOOSE_CLAIMED
    _TAI_LOOSE_CLAIMED=()
    for ll in "${exact_lw[@]}"; do _TAI_LOOSE_CLAIMED[$ll]=1; done
    for ll in "${rest3[@]}"; do
      [[ -z "${_TAI_LOOSE_CLAIMED[$ll]:-}" ]] || continue
      ok=1
      for (( k = 1; k <= ${#want}; k++ )); do
        w=$want[k]; rxc=$rxs_c[k]
        (( ${#w} > ${#ll} )) && { ok=0; break }
        [[ "$ll" == *"$w"* ]] && continue
        [[ -n "$rxc" && "$ll" =~ $rxc ]] && continue
        ok=0; break
      done
      (( ok )) && fuzz_lw+=( "$ll" )
    done
  fi
  # Every tier's matches, back into the lines the user ran, ranked best first
  # within the tier. Three tiers, ranked in that order: a line holding every
  # typed word verbatim outranks one holding a one-letter-away spelling, which
  # outranks one that only holds their letters in order. Without the tiers,
  # `forest` was answered by whichever high-scoring line happened to have an
  # f … o … r … e … s … t in it, and the line the user meant — the one with
  # `forest` in it — ranked below the junk on score. Same ranking the rest of
  # the index already uses, per tier: highest score first, capped. Insertion
  # into a list that is bounded, so the cost stays constant. Exact lines fill
  # the list first; the edit tier comes second; the scatter tier only ever
  # reaches the tail.
  hits_lw=( "${exact_lw[@]}" "${var_lw[@]}" "${fuzz_lw[@]}" )
  _TAI_RANKED=()
  for ll in "${exact_lw[@]}"; do
    line="${_TAI_LOOSE_LINE_OF[$ll]:-}"
    [[ -n "$line" ]] && { _TAI_RANK_LINE="$line"; _tai_ranked_insert; }
  done
  _TAI_RANKED_EXACT=( "${_TAI_RANKED[@]}" )
  _TAI_RANKED=()
  for ll in "${var_lw[@]}"; do
    line="${_TAI_LOOSE_LINE_OF[$ll]:-}"
    [[ -n "$line" ]] && { _TAI_RANK_LINE="$line"; _tai_ranked_insert; }
  done
  _TAI_RANKED_VAR=( "${_TAI_RANKED[@]}" )
  _TAI_RANKED=()
  for ll in "${fuzz_lw[@]}"; do
    line="${_TAI_LOOSE_LINE_OF[$ll]:-}"
    [[ -n "$line" ]] && { _TAI_RANK_LINE="$line"; _tai_ranked_insert; }
  done
  local -a ranked
  ranked=( "${_TAI_RANKED_EXACT[@]}" "${_TAI_RANKED_VAR[@]}" "${_TAI_RANKED[@]}" )
  (( ${#ranked} > $_TAI_LOOSE_CAP )) && \
    ranked=( "${ranked[@]:0:$_TAI_LOOSE_CAP}" )
  local c
  _TAI_LOOSE_LINES=()
  for c in "${ranked[@]}"; do _tai_clean "$c" && _TAI_LOOSE_LINES+=( "$c" ); done
  _TAI_LOOSE_CACHED=( "${_TAI_LOOSE_LINES[@]}" )
  _TAI_LOOSE_FOR="$prefix|$_TAI_LOOSE_GEN"
  _TAI_LOOSE_PAR="$prefix"
  _TAI_LOOSE_PAR_GEN="$_TAI_LOOSE_GEN"
  _TAI_LOOSE_PAR_LOW=( "${hits_lw[@]}" )
}

# One line into the bounded, best-first list _TAI_RANKED, by _TAI_SCORE. The
# line to insert travels in _TAI_RANK_LINE, and both globals rather than
# parameters, because the insertion is shared by the two tiers of the loose
# answer — one copy of the rule, or the exact tier and the fuzzy tier drift
# apart the first time one of them is "fixed". Globals and not `typeset -n`
# references for the same reason every helper here answers in a global: it is
# one mechanism, not two, and there is no nameref scope to reason about on a
# keystroke.
typeset -ga _TAI_RANKED=()
typeset -ga _TAI_RANKED_EXACT=()
typeset -ga _TAI_RANKED_VAR=()
typeset -g _TAI_RANK_LINE=""

_tai_ranked_insert() {
  local line="$_TAI_RANK_LINE" s t
  local -i i
  local -a src=( "${_TAI_RANKED[@]}" )
  s=${_TAI_SCORE[$line]:-0}
  if (( ${#src} >= $_TAI_LOOSE_CAP )); then
    t=${_TAI_SCORE[${src[-1]}]:-0}
    (( s <= t )) && return
  fi
  for (( i = 1; i <= ${#src}; i++ )); do
    t=${_TAI_SCORE[${src[i]}]:-0}
    (( s > t )) && break
  done
  if (( i > ${#src} )); then
    _TAI_RANKED=( "${src[@]}" "$line" )
  else
    local -a tail_=( "${src[@]:$i-1}" )
    local -a head_=( )
    (( i > 1 )) && head_=( "${src[@]:0:$i-1}" )
    _TAI_RANKED=( "${head_[@]}" "$line" "${tail_[@]}" )
  fi
  (( ${#_TAI_RANKED} > $_TAI_LOOSE_CAP )) && \
    _TAI_RANKED=( "${_TAI_RANKED[@]:0:$_TAI_LOOSE_CAP}" )
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
typeset -ga _TAI_LOOSE_LOW=()
# Every line's position in the scan copy, built with it: what a scan walks when
# no narrower scan set applies.
# The parent state: the prefix the last scan answered, the generation it was
# scanned under, and the lowercase lines it matched. A prefix that extends the
# parent's scans the parent's matches instead of every line — every match tier
# is monotone under adding letters to the last word, so the lines the parent
# rejected cannot match the child. This is what makes typing a word out cost
# one full pass instead of one per keystroke.
typeset -g _TAI_LOOSE_PAR=""
typeset -gi _TAI_LOOSE_PAR_GEN=-1
typeset -ga _TAI_LOOSE_PAR_LOW=()
# The lowercase line back to the line the user actually ran, built beside the
# lowercase scan copy: the scan runs on the lowercase copy, and the ranked
# answer has to be the text the history holds.
typeset -gA _TAI_LOOSE_LINE_OF=()
# How many characters one fuzzy-tier letter may sit from the previous one. The
# bound is what keeps the scatter tier honest: letters scattered across a long
# URL are arithmetic, not memory. Three covers a transposition and a doubled
# keystroke; a URL's gaps do not survive it.
typeset -gi _TAI_LOOSE_MAX_GAP=3

