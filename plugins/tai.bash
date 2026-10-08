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
unset _tai_part _TAI_PART_MISSING _TAI_PLUGIN_DIR