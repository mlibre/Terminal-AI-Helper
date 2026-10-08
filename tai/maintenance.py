"""Maintenance lock and index rebuild, shared by every tai command.

The index is rebuilt from the history store by background triggers that can
fire at the same moment from several shells. The lock below is what makes that
at most one writer, and it is built so that every way of losing its holder
eventually reclaims it — a lock that outlives its process would stop every
future rebuild, silently, forever.
"""
import os
import shutil
import time
from pathlib import Path


def _lock_path() -> Path:
    from tai.store import db_path
    return db_path().parent / "maintenance.lock"


def _boot_id() -> str:
    """This boot's identity, or "" where the kernel does not publish one."""
    try:
        with open("/proc/sys/kernel/random/boot_id", "rb") as f:
            return f.read().strip().decode()
    except OSError:
        return ""


# How long a lock may exist before its holder is assumed dead, in seconds. Longer
# than any rebuild: `tai discover` probes every installed tool and is the slowest
# thing that takes this lock. Being wrong in the other direction costs two writers
# in one temp file and a corrupt index, which is worse than waiting.
LOCK_STALE_SECONDS = 900


def _owner_gone(lock: Path) -> bool:
    """Report whether the lock's holder has died, dropping the lock if so.

    A lock that outlives its process would stop every future rebuild, silently,
    forever — the only symptom being that the rebuild did not run. So every
    question here has an answer that eventually says *yes*; it used to answer *no*
    to both it could not answer, and that is how a lock became permanent:

      * no readable pid — a holder that died between `mkdir` and the write, which
        is the disk-full case and the reason maintenance runs at all. It could
        equally be a holder caught mid-write, so the age decides, and a fresh one
        is left alone.
      * a pid that is alive — either the holder is still working, or the machine
        rebooted and the number was reused. The boot id separates those without a
        clock, and the age settles what it cannot.
    """
    if _same_boot(lock) and not _expired(lock):
        try:
            owner = int((lock / "pid").read_text())
            running = True
            try:
                os.kill(owner, 0)
            except PermissionError:
                running = True   # alive, and not ours to signal
            except ProcessLookupError:
                running = False  # gone
        except (OSError, ValueError):
            running = True       # no usable pid: assume a live holder
        if running:
            return False         # this boot, this minute, and still running
    shutil.rmtree(lock, ignore_errors=True)
    return True


def _same_boot(lock: Path) -> bool:
    """False when the lock was taken before the last reboot."""
    taken = _read(lock / "boot")
    now = _boot_id()
    # No boot id on either side means this cannot tell — so it does not claim to.
    return not (taken and now) or taken == now


def _expired(lock: Path) -> bool:
    """True when the lock is older than any rebuild could plausibly be."""
    try:
        return time.time() - lock.stat().st_mtime > LOCK_STALE_SECONDS
    except OSError:
        return False


def _read(path: Path) -> str:
    try:
        return path.read_text().strip()
    except OSError:
        return ""


def _take_lock(lock: Path) -> bool:
    """Create the maintenance lock, reclaiming it if its holder has died.

    A directory is the lock because `mkdir` is the one creation primitive that is
    atomic on every filesystem worth assuming. The retry is the reclaim: it can
    lose the race to the process that took the lock in the meantime, which is
    then the correct answer.
    """
    for _ in range(2):
        try:
            lock.mkdir(parents=True, exist_ok=False)
        except OSError:
            if not _owner_gone(lock):
                return False
        else:
            # The pid says whether the holder is running; the boot id says whether
            # that pid could still mean the same process. Both are written, and
            # either being unwritable leaves the age as the fallback, so a
            # read-only data directory still excludes other writers.
            for name, value in (("pid", str(os.getpid())), ("boot", _boot_id())):
                try:
                    (lock / name).write_text(value)
                except OSError:
                    pass
            return True
    return False


def _rebuild_index():
    """Rebuild both shell indexes under the maintenance lock.

    Returns the number of commands indexed, or None if another run holds the
    lock — in which case the index on disk is already being refreshed. A
    genuine build failure raises: the caller that has a terminal reports it, and
    the ones that do not go through _rebuild_quietly.
    """
    from tai.index import build
    lock = _lock_path()
    if not _take_lock(lock):
        return None
    try:
        return build()
    finally:
        shutil.rmtree(lock, ignore_errors=True)


def _rebuild_quietly() -> None:
    """Best-effort rebuild for a caller with no terminal to complain to.

    A shell prompt must not be able to fail, so everything the rebuild can raise
    stops here. `tai refresh` is where a failure is reported, because that is the
    one a person is watching.
    """
    try:
        _rebuild_index()
    except Exception:
        pass
