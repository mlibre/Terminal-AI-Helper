# tai.zsh — embedded native autocomplete. No daemon, no socket, no Python
# process on the keystroke path. Suggestions are ranked by zsh associative
# arrays loaded from ~/.local/share/tai/zsh-index.zsh.
#
# Two things happen as you type. Ghost text appears after the cursor, in grey,
# and Tab opens a menu of everything that completes the word under the cursor.
# They are answers to different questions — "what did I mean to type" and "what
# could this be" — so they are drawn at different times and taken by different
# keys: the right arrow takes the ghost, Enter takes the selected entry and
# leaves the line there to be run by the Enter after it.
#
# Install: tai refresh && source this file
# Refresh after learning more history: tai refresh
#
# This file is the entry point and nothing else. The plugin is six parts in
# plugins/zsh/, sourced here in dependency order:
#
#   index.zsh    the index file, the state, every limit. The only part that does
#                anything at source time.
#   lookup.zsh   a line to candidates, and the one answer the ghost text shows.
#   files.zsh    path arguments, answered by the filesystem.
#   units.zsh    systemctl unit names, answered from a cached list.
#   menu.zsh     the Tab menu.
#   widgets.zsh  the redraw, the keys, and what is bound to them.
#
# Split because one 1275-line file is not something anyone can hold in their head,
# and because the parts have genuinely different jobs: only `widgets.zsh` knows
# about ZLE, only `files.zsh` knows about paths, and a change to the ranking does
# not have to read the menu to know whether it touched it.
#
# `${0:A:h}` is this file's own directory, which is what makes the parts findable
# from a checkout, a symlink into ~/.local/share, or a path the installer wrote
# into ~/.zshrc — none of which is this directory.
_TAI_PLUGIN_DIR="${0:A:h}"
_TAI_PART_MISSING=""
# The plugin is a guest in the user's rc file, and `set -e` is theirs. A statement
# of ours that returns non-zero would take their shell down at startup, on the
# last line of a file they never edited — and not one of ours: it was zsh's own
# `add-zle-hook-widget`, whose internal `(( del ))` returns false on a shell that
# has no such hook yet, which ended the load *and* the session. Suspend errexit
# for the load and hand it back exactly as it was found; restoring it is the last
# thing this file does, so nothing of ours can run under it.
#
# bash needs none of this and gets none of it: every top-level statement in
# plugins/bash/ was measured to succeed in a shell with `set -e` on, so a guard
# there would be a claim nothing checks. The zsh guard is in this file because
# this file is where sourcing happens — once, for all five parts.
if [[ -o errexit ]]; then
  _tai_errexit_was=1
  set +e
fi
for _tai_part in index lookup files units menu widgets; do
  if [[ -r "$_TAI_PLUGIN_DIR/zsh/$_tai_part.zsh" ]]; then
    source "$_TAI_PLUGIN_DIR/zsh/$_tai_part.zsh"
  else
    _TAI_PART_MISSING="zsh/$_tai_part.zsh"
    break
  fi
done
# Every `zle -N` and every `bindkey` is in the last part, so a load that stopped
# here has bound nothing at all: the prompt looks exactly like a shell without the
# plugin, rather than half of one. `break` and not `return`, because a `return`
# out of a sourced file is a return from the caller's line in some contexts, and
# `exit` would take the user's shell down with it.
[[ -n "$_TAI_PART_MISSING" ]] && print -u2 \
  "tai: cannot read plugins/${_TAI_PART_MISSING} — the install is incomplete"
# Errexit back on before anything else, and only if the user had it: a guard that
# quietly leaves `set -e` off would be a worse bug than the one it prevents.
[[ -n "$_tai_errexit_was" ]] && set -e
unset _tai_part _TAI_PART_MISSING _TAI_PLUGIN_DIR _tai_errexit_was