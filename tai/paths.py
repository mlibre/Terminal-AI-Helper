"""Path liveness for command candidates.

A command is only a useful suggestion if the paths it names still exist. The
failure this exists to prevent: `cd ~/projects/myapp` was typed often enough to
outrank every live alternative, long after the directory it names was renamed,
moved, or never existed on this machine at all. Ranking counts how often you ran
something; it cannot tell that the something stopped working.

The check is deliberately conservative, because a bad check is worse than no
check. It runs while building the index and on the one-shot `tai suggest` path,
never on the keystroke path. A token is judged only when it is unambiguously a
filesystem path:

  * `--flags`, `key=value`, URLs, and `host:path` remote specs are skipped;
  * anything containing shell metacharacters, variables, or globs is reported
    as unknown, because `src/*.py` may well match files;
  * a relative path is judged only when the command was recorded in a directory
    that still exists, since that is the only case where the answer is
    knowable at all.

Unknown means "no opinion" and never removes a candidate. Missing is the only
verdict that does.

One question here is not about liveness at all: `takes_file` asks whether a
line's last word is a file, from what the command means. That is the fact the
shell plugins need before they will read the filesystem for an answer — see
`tai/fresh.py` for what they do with it.
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

# Verdicts. Only MISSING removes a candidate.
EXISTS = "exists"
MISSING = "missing"
UNKNOWN = "unknown"

# Commands whose first argument names a directory, by what the command means. A
# bare name is not shaped like a path, so the shape test below passes it, and
# `cd ter` for a directory that was never there survived every check and stayed
# in the index — where it was then offered as a completion of `cd te`, which is
# the shape of the complaint that found this. Judged from the directory the
# command was recorded in, it is simply missing.
#
# This is a fact about `cd`, not a guess about a name, and it is the only such
# fact used: `z` and `autojump` match a fuzzy name from anywhere, and `ls` and
# `git` take arguments that are often not paths at all. Only the *first*
# argument is judged, because `cd` ignores the rest and a token with no place in
# the command is a token with no opinion.
DIRECTORY_ARGUMENT = frozenset({"cd", "pushd"})

# What a `cd` argument has to be for it to be a *destination*. `..` and `.` are
# relative moves and `-` is a slot in the shell's own state: all three are true
# in every directory that has ever existed, so how often they were typed says
# nothing about where this user wants to be from *here*. They are how often you
# go up, not where you go.
#
# So they are ranked below every real destination and never removed — `cd ..` is
# a command that works and the menu still offers it. It just stops winning a
# ranking it cannot win on evidence, which is what put `cd ..` at the top of
# `cd ` on a history where the user had `cd`-ed into a project directory twice
# as often.
#
# A closed list, and it is about the *first* argument only, for the same reason
# `DIRECTORY_ARGUMENT` is.
NOT_A_DESTINATION = frozenset({".", "..", "-", "./", "../"})


@lru_cache(maxsize=8192)
def is_destination(cmd: str) -> bool:
    """True when `cmd` is a `cd` whose argument names a place to be.

    A line that is not a directory-taking command, or that names no argument, or
    whose argument is a relative move, is not a destination — the shape of the
    answer to "where do I want to be", which is the question `cd ` asks.

    Memoised because it is a pure function of the string and it is asked once per
    candidate per keystroke, on the same few thousand commands: measured on a
    7.6k-command history, the empty-prefix suggest spent 2.7ms of its 8.4ms in
    here, splitting and unquoting the same names over and over. The cache is
    bounded, so a long-running process cannot grow it without limit.
    """
    tokens = (cmd or "").split()
    if len(tokens) < 2 or tokens[0] not in DIRECTORY_ARGUMENT:
        return True
    return _unquote(tokens[1]) not in NOT_A_DESTINATION


def destinations_first(scored: list[tuple[int, str]]) -> list[tuple[int, str]]:
    """Re-rank `(score, cmd)` pairs so `cd` destinations outrank relative moves.

    The moves keep their own scores and their own relative order, and are placed
    as a block below the lowest-scoring real destination, one step apart so the
    block cannot tie with the line above it and leave the order to the sort.

    This lives here, beside the vocabulary it reads, because two rankers need it
    and one of them imports the other: the index writes the scores the shell
    plugins read, and the engine answers `tai suggest`. Sharing the rule is what
    keeps them from disagreeing about the same line.

    A `cd` history with no destination in it is left alone — the moves are then
    the whole answer, and ranking them below nothing would invent an order
    rather than express one.
    """
    floor = None
    moves: list[tuple[int, str]] = []
    out: list[tuple[int, str]] = []
    for score, cmd in scored:
        if not is_destination(cmd):
            moves.append((score, cmd))
            continue
        # Only a directory-taking line sets the floor, and only a destination
        # one: `ls ..` is not `cd ..`, and must not lower where `cd ..` sits.
        if cmd.split(" ", 1)[0] in DIRECTORY_ARGUMENT:
            floor = score if floor is None else min(floor, score)
        out.append((score, cmd))
    if not moves or floor is None:
        return out + moves
    moves.sort(reverse=True)
    return out + [(floor - 1 - i, cmd) for i, (_, cmd) in enumerate(moves)]


# Commands whose *last* word is a file, whatever sits in front of it. `chmod +x
# script` is the case this exists for: the mode is not a flag, and `script` is a
# bare name that an imported row cannot be checked against, because a row from a
# history file carries no directory to check it in. Nothing in the line is shaped
# like a path, so the only evidence is what the command means — the same evidence
# `cd` gives about a directory. Without it, the freshest file on disk is invisible
# behind `chmod +x script` forever.
FILE_LAST = frozenset({"chmod", "chown", "chgrp", "cp", "mv", "ln", "install",
                       "."})

# Commands whose last word is a file when everything in front of it is a flag.
# The flag test is what keeps a subcommand out: `python3 manage.py runserver` has
# a non-flag in front of its last word, so that word is a subcommand rather than
# a file and nothing is marked. A closed list, and no interpreters in it — a file
# you run with one is named like a script, and tai cannot tell which files are
# scripts, so offering the newest video for `python3 ` would be a guess.
FILE_FLAGGED = frozenset({
    "bat", "cat", "code", "diff", "du", "file", "head", "less", "md5sum",
    "more", "nano", "nvim", "readlink", "rm", "sha256sum", "sort", "stat",
    "tail", "tar", "unzip", "vim", "wc", "xdg-open", "zip",
})

# Characters that make a token impossible to resolve statically.
_UNANALYSABLE = set('$`*?[]{}<>|;&()!#\\\n\t"\'')

# Cached (token, cwd) verdicts. Builds and purges ask about the same paths
# thousands of times, and the filesystem does not change under one run.
_cache: dict[tuple[str, str], bool] = {}

# Every account name on this machine, or None where they cannot be read. Read
# once and only when a `~name` token actually turns up, because most machines
# have no such command in their history at all and this is a question about the
# passwd database, not about the filesystem. None means "cannot tell", which
# makes `~name` UNKNOWN and therefore removable by nothing.
_HOME_USERS: set | None = None


def _load_home_users() -> set | None:
    global _HOME_USERS
    if _HOME_USERS is None:
        try:
            import pwd

            _HOME_USERS = {p.pw_name for p in pwd.getpwall()}
        except Exception:
            _HOME_USERS = set()   # unreadable: no `~name` is judged at all
    return _HOME_USERS


def enabled() -> bool:
    """False when TAI_SKIP_PATH_CHECK=1 turns the check off."""
    return os.environ.get("TAI_SKIP_PATH_CHECK", "") != "1"


def data_dir() -> Path:
    """Where tai keeps its files. One decision, asked by everything.

    XDG_DATA_HOME is honoured, and it has to be honoured *everywhere*: the shell
    plugins and the index builder already read it, so a store that hardcoded
    ~/.local/share would split the install across two directories and leave
    `tai uninstall` deleting one half of it and reporting complete.
    """
    base = os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share"
    return Path(base) / "tai"


def db_path() -> Path:
    """Where the history store lives — `TAI_DB`, or beside `data_dir()`.

    This used to live in tai/store, and the cost of that was paid by every
    process that only wanted the *name*: the engine cache is keyed on the
    database file's own (size, mtime_ns), so `tai suggest` asked for the path
    before it could ask whether it needed the store at all — and importing
    the store dragged sqlite3 and re into a warm-cache process that reads
    neither. The decision is a path decision (one env var, one directory),
    so it lives here with the others; tai/store keeps the name as a
    re-export, because the writer and every reader of the store still say
    `store.db_path`.
    """
    return Path(os.environ.get("TAI_DB") or data_dir() / "history.db")


def _unquote(token: str) -> str:
    if len(token) >= 2 and token[0] == token[-1] and token[0] in "\"'":
        return token[1:-1]
    return token


def is_path_like(token: str) -> bool:
    """True when `token` can only be read as a filesystem path."""
    if not token or token.startswith("-") or "=" in token:
        return False
    if any(ch in _UNANALYSABLE for ch in token):
        return False
    if "://" in token:                       # URLs
        return False
    host, sep, _ = token.partition(":")
    if sep and "/" not in host:              # scp/rsync remote spec, `cmd:arg`
        return False
    if token.lstrip("+-").isdigit():         # ports, pids, negative numbers
        return False
    if token in (".", ".."):
        return True
    # `~name` is a home directory only when `name` is a real user. It is also
    # git's revision syntax — `git diff ~main`, `git log ~HEAD`, `git rebase
    # ~origin/main` — and no shell expands `~main` unless a user called `main`
    # exists, so `expanduser` hands it back unchanged and `exists` says no. Read
    # as a path, every one of those is MISSING, and MISSING is the only verdict
    # that removes a command: `tai purge --stale` deleted real history.
    #
    # So the user has to exist for the token to be judged at all. There is no
    # `pwd` import here on purpose — the answer only has to be *safe*, and a
    # token whose user cannot be resolved is UNKNOWN, which is what this
    # function already returns for everything it cannot read statically.
    if token.startswith("~"):
        return is_home_ref(token)
    if "/" not in token:
        return False
    # A first component made only of digits and version punctuation is a date, a
    # version or a ratio, not a directory: `date 1/2/2024` and `pkg/1.2.0` both
    # pass a bare slash test and are not paths. No real directory is named `1` or
    # `2.0`, and the module's own rule is that an ambiguous token gets no opinion.
    head = token.lstrip("~/").split("/", 1)[0]
    if head and set(head) <= set("0123456789.+-:") and any(c.isdigit() for c in head):
        return False
    return True


def takes_file(cmd: str) -> bool:
    """True when the last word of `cmd` names a file, by what the command means.

    This is the fact the shell needs to know that `chmod +x <word>` wants a file
    rather than another flag: what the *user* does with a downloaded AppImage is
    not in any history, so the answer has to come from the filesystem, and the
    filesystem is only consulted for a line this returns True for.

    Two questions, both about the command and neither about the filesystem, so
    the index builder can answer them for every line it holds without a stat:
    does the command end in a path, and is what comes before it a flag?
    """
    tokens = (cmd or "").split()
    if len(tokens) < 2:
        return False
    last = _unquote(tokens[-1])
    # A flag is never a file, and a word nothing can read statically is no
    # evidence at all: `rm "$f"` says nothing about a file, and `bash -c 'x'`
    # would otherwise be marked as a line that ends in one.
    if last.startswith("-") or any(ch in _UNANALYSABLE for ch in last):
        return False
    head = tokens[0]
    if head in FILE_LAST:
        return True
    if head not in FILE_FLAGGED:
        return False
    # Everything in front has to be a flag, or a flag's own argument: `cat -n
    # file` and `tail -n 20 file` both end in a file, and `-n 20` is two tokens
    # for one flag. A token that follows *neither* a flag nor the command is an
    # argument in its own right — a script, a subcommand — and the last word
    # after one of those is not a file.
    previous = head
    for token in tokens[1:-1]:
        if not token.startswith("-") and not previous.startswith("-"):
            return False
        previous = token
    return True


def is_home_ref(token: str) -> bool:
    """True when a `~` token names a home directory we can actually resolve.

    The one place the question is asked, because it is asked twice and the two
    answers have to agree: `is_path_like` asks it before judging a token, and
    `_resolve` asks it again because `token_verdict`'s `as_path` deliberately
    lets a token past the shape test — `cd`'s first argument is judged from what
    `cd` means, and that judgement must not skip the `~user` question.
    """
    if not token.startswith("~"):
        return True
    if token == "~" or token.startswith("~/"):
        return True
    user = token[1:]
    return bool(user) and "/" not in user and user in _load_home_users()


def _resolve(token: str, cwds) -> str:
    """EXISTS, MISSING, or UNKNOWN for one path-like token."""
    if token in (".", ".."):
        return EXISTS          # true in every directory, so no cwd is needed
    if token.startswith("~"):
        # `~main` is git's revision syntax as often as it is a home directory,
        # and it only names a home directory when `main` is an account on this
        # machine. No shell expands it otherwise, so there is nothing here to
        # resolve and the honest verdict is UNKNOWN — which removes nothing.
        if not is_home_ref(token):
            return UNKNOWN
        return EXISTS if os.path.exists(os.path.expanduser(token)) else MISSING
    if os.path.isabs(token):
        return EXISTS if os.path.exists(token) else MISSING
    # A relative path is only knowable from a directory it was actually run in.
    # With no such directory the honest answer is "no opinion".
    known = [c for c in cwds if c and os.path.isdir(c)]
    if not known:
        return UNKNOWN
    for cwd in known:
        key = (token, cwd)
        hit = _cache.get(key)
        if hit is None:
            hit = os.path.exists(os.path.join(cwd, token))
            _cache[key] = hit
        if hit:
            return EXISTS
    return MISSING


def token_verdict(token: str, cwds=(), as_path: bool = False) -> str:
    """EXISTS, MISSING, or UNKNOWN for one token of a command.

    `as_path` judges a token the shape test would pass over, because the command
    it belongs to says what the token is. It is only ever set from
    `command_verdict`, and only for a known directory argument.
    """
    token = _unquote(token)
    if not as_path and not is_path_like(token):
        return UNKNOWN
    return _resolve(token, cwds)


def _cd_destination_verdict(token: str, cwds) -> str:
    """MISSING/EXISTS for a `cd`/`pushd` destination, evidence-split.

    A recorded cwd can be evidence two ways, and which way it is decides both
    halves of the answer:

    * When it *resolves* the token — `/home/u` for `cd proj/`, `proj/` existing
      under it — it was typed from there, and judging the token against it is
      ordinary liveness.
    * When it *is* the token's destination — older rows were written after the
      prompt moved, so `cd proj/` recorded the directory it landed in — the
      command's liveness is whether that destination survived, and judging the
      token against it reads a relative path *inside the target*, condemning the
      most-used row in the store. This was the reported bug: the destination
      user actually cds to most kept no record of ever being typed from.

    The destination reading wins when either fits, because keeping a destination
    that exists is never wrong, while deleting one on a misread is.
    """
    norm = _unquote(token).rstrip("/")
    if norm.startswith("./"):
        norm = norm[2:]
    origins: list[str] = []
    dest_seen = False
    for d in cwds:
        if not d:
            continue
        d = str(d)
        dd = d.rstrip("/")
        if norm and (dd == norm or dd.endswith("/" + norm)):
            if os.path.isdir(d):
                return EXISTS          # the recorded landing spot still stands
            dest_seen = True           # it was the target, and it is gone
        else:
            origins.append(d)
    rest = _resolve(_unquote(token), origins)
    if rest == EXISTS:
        return EXISTS
    if dest_seen or rest == MISSING:
        return MISSING
    return UNKNOWN


def command_verdict(cmd: str, cwds=()) -> str:
    """MISSING when any path in `cmd` is gone, else EXISTS or UNKNOWN."""
    verdict = UNKNOWN
    tokens = (cmd or "").split()
    for position, token in enumerate(tokens):
        # A leading dash is a flag whatever the command means, and `cd -` is "go
        # back" rather than a directory named `-`: judged as a path from the
        # recorded directory it is missing, and a command that always works would
        # be dropped from the index.
        as_path = (position == 1 and tokens[0] in DIRECTORY_ARGUMENT
                   and not token.startswith("-"))
        if as_path:
            one = _cd_destination_verdict(token, cwds)
        else:
            one = token_verdict(token, cwds, as_path=False)
        if one == MISSING:
            return MISSING
        if one == EXISTS:
            verdict = EXISTS
    return verdict


def stale_commands(entries, cwd: str = "") -> set[str]:
    """Commands whose paths no longer exist.

    `entries` yields (cmd, cwds) pairs — the same shape the store and the engine
    already keep. `cwd` is the directory the suggestion would be shown in, which
    is the right answer for a command whose history never recorded one.

    It is a guess, though, and a caller that *deletes* on the answer must not use
    it: every row imported from a history file has no recorded cwd, so judging
    those against wherever the user happens to be standing condemns real history
    that was only ever run somewhere else. Hiding a suggestion on a guess is
    harmless; destroying a row on one is not. `purge --stale` therefore passes no
    cwd and deletes only what the recorded evidence proves is gone.
    """
    out: set[str] = set()
    for cmd, recorded in entries:
        cwds = list(recorded or ())
        if cwd and cwd not in cwds:
            cwds.append(cwd)
        if command_verdict(cmd, cwds) == MISSING:
            out.add(cmd)
    return out


def stale_in(engine, cwd: str = "") -> set[str]:
    """The stale set for a built engine, with the path check applied once.

    The one place that owns the policy. The index builder, the one-shot suggest
    path and the diagnostics all need the same answer from the same engine, and
    writing the walk out three times is how they would drift apart.

    `cwd` is passed through verbatim: an empty string means "recorded evidence
    only". It is deliberately *not* defaulted to the process's own directory —
    the index builder runs in whatever directory the rebuild happened to fire
    in, and falling back to it strips every relative-path command whose history
    row carries no cwd from a snapshot that is shown in every directory. Only
    the callers gating a suggestion (the one-shot path, the diagnostics) pass
    a directory, and hiding one candidate on a guess costs nothing; removing it
    from the shared index costs the same command everywhere.
    """
    if not enabled():
        return set()
    return stale_commands(
        ((cmd, list(st.cwd)) for cmd, st in engine.cmds.items()),
        cwd)
