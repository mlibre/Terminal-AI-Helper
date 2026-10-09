# The index file, the state tai keeps between prompts, and every limit.
#
# This is the only part that does anything at source time: it declares the
# associative arrays and reads the index once, so the parts after it can be
# pure definitions.

# TAI_INDEX names the *zsh* index — the same variable the index builder and
# plugins/tai.zsh read, so there is one decision about where the indexes live
# rather than two that can disagree. This file is one name away from it, which is
# the same rule tai/index.py applies when it writes the pair. It used to read
# TAI_INDEX as if it named *this* file, so with TAI_INDEX set the bash shell
# sourced the zsh snapshot and bash-index.bash was written twice a day and read
# by nobody.
_TAI_INDEX_FILE="${TAI_INDEX:-${XDG_DATA_HOME:-$HOME/.local/share}/tai/zsh-index.zsh}"
_TAI_INDEX_FILE="${_TAI_INDEX_FILE%/*}/bash-index.bash"
# The index is a file, and a file rewritten after this shell started is invisible
# to the arrays this shell already holds. The stamp is a zero-length marker
# touched whenever the file and the shell agree, so `index -nt stamp` answers
# "has it moved on?" with a builtin comparison and no process.
_TAI_INDEX_STAMP="${_TAI_INDEX_FILE}.stamp"
# Declare the maps even when the index file is missing or unreadable. Without
# this, an unset variable is an *indexed* array, and bash evaluates a subscript
# arithmetically — so a command whose name starts with a digit ("9router")
# prints "value too great for base" on every keystroke instead of looking
# anything up. A shell started before the first `tai refresh` still has to
# be quiet and still gets the installed-command fallback.
declare -gA _TAI_SCORE _TAI_FIRST _TAI_WORD _TAI_SEQ _TAI_FILE

# Is this file the index, or something that only looks like it?
#
# The index is sourced, so its contents are code, and in bash the damage from a
# file that is not one does not stay inside that file: an unterminated `(`
# leaves the parser mid-construct, so the *plugin's* next line is read as part of
# it and run as a command — the shell printed
# `bash: [[ -n /home/u/.bash_history && -z ]]: No such file or directory` on every
# start, from a file the user never touched.
#
# One builtin read, no fork: the writer's own first line. It is not proof the body
# is sound, and it does not need to be — `tai/index.py` writes a temporary file
# and `os.replace`s it, so a torn index cannot exist, and this catches the
# failures that can: the wrong TAI_INDEX, an empty file, a truncated download.
_tai_index_readable() {
  local first="" second=""
  # Both lines in one redirect, because a second `< "$file"` opens the file again
  # and reads the *first* line a second time — which is how the check below
  # passed every file, including the ones it exists to refuse. The trailing `true`
  # is because the second read fails on a header-only index, and a non-zero
  # status anywhere in a sourced file kills a shell running `set -e`.
  { IFS= read -r first; IFS= read -r second; true; } < "$_TAI_INDEX_FILE" 2>/dev/null
  [[ "$first" == '# tai'* ]] || {
    echo "tai: $_TAI_INDEX_FILE is not an index — run 'tai refresh'" >&2
    return 1
  }
  # The second line is the first line of *data*, and every one of those starts
  # with `_TAI_`, or declares the maps (`declare` here, `typeset` in zsh), or is a
  # comment. This is what refuses a file with the right header and something else
  # underneath it — the shape a truncated or foreign file has — for the cost of
  # one more builtin read. A header with nothing under it is a legitimately empty
  # index.
  [[ -z "$second" || "$second" == '#'* || "$second" == '_TAI_'* \
     || "$second" == typeset\ * || "$second" == declare\ * ]] && return 0
  echo "tai: $_TAI_INDEX_FILE does not look like an index — run 'tai refresh'" >&2
  return 1
}

_tai_load_index() {
  # Emptied first, because every line in the index is an append. Re-sourcing
  # added and updated but could never remove, so anything a rebuild dropped stayed
  # installed for the life of the shell — the same rule as plugins/tai.zsh, and
  # for the same reason: the file has to be the whole truth rather than a growing
  # superset of every index this shell has ever read.
  _TAI_SCORE=() _TAI_FIRST=() _TAI_WORD=() _TAI_SEQ=() _TAI_FILE=()
  # The sorted-keys array is built on first use, not here: its one fork (a
  # `sort` over every first-word key) belonged to the shell's startup, which
  # is exactly the cost a plugin should not add — and most keystrokes never
  # need it, because the exact-key hit answers first. _tai_first_keys below
  # builds it the first time a half-typed name asks, and every shell start
  # that never types one pays nothing.
  _TAI_FIRST_KEYS_BUILT=""
  _TAI_FIRST_KEYS=()
  [[ -r "$_TAI_INDEX_FILE" ]] && _tai_index_readable && source "$_TAI_INDEX_FILE"
  # The redirect is wrapped in a group whose stderr is redirected, because inside
  # a function bash reports a failed redirect past the simple command's own
  # 2>/dev/null. The data directory may not exist yet, and a message on every
  # shell start is worse than a missing stamp. _tai_index_changed retries.
  { : >| "$_TAI_INDEX_STAMP"; } 2>/dev/null
}

# The _TAI_FIRST keys, sorted once, for the binary search a half-typed command
# name walks instead of scanning every key. One fork per shell that uses it,
# never per keystroke; the comparison in the search uses the shell's own
# collation, and the sort runs in that same locale, so the two agree.
# The flag is what makes "empty" mean empty rather than unbuilt: an index with
# no first-word keys at all would otherwise re-fork on every half-typed letter.
_tai_first_keys() {
  [[ -n "$_TAI_FIRST_KEYS_BUILT" ]] && return 0
  readarray -t _TAI_FIRST_KEYS < <(printf '%s\n' "${!_TAI_FIRST[@]}" | sort)
  _TAI_FIRST_KEYS_BUILT=1
}

_tai_index_changed() {
  [[ -r $_TAI_INDEX_FILE ]] || return 1
  # A shell started before the first `tai refresh` has no data directory, so the
  # stamp could not be created — and switching the watch off there meant the
  # reload this file exists to provide never arrived, for the life of the shell.
  # Retry instead: a redirect costs no fork, and the directory appears with the
  # first index. Requiring the stamp *before* comparing is what stops a missing
  # one from re-sourcing the whole index at every prompt.
  [[ -e $_TAI_INDEX_STAMP ]] || { : >| "$_TAI_INDEX_STAMP"; } 2>/dev/null || return 1
  [[ $_TAI_INDEX_FILE -nt $_TAI_INDEX_STAMP ]]
}

_tai_load_index

# Tell tai which history file this shell is actually writing to.
#
# bash sets HISTFILE as a shell parameter and does not export it either, so every
# tai subprocess sees nothing and `tai refresh` imports only the default paths.
# The plugin is the only thing that knows the file, so it says so — once, and
# only when the user has not already said it themselves.
[[ -n "${HISTFILE:-}" && -z "${TAI_HISTORY_FILES:-}" ]] && export TAI_HISTORY_FILES="$HISTFILE"

# TAI_BIN may be more than one word (the fallback below is "python3 <cli>"), so
# keep an argv array. Expanding "$_TAI_BIN" would look for a single file whose
# name contains a space, which is why recording failed when tai was not on PATH.
read -r -a _TAI_ARGV <<< "${TAI_BIN:-}"
if [[ ${#_TAI_ARGV[@]} -eq 0 ]]; then
  _TAI_ARGV=(tai)
  if ! command -v tai >/dev/null 2>&1; then
    _TAI_CLI="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/../tai/cli.py"
    [[ -r "$_TAI_CLI" ]] && _TAI_ARGV=(python3 -S -E "$_TAI_CLI")
  fi
fi

# The spool: the write side of recording. One user command used to be one
# backgrounded python process — `tai record` forked an interpreter per command
# for the life of an install, about 30ms of CPU each. Now the command is one
# builtin append to this file, and `tai flush` — asked for only when the batch
# is worth a process — ingests it into the store, maintains the index, and
# learns the batch's new tools, all in one interpreter.
#   TAI_SPOOL          names the spool file (default: beside the database)
#   TAI_SPOOL_MAX      pending records that force a flush (default 8)
#   TAI_SPOOL_SECONDS  idle seconds that force one (default 3)
_TAI_SPOOL_FILE="${TAI_SPOOL:-}"
if [[ -z "$_TAI_SPOOL_FILE" && -n "${TAI_DB:-}" ]]; then
  # The spool lives beside the database, the same rule tai/spool.py applies;
  # a relative TAI_DB is the current directory in both.
  [[ "$TAI_DB" == */* ]] && _TAI_SPOOL_FILE="${TAI_DB%/*}/spool.log" || _TAI_SPOOL_FILE="spool.log"
fi
: "${_TAI_SPOOL_FILE:=${XDG_DATA_HOME:-$HOME/.local/share}/tai/spool.log}"
# The knobs are read from their own public names — `: "${_TAI_SPOOL_MAX:=8}"`
# would ask about the underscore variable, never the environment's, and a
# TAI_SPOOL_MAX=1 in the environment would be ignored while the flush waited
# for eight records forever. The `+0` is the boundary: a garbage value
# degrades to zero (flush every command) rather than to a math error in a
# prompt hook.
_TAI_SPOOL_MAX=$(( ${TAI_SPOOL_MAX:-8} + 0 ))
_TAI_SPOOL_SECONDS=$(( ${TAI_SPOOL_SECONDS:-3} + 0 ))
_TAI_SPOOL_N=0
# The timestamp a record carries and the idle clock the flush decision reads
# are this builtin's, because a fork for the time would cost more than the
# record it is attached to. A bash without %(...)T records ts=0, and the flush
# stamps the drain time instead — coarser recency, nothing lost.
printf -v _TAI_FLUSH_AT '%(%s)T' -1 2>/dev/null || _TAI_FLUSH_AT=0

_TAI_SUGGESTION=""
_TAI_LAST=""
# What a path argument could be, and when each of those files was last written.
# Globals rather than locals of the functions that compute them: bash has no
# nameref, so a command substitution would lose the arrays, and a completion
# function that has to fork to hand back its own intermediate result is a fork it
# did not need. _TAI_FRESH_MTIME is parallel to _TAI_FRESH and is what makes the
# "newest first" order in _tai_keep_fresh a comparison rather than a guess.
_TAI_FRESH=()
_TAI_FRESH_MTIME=()
# The answers, written into these rather than captured from stdout: the keystroke
# handlers run under `bind -x`, where a `$( )` around a lookup was a fork per
# keypress for a string the lookup had already written. Initialised so a shell
# with `set -u` can read them before the first lookup answers.
_TAI_OUT=""
_TAI_VALUES=""
_TAI_BEST_LINE=""
_TAI_WRAPPED_OUT=""
_TAI_QUOTED=""
_TAI_FILES=()
_TAI_FILES_Q=()

# Command names considered for a half-typed command name, in one lookup. A key
# holds at most WORD_CANDIDATE_CAP lines, so this bounds the lines too.
_TAI_PREFIX_KEYS=64

# How deep one first-word key is read, and how many learned whole lines Tab
# offers ahead of the installed names (_tai_first_lines in lookup.bash). The
# zsh menu keeps the same two bounds (_TAI_MENU_FIRST_DEPTH / _TAI_MENU_FIRST_CAP):
# one head per key is the ghost's contract, and a menu that showed only the
# keys' heads would hide exactly the lines the web panel ranks beside
# the winner.
_TAI_FIRST_LINES_DEPTH=3
_TAI_FIRST_LINES_CAP=16
