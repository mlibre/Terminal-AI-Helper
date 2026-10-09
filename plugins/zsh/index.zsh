# The index file, the state tai keeps between prompts, and every limit.
#
# This is the only part that does anything at source time: it declares the
# associative arrays and reads the index once, so the parts after it can be
# pure definitions.

_TAI_INDEX_FILE="${TAI_INDEX:-${XDG_DATA_HOME:-$HOME/.local/share}/tai/zsh-index.zsh}"
# The index is a file, and a file rewritten after this shell started is invisible
# to the arrays this shell already holds. Without a reload, learning something is
# only visible in the *next* shell — which is the whole papercut: you run a new
# tool once, and its flags still do not appear. The stamp is a zero-length marker
# touched whenever the file and the shell agree, so `index -nt stamp` answers
# "has it moved on?" with a builtin comparison and no process.
_TAI_INDEX_STAMP="${_TAI_INDEX_FILE}.stamp"
# Declare the maps even when the index file is missing, so a shell started before
# the first `tai refresh` is quiet and still gets the installed-command
# fallback.
typeset -gA _TAI_SCORE _TAI_FIRST _TAI_WORD _TAI_SEQ _TAI_FILE

# Is this file the index, or something that only looks like it?
#
# The index is sourced, so its contents are code. A file that is not one — the
# wrong `TAI_INDEX`, an empty download, something another tool wrote — is then
# *executed*, and in bash the damage does not stay inside the file: an
# unterminated `(` leaves the parser mid-construct, so the plugin's own
# `[[ … ]]` on the next line was run as a command name and the shell printed
# `No such file or directory` on every single start.
#
# One builtin read, no fork: the writer's own first line, plus the shape of the
# first data line. It is not proof the body is sound — nothing short of parsing it
# is — and it does not need to be: `tai/index.py` writes a temporary file and
# `os.replace`s it, so a torn index cannot exist, and the failures this catches
# are the ones that *can* happen.
_tai_index_readable() {
  local first="" second=""
  # Both lines in one redirect, because a second `< "$file"` opens the file
  # again and reads the *first* line a second time — which is how the check below
  # passed every file, including the ones it exists to refuse. The trailing `true`
  # is because the second read fails on a header-only index, and a non-zero status
  # anywhere in a sourced file kills a shell running `set -e`.
  { read -r first; read -r second; true; } < "$_TAI_INDEX_FILE" 2>/dev/null
  [[ "$first" == '# tai'* ]] || {
    print -u2 -- "tai: $_TAI_INDEX_FILE is not an index — run 'tai refresh'"
    return 1
  }
  # The second line is the first line of *data*, and every one of those starts
  # with `_TAI_`, or declares the maps (`typeset` here, `declare` in bash), or is
  # a comment. This is what refuses a file with the right header and something
  # else underneath it — the shape a truncated or foreign file has — for the cost
  # of one more builtin read. A header with nothing under it is a legitimately
  # empty index.
  [[ -z "$second" || "$second" == '#'* || "$second" == '_TAI_'* \
     || "$second" == 'typeset '* ]] && return 0
  print -u2 -- "tai: $_TAI_INDEX_FILE does not look like an index — run 'tai refresh'"
  return 1
}

_tai_load_index() {
  # Emptied first, because every line in the index is an *append* — a pair-append
  # in zsh, a subscript assignment in bash. Re-sourcing therefore added and
  # updated but could never remove, so anything the rebuild dropped stayed
  # installed for the life of the shell: `tai purge --stale` removed a
  # suggestion from the store, the next prompt re-read the index, and the stale
  # key was still there. Clearing first is what makes the file the whole truth
  # rather than a growing superset of every index this shell has ever seen.
  _TAI_SCORE=() _TAI_FIRST=() _TAI_WORD=() _TAI_SEQ=() _TAI_FILE=()
  # A lookup may cache an answer about the lines it was asked from, so a reload
  # has to retire those answers: bumping the generation is what makes a cached
  # answer speak about the index that is installed now rather than the one that
  # was installed when it was asked. Bumped rather than cleared because the
  # lookup that reads it is defined after this file; `:-0` because the first
  # load runs before that variable exists.
  _TAI_LOOSE_GEN=$(( ${_TAI_LOOSE_GEN:-0} + 1 ))
  [[ -r "$_TAI_INDEX_FILE" ]] && _tai_index_readable && source "$_TAI_INDEX_FILE"
  # The redirect is wrapped in a group whose stderr is redirected, because inside
  # a function zsh reports a failed redirect past the simple command's own
  # 2>/dev/null. The data directory may not exist yet, and a message on every
  # shell start is worse than a missing stamp. _tai_index_changed retries.
  { : >| "$_TAI_INDEX_STAMP"; } 2>/dev/null
}

_tai_index_changed() {
  [[ -r "$_TAI_INDEX_FILE" ]] || return 1
  # A shell started before the first `tai refresh` has no data directory, so the
  # stamp could not be created — and switching the watch off there meant the
  # reload this file exists to provide never arrived, for the life of the shell.
  # Retry instead: a redirect costs no fork, and the directory appears with the
  # first index. Requiring the stamp *before* comparing is what stops a missing
  # one from re-sourcing the whole index at every prompt.
  [[ -e "$_TAI_INDEX_STAMP" ]] || { { : >| "$_TAI_INDEX_STAMP"; } 2>/dev/null || return 1; }
  [[ "$_TAI_INDEX_FILE" -nt "$_TAI_INDEX_STAMP" ]]
}

_tai_load_index

# Tell tai which history file this shell is actually writing to.
#
# zsh sets HISTFILE as a shell parameter and does not export it, so every tai
# subprocess sees nothing: `os.environ.get("HISTFILE")` is empty, `tai refresh`
# imports only the default paths, and a machine that keeps its history in
# `~/.zhistory` — which is Manjaro's choice, and not a default any reader could
# guess — contributes nothing at all. Measured on the machine this was reported
# from: 441 recorded commands, every one of them out of `~/.bash_history`, and
# `~/.zhistory` absent from the import table.
#
# The plugin is the only thing that knows the file, so it says so — once, and
# only when the user has not already said it themselves.
[[ -n "${HISTFILE:-}" && -z "${TAI_HISTORY_FILES:-}" ]] && export TAI_HISTORY_FILES="$HISTFILE"

# An explicit TAI_BIN is used as given. Otherwise prefer the installed `tai`,
# and fall back to the in-repo CLI so a source checkout works uninstalled.
# ${(z)_TAI_BIN} splits on shell words, so "python3 <cli>" stays two words.
_TAI_BIN="${TAI_BIN:-}"
if [[ -z "$_TAI_BIN" ]]; then
  _TAI_BIN="tai"
  if ! command -v tai >/dev/null 2>&1; then
    _TAI_CLI="${0:A:h}/../tai/cli.py"
    [[ -r "$_TAI_CLI" ]] && _TAI_BIN="python3 -S -E $_TAI_CLI"
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
_tai_spool_file="${TAI_SPOOL:-}"
if [[ -z "$_tai_spool_file" && -n "${TAI_DB:-}" ]]; then
  # The spool lives beside the database, the same rule tai/spool.py applies;
  # a relative TAI_DB is the current directory in both.
  [[ "$TAI_DB" == */* ]] && _tai_spool_file="${TAI_DB%/*}/spool.log" || _tai_spool_file="spool.log"
fi
: ${_TAI_SPOOL_FILE:=${_tai_spool_file:-${XDG_DATA_HOME:-$HOME/.local/share}/tai/spool.log}}
# The knobs are read from their own public names here, not defaulted into the
# underscore spellings: `: ${_TAI_SPOOL_MAX:=8}` would answer "is the shell
# variable set" — which it never is — and a TAI_SPOOL_MAX=1 in the
# environment would be ignored while the flush waited for eight records
# forever. An integer type, so a garbage value degrades to zero (flush every
# command) rather than to a math error in a prompt hook.
typeset -gi _TAI_SPOOL_MAX=${TAI_SPOOL_MAX:-8}
typeset -gi _TAI_SPOOL_SECONDS=${TAI_SPOOL_SECONDS:-3}
typeset -gi _TAI_SPOOL_N=0
# The timestamp a record carries and the idle clock the flush decision reads
# are both this builtin's, because a fork for the time would cost more than
# the record it is attached to. A zsh without the module records ts=0, and the
# flush stamps the drain time instead — coarser recency, nothing lost.
zmodload zsh/datetime 2>/dev/null
: ${_TAI_FLUSH_AT:=${EPOCHSECONDS:-0}}
unset _tai_spool_file

_TAI_SUGGESTION=""
_TAI_LAST=""
# Set by preexec, read and cleared by precmd. Initialised here because zsh runs
# the first precmd before any preexec, and `setopt nounset` would otherwise
# report it as a missing parameter on the first prompt of a new shell.
_TAI_CMD=""
# region_highlight style for the ghost text: dim, italic, gray. Override before
# sourcing to taste, e.g. "fg=244" for a lighter gray with no italic.
: ${_TAI_HIGHLIGHT_STYLE:=fg=8,bold}
# The selected entry of the Tab menu, and a directory's name in it. `standout`
# is reverse video on every terminal that has it, which is what a menu selection
# has always looked like. `fg=blue,bold` matches the directory colour the rest
# of a terminal already uses. Override either before sourcing, e.g. "bg=blue,
# fg=white" for a selection that does not invert, or "fg=cyan" for directories
# in a palette without blue.
: ${_TAI_MENU_STYLE:=standout}
: ${_TAI_DIR_STYLE:=fg=blue,bold}
# Rows the menu may take, and so the number of entries it can hold. More than
# this is a shortlist: the way to see more of them is to type more of the word.
: ${_TAI_MENU_ROWS:=10}
# The loose list — the learned lines that mention what was typed, shown when
# nothing continues the line — is a glance, not a menu: one learned line per
# row, at most three rows, and each row only so wide. A learned line can be a
# whole curl request, and a screen of those is worse than the three that fit.
: ${_TAI_MENU_LOOSE_ROWS:=3}
: ${_TAI_MENU_LOOSE_CELL:=50}
# Rows of files a path argument is answered from, and how much of each place is
# read. The same four numbers live in tai/fresh.py for the one-shot path, and one
# variable — TAI_FILE_ROOTS, colon separated — adds roots to both, because the
# shell and `tai suggest` have to look in the same places to answer the same
# question. Every one of them is a shortlist rather than a limit: typing more of
# the word narrows all of them at once.
: ${_TAI_FILE_PER_ROOT:=8}    # files read from one root
: ${_TAI_FILE_ROOTS_MAX:=8}   # roots read in all
: ${_TAI_FILE_TOP:=8}         # candidates an answer holds, newest first

_tai_repo() { git rev-parse --show-toplevel 2>/dev/null | rev | cut -d/ -f1 | rev 2>/dev/null; }
_tai_branch() { git rev-parse --abbrev-ref HEAD 2>/dev/null; }
