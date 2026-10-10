# Unit names for systemctl, answered from a cache the shell holds.
#
# The same answer the zsh plugin gives: the unit list is read once — from the
# disk cache when it is fresh, from one `systemctl` call when it is not — and
# from then on Tab completes unit names from an array. A machine without
# systemd never has a cache, the one fork fails quietly, and systemctl lines
# fall through to the rest of the completion as before.

_TAI_UNIT_VERBS=" start stop restart reload try-restart reload-or-restart try-reload-or-restart status show cat enable disable mask unmask preset kill freeze thaw clean "
_TAI_UNITS_TTL=86400            # seconds one unit cache answers for
_TAI_UNITS=()
_TAI_UNITS_USER=()

_tai_units_cache() {
  local base="${XDG_CACHE_HOME:-$HOME/.cache}/tai"
  if (( $1 )); then _TAI_UNITS_FILE="$base/units-user.txt"; else _TAI_UNITS_FILE="$base/units-system.txt"; fi
}

# The cached unit list for one scope, into _TAI_UNITS or _TAI_UNITS_USER.
# Reads the cache file when it is fresh; forks the one `systemctl` call and
# rewrites the cache when it is not. Never fails loudly.
_tai_units_load() {
  local -i user=$1
  local file list line
  _tai_units_cache "$user"
  file="$_TAI_UNITS_FILE"
  # The stamp and the first unit are read together, because a file with a
  # stamp and nothing under it is not a cache that answered — it is a torn
  # or truncated one, and "zero units" would stick for the TTL. The zsh
  # loader has always required both lines; this one matches it now.
  local epoch="" first=""
  if [[ -r "$file" ]]; then
    { read -r epoch; read -r first; true; } < "$file" 2>/dev/null
  fi
  if [[ "$epoch" =~ ^[0-9]+$ && -n "$first" ]] && (( EPOCHSECONDS - epoch < _TAI_UNITS_TTL && EPOCHSECONDS >= epoch )); then
    list=()
    while IFS= read -r line; do [[ -n "$line" ]] && list+=( "$line" ); done < <(tail -n +2 "$file" 2>/dev/null)
    if (( user )); then _TAI_UNITS_USER=( "${list[@]}" ); else _TAI_UNITS=( "${list[@]}" ); fi
    return 0
  fi
  if (( user )); then
    list=()
    while IFS= read -r line; do list+=( "$line" ); done < <(systemctl --user list-unit-files --no-legend --plain 2>/dev/null)
  else
    list=()
    while IFS= read -r line; do list+=( "$line" ); done < <(systemctl list-unit-files --no-legend --plain 2>/dev/null)
  fi
  local -a names=()
  local w
  for line in "${list[@]}"; do
    w="${line%% *}"
    [[ "$w" == *.* ]] && names+=( "$w" )
  done
  if (( ${#names[@]} )); then
    mkdir -p "${file%/*}" 2>/dev/null
    { printf '%s\n' "$EPOCHSECONDS"; printf '%s\n' "${names[@]}"; } >| "$file" 2>/dev/null
  fi
  if (( user )); then _TAI_UNITS_USER=( "${names[@]}" ); else _TAI_UNITS=( "${names[@]}" ); fi
  return 0
}

# Is the line a systemctl line whose next word is a unit? The word into
# _TAI_UNIT_WORD ("" when the line has just opened the argument), the scope
# into _TAI_UNIT_SCOPE (1 for `--user`). Wrappers are transparent, flags are
# skipped, and the verb must be one the list names.
_tai_unit_word() {
  local line="$1"
  local -a ws=()
  local w
  local -i i=0 seen_verb=0
  read -r -a ws <<< "$line"
  local -i n=${#ws[@]}
  (( n >= 2 )) || return 1
  _TAI_UNIT_SCOPE=0
  # A wrapper runs the command behind it, so it is transparent here too.
  if [[ "${ws[0]}" == sudo || "${ws[0]}" == doas ]]; then i=1; fi
  (( i < n )) || return 1
  [[ "${ws[i]}" == systemctl ]] || return 1
  (( ++i ))
  while (( i < n )); do
    w="${ws[i]}"
    case "$w" in
      --user) _TAI_UNIT_SCOPE=1; (( ++i )); continue ;;
      -*) (( ++i )); continue ;;
    esac
    if (( ! seen_verb )); then
      [[ "$_TAI_UNIT_VERBS" == *" $w "* ]] || return 1
      seen_verb=1
      (( ++i ))
      continue
    fi
    _TAI_UNIT_WORD="$w"
    return 0
  done
  # The verb is complete; a unit is asked for only when the line has opened
  # its argument — the space after the verb — otherwise the word being typed
  # is still the verb itself.
  if (( seen_verb )) && [[ "$line" == *" " ]]; then
    _TAI_UNIT_WORD=""
    return 0
  fi
  return 1
}

# The units the typed word could be, into _TAI_UNIT_MS. Loads the cache on
# first use — the one fork a completion press pays, once.
_tai_unit_matches() {
  local word="$1" u
  _TAI_UNIT_MS=()
  if [[ "${_TAI_UNIT_SCOPE:-0}" == "1" ]]; then
    (( ${#_TAI_UNITS_USER[@]} )) || _tai_units_load 1
    # Folded, like every lookup: `Net` reaches NetworkManager.service.
    for u in "${_TAI_UNITS_USER[@]}"; do [[ "${u,,}" == "${word,,}"* ]] && _TAI_UNIT_MS+=( "$u" ); done
  else
    (( ${#_TAI_UNITS[@]} )) || _tai_units_load 0
    for u in "${_TAI_UNITS[@]}"; do [[ "${u,,}" == "${word,,}"* ]] && _TAI_UNIT_MS+=( "$u" ); done
  fi
  (( ${#_TAI_UNIT_MS[@]} > 0 ))
}
