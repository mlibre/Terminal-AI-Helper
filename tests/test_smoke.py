"""The ranker and the rules it is not allowed to decide: paths, files, ranking.

    python3 tests/test_smoke.py

The engine is a pure in-memory ranker. Which filesystem policy applies — is this
`cd` still there, does this line end in a file, does this word name anywhere —
is decided by `tai.paths` and `tai.fresh` and passed in. Keeping the two apart
is what holds `suggest` under a millisecond, and it is what the shell plugins
compare their own answers against.
"""
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from smoke_env import *  # noqa: F401,F403  (scratch db, REPO, Engine, the tai modules)

eng = Engine()
seq = [
    ("cd ~/projects/myapp", "/home/u", "myapp", "main"),
    ("git pull --rebase", "/home/u/projects/myapp", "myapp", "main"),
    ("docker compose up -d", "/home/u/projects/myapp", "myapp", "main"),
    ("docker ps", "/home/u/projects/myapp", "myapp", "main"),
    ("docker compose logs -f", "/home/u/projects/myapp", "myapp", "main"),
] * 30
for cmd, cwd, repo, br in seq:
    eng.add(cmd, cwd=cwd, repo=repo, branch=br)

t0 = time.time()
r = eng.suggest("docker ", cwd="/home/u/projects/myapp", repo="myapp", branch="main", limit=3)
dt = (time.time() - t0) * 1000
print(f"suggest('docker ') engine={r['latency_ms']}ms wall={dt:.1f}ms:")
print(r)

t0 = time.time()
r2 = eng.suggest("", cwd="/home/u/projects/myapp", repo="myapp", branch="main",
                 last_commands=["git pull --rebase"], limit=3)
dt2 = (time.time() - t0) * 1000
print(f"\nnext-after-git-pull engine={r2['latency_ms']}ms wall={dt2:.1f}ms:")
print(r2)

# typo tolerance
r3 = eng.suggest("dokcer ", limit=3)
print("\ntypo 'dokcer ' ->", [c["cmd"] for c in r3["choices"]])

# token n-gram: flags
eng2 = Engine()
for _ in range(20):
    eng2.add("docker compose logs -f --tail 50", cwd="/p", repo="r")
    eng2.add("docker compose logs -f", cwd="/p", repo="r")
r4 = eng2.suggest("docker compose logs -f ", cwd="/p", repo="r", limit=2)
print("flags ->", [c["cmd"] for c in r4["choices"]])

assert r["choice"].startswith("docker "), r
assert "docker compose up -d" in [c["cmd"] for c in r2["choices"]], r2
assert any(c["cmd"].startswith("docker") for c in r3["choices"]), r3

# Recorded-history hygiene: the store must reject secrets and the marker
# envelopes that session-persistence harnesses wrap around real commands.
from tai.store import is_recordable

WRAPPED = ("printf '%s\\n' $'__DSH_PERSISTENT_BASH_START_45e6eb8a-7fa8-46c6-8e07-"
           "48fc019972d5__'; eval -- $'ls -R ~/src'; "
           "__dsh_persistent_bash_status=$?; printf '%s\\n' done")
assert not is_recordable(WRAPPED), "harness envelope must be rejected"
assert not is_recordable("curl -H 'token: abc123' https://x"), "secret must be rejected"
assert not is_recordable(""), "empty must be rejected"
assert not is_recordable("echo " + "x" * 2100), "over-long must be rejected"
assert is_recordable("docker compose up -d"), "normal command must be kept"
assert is_recordable("ls -R ~/src"), "plain command must be kept"
assert is_recordable("git commit -m __START__"), "lookalike text must be kept"
# One key is not vocabulary: an accidental Enter on a stray character is
# recorded like any other command, and recency is the one signal that makes it
# look good — the report that set this rule was an empty Tab whose menu was a
# band of one-key entries above the directory listing. Aliases are shell state;
# a real one-character tool loses nothing but a rank it never deserved.
assert not is_recordable("y"), "a one-key Enter must not be recorded (y)"
assert not is_recordable("n"), "a one-key Enter must not be recorded (n)"
assert not is_recordable("-"), "a one-key Enter must not be recorded (-)"
assert not is_recordable("`"), "a stray backtick must not be recorded"
assert is_recordable("ls"), "a two-letter command is kept"
# A command that spans several of the user's Enters is one command here and two
# candidates there: the index newline-joins what it offers for a word, so the
# second half of it would have been offered as a command of its own that is not.
assert not is_recordable('git commit -m "line one\n\nline two"'), \
    "a command with a newline in it must not be recorded"
assert not is_recordable("git diff\ngit status"), "two commands are not one"
# A recorded buffer can collect raw control bytes — bracketed-paste markers,
# SYN — and as a candidate they would be painted onto the user's terminal
# raw. The two real rows this was found with were exactly that: an opened
# paste envelope, and a lone SYN.
assert not is_recordable('\x1b[200~git push --force-with-lease origin main~'), \
    "a paste envelope must not be recorded"
assert not is_recordable('proxy-ns \x16'), "a SYN byte kills the row"
assert not is_recordable('git commit\x7f'), "a DEL byte kills the row"
assert is_recordable('printf "caf\\u00e9"'), "ordinary UTF-8 must be kept"

# And the model that seeds the index drops such rows too, so a database
# rebuilt from rows — `tai refresh`, or a database written before the
# filter existed — produces a clean index even without a purge.
from tai.engine import Engine
_eng = Engine()
_eng.build_from_rows([
    ("git push", "", "", "", 0, 1),
    ('\x1b[200~git push force~', "", "", "", 0, 2),
    ("git status", "", "", "", 0, 3),
])
assert list(_eng.cmds) == ["git push", "git status"], \
    f"control bytes must not reach the engine: {list(_eng.cmds)!r}"
assert _eng.seq.get("git push") == {"git status": 1}, \
    "the dropped row must not join to either neighbour"
print("\nOK — top-1 + sequence + typo + flags + record filter, all fast.")

# `schema_note` is what tells "no history yet" apart from "a store every reader
# turns into zero rows", and it is the first thing `tai doctor` prints. On a
# machine where tai has never run it used to report a broken schema: it opened the
# database with sqlite3, which *creates* the file, found no `commands` table in
# the database it had just made, and called that an out-of-date schema — the
# alarm a new user runs `tai doctor` to check for, on a machine with no problem.
from tai.store import schema_note

_scratch_dir = pathlib.Path(SCRATCH_DB).parent
_never = _scratch_dir / "tai_never_ran.db"
_never.unlink(missing_ok=True)
_saved_db = os.environ["TAI_DB"]
os.environ["TAI_DB"] = str(_never)
try:
    assert schema_note() == "", "a machine that has never run tai has no problem"
    assert not _never.exists(), "asking must not create the database it asked about"
    # A store with a table from before the branch column is the case that note
    # exists for, and it must still be the one it reports.
    import sqlite3 as _sqlite3
    con = _sqlite3.connect(str(_never))
    con.execute("CREATE TABLE commands(id INTEGER PRIMARY KEY, cmd TEXT, cwd TEXT)")
    con.commit()
    con.close()
    assert "out of date" in schema_note(), schema_note()
    # …and a file that is not a database at all is still called unreadable.
    _never.write_text("this is not a database")
    assert "unusable" in schema_note(), schema_note()
finally:
    os.environ["TAI_DB"] = _saved_db
    _never.unlink(missing_ok=True)
print("OK — the store diagnostic: a first run is not a broken store.")

# A command whose paths are gone is not a suggestion. The reported case was
# `cd ~/projects/myapp`, typed often enough to outrank every live alternative
# long after the directory stopped existing. The check has to be conservative:
# a token it cannot read is "no opinion", never a rejection.
from tai.paths import EXISTS, MISSING, UNKNOWN, command_verdict

VERDICTS = [
    # (command, recorded cwds, expected)
    ("cd ~/projects/myapp", ["/home/u"], MISSING),      # the reported case
    ("cd ~/projects/myapp", [], MISSING),               # ~ needs no cwd to judge
    ("cd ..", [], EXISTS),
    ("cd -", [], UNKNOWN),                              # a flag, not a path
    ("ls -la", [], UNKNOWN),                            # no path token at all
    ("cat file", [], UNKNOWN),                          # a placeholder, not a path
    ("kill -TERM PID", [], UNKNOWN),
    ("git commit -m message", [], UNKNOWN),
    ("curl -sS https://x/y", [], UNKNOWN),              # a URL
    ("scp file host:path", [], UNKNOWN),                # a remote spec
    ("docker run -p 8080:80 img", [], UNKNOWN),         # a port mapping
    ("rm -rf dist/*.py", [], UNKNOWN),                  # a glob may still match
    ("echo $HOME/x", [], UNKNOWN),                      # a variable is unresolvable
    ("python3 src/main.py", ["/nowhere/at/all"], UNKNOWN),   # no live cwd: no opinion
    ("python3 src/main.py", [REPO], MISSING),           # wrong path in a real cwd
    ("code tai/paths.py", [REPO], EXISTS),              # the live case
    ("cd \"/home\"", [], EXISTS),                        # quotes are not part of it
    ("tail -n 20 /etc/hosts", [], EXISTS),
    ("tail -n 20 /etc/definitely-not-here", [], MISSING),
    (f"cd {REPO}", [], EXISTS),
    # A bare name is not shaped like a path, and the shape test passes it — which
    # is how `cd ter` for a directory that was never there stayed in the index and
    # was offered as a completion of `cd te`. `cd`'s argument is a directory by
    # what `cd` means, so it is judged from the directory it was recorded in.
    ("cd ter", [REPO], MISSING),
    ("cd ter", [], UNKNOWN),                            # no live cwd: no opinion
    ("cd tai ter", [REPO], EXISTS),                      # only the first is forced
    ("z ter", [REPO], UNKNOWN),                         # a fuzzy name, not a path
    ("ls ter", [REPO], UNKNOWN),                        # a reader, not a mover
    ("cd -", [REPO], UNKNOWN),                          # "go back", not a path
    ("cd", [REPO], UNKNOWN),                            # no argument to judge
]
for cmd, cwds, want in VERDICTS:
    got = command_verdict(cmd, cwds)
    assert got == want, f"{cmd!r} in {cwds}: got {got}, want {want}"

# A date, a ratio and a version all pass a bare slash test, and all of them were
# being resolved as paths. The module's own rule is that a token is judged only
# when it is unambiguously a path, so these have to come back unknown. Only a
# *leading* numeric component gives it away: `pkg/1.2.0` could be a real
# directory, so it stays judgeable and stays a guess.
for notapath in ("date 1/2/2024", "diff 1/2 2.0/3.0", "sort -t/ 1/2/2024",
                 "echo 1/2/3", "curl 8080/16/x"):
    got = command_verdict(notapath, [REPO])
    assert got == UNKNOWN, f"{notapath!r} must have no opinion, got {got}"

# A relative move is not a destination. `..`, `.` and `-` are true in every
# directory that has ever existed, so how often they were typed is evidence about
# how often the user walks back up, not about where they want to be. The reported
# case was `cd ` answering `cd ..` on a history holding ten visits to a project
# directory and four moves up.
from tai.paths import destinations_first, is_destination

for move in ("cd ..", "cd .", "cd -", "cd ./", "cd ../", "pushd .."):
    assert not is_destination(move), f"{move!r} names no place"
for place in ("cd /tmp", "cd ~", "cd projects", "cd ../sibling", "cd ..foo",
              "cd", "ls ..", "git status", "cd --"):
    assert is_destination(place), f"{place!r} is a destination or not a cd"

# The rule ranks; it never removes. Both moves survive, in their own order, below
# the lowest-scoring destination, and the gap between them and the destination
# above them is real rather than left to the sort.
RANKED_IN = [(900, "cd /best"), (500, "cd /mid"), (800, "cd .."), (700, "cd -"),
             (100, "ls -la")]
out = destinations_first(RANKED_IN)
order = [cmd for _, cmd in out]
assert order.index("cd /mid") < order.index("cd ..") < order.index("cd -"), order
assert set(order) == {c for _, c in RANKED_IN}, "a move must never be dropped"
scores = {cmd: score for score, cmd in out}
assert scores["cd /mid"] > scores["cd .."] > scores["cd -"], scores
assert scores["cd .."] == scores["cd -"] + 1, "the block is stepped, not tied"
# A history with no destination at all is left alone: the moves are the whole
# answer there, and ranking them below nothing would invent an order.
moves_only = destinations_first([(900, "cd .."), (800, "cd -")])
assert [c for _, c in moves_only] == ["cd ..", "cd -"], moves_only
assert {c: s for s, c in moves_only} == {"cd ..": 900, "cd -": 800}, \
    "an untouched history keeps the scores it had"
# A non-`cd` line that looks like a move is not one of these.
assert [c for _, c in destinations_first([(900, "ls .."), (100, "ls -la")])] == \
    ["ls ..", "ls -la"]

# `purge --stale` deletes. Judging a row against the directory the user happens
# to be standing in condemns every command imported from a history file, because
# those carry no cwd at all — so `python3 src/main.py`, typed in a project that
# still exists, was destroyed by running the purge from $HOME. No fallback cwd.
from tai.paths import stale_commands

rows = [("python3 src/main.py", {""}), ("cd /nowhere/gone", {""}),
        ("date 1/2/2024", {""}), ("git status", {REPO})]
without = stale_commands(rows)
with_cwd = stale_commands(rows, REPO)
assert without == {"cd /nowhere/gone"}, without
# A command with no path in it at all is never stale, whatever directory it was
# run from. `is_stale` used to be a second, test-only spelling of this question;
# the callers all use `stale_commands`, and the two could disagree.
assert stale_commands([("git status", {REPO})]) == set()
assert stale_commands([("cd ~/projects/myapp", {"/home/u"})]) == {"cd ~/projects/myapp"}
assert "python3 src/main.py" in with_cwd, "the fallback is what makes purge unsafe"
# A *relative* path in a row with no recorded cwd has no evidence at all, so it
# must never be condemned on any reading. The fallback turns "no evidence" into
# a verdict in *either* direction — rescuing a real path and condemning an absent
# one — which is why it may gate a suggestion and must never gate a delete.
for cmd in ("code tai/engine.py", "code nope/missing.py", "python3 src/main.py",
            "date 1/2/2024"):
    assert stale_commands([(cmd, set())]) == set(), f"{cmd!r} has no evidence to judge"
# An absolute path is its own evidence, so it is judged, and judged correctly,
# with no cwd at all — which is the one case a purge can still act on safely.
assert stale_commands([("cd /nowhere/gone", set())]) == {"cd /nowhere/gone"}
assert stale_commands([("cd /nowhere/gone", set())], REPO) == {"cd /nowhere/gone"}
assert stale_commands([("code tai/engine.py", set())], REPO) == set(), \
    "the fallback rescues a command that is real where the suggestion is shown"
# A `~word` token is git's revision syntax as often as it is a home directory.
# `expanduser` hands `~main` back unchanged because no user called `main` exists,
# `exists` then says no, and MISSING is the only verdict that removes a command —
# so `tai purge --stale` deleted real history. `git diff ~main`, `git log ~HEAD`
# and `git rebase ~origin/main` are everyday commands with no missing path at
# all. The account has to exist for the token to be judged, and `~` and `~/…`
# must keep being judged, or the fix would just move the bug.
for cmd in ("git diff ~main", "git log ~HEAD", "git merge ~feature",
            "git rebase ~origin/main", "git checkout ~wip", "make ~build"):
    assert stale_commands([(cmd, {REPO})]) == set(), \
        f"{cmd!r} is a revision, not a missing path, and was being deleted"
assert stale_commands([("cd ~/projects/myapp", {"/home/u"})]) == {"cd ~/projects/myapp"}, \
    "a real ~ path is still judged"
assert stale_commands([("cd ~", {REPO})]) == set(), "bare ~ is the home directory"
from tai.paths import is_home_ref, is_path_like
assert is_home_ref("~") and is_home_ref("~/x"), "~ and ~/x are home references"
for tok in ("~main", "~HEAD", "~origin/main"):
    assert not is_path_like(tok), f"{tok!r} must not be read as a path"
assert stale_commands([("code nope/missing.py", set())], REPO) != set(), \
    "and the same fallback condemns one that is not — the risk being managed"
# The row stays in SQLite and only leaves the index, which is why the rule can be
# this sure: create the directory and `tai refresh` brings the command back.
assert stale_commands([("cd ter", {REPO})], REPO) == {"cd ter"}, \
    "a cd to a directory that was never there is not a suggestion for its prefix"
assert stale_commands([("cd ter", set())], REPO) != set(), \
    "with no recorded directory a guess is all there is, and a guess only hides"
print(f"OK — path liveness: {len(VERDICTS)} verdicts, unknown never rejects.")

# Which lines end in a file, by what the command means. This is the fact the
# shell plugins need before they will read the filesystem for an answer, and it
# has to be decidable from the command string alone: an imported history row
# carries no directory to check a name against, so `chmod +x script` says nothing
# about `script` except that chmod was followed by something.
from tai.paths import takes_file

FILE_LINES = [
    # The reported case. `script` is a bare name that no shape test can read, and
    # a mode is not a flag — so this is only true because of what chmod means.
    ("chmod +x script", True),
    ("chmod 644 file", True),
    ("chmod --reference=ref file", True),
    ("chown me file", True),
    ("cp -r source destination", True),
    ("mv a b", True),
    (". ~/.venv/bin/activate", True),
    ("cat notes.txt", True),
    ("cat -n notes.txt", True),
    ("tail -n 20 /etc/hosts", True),
    # A flag is never a file.
    ("chmod --help", False),
    ("ls -la", False),
    ("docker ps", False),
    ("git commit -m message", False),
    # A non-flag before the last word means the last word is not the argument:
    # `manage.py` is a script, `runserver` is a subcommand, and neither is a file.
    ("python3 manage.py runserver", False),
    ("cat a b", False),
    # Nothing a static reader cannot resolve is no evidence at all.
    ('bash -c "echo hi"', False),
    ("rm $f", False),
    ("cat *.py", False),
    ("cat", False),
    ("", False),
]
for cmd, want in FILE_LINES:
    got = takes_file(cmd)
    assert got == want, f"{cmd!r}: got {got}, want {want}"
print(f"OK — file arguments: {len(FILE_LINES)} lines, the shape is the command's.")

# The filesystem half, on a scratch tree so the developer's own files are never
# what is being ranked. Newest first is the entire claim: the file you just
# downloaded has to beat one you used last month.
from tai.fresh import files, roots

FRESH_HOME = pathlib.Path("/tmp/tai/tai_fresh_home")
FRESH_WORK = pathlib.Path("/tmp/tai/tai_fresh_work")
for stale in (FRESH_HOME, FRESH_WORK):
    if stale.exists():
        import shutil
        shutil.rmtree(stale)
(FRESH_HOME / "Downloads").mkdir(parents=True)
FRESH_WORK.mkdir(parents=True)
old = FRESH_WORK / "notes.txt"
old.write_text("x")
older = FRESH_HOME / "Downloads" / "an-older-download.tar.gz"
older.write_text("x")
now = time.time()
os.utime(old, (now - 86400, now - 86400))
os.utime(older, (now - 2 * 86400, now - 2 * 86400))
fresh = FRESH_HOME / "Downloads" / "freebuff-0.0.154-linux-x86_64.AppImage"
fresh.write_text("x")
os.utime(fresh, (now, now))
os.environ["TAI_FILE_ROOTS"] = str(FRESH_HOME / "Downloads")
# HOME moved too, so the `~/…` form of the answer is exercised: it is the form a
# line should hold, and it only appears for a root under the home directory.
# HOME is moved and restored explicitly. `expanduser("~")` reads HOME, so
# restoring it that way would put back the scratch directory and every later
# assertion about *your* home would quietly be about this one.
REAL_HOME = os.environ.get("HOME", os.path.expanduser("~"))
os.environ["HOME"] = str(FRESH_HOME)
try:
    from tai import fresh as fresh_mod

    saved = fresh_mod.DOWNLOAD_DIRS
    fresh_mod.DOWNLOAD_DIRS = ()          # only the root this test set
    try:
        got = files("", cwd=str(FRESH_WORK))
        # Newest first, across roots: the file just downloaded beats the file in
        # the current directory, which beats the older download.
        assert got == [f"~/Downloads/{fresh.name}", "notes.txt",
                       f"~/Downloads/{older.name}"], got
        # A word narrows it, and the answer is written the way it was typed.
        assert files("free", cwd=str(FRESH_WORK)) == [f"~/Downloads/{fresh.name}"]
        assert files("~/Downloads/f", cwd=str(FRESH_WORK)) == \
            [f"~/Downloads/{fresh.name}"]
        assert files("~/Downloads", cwd=str(FRESH_WORK)) == \
            [f"~/Downloads/{fresh.name}", f"~/Downloads/{older.name}"]
        # A word nothing matches gets nothing, rather than the newest thing on
        # disk: a completion has to extend what was typed.
        assert files("nope", cwd=str(FRESH_WORK)) == []
        # The cap is on *recency*, not on the order the glob returned. Every
        # root above held two files, so the cap never bit and the bug was
        # invisible: `sorted(glob(...))[:PER_ROOT]` is alphabetical, so a
        # downloads folder with more than PER_ROOT files kept the alphabetically
        # first ones and dropped the file that had just been downloaded — which
        # is the one and only thing this feature exists to offer. Named `zzz` on
        # purpose: alphabetical order puts it last, recency puts it first.
        many = FRESH_HOME / "Downloads3"
        many.mkdir(exist_ok=True)
        # Its own root, because subdirectories are deliberately not scanned and
        # the question here is what happens when a root is *full*.
        os.environ["TAI_FILE_ROOTS"] = str(many)
        for i in range(fresh_mod.PER_ROOT + 4):
            old_one = many / f"aaa{i:02d}.mp4"
            old_one.write_text("x")
            # One second apart, `aaa00` the oldest: equal mtimes would leave the
            # cap's cut to the filesystem's listing order, and then this test
            # passes on one machine and fails on another for no code reason.
            os.utime(old_one, (now - 30 * 86400 + i, now - 30 * 86400 + i))
        newest = many / "zzz-just-downloaded.AppImage"
        newest.write_text("x")
        os.utime(newest, (now, now))
        crowded = files("", cwd=str(FRESH_WORK))
        newest_name = "~/Downloads3/zzz-just-downloaded.AppImage"
        assert newest_name in crowded, \
            f"the newest file in a full root was dropped: {crowded}"
        # Ranked first among that root's entries, and the cap kept the newest
        # few: `aaa00` is the *oldest* of the filler, so it is the one the cap
        # is expected to drop — and dropping it is what "newest first" means.
        assert crowded[0] == newest_name, f"not ranked first: {crowded}"
        from tai.fresh import PER_ROOT
        kept = [c for c in crowded if "/Downloads3/" in c]
        assert len(kept) == PER_ROOT, f"the cap stopped bounding the answer: {crowded}"
        assert not any(c.endswith(f"aaa{i:02d}.mp4") for i in range(4) for c in crowded), \
            f"an oldest file survived a cap meant to keep the newest: {crowded}"
        # Put the root list back: the assertions below are about the default
        # roots, and an environment variable left pointing at a scratch
        # directory would make them pass or fail for the wrong reason.
        os.environ["TAI_FILE_ROOTS"] = str(FRESH_HOME / "Downloads")
        # Roots in the documented order: here first, then what a download lands in.
        assert roots(cwd=str(FRESH_WORK)) == [
            (str(FRESH_WORK), ""), (str(FRESH_HOME / "Downloads"), "~/Downloads/")], \
            roots(cwd=str(FRESH_WORK))
        # Standing in the home directory must not offer the same file twice, once
        # as `Downloads/x` and once as `~/Downloads/x`.
        assert roots(cwd=str(FRESH_HOME)) == [
            (str(FRESH_HOME), ""), (str(FRESH_HOME / "Downloads"), "~/Downloads/")], \
            roots(cwd=str(FRESH_HOME))
        assert files("", cwd=str(FRESH_HOME)) == [f"~/Downloads/{fresh.name}",
                                                  f"~/Downloads/{older.name}"], \
            files("", cwd=str(FRESH_HOME))
        # Subdirectories are not roots. It is what keeps a fresh `__pycache__`
        # entry out of `chmod +x `, and the reported case does not need it: the
        # download lands in ~/Downloads, which is a root in its own right.
        assert files("", cwd=str(FRESH_WORK)) == [f"~/Downloads/{fresh.name}",
                                                  "notes.txt",
                                                  f"~/Downloads/{older.name}"], \
            files("", cwd=str(FRESH_WORK))
        # A subdirectory of the working directory is *not* a root: it is what
        # keeps a fresh `__pycache__` entry out of `chmod +x `, and the reported
        # case never needed it — a download lands in ~/Downloads, which is a root.
        (FRESH_WORK / "artifacts").mkdir()
        (FRESH_WORK / "artifacts" / "built.tgz").write_text("x")
        os.utime(FRESH_WORK / "artifacts" / "built.tgz", (now + 60, now + 60))
        assert files("", cwd=str(FRESH_WORK)) == [f"~/Downloads/{fresh.name}",
                                                  "notes.txt",
                                                  f"~/Downloads/{older.name}"], \
            files("", cwd=str(FRESH_WORK))
    finally:
        fresh_mod.DOWNLOAD_DIRS = saved
finally:
    del os.environ["TAI_FILE_ROOTS"]
    os.environ["HOME"] = REAL_HOME
print("OK — fresh files: newest first, in the roots the docs name.")

# The engine stays a pure in-memory ranker: it applies no filesystem policy of
# its own, it drops what the caller excludes. That split is what keeps suggest
# under a millisecond, and the one-shot path is where the policy lives. It
# matters because the *index* ranks on frequency alone — that is the surface the
# stale command actually won on, and it has no length or token signal to save it.
STALE = "cd ~/projects/myapp"
eng3 = Engine()
for cmd, cwd in ((STALE, "/home/u"), ("cd ..", REPO), ("ls -la", REPO), ("ls -l", REPO)):
    for _ in range(30):
        eng3.add(cmd, cwd=cwd)
assert STALE in [c["cmd"] for c in eng3.suggest("cd ", limit=3)["choices"]], \
    "the engine must not decide path policy on its own"
assert STALE not in [c["cmd"] for c in
                     eng3.suggest("cd ", limit=3, exclude={STALE})["choices"]], \
    "an excluded command must not be suggested"
# A candidate equal to the typed text is not a suggestion: it cannot be shown,
# and letting it win hides the candidate that would have extended the line.
assert eng3.suggest("ls -l", limit=3)["choice"] == "ls -la", eng3.suggest("ls -l", limit=3)
assert eng3.suggest("ls -la", limit=3)["choice"] != "ls -la", "an echo is not an answer"

# The reported case, at the ranker: `cd ` answered `cd ..` while the history said
# where the user actually goes. `..` is true in every directory, so its frequency
# is not evidence about a destination — it has to lose to one, and stay on offer.
eng4 = Engine()
for cmd, n in (("cd ..", 40), ("cd -", 40), ("cd /home/u/work/app", 3)):
    for _ in range(n):
        eng4.add(cmd, cwd="/home/u")
picks = [c["cmd"] for c in eng4.suggest("cd ", limit=3)["choices"]]
assert picks[0] == "cd /home/u/work/app", \
    f"`cd ` must answer with a destination, got {picks}"
assert "cd .." in picks and "cd -" in picks, \
    f"a move is demoted, never removed, got {picks}"
# Once the move is what the word has grown into, it is the only thing that
# extends the line, and it answers — the demotion is about ranking, not hiding.
assert eng4.suggest("cd .", limit=3)["choice"] == "cd ..", eng4.suggest("cd .")
print("OK — stale paths excluded by the caller; an echo never wins.")
print("OK — `cd ` answers with a destination; `cd ..` is ranked, not removed.")

# A one-off that is one edit away from a habit is a typo of it, and it ranks
# just below the habit instead of beside it. The reported case: one accidental
# `tai sintall` haunted the `tai ` hint forever, because one run scored like
# one run. The index builder demotes the typo under the command it shadows;
# nothing is deleted, and a typo with no more-frequent neighbour is left alone.
from tai.index import build  # noqa: E402
from tai.store import append_and_count  # noqa: E402

for cmd, n in (("tai install", 6), ("tai uninstall", 4), ("tai sintall", 1),
               ("git status", 5), ("git stash", 1)):
    for _ in range(n):
        ok, _total, _newest = append_and_count(cmd)
        assert ok, cmd
built = build()
assert built >= 5, built
scores = {}
for line in pathlib.Path(SCRATCH_INDEX).read_text().splitlines():
    if line.startswith("_TAI_SCORE+=("):
        # _TAI_SCORE+=('cmd' 1234)
        head = line[len("_TAI_SCORE+=("):-1]
        cmd, _, num = head.rpartition(" ")
        scores[cmd.strip("'")] = int(num)
assert scores["tai sintall"] < scores["tai install"], \
    (scores["tai sintall"], scores["tai install"])
assert scores["tai sintall"] < scores["tai uninstall"]
assert scores["git stash"] > 0, "an unrelated one-off is not demoted"
# The habit itself is never demoted for shadowing something rarer.
assert scores["tai install"] > scores["tai sintall"]
print("OK — a one-off typo ranks below the habit it shadows, and stays indexed.")

# The report that reopened the rule: `tai unsintall` and `tai unisntall`
# recorded beside `tai uninstall`, each once, within the same second, every
# row exit 0 — frequency, recency and success all tied, the score tie fell
# to lexical order, and the ghost offered `tai unsintall` for `tai un`.
# tai/typo.py's shadow_map resolves that: the strongest spelling stands,
# and a full evidence tie falls to the smallest spelling, which here is the
# right one. Both rankers — the engine below, the index above — share it.
from tai.engine import Engine  # noqa: E402
from tai.typo import shadow_map  # noqa: E402

eng = Engine()
for cmd in ("tai unsintall", "tai unisntall", "tai uninstall"):
    eng.add(cmd, ts=1_700_000_000)
sh = shadow_map(eng)
assert sh.get("tai unsintall") == "tai uninstall", sh
assert sh.get("tai unisntall") == "tai uninstall", sh
assert "tai uninstall" not in sh, "the real spelling is nobody's shadow"
r = eng.suggest("tai un")
assert r["choice"] == "tai uninstall", r["choices"]

# Success is evidence too: a spelling that never worked is a shadow of one
# that did, even when the working one is the older line.
eng2 = Engine()
eng2.add("tai unsintall", exit_code=2, ts=1_700_000_000)
eng2.add("tai uninstall", exit_code=0, ts=1_700_000_000 - 86400)
assert eng2.suggest("tai un")["choice"] == "tai uninstall"

# Two edits is the bound because that is what a real typo spans; three is
# a different word, and a different word is nobody's shadow.
eng3 = Engine()
eng3.add("git stash", ts=1_700_000_000)
eng3.add("git status", ts=1_700_000_000)
assert shadow_map(eng3) == {}, shadow_map(eng3)
print("OK — `tai un` answers `tai uninstall` even when every signal ties.")

# The report behind the length penalty: `go mod download` recorded three times
# a few days back and `go mod vendor` twice more recently. The index's
# milli-scores put the download at the head of the `go` key — the line the
# ghost and the Tab menu read — while `tai suggest` and the dashboard put the
# shorter, fresher vendor first: the two rankers disagreed about exactly the
# lines a one-word question has to choose between, and the user's hint named a
# line their own dashboard did not. The engine's length penalty is what
# separates the two, and its difference between two candidates is
# (len(a) − len(b)) / 60 whatever prefix it is asked with, so the builder bakes
# it in: the same ordering as the engine for every query, and pure seeds keep
# their corpus band below even the weakest, longest real command.
import re as _re  # noqa: E402
import time as _time  # noqa: E402
from tai.seed import SEED_COMMANDS  # noqa: E402
from tai.store import session  # noqa: E402

_now = int(_time.time())
# The ages are chosen so the download's frequency advantage over the vendor
# (+0.284 base) outweighs the vendor's recency advantage by only ~0.02 — a
# gap the penalty's 0.033 difference between the two lines flips. Without the
# baked penalty this assertion fails by 20 milli, which is the whole point.
with session() as _con:
    for _cmd, _ts in (
        ("go mod vendor", _now - 76117),
        ("go mod vendor", _now - 76117 - 5),
        ("go mod download", _now - 259200),
        ("go mod download", _now - 259200 - 60),
        ("go mod download", _now - 259200 - 120),
        ("godot .", _now - 1800),
        ("got log", _now - 43200),
    ):
        _con.execute(
            "INSERT INTO commands(cmd, cwd, exit_code, ts) VALUES (?, '', 0, ?)",
            (_cmd, _ts))
    _con.commit()         # session() does not commit; a raw insert says so
build()
_scores = {}
for _line in pathlib.Path(SCRATCH_INDEX).read_text().splitlines():
    if _line.startswith("_TAI_SCORE+=("):
        _head = _line[len("_TAI_SCORE+=("):-1]
        _cmd, _, _num = _head.rpartition(" ")
        _scores[_cmd.strip("'")] = int(_num)
assert _scores["go mod vendor"] > _scores["go mod download"], _scores
_go = _re.search(r"_TAI_FIRST\+=\('go' '([^']*)'\)",
                 pathlib.Path(SCRATCH_INDEX).read_text(), _re.S)
assert _go, "the go key is written"
assert _go.group(1).split("\n")[0] == "go mod vendor", _go.group(1)
_real_scores = [s for c, s in _scores.items() if c not in set(SEED_COMMANDS)]
from tai.index import SEED_RANK_STEP  # noqa: E402
# The band invariant: a pure seed's ceiling is n_seeds × step, and every real
# command — even one-run, long-ago, failed, and long enough to pay the full
# length penalty — scores above it. Recorded seeds are not in the band; they
# are history, and score like it.
assert _real_scores and min(_real_scores) > len(SEED_COMMANDS) * SEED_RANK_STEP, \
    min(_real_scores)
print("OK — the index carries the engine's length penalty; the head of `go` "
      "is the line the dashboard ranks first.")

# A record reaches the index without waiting a hundred records: when the index
# on disk is older than the newest row, past a short debounce, the background
# rebuild runs — and when the index is fresh, it does not.
import os as _os  # noqa: E402
from tai.cli import _rebuild_when_stale  # noqa: E402

idx_path = pathlib.Path(SCRATCH_INDEX)
idx_path.write_text("# tai embedded zsh index; generated, do not edit\nSENTINEL\n")
old = time.time() - 60
os.utime(idx_path, (old, old))
_rebuild_when_stale()
rebuilt = idx_path.read_text()
assert "SENTINEL" not in rebuilt and rebuilt.startswith("# tai embedded zsh index"), \
    "a stale index must be rebuilt"
_rebuild_when_stale()
assert idx_path.read_text() == rebuilt, "a fresh index must be left alone"
print("OK — a record reaches the index on the next prompt, not a hundred later.")

# The report that shaped two rules: typing `openc` offered `opencoe` — a typo
# of `opencode` — above `opencode` itself, six lines within a hair of one
# another, every gap the length penalty's own step. Two things were missing
# from the ranker's world: a line the shell refused outright (exit 127) is not
# a command, and a tool the machine has is worth more than a word it has not.
# Both rules live in both rankers — the engine here, the index further down.
_T = 1_800_000_000
eng_nf = Engine()
eng_nf.add("opencode", exit_code=0, ts=_T - 3 * 86400)
eng_nf.add("opencode", exit_code=0, ts=_T - 2 * 86400)
eng_nf.add("opencoe", exit_code=127, ts=_T - 60)
eng_nf.add("opencoode", exit_code=127, ts=_T - 120)
_names = [c["cmd"] for c in eng_nf.suggest("openc", now_ts=_T, limit=6)["choices"]]
assert "opencoe" not in _names and "opencoode" not in _names, _names
assert _names[:1] == ["opencode"], _names
# A line that ran and failed is still a line: only the shell's own refusal
# hides one. `opencode auth` failing once is evidence, not absence.
eng_nf.add("opencode auth", exit_code=1, ts=_T - 30)
assert "opencode auth" in [c["cmd"] for c in
                           eng_nf.suggest("opencode ", now_ts=_T, limit=6)["choices"]]
# And typing the phantom itself is answered by the real spelling — typo
# tolerance keeps its direction; the phantom simply cannot be the answer.
assert eng_nf.suggest("opencoe", now_ts=_T, limit=6)["choice"] == "opencode"
print("OK — a line the shell never ran is nobody's suggestion.")

# The user's recorded world, reproduced: six lines, one run each, every row
# exit 0, minutes apart — frequency, recency, success and directory all tied,
# so the freshest line won and the freshest line was the typo. Without the
# installed question the answer is the report verbatim; that shape is pinned
# so no future change to the signal-less path lands silently.
eng_w = Engine()
for _c, _ts in (("opencode2 pair", _T - 4 * 3600), ("opencode auth", _T - 3 * 3600),
                ("opencode web", _T - 2 * 3600), ("opencoode", _T - 5400),
                ("opencode", _T - 3600), ("opencoe", _T)):
    eng_w.add(_c, cwd="/w", exit_code=0, ts=_ts)
_mirrored = [c["cmd"] for c in
             eng_w.suggest("openc", cwd="/w", now_ts=_T, limit=6)["choices"]]
assert _mirrored[:1] == ["opencoe"], _mirrored
# With the question asked, the tool the machine has outranks the words it has
# not — and the one-shot path asks it through the same engine, from a cache
# one `which` per word per process.
_fixed = [c["cmd"] for c in eng_w.suggest("openc", cwd="/w", now_ts=_T, limit=6,
                                          on_path={"opencode"}.__contains__)["choices"]]
assert _fixed[:1] == ["opencode"], _fixed
assert _fixed.index("opencode") < _fixed.index("opencoe"), _fixed
assert _fixed.index("opencode") < _fixed.index("opencoode"), _fixed
# The panel's why: same question, same answer, same total — and the factor
# named, so the number on screen is one a reader can check.
_res = eng_w.suggest("openc", cwd="/w", now_ts=_T, limit=6,
                     on_path={"opencode"}.__contains__)
_exp = eng_w.explain("openc", "opencode web", cwd="/w", now_ts=_T,
                     on_path={"opencode"}.__contains__)
_sc = next(c["score"] for c in _res["choices"] if c["cmd"] == "opencode web")
assert abs(_exp["score"] - _sc) < 0.002, (_exp["score"], _sc)
assert any(f["label"] == "installed command" and f["value"] == 1.0
           for f in _exp["factors"]), _exp["factors"]
_miss = eng_w.explain("openc", "opencoe", cwd="/w", now_ts=_T,
                      on_path={"opencode"}.__contains__)
assert any(f["label"] == "installed command" and f["value"] == 0.0
           for f in _miss["factors"]), _miss["factors"]
import tai.predictor as _pred  # noqa: E402
eng_p = Engine()
for _c, _ts in (("opencode2 pair", _T - 4 * 3600), ("opencode web", _T - 2 * 3600),
                ("opencoode", _T - 5400), ("opencode", _T - 3600), ("opencoe", _T)):
    eng_p.add(_c, cwd="/w", exit_code=0, ts=_ts)
_saved_E, _saved_STALE, _saved_WHICH = _pred._E, _pred._STALE, dict(_pred._WHICH)
try:
    _pred._E, _pred._STALE = eng_p, frozenset()
    _pred._WHICH.update({"opencode": True, "opencoe": False,
                         "opencoode": False, "opencode2": False})
    _names = [c["cmd"] for c in
              _pred.suggest("openc", cwd="/w", limit=6)["choices"]]
    assert _names[:1] == ["opencode"], _names
finally:
    _pred._E, _pred._STALE = _saved_E, _saved_STALE
    _pred._WHICH.clear()
    _pred._WHICH.update(_saved_WHICH)
print("OK — an installed command outranks an uninstalled word, all else equal.")

# The snapshot the plugins read carries the same two rules: the phantom is
# nowhere in it — not in the scores, not as a first-word key, not as a
# sequence entry — while the line that ran stays publishable.
_now2 = int(time.time())
with session() as _con:
    for _cmd, _code, _ts in (("opencoe", 127, _now2 - 60),
                             ("echo gone-typo", 127, _now2 - 50),
                             ("echo kept", 0, _now2 - 40)):
        _con.execute(
            "INSERT INTO commands(cmd, cwd, exit_code, ts) VALUES (?, '', ?, ?)",
            (_cmd, _code, _ts))
    _con.commit()         # session() does not commit; a raw insert says so
build()
_text = pathlib.Path(SCRATCH_INDEX).read_text()
assert "'opencoe'" not in _text, "a command-not-found line is not published"
assert "'echo gone-typo'" not in _text, "every-127 lines are phantoms, whatever the word"
assert "'echo kept'" in _text
print("OK — the index does not publish a line the shell never ran.")

# The poisoned sequel to that world: the typo recorded MORE runs than the
# tool — not-found refusals some install kept as successes — and nothing in
# the ranker can un-know them. This is the shape the panel showed the user
# (`opencoe` first, `opencode` behind it), pinned so the day the arithmetic
# changes nobody wonders whether the report was ever real. The cure is not
# a ranking rule: poisoned rows are indistinguishable from successes, so
# `tai forget opencoe` is how they leave — and the installed question is
# what the panel has left while the rows are still there. Everything about
# the two candidates is equal here except the run count and the bonus:
# without the question the busier spelling wins, with it the tool does.
eng_po = Engine()
eng_po.add("openc", cwd="/w", exit_code=0, ts=_T - 300)
eng_po.add("openc", cwd="/w", exit_code=0, ts=_T - 240)
eng_po.add("openc", cwd="/w", exit_code=0, ts=_T - 180)
eng_po.add("opencoe", cwd="/w", exit_code=0, ts=_T - 120)
eng_po.add("opencoe", cwd="/w", exit_code=0, ts=_T - 60)
eng_po.add("opencode", cwd="/w", exit_code=0, ts=_T - 30)
_po_noq = [c["cmd"] for c in
           eng_po.suggest("openc", cwd="/w", now_ts=_T, limit=6)["choices"]]
assert _po_noq[:1] == ["opencoe"], _po_noq
_po_q = [c["cmd"] for c in
         eng_po.suggest("openc", cwd="/w", now_ts=_T, limit=6,
                        on_path={"opencode"}.__contains__)["choices"]]
assert _po_q[:1] == ["opencode"], _po_q
assert _po_q.index("opencode") < _po_q.index("opencoe"), _po_q
print("OK — a poisoned history is pinned as poisoned; forget is the cure, "
      "the installed question the stopgap.")

# --- Store edges: the parts of the gate and the migration no CLI run hits ---
#
# Everything above asks the store through `is_recordable` and the CLI. These
# sections hold the pieces a user never calls directly and a bug in them would
# be invisible for months: the schema migration an old install needs, the
# semantics of append/purge/forget/load, and the rule that a rejection reports
# the store it left behind rather than a count of zero.

import sqlite3 as _sql
from tai.store import (append_and_count, forget_commands, load_rows,
                       purge_stale, purge_unrecordable)

def _section_db(name: str) -> str:
    """A scratch store for one section, swapped in and out via TAI_DB.

    `db_path()` reads the environment at call time, so the swap is the whole
    mechanism. Each section gets its own file: a row a section seeded must
    never be the row the next section finds.
    """
    p = pathlib.Path("/tmp/tai") / name
    for suffix in ("", "-wal", "-shm"):
        p.with_name(p.name + suffix).unlink(missing_ok=True)
    return str(p)

_saved_db = os.environ["TAI_DB"]

# An install whose schema predates the branch column must open, migrate, and
# keep its rows: the ALTER is the one line between an old history and a store
# every reader refuses.
_old_db = _section_db("tai_edge_old_schema.db")
con = _sql.connect(_old_db)
con.execute("CREATE TABLE commands(id INTEGER PRIMARY KEY, cmd TEXT NOT NULL, "
            "cwd TEXT DEFAULT '', repo TEXT DEFAULT '', exit_code INTEGER DEFAULT 0, "
            "ts INTEGER DEFAULT 0)")
con.execute("INSERT INTO commands(cmd, cwd, ts) VALUES('legacy-echo', '/legacy', 42)")
con.commit()
con.close()
os.environ["TAI_DB"] = _old_db
try:
    from tai.store import connect as _connect
    con = _connect()
    cols = [r[1] for r in con.execute("PRAGMA table_info(commands)")]
    rows = con.execute("SELECT cmd, branch FROM commands").fetchall()
    con.close()
    assert "branch" in cols, cols
    assert rows == [("legacy-echo", "")], rows
finally:
    os.environ["TAI_DB"] = _saved_db
print("OK — an old-schema store migrates in place and keeps its rows.")

# append_and_count: a refusal is an answer about the store, not a shrug — the
# count that comes back is the count the store still has, so `tai record`'s
# maintenance decisions are made on the truth. A Path argument for cwd is an
# ordinary thing for a caller to pass; label fields are bounded, evidence is not.
_rows_db = _section_db("tai_edge_append.db")
os.environ["TAI_DB"] = _rows_db
try:
    ok, total, _ = append_and_count("echo one", cwd="/w")
    assert (ok, total) == (True, 1), (ok, total)
    ok, total, newest = append_and_count("y")
    assert ok is False and total == 1, (ok, total)
    assert newest == 0, newest
    ok, total, _ = append_and_count("echo two", cwd=pathlib.Path("/tmp"),
                                    repo="r" * 300, branch="b" * 300)
    assert (ok, total) == (True, 2), (ok, total)
    con = _sql.connect(_rows_db)
    got = con.execute("SELECT cwd, repo, branch FROM commands ORDER BY id").fetchall()
    con.close()
    assert got == [("/w", "", ""), ("/tmp", "r" * 200, "b" * 200)], got
finally:
    os.environ["TAI_DB"] = _saved_db
print("OK — append: the refusal counts what it kept, evidence stays whole.")

# purge_unrecordable takes out what an older install stored under rules that
# have since tightened, and touches nothing else.
_purge_db = _section_db("tai_edge_purge.db")
os.environ["TAI_DB"] = _purge_db
try:
    con = _sql.connect(_purge_db)
    con.execute("CREATE TABLE commands(id INTEGER PRIMARY KEY, cmd TEXT NOT NULL, "
                "cwd TEXT DEFAULT '', repo TEXT DEFAULT '', branch TEXT DEFAULT '', "
                "exit_code INTEGER DEFAULT 0, ts INTEGER DEFAULT 0)")
    con.executemany("INSERT INTO commands(cmd, cwd, ts) VALUES(?, '/w', 1)",
                    [("keep me",), ("keep me too",), ("y",),
                     ('git commit -m "a\nb"',), ("curl -H 'token: x' https://s",)])
    con.commit()
    con.close()
    assert purge_unrecordable() == 3
    con = _sql.connect(_purge_db)
    left = [r[0] for r in con.execute("SELECT cmd FROM commands ORDER BY id")]
    con.close()
    assert left == ["keep me", "keep me too"], left
    assert purge_unrecordable() == 0, "an empty second sweep is a zero, not a crash"
finally:
    os.environ["TAI_DB"] = _saved_db
print("OK — purge_unrecordable: exactly the rows the gate now refuses.")

# purge_stale deletes on the recorded evidence alone. A row with a path that
# is gone is deleted; a command with no path in it is never stale; a relative
# path in a row whose own directory is gone has no evidence and is kept —
# the delete needs the same conservative verdict the suggestion gets.
_stale_db = _section_db("tai_edge_stale.db")
os.environ["TAI_DB"] = _stale_db
try:
    con = _sql.connect(_stale_db)
    con.execute("CREATE TABLE commands(id INTEGER PRIMARY KEY, cmd TEXT NOT NULL, "
                "cwd TEXT DEFAULT '', repo TEXT DEFAULT '', branch TEXT DEFAULT '', "
                "exit_code INTEGER DEFAULT 0, ts INTEGER DEFAULT 0)")
    con.executemany("INSERT INTO commands(cmd, cwd, ts) VALUES(?, ?, 1)",
                    [("cd /nonexistent-tai-edge-dir", "/w"),
                     ("echo live", ""),
                     ("cat /etc/hosts", ""),
                     ("python3 src/main.py", "/nonexistent-tai-edge-dir")])
    con.commit()
    con.close()
    assert purge_stale() == 1
    con = _sql.connect(_stale_db)
    left = [r[0] for r in con.execute("SELECT cmd FROM commands ORDER BY id")]
    con.close()
    assert left == ["echo live", "cat /etc/hosts", "python3 src/main.py"], left
    # The switch the doctor names: a user who wants the rows back off entirely.
    os.environ["TAI_SKIP_PATH_CHECK"] = "1"
    try:
        assert purge_stale() == 0
    finally:
        os.environ.pop("TAI_SKIP_PATH_CHECK", None)
finally:
    os.environ["TAI_DB"] = _saved_db
print("OK — purge_stale: evidence deletes, no evidence never deletes.")

# forget: exact rows out, name deduplication, a prefix that is not a prefix.
_forget_db = _section_db("tai_edge_forget.db")
os.environ["TAI_DB"] = _forget_db
try:
    con = _sql.connect(_forget_db)
    con.execute("CREATE TABLE commands(id INTEGER PRIMARY KEY, cmd TEXT NOT NULL, "
                "cwd TEXT DEFAULT '', ts INTEGER DEFAULT 0)")
    con.executemany("INSERT INTO commands(cmd, cwd, ts) VALUES(?, '/w', 1)",
                    [("git",), ("git status",), ("git",), ("ls",)])
    con.commit()
    con.close()
    assert forget_commands([]) == 0
    assert forget_commands(["  "]) == 0
    assert forget_commands(["git", "git", " git "]) == 2, "the exact rows only"
    con = _sql.connect(_forget_db)
    left = [r[0] for r in con.execute("SELECT cmd FROM commands ORDER BY id")]
    con.close()
    assert left == ["git status", "ls"], left
    assert forget_commands(["git"]) == 0, "the second ask is honest silence"
finally:
    os.environ["TAI_DB"] = _saved_db
print("OK — forget: dedup, exactness, and silence that means zero rows.")

# load_rows keeps the most recent `limit` rows and returns them oldest-first,
# because the sequence map is built from the order the commands ran in.
_window_db = _section_db("tai_edge_window.db")
os.environ["TAI_DB"] = _window_db
try:
    for i in range(12):
        append_and_count(f"echo row{i:02d}", cwd="/w")
    got = load_rows(5)
    assert [r[0] for r in got] == [f"echo row{i:02d}" for i in range(7, 12)], got
    assert load_rows(5)[0][0] == "echo row07"
finally:
    os.environ["TAI_DB"] = _saved_db
print("OK — load_rows: the newest window, in the order it ran.")

# _history_files: a custom HISTFILE leads, the well-known names follow, and
# an override that repeats one of them must not import it twice.
os.environ["TAI_HISTORY_FILES"] = ""
os.environ.pop("HISTFILE", None)
_home = pathlib.Path.home()
_only = pathlib.Path("/tmp/tai/tai_edge_only_history")
_only.write_text("echo only\n")
os.environ["TAI_HISTORY_FILES"] = str(_only) + os.pathsep + str(_only)
from tai.store import _history_files
_got = _history_files(_home)
assert _got.count(_only) == 1, _got
assert _got[-1] == _only, "the override is read, once — after the known files"
os.environ["TAI_HISTORY_FILES"] = ""
print("OK — history sources are deduplicated before a single row is read.")

# --- The one-shot path: the file answer, the broken store, the why ---
#
# `_file_answer` is the rule that lets the filesystem win a path argument —
# but only on evidence, only when the learned answer is not already a live
# file, and only when the file actually extends the word being typed.
import tai.predictor as tai_predictor
from tai.fresh import files as _fresh_files

_tai_predictor_E = tai_predictor._E
_tai_predictor_S = tai_predictor._STALE

_files_dir = pathlib.Path("/tmp/tai/tai_edge_files")
_files_dir.mkdir(parents=True, exist_ok=True)
# The learned file exists: it wins. The history and the disk agree.
(_files_dir / "notes.txt").write_text("hello\n")
# A file the history never saw: the case the disk answer exists for — the
# download from a moment ago. The history's own backup.sh is not on disk, so
# it cannot win, and the live file that extends the word is the answer.
(_files_dir / "backup2.sh").write_text("#!/bin/sh\necho hi\n")
_eng = Engine()
_eng.add("cat notes.txt", cwd=str(_files_dir))
_eng.add("cat notes.txt", cwd=str(_files_dir))
_eng.add("chmod +x backup.sh", cwd=str(_files_dir))
_eng.add("chmod +x backup.sh", cwd=str(_files_dir))
tai_predictor._E = _eng
tai_predictor._STALE = frozenset()
try:
    _cwd = dict(tai_predictor.__dict__)  # noqa: F841  (readability only)
    os.chdir(_files_dir)
    _res = tai_predictor.suggest("cat ")
    assert _res["choice"] == "cat notes.txt", _res
    # A wrapper is transparent: the answer carries the wrapper the user typed.
    _res = tai_predictor.suggest("sudo cat ")
    assert _res["choice"] == "sudo cat notes.txt", _res
    # A word mid-typing is replaced, not appended after — and the answer is
    # the file that exists, not the one the history remembers.
    _res = tai_predictor.suggest("chmod +x ba")
    assert _res["choice"] == "chmod +x backup2.sh", _res
    assert _res["from_disk"], _res
    assert _res["completion"] == "ckup2.sh", _res
    # No evidence in the history that this verb takes a file: the ranking
    # stands, the filesystem is never asked.
    _res = tai_predictor.suggest("echo ")
    assert "from_disk" not in _res, _res
finally:
    os.chdir(str(REPO))
    tai_predictor._E = _tai_predictor_E
    tai_predictor._STALE = _tai_predictor_S
print("OK — the one-shot file answer: evidence, wrappers, word boundaries.")

# A store that cannot be read is announced and answered from the seed corpus —
# never an empty ranking, and never silence.
_garbage_db = _section_db("tai_edge_garbage.db")
pathlib.Path(_garbage_db).write_text("definitely not a database")
os.environ["TAI_DB"] = _garbage_db
os.environ["TAI_NO_ENGINE_CACHE"] = "1"
_saved_stderr = sys.stderr
import io as _io
_captured = _io.StringIO()
sys.stderr = _captured
try:
    tai_predictor._E = None
    tai_predictor._STALE = frozenset()
    _res = tai_predictor.suggest("git ")
finally:
    sys.stderr = _saved_stderr
    os.environ["TAI_DB"] = _saved_db
    os.environ.pop("TAI_NO_ENGINE_CACHE", None)
    tai_predictor._E = _tai_predictor_E
    tai_predictor._STALE = _tai_predictor_S
assert "cannot read the history database" in _captured.getvalue(), _captured.getvalue()
assert _res["choices"], "a broken store still answers from the seed corpus"
print("OK — a broken store says so, and answers from the seed vocabulary.")

# The panel's why on a line whose every run was command-not-found: the row
# scores nowhere, and the answer says so in words instead of leaving a number
# the reader cannot reconcile with an absent suggestion.
from tai.engine import hour_bucket

_ph = Engine()
for _ts in range(3):
    _ph.add("phantom-cmd --now", cwd="/w", exit_code=127, ts=_T - 1000 + _ts)
_ph.add("live-cmd --now", cwd="/w", exit_code=0, ts=_T - 500)
_ex = _ph.explain("phant", "phantom-cmd --now", cwd="/w", now_ts=_T)
assert _ex["phantom"] is True, _ex
assert any(f["label"] == "command not found" for f in _ex["factors"]), _ex
assert _ph.explain("phant", "never-was-typed") is None
assert hour_bucket(None) in (0, 1, 2, 3)
assert hour_bucket(10**20) == 0, "a timestamp nothing can render has no hour"
print("OK — the why names a phantom; unrenderable time has no hour.")

# The pre-filter: a first word with more candidates than the cap still answers
# correctly — the cache exists to keep it fast, not to change what wins.
_big = Engine()
for _i in range(700):
    _big.add(f"bulk task{_i:03d} --run", cwd="/w")
_big.add("bulk favorite --run", cwd="/w")
for _i in range(30):
    _big.add("bulk favorite --run", cwd="/w", ts=int(time.time()) - _i)
_got = _big.suggest("bulk ", cwd="/w", limit=3)
assert _got["choice"] == "bulk favorite --run", _got["choices"]
assert all(c["cmd"].startswith("bulk ") for c in _got["choices"]), _got
print("OK — a key wider than the pre-filter cap still ranks the same winner.")

# A wrapper on the typed line comes back on the answer: the lookup ran for the
# line behind `sudo`, and the user typed the whole line.
_wr = Engine()
for _i in range(5):
    _wr.add("docker compose up -d", cwd="/w", ts=_T - _i)
_wr_res = _wr.suggest("sudo docker comp", cwd="/w", limit=1)
assert _wr_res["choice"] == "sudo docker compose up -d", _wr_res
assert _wr_res["completion"] == "ose up -d", _wr_res
print("OK — a wrapped prefix is answered with the wrapper still on it.")

# --- The record path's self-refresh, and the one filesystem question it asks ---
#
# `_which` is the CLI's own PATH walk — shutil.which's answer without shutil's
# import, on a process that runs once per command. The empty PATH component
# (which means the current directory) and the not-a-file case are the two
# shapes a real PATH actually takes.
_bin = pathlib.Path("/tmp/tai/tai_edge_bin")
_bin.mkdir(parents=True, exist_ok=True)
_tool = _bin / "taiedgetool"
_tool.write_text("#!/bin/sh\ntrue\n")
_tool.chmod(0o755)
(_bin / "not-executable").write_text("data")
(_bin / "a-directory").mkdir(exist_ok=True)
_saved_path = os.environ["PATH"]
os.environ["PATH"] = f"{_bin}:{_bin}/a-directory:"
try:
    from tai.cli import _which
    assert _which("taiedgetool") == str(_tool), _which("taiedgetool")
    assert _which("not-executable") is None, "data is not a tool"
    assert _which("a-directory") is None, "a directory is not a tool"
    assert _which("never-heard-of") is None
finally:
    os.environ["PATH"] = _saved_path
print("OK — _which: executable and a file, and nothing else counts.")

# The self-refresh: a record rebuilds the index only when the index is behind
# the newest row, and never twice inside the debounce window. Both decisions
# are read off the environment here, with the real index file's mtime as the
# clock.
import tai.index as tai_index_mod
import tai.maintenance as tai_maint_mod

_real_index_path, _real_rebuild = tai_index_mod.index_path, tai_maint_mod._rebuild_quietly
_refresh_dir = pathlib.Path("/tmp/tai/tai_edge_refresh")
_refresh_dir.mkdir(parents=True, exist_ok=True)
_idx = _refresh_dir / "index.zsh"
_idx.write_text("# tai index\n")
_rebuilds: list[str] = []
tai_maint_mod._rebuild_quietly = lambda: _rebuilds.append("build")
try:
    def _with_index_path(fn):
        tai_index_mod.index_path = lambda: _idx
        try:
            return fn()
        finally:
            tai_index_mod.index_path = _real_index_path

    from tai.cli import _auto_maintain, _rebuild_when_stale

    _idx.unlink()
    _with_index_path(lambda: _rebuild_when_stale(None))
    assert _rebuilds == ["build"], "a missing index is rebuilt before it is read"

    _rebuilds.clear()
    _idx.write_text("# tai index\n")   # written now: inside the debounce window
    _with_index_path(lambda: _rebuild_when_stale(int(time.time()) + 5))
    assert _rebuilds == [], "a fresh index is not rebuilt, whatever the rows say"

    _rebuilds.clear()
    old = time.time() - 30
    os.utime(_idx, (old, old))
    _with_index_path(lambda: _rebuild_when_stale(int(old) + 10))
    assert _rebuilds == ["build"], "a stale index older than the newest row is rebuilt"

    _rebuilds.clear()
    _with_index_path(lambda: _rebuild_when_stale(int(old) - 10))
    assert _rebuilds == [], "rows older than the index are not worth a rebuild"

    _rebuilds.clear()
    tai_index_mod.index_path = lambda: _idx
    _auto_maintain(100, 0)
    assert _rebuilds == ["build"], "the 100th command rebuilds outright"
    _rebuilds.clear()
    _auto_maintain(101, 0)
    assert _rebuilds == [], "the 101st asks the freshness question, which says no"
finally:
    tai_index_mod.index_path = _real_index_path
    tai_maint_mod._rebuild_quietly = _real_rebuild
    os.environ["TAI_DB"] = _saved_db
print("OK — the self-refresh: debounce, staleness, and the 100th command.")
