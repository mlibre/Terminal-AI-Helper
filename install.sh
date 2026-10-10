#!/usr/bin/env bash
# tai installer — Linux, stdlib python only, <30 seconds.
#
# Three doors, one install: from a checkout (./install.sh), the one line the
# readme leads with —
#   curl -fsSL https://raw.githubusercontent.com/mlibre/Terminal-AI-Helper/main/install.sh | bash
# — where curl's output reaches bash on stdin and the repository has to be
# fetched before anything below can run, and npm's postinstall hook, which
# runs this script from the package npm just unpacked (bin/npm-postinstall
# guards it). The piped door clones to ~/.tai
# (TAI_DIR moves it; TAI_REMOTE points it at a fork) and then executes the
# cloned copy of this very script, so every door runs the same steps from a
# real checkout and there is exactly one installer to read.
set -euo pipefail

if [[ -f "${BASH_SOURCE[0]:-}" ]]; then
  REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
else
  TAI_REMOTE="${TAI_REMOTE:-https://github.com/mlibre/Terminal-AI-Helper}"
  TAI_DIR="${TAI_DIR:-$HOME/.tai}"
  command -v git >/dev/null 2>&1 || {
    echo "tai: the one-line install needs git — install it, or clone the repo and run ./install.sh" >&2
    exit 1
  }
  if [ -d "$TAI_DIR/.git" ]; then
    # The one-liner is also the update path: an existing checkout is pulled
    # forward and reinstalled, never cloned over. A pull that cannot happen
    # (diverged, offline) fails here, loudly, rather than installing old code.
    echo "→ updating the checkout at $TAI_DIR…"
    git -C "$TAI_DIR" pull --ff-only --quiet
  elif [ -e "$TAI_DIR" ]; then
    echo "tai: $TAI_DIR exists and is not a tai checkout — remove it or set TAI_DIR" >&2
    exit 1
  else
    echo "→ cloning tai to $TAI_DIR…"
    git clone --depth 1 "$TAI_REMOTE" "$TAI_DIR"
  fi
  exec bash "$TAI_DIR/install.sh"
fi
BIN_DIR="${BIN_DIR:-$HOME/.local/bin}"
printf -v TAI_CLI_PATH '%q' "$REPO_DIR/tai/cli.py"
mkdir -p "$BIN_DIR" "${XDG_DATA_HOME:-$HOME/.local/share}/tai"

cat > "$BIN_DIR/tai" <<EOF
#!/usr/bin/env bash
# The checkout this names is the one the installer ran from, and it is the only
# copy: moving or deleting the folder leaves this wrapper pointing at nothing.
# What python prints for that ("can't open file ...: [Errno 2] No such file or
# directory") is not something a person can act on, so say what happened instead.
# One test on the way in; no process on any path that works.
if [ ! -r $TAI_CLI_PATH ]; then
  echo "tai: $TAI_CLI_PATH is gone — reinstall from the checkout, or point this line at it" >&2
  exit 1
fi
exec python3 -S -E $TAI_CLI_PATH "\$@"
EOF
chmod +x "$BIN_DIR/tai"

echo "→ learning from your history, building the index…"
# `|| true` because a failed index must not abort the install: the wrapper and
# the rc lines below are what the user needs, and tai keeps working on the
# previous index. `tai refresh` says so itself when it cannot rebuild.
python3 "$REPO_DIR/tai/cli.py" refresh --quiet || true

# Byte-compile the zsh plugins the same way the index builder compiles the
# index: a `.zwc` beside a script halves zsh's parse cost at every startup,
# and zsh falls back to the text file whenever the timestamps disagree, so a
# stale or half-written compilation can never shadow new code. Best-effort
# twice over: no zsh, or a read-only checkout, simply skips it.
if command -v zsh >/dev/null 2>&1 && [ -w "$REPO_DIR/plugins/zsh" ]; then
  for _f in "$REPO_DIR"/plugins/zsh/*.zsh; do
    zsh -fc "zcompile $_f" >/dev/null 2>&1 || true
  done
fi

# Whether zsh-autosuggestions is actually loaded in the user's zsh, asked of zsh
# rather than guessed from the filesystem: on Manjaro the plugin is sourced by
# /usr/share/zsh/manjaro-zsh-prompt, not by anything in ~/.zshrc, so there is no
# line to look for and a file that exists is not proof it is in use.
_tai_autosuggest_loaded() {
  command -v zsh >/dev/null 2>&1 || return 1
  ZDOTDIR="${ZDOTDIR:-$HOME}" zsh -ic \
    '(( $+functions[_zsh_autosuggest_start] )) && print -r -- yes' 2>/dev/null |
    grep -qx yes
}

# Pause zsh-autosuggestions for the user who wants tai's hints to be the visible
# ones. It draws into the same POSTDISPLAY slot tai uses and fetches
# asynchronously, so both hints work and both are taken by Tab and the arrow —
# but only one can be on screen, and theirs arrives last.
#
# Removing its precmd hook is the supported way to do it, and it only works from
# ~/.zshrc *before the first prompt*: the hook is what installs its widget
# wrappers, and once they are installed, taking the hook away leaves them in
# place. Measured both ways — with the line in the rc file the `forward-char`
# widget is tai's; with the line run at a prompt it is still
# `_zsh_autosuggest_bound_1_forward-char` and nothing changed.
_tai_pause_autosuggest() {
  local rc="$1" add_it=0
  _tai_autosuggest_loaded && [[ "${TAI_KEEP_AUTOSUGGEST:-0}" != "1" ]] && add_it=1
  # An existing block goes either way, including when the user has just asked to
  # keep the plugin: an opt-out that only stopped the *next* install from adding a
  # block would leave the previous one in place, which is worse than not having the
  # option at all.
  grep -q "_zsh_autosuggest_start" "$rc" 2>/dev/null || (( add_it )) || return 1
  mkdir -p "$(dirname "$rc")"
  python3 - "$rc" "$add_it" <<'PY'
import os
import sys
from pathlib import Path

path = Path(sys.argv[1])
add = sys.argv[2] == "1"
text = path.read_text(encoding="utf-8", errors="surrogateescape") if path.exists() else ""
lines = text.splitlines(keepends=True)
kept = []
i = 0
while i < len(lines):
    if lines[i].startswith("# tai: zsh-autosuggestions paused"):
        i += 1
        while i < len(lines) and ("_zsh_autosuggest_start" in lines[i]
                                  or lines[i].startswith("# ")):
            i += 1
        continue
    kept.append(lines[i])
    i += 1
if add:
    if kept and not kept[-1].endswith(("\n", "\r")):
        kept[-1] += "\n"
    kept.append("# tai: zsh-autosuggestions paused (installed by ./install.sh)\n")
    kept.append("# tai draws its hint into the same POSTDISPLAY, and this plugin fetches\n")
    kept.append("# asynchronously, so tai's own hints never reach the screen. Both work.\n")
    kept.append("# Removed by `tai uninstall`, and by TAI_KEEP_AUTOSUGGEST=1.\n")
    kept.append("add-zsh-hook -d precmd _zsh_autosuggest_start 2>/dev/null\n")
tmp = path.with_name(path.name + ".tai-new")
tmp.write_text("".join(kept), encoding="utf-8", errors="surrogateescape")
os.replace(tmp, path)
PY
}

_tai_enable() {
  local rc="$1" plugin="$2" source_line="$3" plugin_name
  plugin_name="${plugin##*/}"
  mkdir -p "$(dirname "$rc")"
  python3 - "$rc" "$source_line" "$plugin_name" <<'PY'
import os
import sys
from pathlib import Path

rc, source_line, plugin_name = sys.argv[1:]
path = Path(rc)
# surrogateescape, not errors="replace": a dotfile in a legacy encoding has bytes
# that are not valid UTF-8, and replacing them on read would silently rewrite the
# user's file with U+FFFD. This round-trips whatever bytes are there.
text = path.read_text(encoding="utf-8", errors="surrogateescape") if path.exists() else ""
marker = "# tai autocomplete (installed by ./install.sh)"
lines = text.splitlines(keepends=True)
cleaned = []
i = 0
while i < len(lines):
    line = lines[i]
    if line.strip() == marker:
        # Replace the managed two-line block when the checkout path changes.
        i += 1
        if i < len(lines) and plugin_name in lines[i] and "source" in lines[i]:
            i += 1
        continue
    cleaned.append(line)
    i += 1

# A manually configured integration is already enabled; do not add a second
# copy when the installer is rerun.
has_source = any(
    plugin_name in line and "source" in line and not line.lstrip().startswith("#")
    for line in cleaned
)
if not has_source:
    if cleaned and not cleaned[-1].endswith(("\n", "\r")):
        cleaned[-1] += "\n"
    cleaned.append(marker + "\n")
    cleaned.append(source_line + "\n")

# Replaced atomically, the same rule the index builder follows. This file is the
# user's shell: an interrupt between truncate and write would otherwise leave a
# half-written .zshrc and a shell that cannot start.
tmp = path.with_name(path.name + ".tai-new")
tmp.write_text("".join(cleaned), encoding="utf-8", errors="surrogateescape")
os.replace(tmp, path)
PY
}

# The two rc files are independent. One failing must not abort the install after
# the other was already rewritten, so each shell reports its own outcome, and a
# failure carries the line to add: naming the file and stopping there leaves the
# reader with nothing to do about it. The line is built once here so the report
# and the fix can never disagree.
_TAI_ENABLED=0
_tai_enable_rc() {
  local shell="$1" rc="$2" line
  printf -v line 'source %q' "$3"
  if _tai_enable "$rc" "$3" "$line"; then
    echo "✓ $shell installed"
    _TAI_ENABLED=$((_TAI_ENABLED + 1))
  else
    echo "! $shell: could not update $rc — add: $line" >&2
  fi
}
_tai_enable_rc zsh "$HOME/.zshrc" "$REPO_DIR/plugins/tai.zsh"
_tai_enable_rc bash "$HOME/.bashrc" "$REPO_DIR/plugins/tai.bash"

# One line, only when there was something to pause and only when it worked. The
# installer's report is a list of claims that are true, and a line that names a
# plugin it did not touch is noise.
_tai_pause_autosuggest "$HOME/.zshrc" &&
  echo "✓ zsh-autosuggestions paused (tai uninstall restores it)"

# The command below is a promise: after it, the shell is running tai. With
# neither rc file written it is not a promise anyone can keep, and a script
# running this installer needs to know the install did not land.
(( _TAI_ENABLED )) || exit 1

# The welcome. The installer's report so far is bookkeeping — which rc file,
# which plugin — and this is the part a new user actually reads: what tai is,
# the three things worth knowing, the keys that matter, and one thing to try.
# It stays plain text so it reads the same everywhere the installer runs,
# piped terminal included.
_tai_welcome() {
  cat <<'EOF'
   _
  | |_  __ _ ___     the terminal that knows your next command
  | ' \/ _` (_-<
  |_||_\__,_/__/

  1. Type a few letters — tai finishes the command in grey. → takes it.
  2. Tab opens a menu of what fits. Enter picks; a second Enter runs.
  3. It learns from your history — offline, private, no account.

  More keys: Ctrl-F takes the whole hint · Ctrl-Space opens the list
             Down peeks at what usually follows

  Try it now: type "cd " and watch the grey.
EOF
}
_tai_welcome

# The installer is a child process, so it cannot reconfigure the shell that
# launched it. It can, however, say which shell has to be replaced — an
# already-loaded plugin cannot pick up an edited file, because the old function
# bodies stay in memory until the file is sourced again. The line is a command to
# copy, so it is the last thing on the screen.
_parent_shell="$(basename "$(ps -o comm= -p "$PPID" 2>/dev/null || echo unknown)")"
case "$_parent_shell" in
  zsh)  _reload=zsh ;;
  bash) _reload=bash ;;
  *)    _reload='$SHELL' ;;
esac

echo "→ exec $_reload"

