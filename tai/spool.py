"""tai spool — the shells' write side of the store, drained in batches.

One user command used to be one Python process: the plugins forked
`tai record` in the background after every single line, for the life of an
install — roughly 30ms of interpreter startup per command, thousands of
processes a day, all to insert one row. The spool replaces that with what the
shell already has: `print` and `printf` append one framed line to this file
(microseconds, no process), and `tai flush` — asked for by the plugin only
when it is worth a process — ingests the whole batch in one connection.

The frame is one record per RS byte, fields joined by US bytes:

    ts US exit US cwd US cmd RS

RS and US (\\x1e and \\x1f) are chosen because a command a person types does
not carry them; one that does (a pasted binary blob) is stripped by the shell
before it is written, and rejected by the store's control-byte filter if it
arrives here anyway. Newlines inside `cmd` survive the framing — which is the
point, because a newline is exactly what makes `is_recordable` refuse the
command later. Nothing is decided twice: the spool is transport, the store's
filters stay the one gate between what was typed and what is learned.

Two facts make concurrent shells safe. `os.replace` rotates the file before it
is parsed, so a record appended mid-drain lands in the fresh spool and waits
for the next flush instead of being torn; and a second flush that loses the
rename race finds no file and returns zeroes. At worst two shells flush twice
and one of those flushes is empty.
"""
from __future__ import annotations

import os
import time

RS = "\x1e"
US = "\x1f"

# A frame larger than this is not a command a person typed. The shell caps its
# own writes well under it; a frame past the bound is skipped at drain time so
# a runaway paste cannot grow the spool without end.
MAX_FRAME_BYTES = 12000

# The commands of the batch just drained, held for the learning step. drain()
# writes it, flush() reads it; nothing else may.
_BATCH: list[str] = []


def spool_path() -> str:
    """Where the shells append records. One decision, three callers agree on.

    `TAI_SPOOL` names it outright. Otherwise it lives beside the database —
    which is what `TAI_DB` moves, so a test or an install that relocates the
    store relocates its spool with it, without being told twice. A relative
    `TAI_DB` resolves to the current directory here exactly as the shells
    resolve it, which is why the no-slash case is spelled out rather than left
    to an os.path.dirname call to guess at.

    The answer is a plain path string, not a `Path`: this module loads on the
    record fallback and the flush, and importing pathlib costs about 9ms of
    enum, re and urllib.parse — per process, for what is here only string
    plumbing. The one caller that genuinely wants a `Path` wraps it itself.
    """
    env = os.environ.get("TAI_SPOOL")
    if env:
        return env
    db = os.environ.get("TAI_DB")
    if db:
        if "/" in db:
            return db.rsplit("/", 1)[0] + "/spool.log"
        return "spool.log"
    from tai.paths import data_dir
    return os.path.join(str(data_dir()), "spool.log")


def spool_append(cmd: str, cwd: str = "", exit_code: int = 0,
                 ts: int | None = None) -> bool:
    """Append one framed record. False when the spool is unwritable.

    This is the Python form of the one line the shell writes; tests and
    tooling use it, the plugins use their own builtins. `repo` and `branch`
    are deliberately absent: resolving them costs two git processes, and the
    flush resolves them per distinct directory once for the whole batch — the
    shell's append stays a builtin.

    The frame separators are stripped here exactly as the shell strips them,
    because the write side is one rule with two transports: a command that
    carries RS or US would tear the framing, and the flush would read half of
    it as a command that was never typed. Nothing here decides recordability —
    a stripped command that is still control-byte-ridden or multiline is
    refused by the store at drain time, the same as everywhere else.
    """
    cmd = (cmd or "").replace(RS, "").replace(US, "")
    cwd = (cwd or "").replace(RS, "").replace(US, "")
    if not cmd.strip():
        return False
    ts = int(ts if ts is not None else time.time())
    line = US.join((str(ts), str(int(exit_code or 0)), cwd or "", cmd))
    try:
        p = spool_path()
        d = os.path.dirname(p)
        if d:
            os.makedirs(d, exist_ok=True)
        with open(p, "ab") as f:
            f.write(line.encode("utf-8", "surrogateescape") + RS.encode())
        return True
    except OSError:
        return False


def drain() -> tuple[int, int, int, int]:
    """Rotate the spool and store every recordable frame in one connection.

    Returns (ingested, skipped, total, newest_ts). Rotation happens before
    parsing, so the bytes this reads are frozen and every record appended
    while this one runs waits for the next flush. The store's own
    `is_recordable` is the only gate a frame passes on its way in — the same
    gate `tai record` applies, which is what makes the two write paths one
    rule with two transports.
    """
    global _BATCH
    _BATCH = []
    src = spool_path()
    work = src + ".flushing"
    try:
        os.replace(src, work)
    except OSError:
        return (0, 0, 0, 0)          # nothing pending, or nothing writable
    try:
        with open(work, "rb") as f:
            data = f.read()
    except OSError:
        data = b""
    finally:
        try:
            os.unlink(work)
        except OSError:
            pass
    if not data:
        return (0, 0, 0, 0)

    from tai.store import connect, is_recordable
    rows: list[tuple] = []
    cwds: dict[str, tuple[str, str]] = {}
    skipped = 0
    for frame in data.split(RS.encode()):
        if not frame or len(frame) > MAX_FRAME_BYTES:
            if frame:
                skipped += 1
            continue
        parts = frame.split(US.encode(), 3)
        if len(parts) != 4:
            skipped += 1
            continue
        ts_b, code_b, cwd_b, cmd_b = parts
        try:
            ts = int(ts_b)
            code = int(code_b)
        except ValueError:
            skipped += 1
            continue
        cwd = cwd_b.decode("utf-8", "surrogateescape")
        cmd = cmd_b.decode("utf-8", "surrogateescape").strip()
        if not is_recordable(cmd):
            skipped += 1
            continue
        rows.append((cmd[:2000], cwd, code, ts))
        if cwd and cwd not in cwds:
            cwds[cwd] = ("", "")

    if not rows:
        return (0, skipped, 0, 0)
    _BATCH = [row[0] for row in rows]

    # A command's repo and branch describe where it ran, so they are resolved
    # from the cwd each row carries — once per distinct directory in the
    # batch, not once per row, and no git process on the shell's prompt path.
    # A directory that has since been deleted resolves to no labels, which is
    # what `tai record` would have stored from it too.
    for cwd in cwds:
        cwds[cwd] = _git_context(cwd)

    try:
        con = connect()
    except Exception:
        return (0, skipped, 0, 0)
    try:
        batch = [(cmd, cwd, cwds.get(cwd, ("", ""))[0][:200],
                  cwds.get(cwd, ("", ""))[1][:200], code, ts)
                 for cmd, cwd, code, ts in rows]
        con.executemany(
            "INSERT INTO commands(cmd,cwd,repo,branch,exit_code,ts) "
            "VALUES(?,?,?,?,?,?)", batch)
        con.commit()
        total, newest = con.execute(
            "SELECT COUNT(*), COALESCE(MAX(ts), 0) FROM commands").fetchone()
    except Exception:
        return (0, skipped, 0, 0)
    finally:
        try:
            con.close()
        except Exception:
            pass
    return (len(batch), skipped, total, newest)


def _git_context(cwd: str) -> tuple[str, str]:
    """(repo, branch) for a recorded directory, or ("", "") anywhere else."""
    import subprocess
    try:
        top = subprocess.run(
            ["git", "-C", cwd, "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=5).stdout.strip()
        branch = subprocess.run(
            ["git", "-C", cwd, "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, timeout=5).stdout.strip()
        return (top.rsplit("/", 1)[-1] if top else "", branch or "")
    except Exception:
        return ("", "")


def flush() -> dict:
    """The whole flush: drain, then the maintenance the record path used to do.

    Returns the counts `tai flush` prints. The rebuild decision and the
    first-use `--help` learning are the ones `cmd_record` ran per command;
    here they run once per batch — the same promise, kept more cheaply: the
    index is asked to catch up exactly once for all of the pending records,
    so learning still shows up within one debounce of the command that taught
    it.
    """
    ingested, skipped, total, newest = drain()
    out = {"ingested": ingested, "skipped": skipped, "total": total,
           "newest": newest, "learned": 0}
    if not ingested:
        return out
    from tai.cli import _auto_maintain
    _auto_maintain(total, newest)
    out["learned"] = _learn_from_batch(_BATCH)
    return out


def _learn_from_batch(commands: list[str]) -> int:
    """Learn the tools this batch ran for the first time, bounded.

    The record path learned one tool per command, in the command's own
    background process. The flush has the whole batch, so it asks once about
    every unknown tool in it — capped, so a batch that touches thirty new
    tools spreads its `--help` probes over later flushes instead of turning
    one into a survey — and rebuilds once for all of them, not once per tool.
    TAI_NO_LEARN=1 turns the whole step off, as it did on the record path.
    """
    if not commands or os.environ.get("TAI_NO_LEARN") == "1":
        return 0
    from tai.cli import _which
    from tai.knowledge import is_known, learn
    from tai.maintenance import _rebuild_quietly
    names: list[str] = []
    for command in commands:
        words = command.split()
        name = words[0] if words else ""
        if not name or name in names:
            continue
        if len(names) >= 8:
            break
        names.append(name)
    unknown = [n for n in names
               if not n.startswith("-") and "/" not in n and len(n) <= 64
               and _which(n) and not is_known(n)]
    if not unknown:
        return 0
    learned = learn(unknown)
    if learned:
        _rebuild_quietly()
    return learned
