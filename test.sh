#!/usr/bin/env bash
# tai — run every check, in the order that fails fastest, and stop at the first
# failure so the output you read is the one that matters.
#
#     ./test.sh              all suites
#     ./test.sh --fast       skip the pty suite, which is nearly all the time
#
# Every suite is a script of assertions that prints what it found, and each has a
# `main()` nothing calls except this. The three smoke scripts and the six pty
# entry points share their setup through tests/smoke_env.py and tests/plugin_*.py,
# so a fixture or a key name is written once.
#
# Nearly all of the runtime is the pty suite driving real bash and zsh. It used
# to be much worse, and for the same reason twice: `time.sleep` where a wait was
# owed, and a `close()` that never closed anything. See the module docstring in
# tests/plugin_pty.py.
set -uo pipefail
cd "$(dirname "$0")"

fast=0
for arg in "$@"; do
  case "$arg" in
    --fast)    fast=1 ;;
    -h|--help) sed -n '2,10p' "$0" | cut -c3-; exit 0 ;;
    *) printf 'unknown option: %s\n' "$arg" >&2; exit 2 ;;
  esac
done

fail=0
step() {
  local name="$1"; shift
  printf '\n\033[1m%s\033[0m\n' "$name"
  if "$@"; then
    return 0
  fi
  fail=1
  printf '\033[31m%s failed\033[0m\n' "$name"
  return 1
}

# Syntax first: a typo in a shell plugin is caught here in milliseconds, and every
# later suite assumes both plugins parse. The zsh half is conditional because the
# pty suite treats a missing shell as something to skip, and dying here on a
# bash-only machine would contradict that.
syntax_check() {
  python3 -m py_compile tai/*.py tests/*.py &&
  bash -n plugins/tai.bash &&
  for f in plugins/bash/*.bash; do bash -n "$f" || return 1; done &&
  if command -v zsh >/dev/null; then
    zsh -n plugins/tai.zsh &&
    for f in plugins/zsh/*.zsh; do zsh -n "$f" || return 1; done &&
    echo "  ok   python, zsh and bash parse"
  else
    echo "  ok   python and bash parse (zsh absent, its checks are skipped)"
  fi
}
step "syntax"        syntax_check

step "smoke"         python3 tests/test_smoke.py
step "smoke cli"     python3 tests/test_smoke_cli.py
step "smoke install" python3 tests/test_smoke_install.py
step "jev"           python3 tests/test_jev.py
step "web"           python3 tests/test_web.py
if (( ! fast )); then
  # One entry point per theme, all sharing plugin_env/plugin_screen/plugin_pty.
  # Order is cheapest-first: plain lookups, then the menu, then what is drawn,
  # then the filesystem, and last the index lifecycle, which rebuilds fixtures.
  for part in lookups menu draw files wide index units; do
    case "$part" in
      lookups) entry=tests/test_plugins.py ;;
      *)       entry="tests/test_plugins_$part.py" ;;
    esac
    step "plugins/$part" python3 "$entry"
  done
fi

printf '\n'
if (( fail )); then
  printf '\033[31mFAILED\033[0m — fix and re-run; ./test.sh --fast skips the pty suite.\n'
  exit 1
fi
printf '\033[32mall suites passed\033[0m\n'
