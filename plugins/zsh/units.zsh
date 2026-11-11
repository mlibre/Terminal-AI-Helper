# Unit names for systemctl, answered from a cache the shell holds.
#
# The default zsh completion for `sudo systemctl restart <Tab>` cycles the real
# service units, and a shell without that feels broken the moment you type one.
# This is the same answer, tai-shaped: the unit list is read once — from the
# disk cache when it is fresh, from one `systemctl` call when it is not — and
# from then on every Tab and every ghost hint answers from an array, with no
# process and no I/O on the keystroke path.
#
# The cache lives beside the other caches tai keeps and is refreshed after a
# day; the prompt hook also pre-loads it in the background the first time a
# command mentioning systemctl has run, so the Tab after it usually forks
# nothing either. A machine without systemd simply never has a cache, the one
# fork fails quietly, and systemctl lines fall through to the rest of the
# menu — the file listing, the learned words — as before.

# Verbs whose next word is a unit. A closed list, deliberately without
# `list-units` and `list-unit-files` (their argument is a pattern, and the
# shell's own listing is the better answer there) and without `daemon-*` and
# friends (they take no unit at all).
typeset -g _TAI_UNIT_VERBS=" start stop restart reload try-restart \
reload-or-restart try-reload-or-restart status show cat enable disable \
mask unmask preset kill freeze thaw clean "
typeset -gi _TAI_UNITS_TTL=86400   # seconds one unit cache answers for
typeset -ga _TAI_UNITS=()
typeset -ga _TAI_UNITS_USER=()

(( $+EPOCHSECONDS )) || zmodload zsh/datetime 2>/dev/null

_tai_units_cache() {
  local base="${XDG_CACHE_HOME:-$HOME/.cache}/tai"
  if (( $1 )); then
    REPLY="$base/units-user.txt"
  else
    REPLY="$base/units-system.txt"
  fi
}

# The cached unit list for one scope, read-only, into the module's array.
# Answers 0 only when a fresh cache file was read — the fork-free half of the
# loader, and the half a widget is allowed to call: a completion press must
# never sit inside a child process waiting for systemctl, which is what the
# combined loader did when the array was empty and the cache had gone stale.
# On a machine whose systemd is slow to answer — a loaded box, a container
# host with a few hundred units — that wait was seconds long, and the report
# that found it read as the shell freezing on Tab. What a press that finds
# nothing gets instead is the kick below: the load starts detached, and the
# next press answers from the array it filled.
_tai_units_read() {
  local -i user=$1
  local file list
  _tai_units_cache "$user"
  file="$REPLY"
  local epoch="" first=""
  if [[ -r "$file" ]]; then
    { read -r epoch; read -r first; true; } < "$file" 2>/dev/null
  fi
  [[ "$epoch" == <-> && -n "$first" ]] || return 1
  (( EPOCHSECONDS - epoch < _TAI_UNITS_TTL && EPOCHSECONDS >= epoch )) || return 1
  list=( "${(@f)$(<"$file")}" )
  list=( "${list[@]:1}" )
  if (( user )); then _TAI_UNITS_USER=( "${list[@]}" ); else _TAI_UNITS=( "${list[@]}" ); fi
  return 0
}

# The one detached fork that may fill the cache. Guarded per scope, so a burst
# of presses starts one child and not one per press.
typeset -gi _TAI_UNITS_KICKED=0
typeset -gi _TAI_UNITS_KICKED_USER=0
_tai_units_kick() {
  local -i user=$1
  if (( user )); then
    (( _TAI_UNITS_KICKED_USER )) && return 0
    _TAI_UNITS_KICKED_USER=1
  else
    (( _TAI_UNITS_KICKED )) && return 0
    _TAI_UNITS_KICKED=1
  fi
  _tai_dbg units "kick detached load user=$user"
  ( ( _tai_units_load "$user" >/dev/null 2>&1 ) & )
  return 0
}

# The forked half: read the cache, and only when it cannot answer, fork the
# one `systemctl` call and rewrite the cache. Callers of this are background
# (the prompt hook's preload and the kick above) — never a widget in the
# foreground. Answers with the array it has — an empty list on a machine
# without systemd — and never raises: a completion must not be able to fail
# loudly.
_tai_units_load() {
  local -i user=$1
  local file list tmp
  _tai_units_read "$user" && return 0
  # One fork, only when the cache cannot answer. `--no-legend --plain` keeps
  # the output one unit per line; the first field of each is the name.
  if (( user )); then
    list=( "${(@f)$(systemctl --user list-unit-files --no-legend --plain 2>/dev/null)}" )
  else
    list=( "${(@f)$(systemctl list-unit-files --no-legend --plain 2>/dev/null)}" )
  fi
  list=( "${(@M)list:#* *}" )
  list=( "${list[@]%% *}" )
  list=( "${(@M)list:#*.*}" )
  if (( ${#list[@]} )); then
    _tai_units_cache "$user"
    file="$REPLY"
    mkdir -p "${file:h}" 2>/dev/null
    tmp="$file.tmp.$$"
    { print -r -- "$EPOCHSECONDS"; print -r -l -- "${list[@]}"; } >| "$tmp" 2>/dev/null && \
      mv -f "$tmp" "$file" 2>/dev/null
  fi
  if (( user )); then _TAI_UNITS_USER=( "${list[@]}" ); else _TAI_UNITS=( "${list[@]}" ); fi
  _tai_dbg units "load user=$user units=${#list[@]}"
  return 0
}

# Is the line a systemctl line whose next word is a unit? The word, into
# $REPLY ("" when the line has just opened the argument); the scope, into
# $REPLY_USER (1 for `--user`). Wrappers are transparent, flags are skipped,
# and the verb must be one the list above names: `systemctl resta` is a verb
# still being typed, and a unit after it would be a guess.
_tai_unit_word() {
  local -a ws
  local w verb=""
  local -i i=1 n user=0 seen_verb=0
  ws=( "${(@s: :)1}" )
  n=${#ws}
  (( n >= 2 )) || return 1
  [[ "${ws[1]}" == sudo || "${ws[1]}" == doas ]] && i=2
  (( i <= n )) || return 1
  [[ "${ws[i]}" == systemctl ]] || return 1
  for (( ++i; i <= n; i++ )); do
    w="${ws[i]}"
    if [[ "$w" == --* ]]; then
      [[ "$w" == --user ]] && user=1
      continue
    fi
    [[ "$w" == -* ]] && continue
    if (( ! seen_verb )); then
      [[ "$_TAI_UNIT_VERBS" == *" $w "* ]] || return 1
      seen_verb=1
      continue
    fi
    REPLY="$w"
    REPLY_USER=$user
    return 0
  done
  # The verb is complete and nothing follows it. A unit is being asked for
  # only when the line has opened its argument — the space after the verb —
  # otherwise the word being typed is still the verb itself.
  if (( seen_verb )) && [[ "$1" == *' ' ]]; then
    REPLY=""
    REPLY_USER=$user
    return 0
  fi
  return 1
}

# The units the typed word could be, into _TAI_UNIT_MS. Served from the
# arrays, then from the cache file — both fork-free. When neither can answer,
# the load is kicked detached and this press answers nothing: the one fork a
# unit completion needs is off the keystroke path, and the press after this
# one finds the array filled.
_tai_unit_matches() {
  local word="$1" list
  _TAI_UNIT_MS=()
  if (( REPLY_USER )); then
    (( ${#_TAI_UNITS_USER} )) || _tai_units_read 1 || _tai_units_kick 1
    list=( "${_TAI_UNITS_USER[@]}" )
  else
    (( ${#_TAI_UNITS} )) || _tai_units_read 0 || _tai_units_kick 0
    list=( "${_TAI_UNITS[@]}" )
  fi
  (( ${#list[@]} )) || return 1
  _tai_globable "$word" || return 1
  # Folded, like every lookup: `Net` reaches NetworkManager.service. (#i)
  # needs extendedglob; localoptions puts this shell's own options back on
  # return.
  setopt localoptions extendedglob
  _TAI_UNIT_MS=( "${(@M)list:#(#i)${(b)word}*}" )
  (( ${#_TAI_UNIT_MS[@]} )) || _tai_dbg units "no unit extends ${(q)word}"
  (( ${#_TAI_UNIT_MS[@]} ))
}

# The ghost-text half: when the line is a unit argument and the cache can
# answer, the best line is the line with the word replaced by the first unit
# that extends it — the same shape the file answer gives the ghost, and the
# same rule that the answer has to extend what was typed. Answers in
# _TAI_UNIT_BEST; never forks, because the ghost path cannot: an empty cache
# simply answers nothing, and the next Tab (or the prompt hook's preload)
# fills it.
typeset -g _TAI_UNIT_BEST=""
typeset -ga _TAI_UNIT_MS=()
_tai_unit_best() {
  _TAI_UNIT_BEST=""
  _tai_unit_word "$1" || return 1
  (( ${#_TAI_UNITS} || ${#_TAI_UNITS_USER} )) || return 1
  local word="$REPLY" headform
  _tai_dbg units "best word=${(q)word} scope=$REPLY_USER line=${(q)1}"
  if [[ -z "$word" ]]; then
    headform="$1"
  else
    headform="${1%"$word"}"
  fi
  _tai_unit_matches "$word" || return 1
  _TAI_UNIT_BEST="${headform}${_TAI_UNIT_MS[1]}"
  _tai_dbg units "best answer=${(q)_TAI_UNIT_BEST} matches=${#_TAI_UNIT_MS[@]}"
  [[ "$_TAI_UNIT_BEST" == "$1"* && "$_TAI_UNIT_BEST" != "$1" ]]
}

# The menu half: the units for the word under the cursor, into _TAI_UNITS_MENU.
# Returns 1 when the line is not a unit argument, so the menu falls through to
# its other sources; when it is, the units ARE the answer and the files, the
# command names and the directory listing would only be noise around them.
typeset -ga _TAI_UNITS_MENU=()
_tai_units_menu() {
  _TAI_UNITS_MENU=()
  _tai_unit_word "$BUFFER" || return 1
  _tai_unit_matches "$REPLY" || return 0
  _TAI_UNITS_MENU=( "${_TAI_UNIT_MS[@]}" )
  _tai_dbg units "menu candidates=${#_TAI_UNITS_MENU[@]}"
  (( ${#_TAI_UNITS_MENU[@]} > _TAI_PREFIX_KEYS )) && \
    _TAI_UNITS_MENU=( "${_TAI_UNITS_MENU[@]:0:_TAI_PREFIX_KEYS}" )
  return 0
}
