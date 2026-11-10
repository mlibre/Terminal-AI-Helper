#!/usr/bin/env bash
# tai — run every check, in the order that fails fastest, and stop at the first
# failure so the output you read is the one that matters.
#
#     ./tests/test.sh              all suites
#     ./tests/test.sh --fast       skip the pty suite, which is nearly all the time
#
# The program is Rust, and so is the test suite: the engine, the store, the
# typo tolerance, the CLI conversation, the dashboard and the golden index are
# `cargo test`; the shell plugins are driven through a real pty by the Rust
# suites under tests/, each sharing the harness in tests/support/. There is no
# Python anywhere in this tree. Each suite fails loudly.
set -uo pipefail
cd "$(dirname "$0")/.."

fast=0
for arg in "$@"; do
  case "$arg" in
    --fast)    fast=1 ;;
    -h|--help) sed -n '2,11p' "$0" | cut -c3-; exit 0 ;;
    *) printf 'unknown option: %s\n' "$arg" >&2; exit 2 ;;
  esac
done

fail=0
# The toolchain is where rustup puts it when the shell has not been handed it.
command -v cargo >/dev/null 2>&1 || export PATH="$HOME/.cargo/bin:$PATH"
step() {
  local name="$1"; shift
  printf '\n\033%s\033\n' "$name"
  if "$@"; then
    return 0
  fi
  fail=1
  printf '\033%s failed\033\n' "$name"
  return 1
}

# Syntax first: a typo in a shell plugin is caught here in milliseconds, and every
# later suite assumes both plugins parse. The zsh half is conditional because the
# pty suite treats a missing shell as something to skip, and dying here on a
# bash-only machine would contradict that.
syntax_check() {
  bash -n plugins/tai.bash &&
  for f in plugins/bash/*.bash; do bash -n "$f" || return 1; done &&
  # The bundled zsh in tests/bin is the floor: a machine with no system zsh
  # still parses the plugins, exactly as the pty suite still runs.
  local zsh_bin; zsh_bin="$(command -v zsh || { [[ -x tests/bin/zsh ]] && echo tests/bin/zsh; })"
  if [[ -n "$zsh_bin" ]]; then
    "$zsh_bin" -n plugins/tai.zsh &&
    for f in plugins/zsh/*.zsh; do "$zsh_bin" -n "$f" || return 1; done &&
    echo "  ok   the shell plugins parse"
  else
    echo "  ok   the shell plugins parse (zsh absent, its checks are skipped)"
  fi
}
step "syntax"        syntax_check

# The compiled program: store, engine, typo, index goldens, CLI conversation,
# dashboard. This also builds the release binary the later suites drive.
step "cargo"         cargo test --release \
                          --bin tai --test cli_conversation \
                          --test golden_index --test web_server

step "install"       cargo test --release --test install_smoke
step "npm"           cargo test --release --test pkg_npm
step "deb"           cargo test --release --test pkg_deb
if (( ! fast )); then
  # One entry point per theme, all sharing the harness in tests/support/.
  # Order is cheapest-first: plain lookups, then the case rules and the menu,
  # then what is drawn, then the ^R search, then the filesystem, and last the
  # index lifecycle, which rebuilds fixtures.
  for part in lookups case menu draw isearch storm files wide index units; do
    step "plugins/$part" cargo test --release --test "plugin_$part"
  done
fi

printf '\n'
if (( fail )); then
  printf '\033FAILED\033 — fix and re-run; ./tests/test.sh --fast skips the pty suite.\n'
  exit 1
fi
printf '\033all suites passed\033\n'
