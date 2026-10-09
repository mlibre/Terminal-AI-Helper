"""tai store — durable sqlite log + loader. Stdlib only."""
from __future__ import annotations

import os
import re
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

from tai.paths import enabled as paths_enabled

# A command must be safe to persist and useful to suggest before it is kept.
_SECRET = re.compile(r"(password|secret|token|AKIA|-----BEGIN)", re.I)

# The raw-control-byte half of the filter, at C speed: this runs once per row
# the engine builds from, and the previous per-character `any(ord(ch) < 32 …)`
# walk was Python-level work on every character of every row. The class matches
# exactly what the walk did — C0 controls and DEL, and nothing above 0x7f, so
# ordinary UTF-8 (whose continuation bytes are all 0x80 and up) is still kept.
_CTRL = re.compile(r"[\x00-\x1f\x7f]")

# Session-persistence harnesses wrap each real command in a marker envelope:
#   printf ... __DSH_PERSISTENT_BASH_START_<uuid>__; eval -- $'ls -R ...';
#   __dsh_persistent_bash_status=$?
# The envelope is not a command anyone would retype, and a 600-char wrapped
# blob becomes a ranking key and an index entry, so it only adds noise.
_WRAPPER = re.compile(
    r"__[A-Za-z0-9]+_(?:START|END)_[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
    r"[0-9a-f]{4}-[0-9a-f]{12}__"
    r"|__[a-z0-9_]*status=\$\?"
)


def is_recordable(cmd: str) -> bool:
    """True when a command is safe to store and worth ranking."""
    cmd = (cmd or "").strip()
    if len(cmd) < 2:
        # One key is not vocabulary. An accidental Enter on a stray character
        # (`y`, `n`, `-`, a backtick) is recorded like any other command, and
        # nothing can rank it usefully: it carries no tool, no argument, and
        # no path — it just happened recently, which is the one signal that
        # makes it look good. The report that set this rule: those one-key
        # entries filled an empty Tab's menu ahead of the directory listing.
        # Aliases are shell state, not history; a real one-character tool
        # loses nothing but a rank it never deserved.
        return False
    if len(cmd) > 2000:
        return False
    # A command with a newline in it is one command here but two there: the
    # index newline-joins its candidates, so `git commit -m 'line one<NL>line
    # two'` would be offered as `git commit -m 'line one` and a second one
    # called `line two'`, and neither is a command that runs. What runs as a
    # candidate has to be one line, so a command that is not one is not
    # recorded.
    if "\n" in cmd or "\r" in cmd:
        return False
    # A recorded buffer can collect raw control bytes — bracketed-paste
    # markers landing in the buffer are the ones seen on a real install — and
    # the plugin paints such a line into the terminal verbatim: the bytes are
    # escape bytes to it, and an ESC at the head of a ghost line went to the
    # terminal unescaped. Not a command anyone would retype, so it is not a
    # command worth storing. (UTF-8 text is outside this range: a multi-byte
    # sequence's continuation bytes are all 0x80 and up.)
    if _CTRL.search(cmd):
        return False
    return not (_SECRET.search(cmd) or _WRAPPER.search(cmd))


def db_path() -> Path:
    from tai.paths import data_dir
    return Path(os.environ.get("TAI_DB") or data_dir() / "history.db")


def ensure_db(con: sqlite3.Connection) -> None:
    con.execute(
        """CREATE TABLE IF NOT EXISTS commands(
            id INTEGER PRIMARY KEY,
            cmd TEXT NOT NULL,
            cwd TEXT DEFAULT '',
            repo TEXT DEFAULT '',
            branch TEXT DEFAULT '',
            exit_code INTEGER DEFAULT 0,
            ts INTEGER DEFAULT 0
        )"""
    )
    # migrate old DBs missing branch. Not wrapped in a swallow: if the ALTER
    # fails, every later reader fails too, and every one of them reports an empty
    # result — which is exactly what a brand new install looks like. Failing here
    # is the only place the cause is still visible; `schema_note` reports it.
    cols = [r[1] for r in con.execute("PRAGMA table_info(commands)")]
    if "branch" not in cols:
        con.execute("ALTER TABLE commands ADD COLUMN branch TEXT DEFAULT ''")
    con.execute("CREATE INDEX IF NOT EXISTS idx_cmd ON commands(cmd)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_ts ON commands(ts DESC)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_cwd ON commands(cwd)")
    # Track each history file so a later install can append only new rows
    # instead of duplicating the entire history every time.
    con.execute(
        """CREATE TABLE IF NOT EXISTS history_imports(
            source TEXT PRIMARY KEY,
            inode INTEGER NOT NULL,
            offset INTEGER NOT NULL
        )"""
    )
    # Cached --help/man knowledge, one row per tool. Here rather than in the
    # module that reads it, so that the schema has one owner: a table created
    # only by its first writer is a table every reader has to catch the absence
    # of and report as "nothing learned yet".
    con.execute(
        """CREATE TABLE IF NOT EXISTS cli_tools(
            name TEXT PRIMARY KEY,
            data TEXT NOT NULL,
            updated INTEGER NOT NULL
        )"""
    )


def connect() -> sqlite3.Connection:
    p = db_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(p), timeout=5.0)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    ensure_db(con)
    return con


@contextmanager
def session():
    """A connection that is closed on every path out, including errors.

    Five copies of `try: con.close() except: pass` had drifted into two shapes and
    the error paths leaked the connection and both of its WAL file descriptors.
    Recording runs in a background process once per command, so that is not a
    one-off cost.
    """
    con = connect()
    try:
        yield con
    finally:
        try:
            con.close()
        except Exception:
            pass


def schema_note() -> str:
    """Why the database is unusable, or "" when it is fine.

    Every reader turns a broken store into an empty result, and an empty result
    is indistinguishable from a fresh install: `tai doctor` reporting zero rows
    is the honest description of both. This is the one place that says which.

    Two things it must not say, and both were said:

      * **Nothing, on a machine that has never run tai.** `sqlite3.connect`
        *creates* the file, so asking a read-only question about a store that does
        not exist made one, found no `commands` table in it, and reported
        "database schema is out of date (no branch column) — every command is being
        read as zero rows". A first run is not a broken schema, and this is the
        command a new user runs to check the install.
      * **Nothing, about a store with no table yet.** An empty file and a file
        whose table has not been created are both "no data yet"; only a `commands`
        table that *exists* and lacks the column is an old schema.
    """
    path = db_path()
    if not path.exists():
        return ""
    try:
        con = sqlite3.connect(str(path))
        try:
            cols = [r[1] for r in con.execute("PRAGMA table_info(commands)")]
        finally:
            con.close()
    except Exception as e:
        return f"database unusable: {e}"
    if not cols:
        return ""            # a store nobody has written to yet
    if "branch" not in cols:
        return "database schema is out of date (no branch column)"
    return ""


def append_and_count(cmd: str, cwd: str = "", repo: str = "", branch: str = "",
                     exit_code: int = 0) -> tuple[bool, int, int]:
    """Store one command, and return (inserted, total_rows, newest_ts).

    `cwd` is stored whole. It used to be cut to 500 characters, which is the one
    field the path check reads as evidence. A cut path is either not a directory
    — so every relative command run there became unjudgeable — or worse, it *is* a
    directory, and the command was then judged against a place the user was never
    in. PATH_MAX is 4096 anyway, so the bound bought nothing. `repo` and `branch`
    are labels rather than evidence, so those are still bounded.

    `newest_ts` rides along on purpose. Every record used to open a *second*
    connection afterwards to ask MAX(ts) — the freshness question the rebuild
    debounce answers — which is a WAL open, a schema check and a query paid by
    every command the user types, forever. One aggregate, one round trip.
    """
    cmd = (cmd or "").strip()
    if not is_recordable(cmd):
        return False, count()[0], 0
    # SQLite binds text and numbers and refuses everything else, and a `Path`
    # for `cwd` is an ordinary thing for a caller to pass — `REPO` in the tests
    # is one. It raised `ProgrammingError` inside the `except` below, which
    # reports "not stored" and returns the current count, so a whole history
    # could be dropped without a word: the command was simply never inserted.
    cwd, repo, branch = str(cwd or ""), str(repo or ""), str(branch or "")
    try:
        with session() as con:
            con.execute(
                "INSERT INTO commands(cmd,cwd,repo,branch,exit_code,ts) VALUES(?,?,?,?,?,?)",
                (cmd, cwd, repo[:200], branch[:200], int(exit_code or 0),
                 int(time.time())),
            )
            con.commit()
            row = con.execute(
                "SELECT COUNT(*), MAX(ts) FROM commands").fetchone()
            return True, row[0], row[1] or 0
    except Exception:
        return False, count()[0], 0


def purge_unrecordable() -> int:
    """Delete already-stored rows that would no longer be recorded.

    Used to drop harness-wrapped or over-long commands written by older
    versions, so a filter added later takes effect on existing history.
    Returns the number of deleted rows.
    """
    try:
        with session() as con:
            stale = [row[0] for row in con.execute("SELECT id,cmd FROM commands")
                     if not is_recordable(row[1])]
            if stale:
                con.executemany("DELETE FROM commands WHERE id=?",
                                [(row_id,) for row_id in stale])
                con.commit()
        return len(stale)
    except Exception:
        return 0


def load_rows(limit: int = 50000) -> list[tuple]:
    """Keep the most recent `limit` rows, returned oldest-first so the
    Engine builds correct prev->next sequence pairs.
    Returns (cmd, cwd, repo, branch, exit_code, ts).

    A store that cannot be read raises, and that is the whole point of this
    function. It used to return `[]`, and an empty list is indistinguishable from
    a brand-new install: one corrupt or locked database therefore rebuilt the
    whole index out of the 60-line seed corpus, replaced 6.2k learned commands
    with it, and `tai refresh` printed a green `✓ 60 commands indexed`. Five
    callers want "no rows" and one of them writes the result, so the distinction
    has to be made where the read happens rather than at each call site.

    A *missing* database is still an empty list, not an error: that is a first
    run, and the seed corpus is exactly the right answer for it.
    """
    if not db_path().exists():
        return []
    with session() as con:
        total = con.execute("SELECT COUNT(*) FROM commands").fetchone()[0]
        off = max(0, total - limit)
        return con.execute(
            "SELECT cmd,cwd,repo,branch,exit_code,ts FROM commands "
            "ORDER BY id ASC LIMIT ? OFFSET ?",
            (limit, off),
        ).fetchall()


def purge_stale() -> int:
    """Delete rows whose paths no longer exist, and return the count.

    `purge_unrecordable` drops rows that should never have been stored.
    This drops rows that *were* real commands and have since stopped working:
    a renamed project directory, an unmounted volume, a path from another
    machine. They are removed from the ranking rather than demoted, because a
    suggestion for a directory that is not there is worse than no suggestion.

    Deliberately judged with no fallback cwd. The index builder may fall back to
    the directory a suggestion would be shown in, because hiding one candidate on
    a guess costs the user nothing; deleting a row does. Rows imported from a
    history file carry no cwd at all, so with a fallback every `python3 src/main.py`
    ever typed would be condemned the moment this ran from the wrong directory.

    The path check is the same one the index builder uses, so the two can never
    disagree about what counts as stale. Set TAI_SKIP_PATH_CHECK=1 to keep the
    rows.
    """
    from tai.paths import stale_commands
    if not paths_enabled():
        return 0
    try:
        with session() as con:
            rows = con.execute("SELECT id,cmd,cwd FROM commands").fetchall()
    except Exception:
        return 0
    cwds: dict[str, set] = {}
    for _id, cmd, cwd in rows:
        cwds.setdefault(cmd, set()).add(cwd or "")
    stale = stale_commands(cwds.items())
    if not stale:
        return 0
    try:
        with session() as con:
            con.executemany("DELETE FROM commands WHERE cmd=?",
                            [(cmd,) for cmd in stale])
            con.commit()
    except Exception:
        return 0
    return len(stale)


def forget_commands(names: list[str]) -> int:
    """Delete every row whose command is exactly one of `names`; return the count.

    The user-facing half of the exit-code repair. A typo that an older install
    recorded as a success — the hook-order bug that read a prompt theme's exit
    status instead of the command's — is indistinguishable from a real success
    for the rest of this program's life: the phantom rule can only drop a line
    whose every run was command-not-found, and these rows claim success. No
    ranking rule can undo what the store was told, so the store is asked
    directly: `tai forget opencoe` drops the rows, `--rebuild` (the default)
    takes the line out of the indexes in the same breath.

    Matching is exact on the stored text, whitespace-stripped the way every
    writer strips it. A name with arguments is one command ("git sta"), not a
    prefix — forgetting `git` must not take `git status` with it. Unknown
    names simply match nothing; the count is the honest answer either way.
    """
    cleaned: list[str] = []
    for name in names:
        name = (name or "").strip()
        if name and name not in cleaned:
            cleaned.append(name)
    if not cleaned:
        return 0
    with session() as con:
        marks = ",".join("?" * len(cleaned))
        cur = con.execute(f"DELETE FROM commands WHERE cmd IN ({marks})", cleaned)
        con.commit()
        return cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0


def count() -> tuple[int, int]:
    try:
        with session() as con:
            n = con.execute("SELECT COUNT(*) FROM commands").fetchone()[0]
            d = con.execute("SELECT COUNT(DISTINCT cmd) FROM commands").fetchone()[0]
        return n, d
    except Exception:
        return 0, 0


def _history_files(home: Path) -> list[Path]:
    """Return supported history files, including a custom HISTFILE."""
    candidates: list[Path] = []
    custom = os.environ.get("HISTFILE", "").strip()
    if custom:
        candidates.append(Path(custom).expanduser())
    candidates.extend((home / ".zsh_history", home / ".bash_history"))
    extra = os.environ.get("TAI_HISTORY_FILES", "").strip()
    if extra:
        candidates.extend(Path(item).expanduser() for item in extra.split(os.pathsep) if item)
    result: list[Path] = []
    seen: set[str] = set()
    for path in candidates:
        key = str(path)
        if key not in seen:
            result.append(path)
            seen.add(key)
    return result


def import_shell_history() -> int:
    """Import all available history rows, and only new rows on later runs.

    History files are append-only in normal shell use. A small source cursor
    makes installation idempotent and also lets later installs pick up commands
    written after the first install.
    """
    try:
        con = connect()
    except Exception:
        return 0
    try:
        return _import_rows(con)
    finally:
        try:
            con.close()
        except Exception:
            pass


def _import_rows(con: sqlite3.Connection) -> int:
    """The body of import_shell_history, given an open connection.

    Split out only so the connection has one owner with a `finally` on it: the
    loop below has a dozen `continue` paths, and each one used to be a chance to
    leak the connection.
    """
    imported = 0
    now = int(time.time())
    home = Path.home()
    for f in _history_files(home):
        try:
            stat = f.stat()
            size = stat.st_size
        except OSError:
            continue
        try:
            state = con.execute(
                "SELECT inode, offset FROM history_imports WHERE source=?", (str(f),)
            ).fetchone()
        except sqlite3.Error:
            state = None
        start = 0
        first_import = state is None
        if state and state[0] == stat.st_ino and 0 <= state[1] <= size:
            start = state[1]
        elif state:
            # The file was replaced/truncated; read it from the beginning.
            first_import = True
        try:
            raw = f.read_bytes()[start:]
        except OSError:
            continue
        if not raw:
            continue
        text = raw.decode("utf-8", "ignore")
        lines = text.splitlines()
        if not lines:
            continue

        # A final line without a newline may still be complete. Import it and
        # advance the cursor; shells add a newline before appending the next
        # command, so the next run will start cleanly.
        batch = []
        plain_seen = set()   # unstamped commands already claimed in this batch
        for line in lines:
            if line.startswith(": "):
                parts = line.split(";", 1)
                cmd = parts[1] if len(parts) > 1 else ""
                try:
                    ts = int(parts[0].split(":")[1].strip().split(":")[0])
                except Exception:
                    ts = None
            else:
                cmd = line
                ts = None
            cmd = cmd.strip()
            if not is_recordable(cmd) or cmd.startswith("#"):
                continue
            if ts is None:
                # A history line with no timestamp is indistinguishable from the
                # same command an earlier import already claimed — and the cursor
                # reset above re-reads the *whole* file every time it shrank
                # (which zsh does at every exit), so re-stamping it with a fresh
                # `now` counted every command one more time and moved its "last
                # used" to now. Frequency doubled, and recency — the two signals
                # the ranking listens to — went flat. Claim an unstamped command
                # once, regardless of which file or which round it appears in.
                if cmd in plain_seen or con.execute(
                        "SELECT 1 FROM commands WHERE cmd=? LIMIT 1",
                        (cmd[:2000],)).fetchone():
                    continue
                plain_seen.add(cmd)
                ts = now
            elif first_import:
                # Avoid duplicating rows created by versions before the
                # history_imports cursor table existed.
                already = con.execute(
                    "SELECT 1 FROM commands WHERE cmd=? AND ts=? LIMIT 1", (cmd[:2000], ts)
                ).fetchone()
                if already:
                    continue
            batch.append((cmd[:2000], "", "", "", 0, ts))
        # One transaction per file, and the cursor moves only with the rows it
        # accounts for. They used to share a `try` that swallowed the error and
        # left the offset behind, so a file that half-imported was re-read whole
        # on the next refresh and every command in it was inserted twice — which
        # inflates frequency and permanently skews the ranking with no message.
        try:
            if batch:
                con.executemany(
                    "INSERT INTO commands(cmd,cwd,repo,branch,exit_code,ts) VALUES(?,?,?,?,?,?)",
                    batch,
                )
            con.execute(
                "INSERT INTO history_imports(source,inode,offset) VALUES(?,?,?) "
                "ON CONFLICT(source) DO UPDATE SET inode=excluded.inode, offset=excluded.offset",
                (str(f), stat.st_ino, start + len(raw)),
            )
            con.commit()
        except sqlite3.Error:
            # This file is skipped whole. Rolling back keeps the count honest:
            # `imported` is what `tai refresh` prints, and a number that describes
            # rows that were rolled back is the same lie as reporting success on a
            # failed index build.
            con.rollback()
            continue
        imported += len(batch)
    return imported
