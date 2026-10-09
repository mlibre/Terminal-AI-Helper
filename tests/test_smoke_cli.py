"""What the command line learns: help text, wrappers, refresh, and the index.

    python3 tests/test_smoke_cli.py

Two rules are load-bearing here. A tool is probed in a scratch working
directory, because not every `--help` is a pure read and one of them wrote a
565KB database into the project root. And the scores the shell plugins read are
built for real here, because the ranking rules are invisible from the engine.
"""
import pathlib
import shutil
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from smoke_env import *  # noqa: F401,F403  (scratch db, REPO, Engine, the tai modules)
from smoke_env import BUILT, _fake_build, _quiet  # noqa: F401  (the build stub the refresh test swaps in)

# Learning on use: the first time a tool is actually run, its help is read and
# cached, so the next session knows its subcommands. It has to be free of side
# effects for anything that is not an installed executable, and it has to
# respect the switch the pty harness needs.
from tai import cli as tai_cli


class _Args:
    command = ""
    cwd = git = branch = ""
    exit = 0


learned: list[str] = []
import tai.knowledge as knowledge
# "Installed" is answered by the CLI's own PATH lookup now, and it is that one
# function the stub replaces — the machine's real PATH decides nothing here.
real_which, real_learn, real_is_known = tai_cli._which, knowledge.learn, knowledge.is_known
knowledge.learn = lambda tools=None: (learned.extend(tools or []), 1)[1]
knowledge.is_known = lambda name: False
# Only these two are on this fictional PATH, so "installed" is decided by the
# stub rather than by whatever the machine running the test happens to have.
INSTALLED = {"9router", "ls"}
tai_cli._which = lambda name: f"/usr/bin/{name}" if name in INSTALLED else None

os.environ["TAI_NO_LEARN"] = "1"
_Args.command = "9router --port 20128"
tai_cli._learn_on_use(_Args.command)
assert learned == [], "TAI_NO_LEARN=1 must switch learn-on-use off"
os.environ.pop("TAI_NO_LEARN")
tai_cli._learn_on_use(_Args.command)
assert learned == ["9router"], learned
# Already learned: the second run must not probe the tool again.
knowledge.is_known = lambda name: name == "9router"
_Args.command = "9router"
tai_cli._learn_on_use(_Args.command)
assert learned == ["9router"], f"learn-on-use must fire once per tool: {learned}"
knowledge.is_known = lambda name: False
# A first word that is not an installed executable, an empty line, a flag, and
# a path are all skipped without probing anything.
for never in ("", "   ", "--version", "/usr/bin/ls -l", "cd /tmp", "ls"):
    learned.clear()
    _Args.command = never
    tai_cli._learn_on_use(_Args.command)
    if never == "ls":
        assert learned == ["ls"], "an installed tool must be learnable"
    else:
        assert learned == [], f"{never!r} must not be learned: {learned}"
# Put the module back before anything else uses it.
tai_cli._which, knowledge.learn, knowledge.is_known = real_which, real_learn, real_is_known

# What `--help` is read into. The guard in the usage-line parser was written with
# a doubled backslash — a backslash, the letter `w` and a dash — where a word
# character or a dash was meant, and inside a raw string that is not a near-miss:
# it blocks a token only where `w` or a dash precedes it, so `[--help]` yielded
# the subcommand `elp` and `[-abc]` yielded `bc`. Both became offered commands.
# A placeholder in angle brackets is an argument rather than a subcommand for the
# same reason.
from tai.helptext import _extract

_VERBS, _FLAGS = _extract("Usage: tool [--help] [-v] <file>", "tool")
assert _VERBS == [], f"a usage line's bracketed parts are not subcommands: {_VERBS}"
assert _FLAGS == ["--help", "-v"], _FLAGS
# The meta options are moved last, not dropped: `--version` used to survive in
# neither the kept flags nor the short forms, so a tool whose only long option
# was its own had nothing at all to offer after `tool --`.
_VERBS, _FLAGS = _extract("Usage: tool [-h] [--help] [-v] [--version] [--no-browser]"
                          " [-n] <file>", "tool")
assert _FLAGS == ["--no-browser", "-n", "--help", "--version", "-h", "-v"], _FLAGS
_VERBS, _FLAGS = _extract("Usage: prog [-abc] [-o outfile] input...", "prog")
assert _VERBS == [], f"an option's argument is not a subcommand: {_VERBS}"
assert _FLAGS[:2] == ["-abc", "-o"], _FLAGS
_VERBS, _FLAGS = _extract("Usage: git [--version] [--exec-path] [-C <path>] <command>",
                          "git")
assert _VERBS == [], _VERBS
assert "--exec-path" in _FLAGS and "--version" in _FLAGS, _FLAGS
# Subcommands named before the options are still read, and keep their order.
_VERBS, _FLAGS = _extract("usage: tool add|remove|list [options]", "tool")
assert _VERBS == ["add", "remove", "list"], _VERBS
# …and the cost of cutting at the first bracket is one clause: `kubectl [-n ns]
# get pods` loses `get`. A usage line does not say which bare words are
# commands, and offering an option's argument is the worse of the two mistakes.
_VERBS, _FLAGS = _extract("Usage: kubectl [-n namespace] get pods", "kubectl")
assert _VERBS == [], _VERBS
assert _FLAGS == ["-n"], _FLAGS
# The catalog a real tool prints is still read, and keeps its order.
_VERBS, _FLAGS = _extract("Usage: tool <cmd>\n\nCommands:\n  add     one\n"
                          "  remove  two\n", "tool")
assert _VERBS[:2] == ["add", "remove"], _VERBS
print("OK — learn-on-use: once per installed tool, no-op when switched off.")

# Discovery must not probe in the caller's working directory. Not every tool
# treats `--help` as a pure read: `temporal-service --help` wrote a 565KB SQLite
# database into the project root before printing a line, so a discovery run used
# to leave it there. A tool that writes to its cwd must write to a scratch one.
import stat as stat_mod
import tempfile

from tai import knowledge

FAKE = "tai-probe-fixture"
with tempfile.TemporaryDirectory() as bin_dir:
    script = pathlib.Path(bin_dir) / FAKE
    script.write_text(
        "#!/bin/sh\n"
        "touch probe-marker-in-cwd\n"
        "echo 'Usage: tai-probe-fixture <command>'\n"
        "echo 'Commands:'\n"
        "echo '  start   Start it'\n"
    )
    script.chmod(script.stat().st_mode | stat_mod.S_IEXEC)
    saved = os.environ["PATH"]
    os.environ["PATH"] = bin_dir + os.pathsep + saved
    try:
        found = knowledge.discover(FAKE)
    finally:
        os.environ["PATH"] = saved
assert "start" in found.verbs, found.verbs
assert not os.path.exists("probe-marker-in-cwd"), \
    "discovery ran a tool in the caller's working directory"
print("OK — discovery probes in a scratch cwd, not yours.")

# A tool that answers for itself. `tool __complete <args…> <word>` is the only
# machine-readable statement a CLI makes about its own vocabulary, and it is what
# takes `gh pr c` from nothing to `gh pr checkout`. The fixture speaks it, and
# also writes a marker on *every* invocation, because the probe is a second tool
# being run and it has to be in the scratch directory for the same reason `--help`
# is: a tool that writes to its cwd must not write to the caller's.
COBRA_FAKE = "tai-cobra-fixture"
with tempfile.TemporaryDirectory() as bin_dir2:
    cobra = pathlib.Path(bin_dir2) / COBRA_FAKE
    cobra.write_text(
        "#!/bin/sh\n"
        "touch probe-marker-in-cwd\n"
        "if [ \"$1\" = '__complete' ]; then\n"
        "  case \"$2\" in\n"
        "    '') printf 'run\\tRun a thing\\nbuild\\tBuild a thing\\n--rm\\tRemove\\n'\n"
        "        printf ':4\\nCompletion ended with directive: NoFileComp\\n' ;;\n"
        "    run) if [ \"$3\" = '--' ]; then\n"
        "           printf -- '--detach\\tRun detached\\n--rm\\tRemove\\n--help\\tShow help\\n'\n"
        "         else\n"
        "           printf 'fast\\tFast mode\\n'\n"
        "         fi\n"
        "         printf ':4\\nCompletion ended with directive: NoFileComp\\n' ;;\n"
        "    *) printf ':4\\nCompletion ended with directive: NoFileComp\\n' ;;\n"
        "  esac\n"
        "  exit 0\n"
        "fi\n"
        "echo 'Usage: tai-cobra-fixture <command>'\n"
        "echo 'Commands:'\n"
        "echo '  run   Run a thing'\n"
    )
    cobra.chmod(cobra.stat().st_mode | stat_mod.S_IEXEC)
    saved2 = os.environ["PATH"]
    os.environ["PATH"] = bin_dir2 + os.pathsep + saved2
    try:
        declared_tool = knowledge.discover(COBRA_FAKE, probe=["run", "nonesuch"])
    finally:
        os.environ["PATH"] = saved2
assert not os.path.exists("probe-marker-in-cwd"), \
    "the __complete probe ran in the caller's working directory"

# The tool's own answer outranks the parser's guess, and a flag is not a verb:
# `--rm` came back in the same reply as the subcommands, and putting it in the
# verb list would offer `tool --rm` as a thing to type after `tool `.
assert declared_tool.verbs[:2] == ["run", "build"], declared_tool.verbs
assert "--rm" not in declared_tool.verbs, declared_tool.verbs
# `--help` is filtered out of the flags: it teaches nothing the user could not
# have typed, and it would otherwise take the top slot of every flag menu.
assert declared_tool.subs.get("run") == ["--detach", "--rm"], declared_tool.subs
assert declared_tool.subverbs.get("run") == ["fast"], declared_tool.subverbs
# A subcommand the tool does not have is not probed, and a fixture with no
# protocol at all is not mistaken for a tool with no subcommands.
assert "nonesuch" not in declared_tool.subs
assert knowledge._complete_candidates(
    "git: '__complete' is not a git command. See 'git --help'.") is None, \
    "a tool without the protocol must not look like one with nothing to say"
assert knowledge._complete_candidates(":4\nCompletion ended with directive: X") is None
# A row written before these fields existed has no key for them at all.
assert knowledge.ToolKnowledge.from_dict({"name": "x", "verbs": ["v"]}).subs == {}

# The position is the whole point: `docker run --` is not answered by docker's own
# flags, and the answer replaces the half-typed word rather than following it —
# appending would give `tool run --d--detach`.
got_flags = knowledge.candidates_for_prefix(f"{COBRA_FAKE} run --d", [declared_tool])
assert got_flags == [f"{COBRA_FAKE} run --detach"], got_flags
got_subs = knowledge.candidates_for_prefix(f"{COBRA_FAKE} run f", [declared_tool])
assert got_subs == [f"{COBRA_FAKE} run fast"], got_subs
# `tool run ` asks about verbs before options, so the subcommand leads.
lead = knowledge.candidates_for_prefix(f"{COBRA_FAKE} run ", [declared_tool])
assert lead.index(f"{COBRA_FAKE} run fast") < lead.index(f"{COBRA_FAKE} run --detach"), lead
print("OK — declared vocabulary: subcommands, their flags, their subcommands.")

# A wrapper runs the command after it, so the history behind it must stay
# reachable: `sudo git ` found nothing on a history full of `git ...`. The rule is
# one closed list, and the two wrappers that change what follows them are out.
from tai.engine import split_wrapper

for line, want in (
    ("sudo git ", ("sudo", "git ")),
    ("sudo shutdown 3", ("sudo", "shutdown 3")),
    ("doas ls -l", ("doas", "ls -l")),
    ("nohup tail -f", ("nohup", "tail -f")),
    ("sudo FOO=1 git ", None),        # the wrapper carries an assignment
    ("env git ", None),               # changes what follows it
    ("xargs rm ", None),              # the command is fed, not wrapped
    ("sudo", None),                   # not a wrapper use, just a name
    ("sudo ", None),                  # nothing behind the wrapper
    ("ls -l", None),                  # not a wrapper at all
    ("", None),
):
    assert split_wrapper(line) == want, f"{line!r}: {split_wrapper(line)} != {want}"

eng4 = Engine()
for _ in range(20):
    eng4.add("shutdown 30", cwd="/home/mlibre")
    eng4.add("git status", cwd="/home/mlibre")
assert eng4.suggest("sudo shutdown", limit=1)["choice"] == "sudo shutdown 30", \
    eng4.suggest("sudo shutdown", limit=1)
assert eng4.suggest("sudo shutdown ", limit=1)["completion"] == "30", \
    eng4.suggest("sudo shutdown ", limit=1)
assert eng4.suggest("sudo git ", limit=1)["choice"] == "sudo git status", \
    eng4.suggest("sudo git ", limit=1)
assert eng4.suggest("sudo git s", limit=1)["completion"] == "tatus"
assert eng4.suggest("env git ", limit=1)["choice"] == "", "env is not transparent"
assert eng4.suggest("xargs git ", limit=1)["choice"] == "", "xargs is not transparent"
# The wrapper is re-attached to the whole answer, not just the completion.
wrapped = eng4.suggest("sudo git ", limit=1)
assert [c["cmd"] for c in wrapped["choices"]] == ["sudo git status"], wrapped
assert wrapped["wrapped"] == "sudo"
print("OK — wrappers are transparent, and only the ones that really are.")

# A command name is a word being written. `god` is the reported case: the engine
# walked its sorted command names and answered `godot .`, while both plugins asked
# for one exact key and said nothing. One rule, and this is the half of it the
# engine has always got right — asserted here so the plugins have something to
# agree *with*, because "the two implementations must match" is only checkable
# when one of them is pinned down.
eng5 = Engine()
for cmd, n in (("godot .", 9), ("godot --editor", 4), ("whereis godot", 2),
               ("git status", 6)):
    for _ in range(n):
        eng5.add(cmd, cwd="/home/u/games")
assert eng5.suggest("god", limit=1)["completion"] == "ot .", \
    eng5.suggest("god", limit=1)
# Half-typed is not a licence to invent: nothing begins with this, so nothing is
# an answer, and the whole line is the command name so there is no other word to
# fall back on either.
assert eng5.suggest("godx", limit=1)["choice"] == "", eng5.suggest("godx", limit=1)
print("OK — a half-typed command name answers; a name nothing begins with does not.")

# `tai refresh` is the command a user reaches for, so it has to actually make
# new history visible: import the rows, write both indexes, and say so in one
# line. It must also stay quiet on request, because the installer calls it on
# every install and a second of work should not print a paragraph.
import contextlib
import io
import shutil

real_import = tai_store.import_shell_history

# The import is stubbed rather than run: against the developer's own
# ~/.zsh_history it would put a machine-dependent number in the assertion, and
# write rows nobody asked for into the scratch database.
tai_store.import_shell_history = lambda *a, **k: 4
tai_index.build = _fake_build
out = io.StringIO()
with contextlib.redirect_stdout(out):
    assert tai_cli.cmd_refresh(_quiet(False)) == 0
assert len(BUILT) == 1, "refresh must rebuild the index"
# One short line. A count is a fact; paths and row tallies are noise.
assert out.getvalue().strip() == "✓ 3 commands indexed, 4 new", out.getvalue()

tai_store.import_shell_history = lambda *a, **k: 0
out = io.StringIO()
with contextlib.redirect_stdout(out):
    assert tai_cli.cmd_refresh(_quiet(False)) == 0
assert out.getvalue().strip() == "✓ 3 commands indexed", out.getvalue()

out = io.StringIO()
with contextlib.redirect_stdout(out):
    assert tai_cli.cmd_refresh(_quiet(True)) == 0
assert out.getvalue() == "", f"--quiet must print nothing: {out.getvalue()}"

# A rebuild that could not run is reported, never swallowed: a silent failure
# looks exactly like a working install that simply learned nothing new.
def _boom(*a, **k):
    raise RuntimeError("disk full")


tai_index.build = _boom
out = io.StringIO()
with contextlib.redirect_stdout(out):
    assert tai_cli.cmd_refresh(_quiet(True)) == 1
assert "index rebuild failed: disk full" in out.getvalue(), out.getvalue()
tai_index.build = real_build
# The lock exists so two background rebuilds cannot write one index, and a
# rebuild that finished must not leave it behind: the next one would see a live
# holder that is not.
assert not tai_maintenance._lock_path().exists(), "a finished rebuild left its lock"
print("OK — refresh: imports, rebuilds, one line, and reports a failed build.")

# The score the shell plugins read, built for real. The reported case was `cd `
# answering `cd ..`: `cd ..` is in the seed corpus, and the corpus used to add a
# flat bonus to *every* seed's score, so a convention the user had run four times
# outranked a project directory they had visited ten times. Two rules, both about
# ranking, and both are invisible from the engine alone — this is the surface
# they were both wrong on.
import re as _re

_seed_db = "/tmp/tai/tai_seedband.db"
_seed_index = "/tmp/tai/tai_seedband_index.zsh"
for _p in (_seed_db, _seed_db + "-wal", _seed_db + "-shm", _seed_index):
    pathlib.Path(_p).unlink(missing_ok=True)
_saved_db, _saved_index = os.environ["TAI_DB"], os.environ["TAI_INDEX"]
os.environ["TAI_DB"], os.environ["TAI_INDEX"] = _seed_db, _seed_index
# A destination that is really there: the liveness check drops a `cd` into a
# directory that does not exist, which is a different rule and would leave this
# one untested.
_LIVE = os.path.expanduser("~")
_DEST = f"cd {_LIVE}"
try:
    from tai.seed import SEED_COMMANDS
    for cmd, n in (("cd ..", 4), ("cd -", 4), (_DEST, 10),
                   ("ls -lah", 2), ("git status", 1)):
        for _ in range(n):
            tai_store.append_and_count(cmd, cwd=_LIVE)
    real_build()
    written = pathlib.Path(_seed_index).read_text()
    # `+=` is escaped as `\+` because a bare `+` after another one is a possessive
    # quantifier, and the pattern then matches nothing at all — silently, since
    # `finditer` over a file that plainly contains the lines returns empty.
    got = {m.group(1).replace("'\\''", "'"): int(m.group(2)) for m in
           _re.finditer(r"_TAI_SCORE\+=\('((?:[^']|\\')*)' (-?\d+)\)", written)}
    assert got, "the build wrote no scores at all"
    observed = min(got[c] for c in (_DEST, "ls -lah", "git status"))
    # The reported inversion, on the scores the shell actually compares. `cd ..`
    # is in the corpus *and* in the history, and it used to collect the corpus
    # bonus on top of its four runs — beating a directory visited ten times.
    assert got[_DEST] > got["cd .."], \
        f"a convention outranked observed usage: {got['cd ..']} vs {got[_DEST]}"
    # A convention the user has never run is scored *only* by its place in the
    # corpus, in a band below everything the history saw. `ls -la` is first in
    # the corpus and must still beat `ls -l`: that ordering is the whole reason
    # the corpus is ordered, and it is what answers `ls -` before any history.
    for seed in ("ls -la", "ls -l", "pwd", "top"):
        assert seed in got, f"{seed!r} is in the corpus and must be indexed"
        assert got[seed] < observed, \
            f"{seed!r} is a convention and must rank below observed usage"
    assert got["ls -la"] > got["ls -l"], "the corpus order is the cold-start ranking"
    assert got["ls -la"] > 0, "a convention must still be on offer"
    # The band is the corpus length times the step, so it cannot grow into the
    # observed range however long the corpus gets — that gap *is* the invariant.
    pure = [got[c] for c in SEED_COMMANDS if c in got and c not in
            (_DEST, "ls -lah", "git status", "cd ..", "cd -")]
    assert max(pure) < observed, \
        f"the convention band reaches observed usage: {max(pure)} vs {observed}"
finally:
    os.environ["TAI_DB"], os.environ["TAI_INDEX"] = _saved_db, _saved_index
    for _p in (_seed_db, _seed_db + "-wal", _seed_db + "-shm", _seed_index):
        pathlib.Path(_p).unlink(missing_ok=True)

# A history where every command is stale must not be indexed at all. Publishing
# the seed corpus here would replace everything the user learned with a few dozen
# conventions and report success, which is the same destruction as a corrupt
# store — reached honestly rather than by accident. The guard existed and could
# not fire: it asked whether the *engine* was empty, and by then the seed corpus
# and the generated tool vocabulary had been added to it.
_dead_db = "/tmp/tai/tai_alldead.db"
_dead_index = "/tmp/tai/tai_alldead.zsh"
for _p in (_dead_db, _dead_db + "-wal", _dead_db + "-shm", _dead_index):
    pathlib.Path(_p).unlink(missing_ok=True)
pathlib.Path(_dead_index).write_text("# tai index — the good one\n_TAI_SCORE+=('x' '1')\n")
_saved_db, _saved_index = os.environ["TAI_DB"], os.environ["TAI_INDEX"]
os.environ["TAI_DB"], os.environ["TAI_INDEX"] = _dead_db, _dead_index
try:
    for _c in ("cd /nowhere/gone-abc", "code /nowhere/gone-abc/x.py"):
        tai_store.append_and_count(_c, cwd="/nowhere")
    try:
        tai_index.build()
        raise AssertionError("an all-stale history was indexed")
    except RuntimeError as _e:
        assert "purge --stale" in str(_e), _e   # the message names the way out
    # …and the index that was already there is the one that is still there.
    assert pathlib.Path(_dead_index).read_text().startswith("# tai index — the good one"), \
        "an all-stale rebuild replaced the last good index"
    # One command that is still real is enough to build again, so the answer is
    # "nothing to index" and not "no index, ever".
    tai_store.append_and_count("ls -la", cwd=os.path.expanduser("~"))
    assert tai_index.build() > 0, "a live command must be indexable again"
finally:
    os.environ["TAI_DB"], os.environ["TAI_INDEX"] = _saved_db, _saved_index
    for _p in (_dead_db, _dead_db + "-wal", _dead_db + "-shm", _dead_index):
        pathlib.Path(_p).unlink(missing_ok=True)
print("OK — the index: conventions rank below observed usage, and stay on offer.")

# Both plugins refuse to source a file that is not tai's — the index is shell
# code, so the shell has to be told whose it is — and that decision is a header
# line the writer and the two readers each spell out separately. Nothing here
# would notice the three drifting apart except the thing that has to be true: a
# real `tai index build` output, read by each shell's own loader. So build it
# with the writer under test and source it in the two shells, and require
# silence — a refusal names the file, so any output at all is the failure.
_repo = pathlib.Path(__file__).resolve().parent.parent
_gen_dir = pathlib.Path("/tmp/tai/tai_gen_dir")
_gen_db = "/tmp/tai/tai_gen_dir/history.db"
# The bash snapshot is one derived name away from the zsh one, so both live in
# this scratch directory and nothing this block writes can be another test's file.
_gen_index = str(_gen_dir / "zsh-index.zsh")
_gen_paths = [_gen_dir / n for n in ("zsh-index.zsh", "bash-index.bash", "history.db",
                                     "history.db-wal", "history.db-shm")]
_saved_db, _saved_index = os.environ["TAI_DB"], os.environ["TAI_INDEX"]
_gen_dir.mkdir(parents=True, exist_ok=True)
for _p in _gen_paths:
    _p.unlink(missing_ok=True)
os.environ["TAI_DB"], os.environ["TAI_INDEX"] = _gen_db, _gen_index
try:
    for _c in ("git status", "cd ~/somewhere", "ls -la"):
        tai_store.append_and_count(_c, cwd=os.path.expanduser("~"))
    assert tai_index.build() > 0, "the real writer built nothing to check"
    # The bash snapshot sits one derived name away from the zsh one, and it is
    # read by the same check, so both files have to be accepted.
    for _p in _gen_paths[:2]:
        assert _p.exists(), f"the writer did not produce {_p}"
    for _shell, _plugin in (("zsh", "plugins/tai.zsh"), ("bash", "plugins/tai.bash")):
        # A bash-only machine skips the zsh half rather than failing here; the
        # rest of this suite has already run, which is the point of the suite.
        if _shell == "zsh" and not zsh_bin():
            continue
        _env = dict(os.environ, TAI_INDEX=_gen_index, TAI_HISTORY_FILES="")
        # zsh needs `-f` (no rc) and bash must not read a profile; both take the
        # program with `-c`.
        _argv = [_shell, "-f", "-c"] if _shell == "zsh" else [_shell, "--noprofile",
                                                               "--norc", "-c"]
        _out = subprocess.run([*_argv, f"source {_repo / _plugin}"],
                              capture_output=True, text=True, env=_env, timeout=60)
        assert _out.returncode == 0, f"{_shell} failed on a real index: {_out.stderr}"
        assert _out.stderr == "", f"{_shell} refused a real index: {_out.stderr}"
finally:
    os.environ["TAI_DB"], os.environ["TAI_INDEX"] = _saved_db, _saved_index
    for _p in _gen_paths:
        _p.unlink(missing_ok=True)
print("OK — both plugins accept the index the real writer produces.")
print("OK — an all-stale history is refused, and the last good index survives it.")

# `tai tune` is the only code here that rewrites its own source, so it gets a test
# that runs against a *copy* of the package — a test that imported the real
# `tai.tune` would rewrite the real `tai/engine.py` to prove a point.
#
# What it checks is the report, because that is what was wrong: `re.sub` reports
# "no match" as success, so a constant the writer and the source spelled
# differently each was left alone while the run printed `wrote …/engine.py` after
# minutes of search. A tune that changed nothing must say so and touch nothing.
_tune_dir = pathlib.Path("/tmp/tai/tai_tune_copy")
shutil.rmtree(_tune_dir, ignore_errors=True)
_tune_dir.mkdir(parents=True)
shutil.copytree(_repo / "tai", _tune_dir / "tai")
_tune_db = "/tmp/tai/tai_tune_copy/history.db"
_tune_env = dict(os.environ, TAI_DB=_tune_db, TAI_INDEX="/tmp/tai/tai_tune_copy/i.zsh",
                 TAI_NO_AUTO_RECORD="1", TAI_DATA_DIR="/tmp/tai/tai_tune_copy/data")
_saved_db = os.environ["TAI_DB"]
os.environ["TAI_DB"] = _tune_db
try:
    _words = ["git status", "git pull --rebase", "ls -la", "cd ~/proj", "npm test",
              "docker ps", "cargo build", "grep -rn x .", "make -j8"]
    for _i in range(400):
        tai_store.append_and_count(_words[_i % len(_words)], cwd="/tmp")
finally:
    os.environ["TAI_DB"] = _saved_db
_tune_cli = _tune_dir / "tai" / "cli.py"
_engine = _tune_dir / "tai" / "engine.py"
_tuned = subprocess.run(["python3", str(_tune_cli), "tune", "--trials", "2"],
                        env=_tune_env, capture_output=True, text=True, timeout=900)
assert _tuned.returncode == 0, _tuned.stderr
assert "weights changed" in _tuned.stdout or "no weight changed" in _tuned.stdout, _tuned.stdout
assert not list(_tune_dir.glob("tai/*.py.tmp")), "the temporary file was left behind"
# The same search, with one constant spelled the way a future edit might spell it.
_engine.write_text(_engine.read_text().replace("W_RECENCY = 2.0", "W_RECENCY: float = 2.0"))
_before = _engine.read_text()
_refused = subprocess.run(["python3", str(_tune_cli), "tune", "--trials", "1"],
                          env=_tune_env, capture_output=True, text=True, timeout=900)
assert _refused.returncode == 1, f"a tune that changed nothing reported success: {_refused.stdout}"
assert "W_RECENCY" in _refused.stdout and "not written" in _refused.stdout, _refused.stdout
assert _engine.read_text() == _before, "engine.py was rewritten by a run that could not tune it"
print("OK — tune: writes only what it changed, and says so when it changed nothing.")

# The one-shot paths are answered from a pickled engine keyed on the store
# file, so a fresh process loads instead of rebuilding. Two properties matter:
# a store that gained a row is reflected the moment the next process asks, and
# a stale-or-disabled cache only ever costs time, never correctness.
import tai.predictor as predictor  # noqa: E402
from tai import engcache  # noqa: E402

_predict_env = dict(os.environ, TAI_DB=os.environ["TAI_DB"],
                    TAI_INDEX=os.environ["TAI_INDEX"], PYTHONPATH=str(REPO),
                    TAI_NO_LEARN="1")
_ok, _total, _newest = tai_store.append_and_count("git status --short", cwd="/tmp")
assert _ok and _total and _newest > 0, "append_and_count must report the newest row's timestamp"
predictor._E = None
_first = predictor.suggest("git status --s")
assert _first["choice"] == "git status --short", _first
# The cache file is beside the store, named for it.
assert engcache._cache_path(pathlib.Path(os.environ["TAI_DB"])).exists(), \
    "a primed one-shot run must leave its engine cache beside the store"
# A row the cache has never seen is answered by the *next* process: invalidate,
# fork, ask. The new line must be the answer even though the pickle predates it.
tai_store.append_and_count("git status -sb", cwd="/tmp")
_fresh = subprocess.run(
    [sys.executable, "-c",
     "import sys; sys.path.insert(0, r'%s'); from tai.predictor import suggest; "
     "print(suggest('git status -s')['choice'])" % REPO],
    env=_predict_env, capture_output=True, text=True, timeout=120)
assert "git status -sb" in _fresh.stdout, (_fresh.stdout, _fresh.stderr)
# The cache is used, not just tolerated: with it armed, a patched builder that
# would raise is never called.
predictor._E = None
_real_build = predictor._build_engine
def _must_not_build():
    raise AssertionError("the engine was rebuilt with a current cache present")
predictor._build_engine = _must_not_build
try:
    _warm = predictor.suggest("git status --s")
    assert _warm["choice"] == "git status --short", _warm
finally:
    predictor._build_engine = _real_build
# And the opt-out switch rebuilds by hand the old way.
predictor._E = None
os.environ["TAI_NO_ENGINE_CACHE"] = "1"
try:
    _hand = predictor.suggest("git status --s")
    assert _hand["choice"] == "git status --short", _hand
finally:
    os.environ.pop("TAI_NO_ENGINE_CACHE")
    predictor._E = None
print("OK — the engine disk cache: primed, invalidated by new rows, and optional.")

# `record` parses its own five flags without argparse (it runs once per command
# typed, forever), and anything it does not recognise falls through to the real
# parser. Both are exercised through the CLI exactly as the plugins call it.
_rec = subprocess.run([sys.executable, "-S", "-E", str(REPO / "tai" / "cli.py"),
                       "record", "echo fast-path", "--cwd", "/tmp", "--exit", "0"],
                      env=_predict_env, capture_output=True, text=True, timeout=60)
assert "recorded" in _rec.stdout, (_rec.stdout, _rec.stderr)
_help = subprocess.run([sys.executable, "-S", "-E", str(REPO / "tai" / "cli.py"),
                        "record", "--help"],
                       env=_predict_env, capture_output=True, text=True, timeout=60)
assert _help.returncode == 0 and "--exit" in _help.stdout, _help.stdout
assert tai_store.count()[0] > 0
_ver = subprocess.run([sys.executable, "-S", "-E", str(REPO / "tai" / "cli.py"), "version"],
                      env=_predict_env, capture_output=True, text=True, timeout=60)
assert _ver.stdout.strip() == f"tai {(REPO / 'VERSION').read_text().strip()}", _ver.stdout
_alias = subprocess.run([sys.executable, "-S", "-E", str(REPO / "tai" / "cli.py"), "--help"],
                        env=_predict_env, capture_output=True, text=True, timeout=60)
assert "upgrade" in _alias.stdout, "the `tai upgrade` alias is missing from --help"
print("OK — record answers without argparse; version and the upgrade alias report.")

# `tai forget` is the user-facing half of the exit-code repair: a typo that an
# older install recorded as a success — the hook-order bug read a prompt
# theme's exit status instead of the command's — sits in the ranking for good,
# because no rule can tell it from a command that worked. The command drops
# the rows and rebuilds, says how many rows it took, and is honest about a
# name that matched nothing.
_forget_db = "/tmp/tai/tai_forget.db"
_forget_index = "/tmp/tai/tai_forget_index.zsh"
_forget_spool = "/tmp/tai/tai_forget_spool.log"
for _sfx in ("", "-wal", "-shm"):
    pathlib.Path(_forget_db + _sfx).unlink(missing_ok=True)
pathlib.Path(_forget_index).unlink(missing_ok=True)
pathlib.Path(_forget_spool).unlink(missing_ok=True)
# A spool of its own: every CLI drains first, and the developer's real spool
# must never land in a scratch database — or in these assertions.
_forget_env = dict(os.environ, TAI_DB=_forget_db, TAI_INDEX=_forget_index,
                   TAI_SPOOL=_forget_spool, TAI_NO_LEARN="1")
import sqlite3


def _forget_rows() -> list[str]:
    con = sqlite3.connect(_forget_db)
    try:
        return [r[0] for r in con.execute("select cmd from commands order by id")]
    finally:
        con.close()


def _seed_forget() -> None:
    con = sqlite3.connect(_forget_db)
    con.execute(
        "CREATE TABLE IF NOT EXISTS commands(id INTEGER PRIMARY KEY, "
        "cmd TEXT NOT NULL, cwd TEXT DEFAULT '', repo TEXT DEFAULT '', "
        "branch TEXT DEFAULT '', exit_code INTEGER DEFAULT 0, ts INTEGER DEFAULT 0)")
    con.executemany(
        "INSERT INTO commands(cmd,cwd,repo,branch,exit_code,ts) VALUES(?,?,?,?,?,?)",
        [("tai-junk-opencoe", "/w", "", "", 0, 1728500000),
         ("tai-junk-opencoe", "/w", "", "", 0, 1728500100),
         ("tai-junk-openc", "/w", "", "", 0, 1728500200),
         ("tai-real-opencode", "/w", "", "", 0, 1728500300),
         ("tai-real-opencode web", "/w", "", "", 0, 1728500400)])
    con.commit()
    con.close()


_seed_forget()
_f1 = subprocess.run([sys.executable, "-S", "-E", str(REPO / "tai" / "cli.py"),
                      "forget", "tai-junk-opencoe", "tai-junk-openc"],
                     env=_forget_env, capture_output=True, text=True, timeout=60)
assert _f1.returncode == 0, (_f1.stdout, _f1.stderr)
assert "forgot 3 rows for 2 commands" in _f1.stdout, _f1.stdout
assert "index rebuilt" in _f1.stdout, _f1.stdout
assert _forget_rows() == ["tai-real-opencode", "tai-real-opencode web"], _forget_rows()
# The rebuild really wrote the index, and the forgotten names are out of it.
_idx = pathlib.Path(_forget_index).read_text()
assert "tai-junk" not in _idx, "a forgotten command is still in the index"
assert "tai-real-opencode" in _idx, "the survivor lost its index entry"
# A name that matches nothing is a printed answer, not a shrug.
_f2 = subprocess.run([sys.executable, "-S", "-E", str(REPO / "tai" / "cli.py"),
                      "forget", "tai-junk-opencoe", "never-was-cmd"],
                     env=_forget_env, capture_output=True, text=True, timeout=60)
assert _f2.returncode == 0 and "nothing forgotten" in _f2.stdout, (_f2.stdout, _f2.stderr)
# --no-rebuild leaves the index behind and says so.
_seed_forget()
_before = pathlib.Path(_forget_index).read_text()
_f3 = subprocess.run([sys.executable, "-S", "-E", str(REPO / "tai" / "cli.py"),
                      "forget", "--no-rebuild", "tai-junk-openc"],
                     env=_forget_env, capture_output=True, text=True, timeout=60)
assert _f3.returncode == 0 and "tai refresh" in _f3.stdout, (_f3.stdout, _f3.stderr)
assert "index rebuilt" not in _f3.stdout, _f3.stdout
assert pathlib.Path(_forget_index).read_text() == _before, "--no-rebuild wrote the index"
assert "tai-junk-opencoe" in _forget_rows(), "the survivor of --no-rebuild went missing"
print("OK — forget: exact rows out, siblings kept, rebuild on and off, honest silence.")
