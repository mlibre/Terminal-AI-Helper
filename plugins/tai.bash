# tai.bash — embedded native autocomplete. No daemon, no socket, no Python
# process on the keystroke path. Uses bash associative arrays loaded from the
# generated ~/.local/share/tai/bash-index.bash snapshot.
#
# Install: tai refresh && source this file
# Refresh after learning more history: tai refresh
#
# This file is the entry point and nothing else. The plugin is five parts in
# plugins/bash/, sourced here in dependency order:
#
#   index.bash   the index file, the state, every limit. The only part that does
#                anything at source time.
#   lookup.bash  a line to candidates, and the ranking.
#   files.bash   path arguments, answered by the filesystem.
#   units.bash   systemctl unit names, answered from a cached list.
#   keys.bash    what the keys do, and the recording hook on every prompt.
#
# Split from one 570-line file: the parts have different jobs, and a change to
# the ranking should not have to read the key bindings to know whether it
# touched them.
#
# BASH_SOURCE[0] is this file's own path, which is what makes the parts
# findable from a checkout, a symlink into ~/.local/share, or a path the
# installer wrote into ~/.bashrc — none of which is this directory.
_TAI_PLUGIN_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_TAI_PART_MISSING=""
for _tai_part in index lookup files units keys; do
  if [[ -r "$_TAI_PLUGIN_DIR/bash/$_tai_part.bash" ]]; then
    source "$_TAI_PLUGIN_DIR/bash/$_tai_part.bash"
  else
    _TAI_PART_MISSING="bash/$_tai_part.bash"
    break
  fi
done
# Every binding and the PROMPT_COMMAND hook live in the last part, so a load
# that stopped early has attached nothing: the prompt looks like a shell without
# the plugin, not like half of one.
[[ -n "$_TAI_PART_MISSING" ]] && echo "tai: cannot read plugins/${_TAI_PART_MISSING} — the install is incomplete" >&2

# The release this shell sourced, recorded where `tai version` reads it. A shell
# that started before an update keeps the old code in memory for its whole life:
# the rc file can already name the new tree and the running session still runs
# the old one, and nothing outside the shell can see the difference. One builtin
# read and one builtin write per shell start, both silent on failure: the file
# is a diagnostic, not a dependency. Inside an if, not a bare statement — a data
# directory that does not exist yet fails the redirect, and a shell running
# `set -e` must not fall over a diagnostic.
_tai_self_version=""
[[ -r "$_TAI_PLUGIN_DIR/../VERSION" ]] && _tai_self_version="$(<"$_TAI_PLUGIN_DIR/../VERSION")"
if [[ -n "$_tai_self_version" ]] && { echo "bash $_tai_self_version" >| "${XDG_DATA_HOME:-$HOME/.local/share}/tai/plugin-version"; } 2>/dev/null; then
  :
fi
unset _tai_part _TAI_PART_MISSING _TAI_PLUGIN_DIR _tai_self_version