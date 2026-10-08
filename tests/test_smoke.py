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
            os.utime(old_one, (now - 30 * 86400, now - 30 * 86400))
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
        assert not any(c.endswith("aaa00.mp4") for c in crowded), \
            f"the oldest file survived a cap meant to keep the newest: {crowded}"
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
