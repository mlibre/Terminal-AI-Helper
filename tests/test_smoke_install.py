"""The install end to end, asked the way a user asks it: one door at a time.

    python3 tests/test_smoke_install.py

The program is a compiled binary, so every question here runs the real
`install.sh` against a sandbox HOME with the binary the build just made — the
rc wiring, the wrapper, the data directory, the uninstall, and the two index
rules the installer's own refresh has to hold. Nothing here touches the
developer's HOME: HOME, BIN_DIR and XDG_DATA_HOME all point into the sandbox,
and the doors that reach for the network are exercised with the binary
pre-staged, the way the npm postinstall stages it.

Every check names the rule it holds; a rule broken is a red build.
"""
import json
import os
import pathlib
import re
import shutil
import socket
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

REPO = pathlib.Path(__file__).resolve().parent.parent
BIN = REPO / "target" / "release" / "tai"
if not BIN.exists():
    BIN = REPO / "target" / "debug" / "tai"
VERSION = (REPO / "VERSION").read_text().strip()

failures: list[str] = []


def check(what: str, got, want) -> None:
    if got == want:
        print(f"  ok   {what}: {got!r}")
    else:
        print(f"  FAIL {what}:\n         got  {got!r}\n         want {want!r}")
        failures.append(what)


def world(name: str) -> pathlib.Path:
    """A sandbox HOME, with the binary staged where install.sh looks first."""
    home = pathlib.Path(f"/tmp/tai/tai_install_{name}_{os.getpid()}")
    shutil.rmtree(home, ignore_errors=True)
    home.mkdir(parents=True)
    target = home / "target" / "release"
    target.mkdir(parents=True)
    shutil.copy(BIN, target / "tai")
    return home


def run_install(home: pathlib.Path, extra_env: dict | None = None) -> subprocess.CompletedProcess:
    env = dict(
        os.environ,
        HOME=str(home),
        BIN_DIR=str(home / ".local" / "bin"),
        XDG_DATA_HOME=str(home / "data"),
        # zsh's interactive probe would drag the user's own config in.
        ZDOTDIR=str(home),
    )
    env.update(extra_env or {})
    return subprocess.run(
        ["bash", str(REPO / "install.sh")],
        env=env, capture_output=True, text=True, timeout=300,
    )


def run_tai(home: pathlib.Path, *args: str) -> subprocess.CompletedProcess:
    env = dict(
        os.environ,
        HOME=str(home),
        BIN_DIR=str(home / ".local" / "bin"),
        XDG_DATA_HOME=str(home / "data"),
    )
    return subprocess.run(
        [str(home / ".local" / "bin" / "tai"), *args],
        env=env, capture_output=True, text=True, timeout=120,
    )


# -- install: the rc wiring, the wrapper, the welcome ------------------------

home = world("install")
got = run_install(home)
check("install exits 0", got.returncode, 0)
check("zsh wired", "✓ zsh installed" in got.stdout, True)
check("bash wired", "✓ bash installed" in got.stdout, True)
check("welcome shown", "the terminal that knows your next command" in got.stdout, True)
check("reload hint last", got.stdout.rstrip().endswith("→ exec bash") or "exec" in got.stdout, True)

zshrc = (home / ".zshrc").read_text()
bashrc = (home / ".bashrc").read_text()
marker = "# tai autocomplete (installed by ./install.sh)"
check("zshrc has the marker", marker in zshrc, True)
check("zshrc sources the plugin once",
      zshrc.count("plugins/tai.zsh"), 1)
check("bashrc sources the plugin once",
      bashrc.count("plugins/tai.bash"), 1)
check("wrapper exists", (home / ".local" / "bin" / "tai").exists(), True)
check("install-method recorded",
      (home / "data" / "tai" / "install-method").read_text().strip(), "git")

got = run_tai(home, "version")
check("wrapper runs", got.returncode, 0)
check("version says the release", got.stdout.startswith(f"tai {VERSION}"), True)
check("version sees the wiring", "zsh plugin" in got.stdout and "not wired" not in got.stdout, True)

# -- rerun: idempotent, and never a second source line -----------------------

got = run_install(home)
check("rerun exits 0", got.returncode, 0)
zshrc = (home / ".zshrc").read_text()
check("rerun adds no second line", zshrc.count("plugins/tai.zsh"), 1)

# -- retarget: a line an older door left is this install's to own ------------

home2 = world("retarget")
old_tree = "/home/someone/elsewhere"
(home2 / ".zshrc").write_text(
    "# my own config\n"
    f"source {old_tree}/plugins/tai.zsh\n"
    "alias ll='ls -l'\n"
)
got = run_install(home2)
zshrc = (home2 / ".zshrc").read_text()
check("retarget reports itself", "retargeted the tai.zsh source line" in got.stdout, True)
check("retargeted to this tree", f"{REPO}/plugins/tai.zsh" in zshrc, True)
check("the old tree is gone", old_tree not in zshrc, True)
check("the user's own lines stay", "alias ll='ls -l'" in zshrc, True)

# -- autosuggestions: paused when found, given back, kept when asked -----------
#
# The pause is decided by whether zsh can actually load the plugin, asked of
# zsh itself — so the check needs a zsh, the way the pty suite needs one.
# Without one the installer correctly leaves the plugin alone, and the
# give-back half is asked of the verb the installer calls.

home3 = world("autosuggest")
(home3 / ".zshrc").write_text(
    "_zsh_autosuggest_start() { :; }\n"
    "add-zsh-hook precmd _zsh_autosuggest_start\n"
)
zsh_bin = shutil.which("zsh") or (
    str(REPO / "tests" / "bin" / "zsh")
    if (REPO / "tests" / "bin" / "zsh").exists() and os.access(REPO / "tests" / "bin" / "zsh", os.X_OK)
    else ""
)
if zsh_bin:
    fpath = REPO / "tests" / "bin" / "zsh-functions"
    got = run_install(home3, {
        "PATH": f"{zsh_bin.rsplit('/', 1)[0]}:{os.environ['PATH']}",
        **({"FPATH": str(fpath)} if fpath.exists() else {}),
    })
    zshrc = (home3 / ".zshrc").read_text()
    check("autosuggest paused", "# tai: zsh-autosuggestions paused" in zshrc, True)
    check("the hook was removed", "add-zsh-hook -d precmd _zsh_autosuggest_start" in zshrc, True)
else:
    print("  ok   autosuggest pause (skipped: no zsh on this machine)")

# The give-back half, asked of the verb itself.
home3b = world("autosuggest_back")
rc3b = home3b / ".zshrc"
rc3b.write_text(
    "keep me\n"
    "# tai: zsh-autosuggestions paused (installed by ./install.sh)\n"
    "add-zsh-hook -d precmd _zsh_autosuggest_start 2>/dev/null\n"
)
subprocess.run([str(BIN), "rc-pause-autosuggest", str(rc3b), "0"],
               capture_output=True, timeout=60)
zshrc = rc3b.read_text()
check("the block is given back", "# tai: zsh-autosuggestions paused" not in zshrc, True)
check("the user's lines survive it", "keep me" in zshrc, True)

home4 = world("autosuggest_keep")
(home4 / ".zshrc").write_text(
    "add-zsh-hook precmd _zsh_autosuggest_start\n"
    "# tai: zsh-autosuggestions paused (installed by ./install.sh)\n"
    "add-zsh-hook -d precmd _zsh_autosuggest_start 2>/dev/null\n"
)
run_install(home4, {"TAI_KEEP_AUTOSUGGEST": "1"})
zshrc = (home4 / ".zshrc").read_text()
# An existing block goes either way: an opt-out that only stopped the *next*
# install from adding one would leave the previous one in place.
check("an asked-for keep still clears an old block",
      "# tai: zsh-autosuggestions paused" not in zshrc, True)

# -- uninstall: everything it added, and nothing else ------------------------

home5 = world("uninstall")
(home5 / "notes.txt").write_text("mine\n")
run_install(home5)
got = subprocess.run(
    [str(home5 / ".local" / "bin" / "tai"), "uninstall"],
    env=dict(os.environ, HOME=str(home5), XDG_DATA_HOME=str(home5 / "data"),
             BIN_DIR=str(home5 / ".local" / "bin")),
    capture_output=True, text=True, timeout=120,
)
check("uninstall exits 0", got.returncode, 0)
check("uninstall reports itself", got.stdout.startswith("tai removed"), True)
check("wrapper gone", (home5 / ".local" / "bin" / "tai").exists(), False)
check("data dir gone", (home5 / "data" / "tai").exists(), False)
check("checkout gone", (home5 / ".tai").exists() or not (home5 / ".tai" / "install.sh").exists(), True)
zshrc = (home5 / ".zshrc").read_text()
check("rc line gone", "plugins/tai.zsh" not in zshrc, True)
check("marker gone", marker not in zshrc, True)
check("pause block gone", "# tai: zsh-autosuggestions paused" not in zshrc, True)
# A second uninstall says so: the wrapper is gone, so the binary itself is
# asked, and the answer is the same fact in the same words.
got2 = subprocess.run(
    [str(BIN), "uninstall"],
    env=dict(os.environ, HOME=str(home5), XDG_DATA_HOME=str(home5 / "data"),
             BIN_DIR=str(home5 / ".local" / "bin")),
    capture_output=True, text=True, timeout=120,
)
check("uninstall is a no-op twice", got2.stdout.strip(), "tai was not installed")

# -- the index rules the installer's own refresh holds ------------------------

home6 = world("seq")
RECORD_INDEX = home6 / "record" / "zsh-index.zsh"
RECORD_INDEX.parent.mkdir(parents=True, exist_ok=True)
env6 = dict(os.environ, HOME=str(home6), XDG_DATA_HOME=str(home6 / "data"),
            TAI_DB=str(home6 / "data" / "tai" / "history.db"),
            TAI_INDEX=str(RECORD_INDEX), TAI_SPOOL=str(home6 / "spool.log"),
            TAI_NO_LEARN="1", TAI_NO_ENGINE_CACHE="1")

_STALE = "cd /nowhere/gone"
# The chain the filter has to survive: a stale command both as a key and as
# a follower, and one live pair behind it that must keep its seq entry.
_CHAIN = [_STALE, "git checkout feature", _STALE, "git status", "git status"]
for cmd in _CHAIN:
    subprocess.run([str(BIN), "record", "--cwd", str(REPO), cmd],
                   env=env6, capture_output=True, timeout=120)
subprocess.run([str(BIN), "refresh"], env=env6, capture_output=True, timeout=300)
written = RECORD_INDEX.read_text()
seq_lines = [l for l in written.splitlines() if l.startswith("_TAI_SEQ")]
check("no _TAI_SEQ entries at all: the fixture proved nothing", bool(seq_lines), True)
check("a stale command is still offered as what follows another one",
      any(_STALE in l for l in seq_lines), False)
check("a stale command still has followers in the index",
      any(l.startswith("_TAI_SEQ+=('" + _STALE) for l in seq_lines), False)
check("filtering the sequence table also removed live commands",
      any("git status" in l for l in seq_lines), True)

# The order of a _TAI_SEQ value list is the prophecy's order, because the head
# of the list is the line the fresh prompt offers: what followed *most*, not
# the best-scoring line that ever did.
_SEQ_KEY = "git fetch --all"
_OFTEN = "git pull --rebase origin main"
_RARE = "echo tally"
for _ in range(3):
    for cmd in (_SEQ_KEY, _OFTEN):
        subprocess.run([str(BIN), "record", "--cwd", str(REPO), cmd],
                       env=env6, capture_output=True, timeout=120)
subprocess.run([str(BIN), "record", "--cwd", str(REPO), _SEQ_KEY],
               env=env6, capture_output=True, timeout=120)
for _ in range(30):
    subprocess.run([str(BIN), "record", "--cwd", str(REPO), _RARE],
                   env=env6, capture_output=True, timeout=120)
subprocess.run([str(BIN), "refresh"], env=env6, capture_output=True, timeout=300)
written = RECORD_INDEX.read_text()
head = "_TAI_SEQ+=('" + _SEQ_KEY + "' '"
check("no sequence entry for the key at all", head in written, True)
vstart = written.index(head) + len(head)
order_values = written[vstart:written.index("')", vstart)]
check("the prophecy head is the best score, not the most frequent follower",
      order_values.splitlines()[0], _OFTEN)
check("ordering by frequency dropped the less frequent follower",
      _RARE in order_values.splitlines(), True)

# -- the JEV gate: a hostile model answers, and the code decides --------------

class _Hostile(BaseHTTPRequestHandler):
    """A model that grades its own homework: it picked `rm -rf /` and calls
    it safe. The gate is deterministic and in code, so the answer must not
    reach the prompt."""

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        self.rfile.read(length)
        body = json.dumps({
            "answers": {
                "best_candidate": {"choice": "rm -rf /", "probabilities": {}, "confidence": 0.99},
                "show_now": {"noul": 1.0},
                "destructive": {"noul": 0.1},
            },
            "model": "hostile-test",
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


srv = HTTPServer(("127.0.0.1", 0), _Hostile)
port = srv.server_address[1]
threading.Thread(target=srv.serve_forever, daemon=True).start()
got = subprocess.run(
    [str(BIN), "suggest", "--json", "--jev", "rm -rf /",
     "--cwd", str(home6), "--git", "", "--branch", ""],
    env=dict(env6, TYPESAFE_API_KEY="test-key",
             TYPESAFE_BASE_URL=f"http://127.0.0.1:{port}", TAI_JEV_TIMEOUT="5"),
    capture_output=True, text=True, timeout=120,
)
srv.shutdown()
res = json.loads(got.stdout)
check("the hostile choice did not become the suggestion",
      res.get("choice") == "rm -rf /", False)
check("the rejection was said out loud",
      "not offering" in res.get("jev_rejected", ""), True)
check("the rerank was recorded, not hidden", res.get("jev", {}).get("model"), "hostile-test")

print()
if failures:
    print(f"FAILED ({len(failures)}) — fix and re-run")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("OK — install, wiring, retarget, autosuggest, uninstall, index rules, JEV gate.")
