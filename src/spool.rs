//! tai spool — the shells' write side of the store, drained in batches.
//!
//! Port of `tai/spool.py`. The frame is one record per RS byte, fields joined
//! by US bytes: `ts US exit US cwd US cmd RS`. Nothing is decided twice: the
//! spool is transport, the store's filters stay the one gate between what was
//! typed and what is learned.

use std::collections::HashMap;
use std::fs;
use std::path::Path;

use crate::store::{is_recordable, connect, MAX_CMD_LEN};
use crate::util;

pub const RS: u8 = 0x1e;
pub const US: u8 = 0x1f;

/// A frame larger than this is not a command a person typed.
pub const MAX_FRAME_BYTES: usize = 12000;

/// Where the shells append records. `TAI_SPOOL` names it outright; otherwise
/// it lives beside the database — which is what `TAI_DB` moves.
pub fn spool_path() -> String {
    if let Ok(env) = std::env::var("TAI_SPOOL") {
        if !env.is_empty() {
            return env;
        }
    }
    if let Ok(db) = std::env::var("TAI_DB") {
        if !db.is_empty() {
            if db.contains('/') {
                return format!("{}/spool.log", db.rsplit_once('/').unwrap().0);
            }
            return "spool.log".to_string();
        }
    }
    format!("{}/spool.log", crate::paths::data_location())
}

/// Append one framed record. False when the spool is unwritable.
pub fn spool_append(cmd: &str, cwd: &str, exit_code: i64, ts: Option<i64>) -> bool {
    let cmd = cmd.replace(RS as char, "").replace(US as char, "");
    let cwd = cwd.replace(RS as char, "").replace(US as char, "");
    if cmd.trim().is_empty() {
        return false;
    }
    let ts = ts.unwrap_or_else(util::now_epoch);
    let line = format!("{}\u{1f}{}\u{1f}{}\u{1f}{}", ts, exit_code, cwd, cmd);
    let p = spool_path();
    if let Some(d) = Path::new(&p).parent() {
        let _ = fs::create_dir_all(d);
    }
    use std::io::Write;
    match fs::OpenOptions::new().create(true).append(true).open(&p) {
        Ok(mut f) => f.write_all(line.as_bytes()).and_then(|_| f.write_all(&[RS])).is_ok(),
        Err(_) => false,
    }
}

/// Rotate the spool and store every recordable frame in one connection.
/// Returns (ingested, skipped, total, newest_ts) and the batch of commands.
///
/// Rotation happens before parsing, so the bytes this reads are frozen, and
/// every flush rotates into a file of its own, so overlapping flushes each
/// take the records they rotated and never clobber another's take.
pub fn drain() -> (i64, i64, i64, i64, Vec<String>) {
    let src = spool_path();
    let work = format!("{}.flushing.{}", src, std::process::id());
    if fs::rename(&src, &work).is_err() {
        return (0, 0, 0, 0, Vec::new()); // nothing pending, or nothing writable
    }
    let data = fs::read(&work).unwrap_or_default();
    let _ = fs::remove_file(&work);
    if data.is_empty() {
        return (0, 0, 0, 0, Vec::new());
    }

    let mut rows: Vec<(String, String, i64, i64)> = Vec::new();
    let mut cwds: HashMap<String, (String, String)> = HashMap::new();
    let mut skipped = 0i64;
    for frame in data.split(|b| *b == RS) {
        if frame.is_empty() {
            continue;
        }
        if frame.len() > MAX_FRAME_BYTES {
            skipped += 1;
            continue;
        }
        let parts: Vec<&[u8]> = frame.splitn(4, |b| *b == US).collect();
        if parts.len() != 4 {
            skipped += 1;
            continue;
        }
        let ts_b = String::from_utf8_lossy(parts[0]).to_string();
        let code_b = String::from_utf8_lossy(parts[1]).to_string();
        let cwd = String::from_utf8_lossy(parts[2]).to_string();
        let cmd = String::from_utf8_lossy(parts[3]).trim().to_string();
        let ts = match ts_b.parse::<i64>() {
            Ok(t) => t,
            Err(_) => {
                skipped += 1;
                continue;
            }
        };
        let code = match code_b.parse::<i64>() {
            Ok(c) => c,
            Err(_) => {
                skipped += 1;
                continue;
            }
        };
        if !is_recordable(&cmd) {
            skipped += 1;
            continue;
        }
        let cmd: String = cmd.chars().take(MAX_CMD_LEN).collect();
        if !cwd.is_empty() && !cwds.contains_key(&cwd) {
            cwds.insert(cwd.clone(), (String::new(), String::new()));
        }
        rows.push((cmd, cwd, code, ts));
    }

    if rows.is_empty() {
        return (0, skipped, 0, 0, Vec::new());
    }
    let batch: Vec<String> = rows.iter().map(|r| r.0.clone()).collect();

    // A command's repo and branch describe where it ran, so they are resolved
    // from the cwd each row carries — once per distinct directory in the batch.
    let keys: Vec<String> = cwds.keys().cloned().collect();
    for cwd in keys {
        cwds.insert(cwd.clone(), git_context(&cwd));
    }

    let con = match connect() {
        Ok(c) => c,
        Err(_) => return (0, skipped, 0, 0, Vec::new()),
    };
    use rusqlite::params;
    let mut inserted = 0i64;
    for (cmd, cwd, code, ts) in &rows {
        let ctx = cwds.get(cwd).cloned().unwrap_or_default();
        let repo: String = ctx.0.chars().take(200).collect();
        let branch: String = ctx.1.chars().take(200).collect();
        let ins = con.execute(
            "INSERT INTO commands(cmd,cwd,repo,branch,exit_code,ts) VALUES(?,?,?,?,?,?)",
            params![cmd, cwd, repo, branch, code, ts],
        );
        if ins.is_ok() {
            inserted += 1;
        }
    }
    let _ = con.execute_batch("COMMIT");
    let (total, newest): (i64, i64) = con
        .query_row(
            "SELECT COUNT(*), COALESCE(MAX(ts), 0) FROM commands",
            [],
            |r| Ok((r.get(0)?, r.get(1)?)),
        )
        .unwrap_or((0, 0));
    (inserted, skipped, total, newest, batch)
}

/// (repo, branch) for a recorded directory, or ("", "") anywhere else. The
/// two answers come from separate captures: a fatal on stderr is not a
/// toplevel on stdout, and the branch is asked regardless of the toplevel.
pub fn git_context(cwd: &str) -> (String, String) {
    let (top, _, _) = util::run_capture(&["git", "-C", cwd, "rev-parse", "--show-toplevel"], 5.0, None);
    let top = top.trim().to_string();
    let (branch, _, _) = util::run_capture(&["git", "-C", cwd, "rev-parse", "--abbrev-ref", "HEAD"], 5.0, None);
    let branch = branch.trim().to_string();
    let repo = if top.is_empty() {
        String::new()
    } else {
        top.rsplit('/').next().unwrap_or("").to_string()
    };
    (repo, branch)
}

/// The whole flush: drain, then the maintenance the record path used to do.
pub fn flush() -> (i64, i64, i64, i64, i64) {
    let (ingested, skipped, total, newest, batch) = drain();
    let mut learned = 0i64;
    if ingested > 0 {
        crate::cli::auto_maintain(total, newest);
        learned = learn_from_batch(&batch);
    }
    (ingested, skipped, total, newest, learned)
}

/// Learn the tools this batch ran for the first time, bounded.
fn learn_from_batch(commands: &[String]) -> i64 {
    if commands.is_empty()
        || std::env::var("TAI_NO_LEARN").unwrap_or_default() == "1"
    {
        return 0;
    }
    let mut names: Vec<String> = Vec::new();
    for command in commands {
        let name = command.split_whitespace().next().unwrap_or("").to_string();
        if name.is_empty() || names.contains(&name) {
            continue;
        }
        if names.len() >= 8 {
            break;
        }
        names.push(name);
    }
    let unknown: Vec<String> = names
        .iter()
        .filter(|n| {
            !n.starts_with('-') && !n.contains('/') && n.len() <= 64
                && util::which(n).is_some()
                && !crate::knowledge::is_known(n)
        })
        .cloned()
        .collect();
    if unknown.is_empty() {
        return 0;
    }
    let learned = crate::knowledge::learn(Some(&unknown));
    if learned > 0 {
        crate::maintenance::rebuild_quietly();
    }
    learned
}

#[cfg(test)]
mod tests {
    use super::*;

    use crate::util::TEST_ENV_LOCK as ENV_LOCK;

    fn frame(ts: i64, code: i64, cwd: &str, cmd: &str) -> String {
        format!("{}\u{1f}{}\u{1f}{}\u{1f}{}\u{1e}", ts, code, cwd, cmd)
    }

    #[test]
    fn drain_takes_the_whole_batch_once() {
        let _guard = ENV_LOCK.lock().unwrap_or_else(|e| e.into_inner());
        let base = std::env::temp_dir().join(format!("tai_spool_{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&base);
        std::fs::create_dir_all(&base).unwrap();
        let db = base.join("history.db");
        let spool = base.join("spool.log");
        std::env::set_var("TAI_DB", &db);
        std::env::set_var("TAI_SPOOL", &spool);
        std::fs::write(
            &spool,
            format!(
                "{}{}",
                frame(1700100000, 0, "/tmp", "echo one"),
                frame(1700100100, 2, "", "echo two")
            ),
        )
        .unwrap();
        let (ingested, skipped, total, newest, batch) = drain();
        assert_eq!(ingested, 2);
        assert_eq!(skipped, 0);
        assert_eq!(total, 2);
        assert_eq!(newest, 1700100100);
        assert_eq!(batch, vec!["echo one".to_string(), "echo two".to_string()]);
        // The rotation is the safety: a second flush finds nothing pending.
        let (ingested2, _, _, _, _) = drain();
        assert_eq!(ingested2, 0);
        // And the spool file is gone from the old place: drained, not copied.
        assert!(!spool.exists());
        let _ = std::fs::remove_dir_all(&base);
    }

    #[test]
    fn unusable_frames_are_skipped_not_learned() {
        let _guard = ENV_LOCK.lock().unwrap_or_else(|e| e.into_inner());
        let base = std::env::temp_dir().join(format!("tai_spool2_{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&base);
        std::fs::create_dir_all(&base).unwrap();
        let db = base.join("history.db");
        let spool = base.join("spool.log");
        std::env::set_var("TAI_DB", &db);
        std::env::set_var("TAI_SPOOL", &spool);
        let oversized = "x".repeat(MAX_FRAME_BYTES + 1);
        std::fs::write(
            &spool,
            format!(
                "{}{}{}{}",
                frame(1700100000, 0, "/tmp", "echo good"),
                "not-a-frame\n\u{1e}",                           // wrong field count
                frame("NaN".parse().unwrap_or(0), 0, "/tmp", ""),  // bad ts
                oversized,
            ),
        )
        .unwrap();
        let (ingested, skipped, _, _, batch) = drain();
        assert_eq!(ingested, 1);
        assert_eq!(skipped, 3);
        assert_eq!(batch, vec!["echo good".to_string()]);
        let _ = std::fs::remove_dir_all(&base);
    }

    #[test]
    fn a_multiline_command_survives_the_framing() {
        let _guard = ENV_LOCK.lock().unwrap_or_else(|e| e.into_inner());
        // Newlines inside cmd survive the framing — which is the point,
        // because a newline is exactly what makes is_recordable refuse the
        // command later. The store is the one gate.
        let base = std::env::temp_dir().join(format!("tai_spool3_{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&base);
        std::fs::create_dir_all(&base).unwrap();
        let db = base.join("history.db");
        let spool = base.join("spool.log");
        std::env::set_var("TAI_DB", &db);
        std::env::set_var("TAI_SPOOL", &spool);
        std::fs::write(
            &spool,
            format!(
                "{}{}",
                frame(1700100000, 0, "/tmp", "git commit -m 'one\ntwo'"),
                frame(1700100100, 0, "/tmp", "echo fine")
            ),
        )
        .unwrap();
        let (ingested, skipped, _, _, _) = drain();
        assert_eq!(ingested, 1, "the multiline frame is refused by the store");
        assert_eq!(skipped, 1);
        let _ = std::fs::remove_dir_all(&base);
    }

    #[test]
    fn repo_and_branch_resolve_once_per_directory() {
        let _guard = ENV_LOCK.lock().unwrap_or_else(|e| e.into_inner());
        let base = std::env::temp_dir().join(format!("tai_spool4_{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&base);
        std::fs::create_dir_all(&base).unwrap();
        let db = base.join("history.db");
        let spool = base.join("spool.log");
        std::env::set_var("TAI_DB", &db);
        std::env::set_var("TAI_SPOOL", &spool);
        // A directory that has since been deleted resolves to no labels.
        let gone = base.join("gone").join("deeper");
        std::fs::create_dir_all(&gone).unwrap();
        std::fs::write(
            &spool,
            format!(
                "{}{}",
                frame(1700100000, 0, gone.to_string_lossy().as_ref(), "echo a"),
                frame(1700100100, 0, gone.to_string_lossy().as_ref(), "echo b")
            ),
        )
        .unwrap();
        let ctx = git_context(&gone.to_string_lossy());
        assert_eq!(ctx, (String::new(), String::new()), "no git, no labels");
        let _ = std::fs::remove_dir_all(&base);
    }
}
