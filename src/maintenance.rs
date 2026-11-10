//! Maintenance lock and index rebuild, shared by every tai command.
//!
//! Port of `tai/maintenance.py`. The index is rebuilt from the history store
//! by background triggers that can fire at the same moment from several
//! shells. The lock below is what makes that at most one writer, and it is
//! built so that every way of losing its holder eventually reclaims it — a
//! lock that outlives its process would stop every future rebuild, silently,
//! forever.

use std::fs;
use std::path::{Path, PathBuf};

/// How long a lock may exist before its holder is assumed dead, in seconds.
/// Longer than any rebuild: `tai discover` probes every installed tool and is
/// the slowest thing that takes this lock.
pub const LOCK_STALE_SECONDS: u64 = 900;

fn lock_path() -> PathBuf {
    crate::paths::db_path()
        .parent()
        .map(|p| p.join("maintenance.lock"))
        .unwrap_or_else(|| PathBuf::from("maintenance.lock"))
}

/// This boot's identity, or "" where the kernel does not publish one.
fn boot_id() -> String {
    fs::read_to_string("/proc/sys/kernel/random/boot_id")
        .map(|s| s.trim().to_string())
        .unwrap_or_default()
}

fn read(path: &Path) -> String {
    fs::read_to_string(path).map(|s| s.trim().to_string()).unwrap_or_default()
}

fn expired(lock: &Path) -> bool {
    fs::metadata(lock)
        .and_then(|m| m.modified())
        .ok()
        .map(|t| {
            t.elapsed().map(|e| e.as_secs() > LOCK_STALE_SECONDS).unwrap_or(false)
        })
        .unwrap_or(false)
}

fn same_boot(lock: &Path) -> bool {
    let taken = read(&lock.join("boot"));
    let now = boot_id();
    // No boot id on either side means this cannot tell — so it does not claim
    // to.
    (taken.is_empty() || now.is_empty()) || taken == now
}

/// Report whether the lock's holder has died, dropping the lock if so.
///
/// Every question here has an answer that eventually says *yes*:
///   * no readable pid — the age decides, and a fresh one is left alone;
///   * a pid that is alive — either the holder is still working, or the
///     machine rebooted and the number was reused. The boot id separates
///     those without a clock, and the age settles what it cannot.
fn owner_gone(lock: &Path) -> bool {
    let mut reclaim = false;
    if same_boot(lock) && !expired(lock) {
        let mut running = true;
        if let Ok(pid_text) = fs::read_to_string(lock.join("pid")) {
            if let Ok(owner) = pid_text.trim().parse::<i32>() {
                // kill(pid, 0): alive, or gone, or alive and not ours to signal.
                let rc = unsafe { libc::kill(owner, 0) };
                if rc == -1 && std::io::Error::last_os_error().raw_os_error() == Some(libc::ESRCH)
                {
                    running = false;
                }
            }
        }
        if running {
            return false; // this boot, this minute, and still running
        }
        reclaim = true;
    } else {
        reclaim = true;
    }
    if reclaim {
        let _ = fs::remove_dir_all(lock);
        return true;
    }
    false
}

/// Create the maintenance lock, reclaiming it if its holder has died.
///
/// A directory is the lock because `mkdir` is the one creation primitive that
/// is atomic on every filesystem worth assuming. The retry is the reclaim: it
/// can lose the race to the process that took the lock in the meantime, which
/// is then the correct answer.
fn take_lock(lock: &Path) -> bool {
    for _ in 0..2 {
        match fs::create_dir(lock) {
            Ok(_) => {
                // The pid says whether the holder is running; the boot id says
                // whether that pid could still mean the same process. Both
                // are written, and either being unwritable leaves the age as
                // the fallback.
                let _ = fs::write(lock.join("pid"), std::process::id().to_string());
                let _ = fs::write(lock.join("boot"), boot_id());
                return true;
            }
            Err(_) => {
                if !owner_gone(lock) {
                    return false;
                }
            }
        }
    }
    false
}

/// Rebuild both shell indexes under the maintenance lock.
///
/// Returns the number of commands indexed, or None if another run holds the
/// lock — in which case the index on disk is already being refreshed. A
/// genuine build failure raises: the caller that has a terminal reports it,
/// and the ones that do not go through `rebuild_quietly`.
pub fn rebuild_index() -> Option<Result<usize, String>> {
    let lock = lock_path();
    if !take_lock(&lock) {
        return None;
    }
    let out = crate::index::build(20000);
    let _ = fs::remove_dir_all(&lock);
    Some(out)
}

/// Best-effort rebuild for a caller with no terminal to complain to.
///
/// A shell prompt must not be able to fail, so everything the rebuild can
/// raise stops here. `tai refresh` is where a failure is reported, because
/// that is the one a person is watching.
pub fn rebuild_quietly() {
    let _ = rebuild_index();
}
