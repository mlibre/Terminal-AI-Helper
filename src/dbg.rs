//! The realtime trail, on the binary's side.
//!
//! The shell plugin and this binary write one log, the file
//! `$TAI_DEBUG_FILE` names or `~/.cache/tai/debug.log` by default, with one
//! line shape: `<epoch> tai <zsh|bin> pid=<pid> <tag>: <text>`. The plugin's
//! half lives in plugins/zsh/debug.zsh; this half answers for everything the
//! process does — every invocation's argv, every command's begin and end with
//! its code and duration, and the panics, through the hook `main` installs.
//!
//! Every line is one open-append-write-flush on the same file the shell
//! appends to. Nothing is buffered for later, because the bug that asked for
//! this facility kills processes: whatever the binary was doing when it died,
//! the lines it managed to write are already on the disk. There is no lock on
//! the file worth taking either — an append of one line under the pipe buffer
//! size is atomic in practice, and a debug trail is allowed to interleave.

use std::fs::OpenOptions;
use std::io::Write;
use std::path::PathBuf;
use std::sync::OnceLock;
use std::time::{SystemTime, UNIX_EPOCH};

/// The file the trail writes to. `TAI_DEBUG_FILE` names it; the default is
/// beside the plugin's own caches, which is where `tai debug` looks first.
pub fn file_path() -> PathBuf {
    if let Ok(f) = std::env::var("TAI_DEBUG_FILE") {
        if !f.trim().is_empty() {
            return PathBuf::from(f);
        }
    }
    let base = std::env::var("XDG_CACHE_HOME")
        .ok()
        .filter(|s| !s.trim().is_empty())
        .map(PathBuf::from)
        .unwrap_or_else(|| {
            PathBuf::from(crate::paths::home_dir()).join(".cache")
        });
    base.join("tai").join("debug.log")
}

/// Is the trail on? The plugin and this binary share one switch: `TAI_DEBUG=1`.
pub fn enabled() -> bool {
    static ON: OnceLock<bool> = OnceLock::new();
    *ON.get_or_init(|| std::env::var("TAI_DEBUG").map(|v| v.trim() == "1").unwrap_or(false))
}

/// Microseconds since the epoch, the same clock `zsh/datetime`'s
/// EPOCHREALTIME speaks: whole seconds, a dot, the fraction. The two sides'
/// lines interleave by this column, so the order in the file is the order the
/// events happened in.
fn epoch_us() -> u128 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_micros())
        .unwrap_or(0)
}

/// One line, now. The whole point is that this is the last thing the
/// process does before moving on: open in append mode, write, flush, and the
/// file descriptor dies with the block. A crash between two events costs the
/// unwritten one, never the written ones.
pub fn log(tag: &str, msg: &str) {
    if !enabled() {
        return;
    }
    let path = file_path();
    if let Some(dir) = path.parent() {
        let _ = std::fs::create_dir_all(dir);
    }
    let line = format!(
        "{}.{} tai bin pid={} {}: {}\n",
        epoch_us() / 1_000_000,
        format!("{:06}", epoch_us() % 1_000_000),
        std::process::id(),
        tag,
        msg
    );
    if let Ok(mut f) = OpenOptions::new().create(true).append(true).open(&path) {
        let _ = f.write_all(line.as_bytes());
        let _ = f.flush();
    }
}

/// Panic messages go where the trail is. A plugin crash report that names
/// zsh can still be the binary's panic arriving as the shell's exit — a child
/// that dies does not crash the shell, but a widget's stderr can reach the
/// terminal, and a panic in a process the *user* ran from the line is
/// indistinguishable from a shell crash to the person watching the window.
/// The hook puts the payload, the location and the argv of the dying process
/// on the same trail the shell wrote its keystrokes to, then the default hook
/// does its own work.
pub fn install_panic_hook() {
    let default = std::panic::take_hook();
    std::panic::set_hook(Box::new(move |info| {
        let msg = if let Some(s) = info.payload().downcast_ref::<&str>() {
            (*s).to_string()
        } else if let Some(s) = info.payload().downcast_ref::<String>() {
            s.clone()
        } else {
            "unknown panic payload".to_string()
        };
        let at = match info.location() {
            Some(l) => format!("{}:{}", l.file(), l.line()),
            None => "unknown location".to_string(),
        };
        log(
            "panic",
            &format!(
                "{} at {} argv={:?}",
                msg,
                at,
                std::env::args().collect::<Vec<_>>()
            ),
        );
        default(info);
    }));
}
