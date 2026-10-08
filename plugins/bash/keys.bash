# What the keys do, and the recording hook that runs at every prompt.

_tai_prompt() {
  local code=$? raw last_cmd
  # Learn something, and the shell you are sitting in gets it now. A rebuild
  # replaces the file atomically, so this sees either the old index or the new
  # one and never a half-written file.
  if _tai_index_changed; then _tai_load_index; fi
  # bash has no preexec hook, so the last command has to be read back. Capturing
  # `history 1` costs one subshell; it used to cost a second fork for the `sed`
  # that stripped the "  123  " prefix, which parameter expansion does without a
  # process. It is read even when recording is off, because the in-memory last
  # command is what predictions are made from.
  raw="$(history 1)"
  raw="${raw#"${raw%%[![:space:]]*}"}"       # drop the leading indent
  while [[ "$raw" == [0-9]* ]]; do raw="${raw#?}"; done   # drop the history number
  last_cmd="${raw#"${raw%%[![:space:]]*}"}"  # drop the space after it
  if [[ -n "$last_cmd" && "$last_cmd" != "$_TAI_LAST" ]]; then
    _TAI_LAST="$last_cmd"
    # The unit cache pre-loads itself in the background after a command that
    # mentions systemctl has run — the next Tab or hint on a systemctl line
    # then answers from an array instead of forking. Off the critical path.
    if [[ "$last_cmd" == *systemctl* ]]; then
      ( _tai_units_load 0 >/dev/null 2>&1; _tai_units_load 1 >/dev/null 2>&1 ) &
    fi
    # TAI_NO_AUTO_RECORD=1 keeps the in-memory last command for predictions
    # but skips the durable write. Read per prompt so it can be set per command.
    [[ "${TAI_NO_AUTO_RECORD:-0}" == "1" ]] || \
      ( "${_TAI_ARGV[@]}" record "$last_cmd" --cwd "${_TAI_CWD:-$PWD}" --git "$(_tai_repo)" --branch "$(_tai_branch)" --exit $code >/dev/null 2>&1 & )
  fi
  # The directory this prompt was written in is where the command it offers
  # will be typed, so it is the next command's *origin*. Recording prompt-time
  # $PWD instead meant a successful `cd src` was stored with the destination
  # as the place it was run from, and the liveness check condemned it.
  _TAI_CWD="$PWD"
}
if [[ "${PROMPT_COMMAND:-}" != *"_tai_prompt"* ]]; then PROMPT_COMMAND="_tai_prompt${PROMPT_COMMAND:+;$PROMPT_COMMAND}"; fi

# Every handler here reads the answer out of _TAI_OUT rather than out of a
# command substitution: `bind -x` runs in the shell itself, so a `$( )` around
# a lookup was a fork per keypress for a string the lookup had already written
# into a variable. _tai_query still prints — that is the contract the tests
# read — but nothing on this path captures it any more.
_tai_accept() {
  _tai_query "$READLINE_LINE" "$_TAI_LAST"
  local out="$_TAI_OUT"
  if [[ -n "$out" && "$out" == "$READLINE_LINE"* ]]; then
    READLINE_LINE="$out"; READLINE_POINT=${#READLINE_LINE}
  fi
}
_tai_next() {
  _tai_query "" "$_TAI_LAST"
  local out="$_TAI_OUT"
  [[ -n "$out" ]] && READLINE_LINE="$out" && READLINE_POINT=${#READLINE_LINE}
}
# One word of the suggestion, as in zsh: the leading space and the first word of
# the answer, and only those. With nothing to accept the key moves right one
# character, which is what the right arrow itself does in this plugin — the
# alternative was a key that reads as a suggestion and does nothing.
_tai_accept_word() {
  _tai_query "$READLINE_LINE" "$_TAI_LAST"
  local out="$_TAI_OUT"
  if [[ -n "$out" && "$out" == "$READLINE_LINE"* ]]; then
    out="${out#$READLINE_LINE}"
    if [[ "$out" =~ ^([[:space:]]*[^[:space:]]+) ]]; then
      READLINE_LINE+="${BASH_REMATCH[1]}"
      READLINE_POINT=${#READLINE_LINE}
      return
    fi
    # The hint was whitespace only, so there is no word in it; take all of it.
    READLINE_LINE+="$out"; READLINE_POINT=${#READLINE_LINE}
  else
    READLINE_POINT=$((READLINE_POINT + 1))
  fi
}
# Right arrow accepts the suggestion at end of line, otherwise moves right.
_tai_accept_or_right() {
  _tai_query "$READLINE_LINE" "$_TAI_LAST"
  local out="$_TAI_OUT"
  if [[ -n "$out" && "$out" == "$READLINE_LINE"* && $READLINE_POINT == ${#READLINE_LINE} ]]; then
    READLINE_LINE="$out"; READLINE_POINT=${#READLINE_LINE}
  elif (( READLINE_POINT < ${#READLINE_LINE} )); then
    READLINE_POINT=$((READLINE_POINT + 1))
  fi
}
_tai_complete() {
  local line="${COMP_LINE:0:$COMP_POINT}" first word values="" c
  if [[ "$line" == *' '* ]]; then
    # Same rule as _tai_query: the last complete word is the key, so `ls -l`
    # also offers `ls -la` — and a key with no space in it is the command name,
    # which _TAI_FIRST holds.
    word="${line% *}"
    if [[ "$word" == *" "* ]]; then
      values="${_TAI_WORD[$word]:-}"
    else
      _tai_first_values "$word"
      values="$_TAI_VALUES"
    fi
  elif [[ -n "$line" ]]; then
    first="$line"
    _tai_first_values "$first"
    values="$_TAI_VALUES"
  else
    values=""
  fi
  COMPREPLY=()
  # Units first for a systemctl line: the cached unit list IS the vocabulary
  # there, and files and learned words around it would be noise. Loaded on
  # first use — the one fork a completion press pays, once.
  if _tai_unit_word "$line" && _tai_unit_matches "$_TAI_UNIT_WORD"; then
    for c in "${_TAI_UNIT_MS[@]}"; do _tai_reply "$c"; done
    return 0
  fi
  # Files first for a line that ends in one: the filesystem is the vocabulary for
  # a path, and what is on disk beats a name from the history that may not be
  # there at all. Read through the same lookup _tai_query makes, so bash and
  # zsh answer the same question the same way — answered in globals, not in a
  # process substitution, which was a fork per completion press.
  _tai_file_answer "$line"
  for c in "${_TAI_FILES[@]}"; do
    [[ -n "$c" ]] && _tai_reply "$c"
  done
  # readline replaces the *current word* with each entry, so a whole command
  # line as an entry would be inserted in the middle of the line. Offer the
  # last word; everything before it is already typed.
  while IFS= read -r c; do
    [[ -n "$c" && "$c" == "$line"* ]] && _tai_reply "${c##*[[:space:]]}"
  done <<< "$values"
}

# Add one completion, once. A learned file argument that still exists is offered
# by both loops above — once as the file that is here, once as the line the
# history holds — and COMPREPLY holding it twice makes readline print it twice.
_tai_reply() {
  local c
  # A learned line carrying raw control bytes is not a completion it is this
  # plugin's promise to never paint: what it would write is escape bytes.
  [[ "$1" =~ [[:cntrl:]] ]] && return 0
  for c in "${COMPREPLY[@]}"; do [[ "$c" == "$1" ]] && return 0; done
  COMPREPLY+=("$1")
}
complete -o nospace -F _tai_complete tai 2>/dev/null
# -o default is what makes Tab fall back to readline's own filename completion
# when tai has nothing: without it, `chmod +x ` in a shell that has never seen the
# line completes to nothing at all, where the shell knows exactly what to offer.
if [[ "${TAI_COMPLETE_ALL:-0}" == "1" ]]; then complete -D -o nospace -o default -F _tai_complete 2>/dev/null; fi
if [[ $- == *i* ]]; then
  # Tab shows what matches and leaves the line alone, which is as close as bash
  # gets to the zsh menu. readline has no menu of its own: `menu-complete`
  # inserts the entry it is on as it cycles, and it needs a terminal that answers
  # a cursor-position query, which is not something a plugin may assume. With
  # this set, Tab lists while the word is ambiguous and still completes the
  # moment it is not. TAI_NO_MENU=1 leaves readline's own behaviour alone.
  if [[ "${TAI_NO_MENU:-0}" != "1" ]]; then
    bind 'set show-all-if-ambiguous on' 2>/dev/null
    # Colour that listing from the user's own LS_COLORS: a directory's name comes
    # out in the directory colour and its trailing slash in the default one, so a
    # row of entries reads as paths. Nothing here invents LS_COLORS — a shell
    # without a colour scheme says nothing rather than guessing. There is no
    # selected cell to highlight, because readline's listing has no selection:
    # listing and choosing are separate things there, and only zsh does both.
    bind 'set colored-stats on' 2>/dev/null
  fi
  bind -x '"\C-f": _tai_accept' 2>/dev/null
  bind -x '"\e[C": _tai_accept_or_right' 2>/dev/null
  # The arrow has two byte-string spellings, and both must reach the widget:
  # a terminal in application cursor-key mode sends ESC O C, and without this
  # binding the hint would be visible while the key silently stops taking it —
  # the same miss zsh had, fixed the same way here.
  bind -x '"\eOC": _tai_accept_or_right' 2>/dev/null
  # One word of the suggestion, as in zsh. Alt-F is left to readline here — it is
  # readline's own `forward-word` and changing it would surprise a bash user —
  # and Ctrl-Right takes the word, which is the same chord the hand already
  # makes. `ESC [ 1 ; 5 C` is the xterm/kitty/konsole form; the modified arrows do
  # not change with the cursor-key mode, only the bare ones do.
  bind -x '"\e[1;5C": _tai_accept_word' 2>/dev/null
  bind -x '"\eg": _tai_next' 2>/dev/null
fi
