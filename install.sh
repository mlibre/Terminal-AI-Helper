#!/usr/bin/env bash
# tai installer — one Rust binary, two shell plugins, <30 seconds.
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
#
# The program is a compiled binary now, so this script has one extra job the
# Python install never had: put a `tai` executable where the wrapper can run
# it. Three sources, first that exists:
#   1. a binary already built beside the checkout (target/release/tai, or the
#      bin/tai an npm postinstall staged),
#   2. a release tarball from GitHub, for the machine with no Rust toolchain,
#   3. cargo build, for the machine with one.
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
    # The one-liner is also the update path: an existing checkout is refetched
    # and reinstalled, never cloned over. The checkout is this install's own —
    # nobody develops in it — so the update is one commit deep every time:
    # fetch --depth 1, reset to what origin named, prune what is left behind.
    echo "→ updating the checkout at $TAI_DIR…"
    _branch="$(git -C "$TAI_DIR" rev-parse --abbrev-ref HEAD)"
    git -C "$TAI_DIR" fetch --depth 1 --quiet origin "$_branch"
    git -C "$TAI_DIR" reset --hard --quiet FETCH_HEAD
    git -C "$TAI_DIR" gc --prune=now --quiet 2>/dev/null || true
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
mkdir -p "$BIN_DIR" "${XDG_DATA_HOME:-$HOME/.local/share}/tai"

# -- the binary ---------------------------------------------------------------

_tai_binary() {
  # 1. Already built or already staged. -f as well as -x: a leftover
  #    tai-linux-<arch> directory from an extraction is traversable and would
  #    otherwise pass a bare -x test.
  for cand in "$REPO_DIR/target/release/tai" "$REPO_DIR/bin/tai"; do
    if [ -f "$cand" ] && [ -x "$cand" ]; then
      echo "$cand"
      return 0
    fi
  done
  # 2. A release tarball, keyed to this machine's architecture.
  local arch tarball url
  arch="$(uname -m)"
  case "$arch" in
    x86_64) arch="x86_64" ;;
    aarch64 | arm64) arch="aarch64" ;;
    *) arch="" ;;
  esac
  if [ -n "$arch" ] && command -v tar >/dev/null 2>&1; then
    url="${TAI_RELEASE_URL:-https://github.com/mlibre/Terminal-AI-Helper/releases/latest/download/tai-linux-${arch}.tar.gz}"
    if command -v curl >/dev/null 2>&1 || command -v wget >/dev/null 2>&1; then
      # Progress lines go to stderr: the function's stdout is captured by the
      # caller as TAI_SRC, and an echo on stdout would become the thing the
      # installer tries to copy.
      echo "→ fetching the prebuilt binary for linux-$arch…" >&2
      tarball="$REPO_DIR/bin/tai-linux-${arch}.tar.gz"
      mkdir -p "$REPO_DIR/bin"
      if command -v curl >/dev/null 2>&1; then
        curl -fsSL "$url" -o "$tarball" 2>/dev/null || true
      else
        wget -qO "$tarball" "$url" 2>/dev/null || true
      fi
      # The release tarball unpacks a directory — tai-linux-<arch>/tai — so the
      # executable is one level below where the old test looked for it. Both
      # shapes are accepted, and the nested one is moved into place.
      if [ -s "$tarball" ]; then
        tar -xzf "$tarball" -C "$REPO_DIR/bin" 2>/dev/null || true
        rm -f "$tarball"
        for cand in "$REPO_DIR/bin/tai" "$REPO_DIR/bin/tai-linux-$arch/tai"; do
          [ -f "$cand" ] && [ -x "$cand" ] || continue
          if [ "$cand" != "$REPO_DIR/bin/tai" ]; then
            mv -f "$cand" "$REPO_DIR/bin/tai"
          fi
          echo "$REPO_DIR/bin/tai"; return 0
        done
      fi
      echo "→ no prebuilt binary yet — building from source instead…" >&2
    fi
  fi
  # 3. Build it. A checkout carries the source; a machine with cargo makes
  #    this the same path the developer runs.
  if command -v cargo >/dev/null 2>&1; then
    echo "→ building the binary (cargo, first time takes a minute)…" >&2
    (cd "$REPO_DIR" && cargo build --release --quiet) || {
      echo "tai: cargo build failed — install Rust from https://rustup.rs and retry" >&2
      exit 1
    }
    echo "$REPO_DIR/target/release/tai"
    return 0
  fi
  echo "tai: no prebuilt binary, and no Rust toolchain to build one." >&2
  echo "     install Rust from https://rustup.rs and re-run this installer," >&2
  echo "     or grab a release binary from https://github.com/mlibre/Terminal-AI-Helper/releases" >&2
  exit 1
}

TAI_SRC="$(_tai_binary)"
install -m 755 "$TAI_SRC" "$BIN_DIR/tai"

# One line of state, read by `tai update`: which door put the binary where it
# is. A binary copied out of the tree it came from cannot be asked at runtime,
# so the installer says so — one word, beside the data it describes.
printf '%s\n' "${TAI_INSTALL_METHOD:-git}" \
  > "${XDG_DATA_HOME:-$HOME/.local/share}/tai/install-method"

echo "→ learning from your history, building the index…"
# `|| true` because a failed index must not abort the install: the wrapper and
# the rc lines below are what the user needs, and tai keeps working on the
# previous index. `tai refresh` says so itself when it cannot rebuild.
"$BIN_DIR/tai" refresh --quiet || true

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
  "$BIN_DIR/tai" rc-pause-autosuggest "$rc" "$add_it"
}

_tai_enable() {
  local rc="$1" plugin="$2" source_line="$3" plugin_name
  plugin_name="${plugin##*/}"
  mkdir -p "$(dirname "$rc")"
  "$BIN_DIR/tai" rc-enable "$rc" "$source_line" "$plugin_name" "$plugin"
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
