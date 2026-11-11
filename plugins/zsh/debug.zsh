# The realtime trail. TAI_DEBUG=1 (or the `tai-debug` function, below) turns it
# on; every part of the plugin then appends one timestamped line per thing it
# does to one file, and the binary does the same from its side — same file,
# same line shape, interleaved in the order it happened.
#
# The file, not the terminal, is the point. The bug that asked for this kills
# the shell: whatever tai was doing when zsh died, the answer has to be
# already on the disk when the crash wipes the process — so every write here
# is one builtin `print` in append mode. No buffers, no child, no flush to
# forget: the line lands the moment the line is handed over, and a shell that
# dies on the next keystroke leaves the last line it was able to write behind.
#
# Everything is read back with `tai debug`, which prints the file whole beside
# the versions, the paths and the knobs that shaped the run.
#
# The default file is under the cache directory — throwaway by nature, like
# any log — and TAI_DEBUG_FILE moves it, which is also how the tests keep one
# run's trail away from another's. It is capped: a trail that wrote a line per
# keystroke forever would grow a file the size of the day's typing, so a file
# past the cap is moved aside once, at the moment debug is armed, and the trail
# starts fresh.

typeset -g _TAI_DEBUG_FILE="${TAI_DEBUG_FILE:-${XDG_CACHE_HOME:-$HOME/.cache}/tai/debug.log}"
# One number decides everything: 0 silences every call below, 1 lets them
# write. Read once from the environment here; `tai-debug` below flips it
# mid-session without a re-source.
typeset -gi _TAI_DEBUG_ON=0
[[ "${TAI_DEBUG:-0}" == "1" ]] && _TAI_DEBUG_ON=1
# The heartbeat and the ZERR trap are armed once per shell, whatever path
# armed them — the load-time check or the first `tai-debug`.
typeset -gi _TAI_DEBUG_BEAT=0
typeset -gi _TAI_DEBUG_TRAP=0
# The clock. The plugin's own parts load after this one, and two of them
# zmodload this module anyway; loading it here means every `_tai_dbg` call in
# every part can trust it, whoever loads first. A zsh without the module
# timestamps with 0 — coarser, not broken.
(( $+EPOCHREALTIME )) || zmodload zsh/datetime 2>/dev/null

# One line, one builtin, append mode, stderr silent. The append is the whole
# realtime guarantee: the kernel writes the bytes as `print` hands them over,
# and nothing of tai's sits between the event and the disk. `${*:-}` and the
# quoting keep a buffer with newlines in it one log line, and `(q)` at the
# call sites does the rest; here, whatever arrives is written as given.
_tai_dbg() {
  (( _TAI_DEBUG_ON )) || return 0
  { print -r -- "${EPOCHREALTIME:-0} tai zsh pid=$$ ${1}: ${*:2}" >> "$_TAI_DEBUG_FILE" } 2>/dev/null
  return 0
}

# A log past this size starts over. Checked when debug is armed — at load, or
# by `tai-debug` — not per line: the cap is a courtesy for the reader of the
# dump, and `tai debug` is where the size is reported either way. The size
# comes from zsh's own stat module, so arming is still a handful of builtins
# unless a rotation is actually due; the move is one `mv` fork, once.
typeset -gi _TAI_DEBUG_MAX_BYTES=$(( 8 * 1024 * 1024 ))
_tai_debug_rotate() {
  zmodload zsh/stat 2>/dev/null
  (( $+functions[zstat] )) || return 0
  local -a st
  zstat -A st +size -- "$_TAI_DEBUG_FILE" 2>/dev/null || return 0
  (( st[1] > _TAI_DEBUG_MAX_BYTES )) || return 0
  { mv -f "$_TAI_DEBUG_FILE" "$_TAI_DEBUG_FILE.1" } 2>/dev/null
  return 0
}

# The pulse. A background copy of the shell wakes once a second and writes one
# word, for as long as the shell lives, and one last line when it stops. This
# is the line that tells a crash from a silence: a trail whose events stop
# mid-widget with no "alive" after them is a shell that hung; a "shell gone"
# between two keystrokes, without the exit line the exit hook writes, is a
# shell that died — and the timestamps on either side say exactly when. The
# double fork keeps it out of the job table, the same rule the spool flush and
# the unit pre-load follow, so a debug session leaves nothing behind but the
# file.
_tai_debug_arm() {
  _TAI_DEBUG_ON=1
  _tai_debug_rotate
  { mkdir -p "${_TAI_DEBUG_FILE:h}" } 2>/dev/null
  if (( ! _TAI_DEBUG_BEAT )); then
    _TAI_DEBUG_BEAT=1
    ( ( beat_parent=$$; beat_file="$_TAI_DEBUG_FILE"
        # The pulse outlives the shell — that is its whole job. A zsh that
        # exits hands SIGHUP to its jobs' process groups, and this one is in
        # the outer subshell's group, which the shell still counts as a job:
        # measured, the pulse died with the shell it was watching and the
        # one line that says "died" was never written. Ignoring HUP is the
        # whole fix; the loop still ends on its own the moment `kill -0`
        # can no longer see the shell.
        trap '' HUP
        integer beat_n=0
        while kill -0 "$beat_parent" 2>/dev/null; do
          { print -r -- "${EPOCHREALTIME:-0} tai zsh pid=$beat_parent alive" >> "$beat_file" } 2>/dev/null
          # One hour of pulses, then home. The loop's only other exit is the
          # shell's death; this bound is the answer to a pid the kernel
          # handed to some other process — without it, a pulse could watch a
          # stranger forever.
          (( ++beat_n > 3600 )) && { print -r -- "${EPOCHREALTIME:-0} tai zsh pid=$beat_parent pulse ended" >> "$beat_file" 2>/dev/null; break }
          sleep 1
        done
        { print -r -- "${EPOCHREALTIME:-0} tai zsh pid=$beat_parent shell gone" >> "$beat_file" } 2>/dev/null
      ) & )
  fi
  if (( ! _TAI_DEBUG_TRAP )); then
    _TAI_DEBUG_TRAP=1
    # Every command status the shell reports while debug is on, with the
    # function stack it happened in. zle suppresses this inside widget
    # execution, which is why the widgets log their own breadcrumbs — this
    # covers the other half, the load and the hooks outside the editor.
    trap '_tai_dbg zerr "status=$? funcs=${funcstack[*]:-}"' ZERR
  fi
}

if (( _TAI_DEBUG_ON )); then
  _tai_debug_arm
fi

# Turn it on from inside the shell that is already running — the session with
# the bug in it usually did not start with TAI_DEBUG set, and the bug does not
# ask before it crashes. Prints the file it is writing to, so the next
# `tai debug` is one copy-paste away.
tai-debug() {
  _tai_debug_arm
  export TAI_DEBUG=1
  _tai_dbg state "debug armed mid-session by tai-debug"
  print -r -- "tai: debug on — every keystroke is logged to $_TAI_DEBUG_FILE"
  print -r -- "     reproduce the problem, then run: tai debug"
}

tai-debug-off() {
  _TAI_DEBUG_ON=0
  unset TAI_DEBUG
  _tai_dbg state "debug disarmed by tai-debug-off" 2>/dev/null
  print -r -- "tai: debug off — the trail is at $_TAI_DEBUG_FILE"
}
