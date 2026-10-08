# Turning a line into candidates and ranking them.

# The candidate lines for a command name — the word before the first space — on
# stdout, newline-joined the way the index stores them. The mirror of
# _tai_first_values in plugins/tai.zsh, and the same three steps, because the two
# are one rule: the exact name, and then the names that begin with it.
#
# The index keys _TAI_FIRST by the *whole* name, so `godot` is a key and `god` is
# not, and a half-typed command is the ordinary state of a line being written.
# That is what `tai suggest god` answered `godot .` through while this plugin
# said nothing at all: the engine sorts its command names and walks a prefix
# range, and this was a single exact key.
#
# A subshell is the one cost zsh does not pay here — it answers in a global
# because the ghost-text path cannot afford a fork — and this is on a completion
# press, which bash is already spending processes on: 2ms of it on this machine's
# 754-key index, of which the scan is about half and the word with no match the
# whole loop, since only a match can stop it early.
_tai_first_values() {
  local word="$1" values="" k n=0 v head lo hi mid
  [[ -n "$word" ]] || { _TAI_VALUES=""; _TAI_VALUES_ONE=0; return 0; }
  values="${_TAI_FIRST[$word]:-}"
  if [[ -n "$values" ]]; then
    _TAI_VALUES="$values"
    _TAI_VALUES_ONE=1
    return 0
  fi
  _TAI_VALUES=""; _TAI_VALUES_ONE=0
  # A glob character in the word would make the test mean something other than
  # "begins with the word", and no command is spelled with one.
  [[ "$word" != *[\*\?\[]* ]] || return 0
  # Half-typed name. The keys were sorted once, at load, so the ones that begin
  # with the word are a contiguous run found by binary search — a scan of every
  # key here was most of a millisecond per keystroke on a real index, paid
  # again on every letter of a first word being typed. From each key the answer
  # takes only its *head*, which is that key's best line: the winner is the best
  # of the heads, by score, in _tai_best — provably the same winner a scan of
  # every line would find, because each key's head is the best of that key.
  lo=0; hi=${#_TAI_FIRST_KEYS[@]}
  while (( lo < hi )); do
    mid=$(( (lo + hi) / 2 ))
    if [[ "${_TAI_FIRST_KEYS[mid]}" < "$word" ]]; then lo=$(( mid + 1 )); else hi=$mid; fi
  done
  while (( lo < ${#_TAI_FIRST_KEYS[@]} )); do
    k="${_TAI_FIRST_KEYS[lo]}"
    [[ "$k" == "$word"* ]] || break
    v="${_TAI_FIRST[$k]}"
    while [[ -n "$v" ]]; do
      head="${v%%$'\n'*}"
      if [[ ! "$head" =~ [[:cntrl:]] ]]; then
        values+="$head"$'\n'
        (( ++n >= _TAI_PREFIX_KEYS )) && break 2
      fi
      [[ "$v" == *$'\n'* ]] || break
      v="${v#*$'\n'}"
    done
    (( ++lo ))
  done
  values="${values%$'\n'}"
  _TAI_VALUES="$values"
  return 0
}

# 1 when _TAI_VALUES is one key's own score-ordered list (the head of it is the
# winner), 0 when it is one head per key and _tai_best must rank by score.
_TAI_VALUES_ONE=0

# A candidate identical to what is already typed is skipped: it can never be
# shown, and letting it win only hides the candidate that could have extended
# the line (`ls -l` must not beat `ls -la`).
#
# When _TAI_VALUES_ONE is set, the values came from one key's own list in the
# score order the generator wrote — so the first line that extends the prefix is
# the winner, and the ranking loop does not run at all. That is the shape of
# every ordinary keystroke past a complete command name, and on a real index it
# replaces a walk over 2,600 lines with two or three tests.
_tai_best() {
  local values="$1" prefix="$2" best="" best_score=-1 c s
  if [[ "${_TAI_VALUES_ONE:-0}" == "1" ]]; then
    while IFS= read -r c; do
      [[ -n "$c" && "$c" == "$prefix"* && "$c" != "$prefix" ]] || continue
      # The store refuses control bytes at record time, but this shell sources
      # what it finds, and a byte that slips through is an escape sequence on
      # someone's terminal.
      [[ "$c" =~ [[:cntrl:]] ]] && continue
      best="$c"
      break
    done <<< "$values"
    _TAI_BEST_LINE="$best"
    return 0
  fi
  while IFS= read -r c; do
    [[ -z "$c" || "$c" == "$prefix" || "$c" != "$prefix"* ]] && continue
    # A stored command whose text holds a raw control byte is unpaintable as
    # ghost text: the byte goes to the terminal as an escape sequence. The
    # store refuses at record time now, but this shell sources what it finds.
    [[ "$c" =~ [[:cntrl:]] ]] && continue
    s="${_TAI_SCORE[$c]:-0}"
    if (( s > best_score )); then best="$c"; best_score="$s"; fi
  done <<< "$values"
  _TAI_BEST_LINE="$best"
}

# An installed command the history has never seen. `--help` is the only thing
# worth offering: the bare name is an echo of what is already typed, and any
# flag would be a guess. `command -v` is a builtin, so this costs no process,
# and it only runs when the index has no answer, so a tool with learned
# knowledge never reaches it.
_tai_installed() { command -v -- "$1" >/dev/null 2>&1; }

_TAI_WRAPPERS=" sudo doas nohup time nice ionice stdbuf command "

# The head of the line, behind one wrapper: is this a command that takes a
# directory and nothing else? `cd` and `pushd` are the whole closed list — the
# same list tai/paths.py judges destinations with, and the reason no file is
# ever offered after `cd`: the shell answers "not a directory", and a
# suggestion the shell refuses is not a suggestion.
_tai_dirarg() {
  local head="${1%% *}" rest
  if [[ "$_TAI_WRAPPERS" == *" $head "* ]]; then
    rest="${1#* }"
    head="${rest%% *}"
  fi
  [[ "$head" == "cd" || "$head" == "pushd" ]]
}

# A directory from where the user stands. `-` is the shell's own slot and
# works in every directory; `~` is rewritten and tested; everything else is
# tested as written — a relative destination recorded somewhere else is
# exactly the suggestion that fails with "no such file or directory" here.
_tai_destination_live() {
  local t="$1"
  [[ "$t" == "-" ]] && return 0
  [[ "$t" == "~"* ]] && t="${t/\~/$HOME}"
  [[ -d "$t" ]]
}

# Rank the line behind the wrapper and put the wrapper back. `sudo git ` completes
# from the `git ...` the user has run.
_tai_wrapped() {
  local prefix="$1" head rest key
  _TAI_WRAPPED_OUT=""
  head="${prefix%% *}"
  rest="${prefix#* }"
  [[ "$_TAI_WRAPPERS" == *" $head "* ]] || return 0
  [[ -n "$rest" && "$rest" != *=* ]] || return 0
  key="${rest%% *}"
  # An empty key is a hard error in bash, and a line of nothing but spaces has no
  # complete word to look up.
  [[ -n "$key" ]] || return 0
  # The same lookup _tai_query makes, and for the same reason: a key with no
  # space in it is the command name itself, and the index keeps that list once,
  # in _TAI_FIRST. `tai/index.py` writes _TAI_WORD keys from two words on, so
  # reading a one-word key there is a miss against every real index — which is
  # what made `sudo git ` answer nothing, and a test fixture built the other way
  # round hid it.
  local values="" out
  if [[ "$key" == *" "* ]]; then values="${_TAI_WORD[$key]:-}"; _TAI_VALUES_ONE=1; else _tai_first_values "$key"; values="$_TAI_VALUES"; fi
  _tai_best "$values" "$rest"
  out="$_TAI_BEST_LINE"
  [[ -n "$out" ]] || return 0
  _TAI_WRAPPED_OUT="$head $out"
}

# Look up the longest *complete* word of the line, not the line itself. The
# generator writes one key per cumulative word boundary, so the shorter key
# always holds a superset of the exact-prefix key, and that superset is what lets
# `ls -l` extend to `ls -la`.
#
# The answer lands in _TAI_OUT and nowhere else. Every caller runs under
# `bind -x`, where stdout is the terminal — the old `$( _tai_query … )` forks
# both hid the answer from the screen and paid a process for it per keypress,
# which is the one cost the keystroke path can decline.
_tai_query() {
  # `values=""` rather than a bare `values`: bash leaves a `local` with no
  # assignment *unset*, and the spaces-only line below never reaches the branch
  # that assigns it, so `set -o nounset` would fail the whole lookup there.
  # zsh has no such distinction, which is why only bash needs the empty value.
  local prefix="$1" last="$2" first word values="" out
  if [[ -z "$prefix" ]]; then
    out=""
    if [[ -n "$last" ]]; then
      # The sequence key's values are score-ordered like every other list the
      # generator writes, so the head of it is the prediction — the same shape
      # the exact-key lookup answers in.
      _TAI_VALUES_ONE=1
      _tai_best "${_TAI_SEQ[$last]:-}" ""
      out="$_TAI_BEST_LINE"
    fi
    _TAI_OUT="$out"
    return 0
  fi
  if [[ "$prefix" == *' '* ]]; then
    word="${prefix% *}"
    # A key with no space in it is the command name itself, and the index keeps
    # that list once, in _TAI_FIRST rather than twice: `ls -` looks `ls` up, and
    # a second copy of every command the shell has ever run was the largest thing
    # in the file. An empty key is a hard error in bash ("bad array subscript"), so
    # a line of nothing but spaces never reaches either branch.
    if [[ "$word" == *" "* ]]; then
      values="${_TAI_WORD[$word]:-}"
      _TAI_VALUES_ONE=1
    else
      _tai_first_values "$word"
      values="$_TAI_VALUES"
    fi
  else
    first="$prefix"
    _tai_first_values "$first"
    values="$_TAI_VALUES"
  fi
  _tai_best "$values" "$prefix"
  out="$_TAI_BEST_LINE"
  # Wrapper transparency comes before the `--help` fallback, or that answers
  # `sudo git ` with `sudo --help` and the wrapped line is never looked at.
  if [[ -z "$out" && "$prefix" == *' '* ]]; then
    _tai_wrapped "$prefix" && out="$_TAI_WRAPPED_OUT"
  fi
  # A path argument is answered by the filesystem, and the same rule as
  # _tai_complete, so accepting with the arrow and completing with Tab cannot
  # disagree about `chmod +x `. The answer is a *word*, so it replaces the word
  # being typed rather than being appended after it. Answered in globals and
  # not in a pipeline: the head-of-list read used to cost two processes — the
  # command substitution and the `head` — on every keystroke that reached here.
  if [[ "$prefix" == *" "* ]]; then
    _tai_file_answer "$prefix"
    local file="${_TAI_FILES[0]:-}" join
    if [[ -n "$file" ]]; then
      if [[ "$prefix" == *" " ]]; then join="$prefix"; else join="${prefix% *} "; fi
      # Quoted here and not in _tai_file_answer, because the two callers need
      # different things: readline quotes its own COMPREPLY when it inserts, and
      # quoting twice would give `My\\ Document.pdf`. This one writes the line
      # itself, so it has to do it — see _tai_quote.
      _tai_quote "$file"
      out="$join$_TAI_QUOTED"
    fi
  fi
  # A systemctl line is answered by its units, from the cached list — the same
  # hook the zsh ghost makes. It never forks: an empty cache answers nothing
  # here, and the prompt hook's preload fills it after a systemctl command.
  if [[ -z "$out" ]] && _tai_unit_word "$prefix"; then
    local uw="$_TAI_UNIT_WORD" ujoin="$prefix"
    [[ -n "$uw" ]] && ujoin="${prefix%"$uw"}"
    if _tai_unit_matches "$uw"; then
      out="$ujoin${_TAI_UNIT_MS[0]}"
      [[ "$out" == "$prefix"* && "$out" != "$prefix" ]] || out=""
    fi
  fi
  # An installed command the history has never seen. `--help` is the only thing
  # worth offering: the bare name is an echo of what is already typed, and any
  # flag would be a guess. `command -v` is a builtin, so this costs no process,
  # and it only runs when the index has no answer, so a tool with learned
  # knowledge never reaches it. A directory argument is never answered with
  # --help: `cd --help` is a man page where a destination was asked for.
  if [[ -z "$out" ]] && ! _tai_dirarg "$prefix"; then
    first="${prefix%% *}"
    # Last resort, and only while the line is still just the command name or has
    # just opened its first argument. `tool --p` is not answered with
    # `tool --help`.
    if [[ "$prefix" == "$first" || "$prefix" == *' ' ]] && _tai_installed "$first"; then
      out="$first --help"
    fi
  fi
  # A learned cd destination was judged for liveness where it was recorded,
  # and alive there. From the directory the user stands in now it can be
  # anything but — bare `vllm` offered two directories away from vllm is the
  # reported case, and Enter on it answers "no such file or directory". One
  # builtin stat, paid only on a line whose head is cd or pushd.
  if [[ -n "$out" ]] && _tai_dirarg "$prefix"; then
    _tai_destination_live "${out##* }" || out=""
  fi
  _TAI_OUT="$out"
}

_tai_repo() {
  # The repository's *name*, which is the last path segment. It was
  # `git rev-parse --show-toplevel | rev | cut -d/ -f1 | rev`, which is the same
  # string with two more processes on the way to every recorded command — a
  # command substitution has already stripped the trailing newline, and
  # parameter expansion cannot fork.
  local top
  top=$(git rev-parse --show-toplevel 2>/dev/null) || return 0
  printf '%s' "${top##*/}"
}
_tai_branch() {
  git rev-parse --abbrev-ref HEAD 2>/dev/null
}
