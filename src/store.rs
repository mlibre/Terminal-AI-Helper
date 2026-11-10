//! tai store — durable sqlite log + loader.
//!
//! Port of `tai/store.py`. One table of commands, one table of history-file
//! cursors, one table of cached `--help` knowledge. A store that cannot be
//! read raises (the caller refuses to publish over a good index); a *missing*
//! store reads as empty, because that is a first run.

use std::collections::HashSet;
use std::os::unix::fs::MetadataExt;
use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

use rusqlite::{params, Connection, OpenFlags};
use regex::Regex;
use std::sync::OnceLock;

use crate::paths;
use crate::util::now_epoch;

/// The longest command worth learning. A paste is not vocabulary.
pub const MAX_CMD_LEN: usize = 1000;

fn reject_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| {
        Regex::new(
            r"(?i:password|secret|token|AKIA|-----BEGIN)|__[A-Za-z0-9]+_(?:START|END)_[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}__|__[a-z0-9_]*status=\$\?",
        )
        .unwrap()
    })
}

fn ctrl_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"[\x00-\x1f\x7f]").unwrap())
}

/// True when a command is safe to store and worth ranking.
pub fn is_recordable(cmd: &str) -> bool {
    let cmd = cmd.trim();
    if cmd.chars().count() < 2 {
        // One key is not vocabulary. An accidental Enter on a stray character
        // carries no tool, no argument and no path — it just happened recently.
        return false;
    }
    if cmd.len() > MAX_CMD_LEN {
        return false;
    }
    // A command with a newline in it is one command here but two there.
    if cmd.contains('\n') || cmd.contains('\r') {
        return false;
    }
    // Raw control bytes are unpaintable as ghost text.
    if ctrl_re().is_match(cmd) {
        return false;
    }
    !reject_re().is_match(cmd)
}

/// One stored row: (cmd, cwd, repo, branch, exit_code, ts).
#[derive(Clone, Debug)]
pub struct Row {
    pub cmd: String,
    pub cwd: String,
    pub repo: String,
    pub branch: String,
    pub exit_code: i64,
    pub ts: i64,
}

fn open_connection(path: &str) -> Result<Connection, rusqlite::Error> {
    let con = Connection::open_with_flags(
        path,
        OpenFlags::SQLITE_OPEN_READ_WRITE | OpenFlags::SQLITE_OPEN_CREATE,
    )?;
    con.busy_timeout(std::time::Duration::from_millis(5000))?;
    con.execute_batch("PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL;")?;
    Ok(con)
}

/// The schema has one owner: every table is created by every connector.
pub fn ensure_db(con: &Connection) -> Result<(), rusqlite::Error> {
    con.execute_batch(
        "CREATE TABLE IF NOT EXISTS commands(
            id INTEGER PRIMARY KEY,
            cmd TEXT NOT NULL,
            cwd TEXT DEFAULT '',
            repo TEXT DEFAULT '',
            branch TEXT DEFAULT '',
            exit_code INTEGER DEFAULT 0,
            ts INTEGER DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_cmd ON commands(cmd);
        CREATE INDEX IF NOT EXISTS idx_ts ON commands(ts DESC);
        CREATE INDEX IF NOT EXISTS idx_cwd ON commands(cwd);
        CREATE TABLE IF NOT EXISTS history_imports(
            source TEXT PRIMARY KEY,
            inode INTEGER NOT NULL,
            offset INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS cli_tools(
            name TEXT PRIMARY KEY,
            data TEXT NOT NULL,
            updated INTEGER NOT NULL
        );",
    )?;
    let mut cols = con.prepare("PRAGMA table_info(commands)")?;
    let mut rows = cols.query([])?;
    let mut has_branch = false;
    while let Some(r) = rows.next()? {
        let name: String = r.get(1)?;
        if name == "branch" {
            has_branch = true;
        }
    }
    if !has_branch {
        con.execute_batch("ALTER TABLE commands ADD COLUMN branch TEXT DEFAULT ''")?;
    }
    Ok(())
}

pub fn connect() -> Result<Connection, String> {
    let p = paths::db_path();
    if let Some(dir) = p.parent() {
        let _ = std::fs::create_dir_all(dir);
    }
    let con = open_connection(&p.to_string_lossy()).map_err(|e| e.to_string())?;
    ensure_db(&con).map_err(|e| e.to_string())?;
    Ok(con)
}

/// Why the database is unusable, or "" when it is fine.
///
/// Two things it must not say: nothing, on a machine that has never run tai
/// (the file does not exist yet), and nothing, about a store with no table yet.
pub fn schema_note() -> String {
    let path = paths::db_path();
    if !path.exists() {
        return String::new();
    }
    let con = match Connection::open_with_flags(
        path.to_string_lossy().as_ref(),
        OpenFlags::SQLITE_OPEN_READ_ONLY,
    ) {
        Ok(c) => c,
        Err(e) => return format!("database unusable: {}", e),
    };
    let mut stmt = match con.prepare("PRAGMA table_info(commands)") {
        Ok(s) => s,
        Err(e) => return format!("database unusable: {}", e),
    };
    let cols = match stmt.query_map([], |r| r.get::<_, String>(1)) {
        Ok(rows) => rows.filter_map(|r| r.ok()).collect::<Vec<String>>(),
        Err(e) => return format!("database unusable: {}", e),
    };
    if cols.is_empty() {
        return String::new(); // a store nobody has written to yet
    }
    if !cols.iter().any(|c| c == "branch") {
        return "database schema is out of date (no branch column)".to_string();
    }
    String::new()
}

/// Store one command, and return (inserted, total_rows, newest_ts).
pub fn append_and_count(
    cmd: &str,
    cwd: &str,
    repo: &str,
    branch: &str,
    exit_code: i64,
) -> (bool, i64, i64) {
    let cmd = cmd.trim().to_string();
    if !is_recordable(&cmd) {
        let (n, _) = count();
        return (false, n, 0);
    }
    let repo: String = repo.chars().take(200).collect();
    let branch: String = branch.chars().take(200).collect();
    match connect() {
        Ok(con) => {
            let ins = con.execute(
                "INSERT INTO commands(cmd,cwd,repo,branch,exit_code,ts) VALUES(?,?,?,?,?,?)",
                params![cmd, cwd, repo, branch, exit_code, now_epoch()],
            );
            match ins {
                Ok(_) => {
                    let r = con
                        .query_row("SELECT COUNT(*), MAX(ts) FROM commands", [], |row| {
                            Ok((row.get::<_, i64>(0)?, row.get::<_, i64>(1).unwrap_or(0)))
                        })
                        .unwrap_or((0, 0));
                    (true, r.0, r.1)
                }
                Err(_) => {
                    let (n, _) = count();
                    (false, n, 0)
                }
            }
        }
        Err(_) => {
            let (n, _) = count();
            (false, n, 0)
        }
    }
}

/// Delete already-stored rows that would no longer be recorded.
pub fn purge_unrecordable() -> i64 {
    match connect() {
        Ok(con) => {
            let rows: Vec<(i64, String)> = match con
                .prepare("SELECT id,cmd FROM commands")
                .and_then(|mut s| {
                    s.query_map([], |r| Ok((r.get::<_, i64>(0)?, r.get::<_, String>(1)?)))
                        .map(|it| it.filter_map(|x| x.ok()).collect())
                }) {
                Ok(v) => v,
                Err(_) => return 0,
            };
            let stale: Vec<i64> = rows
                .into_iter()
                .filter(|(_, cmd)| !is_recordable(cmd))
                .map(|(id, _)| id)
                .collect();
            let n = stale.len() as i64;
            if n > 0 {
                if con.execute_batch("BEGIN").is_err() {
                    return 0;
                }
                for id in &stale {
                    let _ = con.execute("DELETE FROM commands WHERE id=?", params![id]);
                }
                if con.execute_batch("COMMIT").is_err() {
                    return 0;
                }
            }
            n
        }
        Err(_) => 0,
    }
}

/// Keep the most recent `limit` rows, returned oldest-first so the engine
/// builds correct prev->next sequence pairs.
///
/// A store that cannot be read raises, and that is the whole point: an empty
/// list is indistinguishable from a brand-new install. A *missing* database is
/// still an empty list, not an error: that is a first run.
pub fn load_rows(limit: i64) -> Result<Vec<Row>, String> {
    if !paths::db_path().exists() {
        return Ok(Vec::new());
    }
    let con = connect()?;
    let total: i64 = con
        .query_row("SELECT COUNT(*) FROM commands", [], |r| r.get(0))
        .map_err(|e| e.to_string())?;
    let off = (total - limit).max(0);
    let mut stmt = con
        .prepare("SELECT cmd,cwd,repo,branch,exit_code,ts FROM commands ORDER BY id ASC LIMIT ?1 OFFSET ?2")
        .map_err(|e| e.to_string())?;
    let rows = stmt
        .query_map(params![limit, off], |r| {
            Ok(Row {
                cmd: r.get(0)?,
                cwd: r.get(1)?,
                repo: r.get(2)?,
                branch: r.get(3)?,
                exit_code: r.get(4)?,
                ts: r.get(5)?,
            })
        })
        .map_err(|e| e.to_string())?
        .filter_map(|r| r.ok())
        .collect();
    Ok(rows)
}

/// Delete rows whose paths no longer exist, and return the count.
///
/// Deliberately judged with no fallback cwd: deleting a row on a guess is
/// destruction, hiding a suggestion on one is harmless.
pub fn purge_stale() -> i64 {
    if !paths::enabled() {
        return 0;
    }
    let rows: Vec<(String, String)> = match connect() {
        Ok(con) => match con.prepare("SELECT id,cmd,cwd FROM commands").and_then(|mut s| {
            s.query_map([], |r| Ok((r.get::<_, String>(1)?, r.get::<_, String>(2)?)))
                .map(|it| it.filter_map(|x| x.ok()).collect())
        }) {
            Ok(v) => v,
            Err(_) => return 0,
        },
        Err(_) => return 0,
    };
    let mut grouped: std::collections::HashMap<String, HashSet<String>> =
        std::collections::HashMap::new();
    for (cmd, cwd) in rows {
        grouped.entry(cmd).or_default().insert(cwd);
    }
    let entries: Vec<(String, Vec<String>)> = grouped
        .into_iter()
        .map(|(cmd, cwds)| (cmd, cwds.into_iter().collect()))
        .collect();
    let stale = paths::stale_commands(&entries, "");
    if stale.is_empty() {
        return 0;
    }
    match connect() {
        Ok(con) => {
            let _ = con.execute_batch("BEGIN");
            for cmd in &stale {
                let _ = con.execute("DELETE FROM commands WHERE cmd=?", params![cmd]);
            }
            let _ = con.execute_batch("COMMIT");
            stale.len() as i64
        }
        Err(_) => 0,
    }
}

/// Delete every row whose command is exactly one of `names`; return the count.
/// Matching is exact on the stored text: a name with arguments is one command.
pub fn forget_commands(names: &[String]) -> Result<i64, String> {
    let mut cleaned: Vec<String> = Vec::new();
    for name in names {
        let name = name.trim().to_string();
        if !name.is_empty() && !cleaned.contains(&name) {
            cleaned.push(name);
        }
    }
    if cleaned.is_empty() {
        return Ok(0);
    }
    let con = connect()?;
    let marks = cleaned.iter().map(|_| "?").collect::<Vec<_>>().join(",");
    let sql = format!("DELETE FROM commands WHERE cmd IN ({})", marks);
    let p: Vec<&str> = cleaned.iter().map(|s| s.as_str()).collect();
    let n = con
        .execute(&sql, rusqlite::params_from_iter(cleaned.iter()))
        .map_err(|e| e.to_string())?;
    let _ = con.execute_batch("COMMIT");
    Ok(n as i64)
}

/// (total rows, distinct commands).
pub fn count() -> (i64, i64) {
    match connect() {
        Ok(con) => {
            let n: i64 = con
                .query_row("SELECT COUNT(*) FROM commands", [], |r| r.get(0))
                .unwrap_or(0);
            let d: i64 = con
                .query_row("SELECT COUNT(DISTINCT cmd) FROM commands", [], |r| r.get(0))
                .unwrap_or(0);
            (n, d)
        }
        Err(_) => (0, 0),
    }
}

/// Return supported history files, including a custom HISTFILE.
fn history_files() -> Vec<std::path::PathBuf> {
    let mut candidates: Vec<PathBuf> = Vec::new();
    if let Ok(custom) = std::env::var("HISTFILE") {
        let custom = custom.trim().to_string();
        if !custom.is_empty() {
            candidates.push(PathBuf::from(paths::expanduser(&custom)));
        }
    }
    let home = paths::home_dir();
    candidates.push(PathBuf::from(format!("{}/.zsh_history", home)));
    candidates.push(PathBuf::from(format!("{}/.bash_history", home)));
    if let Ok(extra) = std::env::var("TAI_HISTORY_FILES") {
        for item in extra.split(':') {
            let item = item.trim();
            if !item.is_empty() {
                candidates.push(PathBuf::from(paths::expanduser(item)));
            }
        }
    }
    let mut result: Vec<PathBuf> = Vec::new();
    let mut seen = HashSet::new();
    for path in candidates {
        let key = path.to_string_lossy().to_string();
        if seen.insert(key) {
            result.push(path);
        }
    }
    result
}

/// Import all available history rows, and only new rows on later runs.
pub fn import_shell_history() -> i64 {
    match connect() {
        Ok(con) => import_rows(&con),
        Err(_) => 0,
    }
}

fn import_rows(con: &Connection) -> i64 {
    let mut imported = 0i64;
    let now = now_epoch();
    let home = paths::home_dir();
    for f in history_files() {
        let stat = match fs_meta(&f) {
            Some(s) => s,
            None => continue,
        };
        let size = stat.len();
        let state: Option<(i64, i64)> = con
            .query_row(
                "SELECT inode, offset FROM history_imports WHERE source=?1",
                params![f.to_string_lossy()],
                |r| Ok((r.get(0)?, r.get(1)?)),
            )
            .ok();
        let mut start: u64 = 0;
        let mut first_import = state.is_none();
        if let Some((inode, offset)) = state {
            if inode == stat.ino() as i64 && offset >= 0 && (offset as u64) <= size {
                start = offset as u64;
            } else {
                first_import = true; // replaced or truncated: read from the start
            }
        }
        let raw = match std::fs::read(&f) {
            Ok(mut b) => {
                if (start as u64) <= b.len() as u64 {
                    b.drain(..start as usize);
                    b
                } else {
                    continue;
                }
            }
            Err(_) => continue,
        };
        if raw.is_empty() {
            continue;
        }
        let text = String::from_utf8_lossy(&raw).to_string();
        let lines: Vec<&str> = text.lines().collect();
        if lines.is_empty() {
            continue;
        }
        let mut batch: Vec<(String, i64)> = Vec::new();
        let mut plain_seen: HashSet<String> = HashSet::new();
        for line in lines {
            let (mut cmd, mut ts): (String, Option<i64>) = if line.starts_with(": ") {
                let parts = line.splitn(2, ';');
                let p: Vec<&str> = parts.collect();
                let c = if p.len() > 1 { p[1].to_string() } else { String::new() };
                let t = p[0]
                    .split(':')
                    .nth(1)
                    .and_then(|s| s.trim().split(':').next().and_then(|x| x.parse::<i64>().ok()));
                (c, t)
            } else {
                (line.to_string(), None)
            };
            cmd = cmd.trim().to_string();
            if !is_recordable(&cmd) || cmd.starts_with('#') {
                continue;
            }
            if ts.is_none() {
                // A history line with no timestamp is indistinguishable from the
                // same command an earlier import already claimed. Claim an
                // unstamped command once, whichever file or round it appears in.
                if plain_seen.contains(&cmd) {
                    continue;
                }
                let probe = cmd.chars().take(MAX_CMD_LEN).collect::<String>();
                let exists: Option<i64> = con
                    .query_row(
                        "SELECT 1 FROM commands WHERE cmd=?1 LIMIT 1",
                        params![probe],
                        |r| r.get(0),
                    )
                    .ok();
                if exists.is_some() {
                    continue;
                }
                plain_seen.insert(cmd.clone());
                ts = Some(now);
            } else if first_import {
                // Avoid duplicating rows created before the cursor table existed.
                let probe = cmd.chars().take(MAX_CMD_LEN).collect::<String>();
                let exists: Option<i64> = con
                    .query_row(
                        "SELECT 1 FROM commands WHERE cmd=?1 AND ts=?2 LIMIT 1",
                        params![probe, ts],
                        |r| r.get(0),
                    )
                    .ok();
                if exists.is_some() {
                    continue;
                }
            }
            batch.push((cmd.chars().take(MAX_CMD_LEN).collect(), ts.unwrap_or(now)));
        }
        // One transaction per file, and the cursor moves only with the rows it
        // accounts for.
        let _ = con.execute_batch("BEGIN");
        let mut ok = true;
        for (cmd, ts) in &batch {
            if con
                .execute(
                    "INSERT INTO commands(cmd,cwd,repo,branch,exit_code,ts) VALUES(?1,'','', '',0,?2)",
                    params![cmd, ts],
                )
                .is_err()
            {
                ok = false;
                break;
            }
        }
        if ok {
            let up = con.execute(
                "INSERT INTO history_imports(source,inode,offset) VALUES(?1,?2,?3) \
                 ON CONFLICT(source) DO UPDATE SET inode=excluded.inode, offset=excluded.offset",
                params![f.to_string_lossy(), stat.ino() as i64, (start + raw.len() as u64) as i64],
            );
            if up.is_err() {
                ok = false;
            }
        }
        if ok {
            if con.execute_batch("COMMIT").is_err() {
                let _ = con.execute_batch("ROLLBACK");
                continue;
            }
            imported += batch.len() as i64;
        } else {
            let _ = con.execute_batch("ROLLBACK");
        }
        let _ = home;
    }
    imported
}

fn fs_meta(p: &Path) -> Option<std::fs::Metadata> {
    std::fs::metadata(p).ok()
}

#[cfg(test)]
mod tests {
    use super::*;

    /// The tests that point TAI_DB at their own scratch world run in one
    /// process; a lock keeps two of them from moving the same env var under
    /// each other's feet.
    use crate::util::TEST_ENV_LOCK as ENV_LOCK;

    #[test]
    fn the_recordable_gate() {
        assert!(is_recordable("ls -la"));
        assert!(is_recordable("echo 'héllo wörld'"));
        assert!(is_recordable("rm -rf /")); // recordable; the JEV gate decides offering
        assert!(is_recordable("git commit -m 'fix the thing'"));
        // One key is not vocabulary.
        assert!(!is_recordable("y"));
        assert!(!is_recordable(""));
        assert!(!is_recordable("  "));
        // A newline makes it two commands, and either is not the line.
        assert!(!is_recordable("git commit -m 'one\n two'"));
        assert!(!is_recordable("echo one\r\ntwo"));
        // Raw control bytes are unpaintable as ghost text.
        assert!(!is_recordable("echo \x1b[31mred\x1b[0m"));
        assert!(!is_recordable("echo a\x7fb"));
        // Secret-shaped words, case-insensitively.
        assert!(!is_recordable("echo password=hunter2"));
        assert!(!is_recordable("curl -H 'Authorization: Bearer TOKEN'"));
        assert!(!is_recordable("AWS AKIA12345 x"));
        assert!(!is_recordable("cat -----BEGIN PRIVATE KEY"));
        // Session-persistence harness envelopes are not commands.
        assert!(!is_recordable(
            "__DSH_START_12345678-1234-1234-1234-123456789abc__; eval ls"
        ));
        assert!(!is_recordable("__dsh_persistent_bash_status=$?"));
        // Over-long is not vocabulary.
        let long = format!("echo {}", "x".repeat(1001));
        assert!(!is_recordable(&long));
        // At the bound it is.
        let edge = format!("echo {}", "x".repeat(994));
        assert!(is_recordable(&edge));
    }

    #[test]
    fn read_absence_is_empty_and_creates_nothing() {
        let _guard = ENV_LOCK.lock().unwrap_or_else(|e| e.into_inner());
        let absent = std::env::temp_dir().join(format!(
            "tai_rules_absent_{}.db",
            std::process::id()
        ));
        let _ = std::fs::remove_file(&absent);
        std::env::set_var("TAI_DB", &absent);
        assert!(load_rows(50000).unwrap().is_empty());
        assert!(!absent.exists(), "asking must not create the database it asked about");
        let _ = std::fs::remove_file(&absent);
    }

    #[test]
    fn read_failure_raises_not_reads_as_fresh() {
        let _guard = ENV_LOCK.lock().unwrap_or_else(|e| e.into_inner());
        let garbage = std::env::temp_dir().join(format!(
            "tai_rules_garbage_{}.db",
            std::process::id()
        ));
        std::fs::write(&garbage, b"this is not a database, it just looks like one").unwrap();
        std::env::set_var("TAI_DB", &garbage);
        assert!(
            load_rows(50000).is_err(),
            "a garbage store must raise, not read as a fresh install"
        );
        let _ = std::fs::remove_file(&garbage);
    }

    #[test]
    fn forget_is_exact_not_prefix() {
        let _guard = ENV_LOCK.lock().unwrap_or_else(|e| e.into_inner());
        let db = std::env::temp_dir().join(format!("tai_forget_{}.db", std::process::id()));
        std::env::set_var("TAI_DB", &db);
        let _ = std::fs::remove_file(&db);
        let (ok, _, _) = append_and_count("git status", "", "", "", 0);
        assert!(ok);
        let (ok, _, _) = append_and_count("git status --short", "", "", "", 0);
        assert!(ok);
        let n = forget_commands(&["git status".to_string()]).unwrap();
        assert_eq!(n, 1, "forgetting `git` must not take `git status --short` with it");
        let rows = load_rows(50000).unwrap();
        assert_eq!(rows.len(), 1);
        assert_eq!(rows[0].cmd, "git status --short");
        let _ = std::fs::remove_file(&db);
    }

    #[test]
    fn history_import_is_idempotent() {
        let _guard = ENV_LOCK.lock().unwrap_or_else(|e| e.into_inner());
        let base = std::env::temp_dir().join(format!("tai_import_{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&base);
        std::fs::create_dir_all(&base).unwrap();
        let hist = base.join("zsh_history");
        std::fs::write(
            &hist,
            ": 1690000100:0;cargo build --release\n: 1690000200:0;cargo test\nplain --line\n",
        )
        .unwrap();
        let db = base.join("history.db");
        std::env::set_var("TAI_DB", &db);
        std::env::set_var("TAI_HISTORY_FILES", &hist);
        let first = import_shell_history();
        assert_eq!(first, 3, "two stamped rows and one unstamped row");
        let second = import_shell_history();
        assert_eq!(second, 0, "a second import must not duplicate anything");
        let rows = load_rows(50000).unwrap();
        assert_eq!(rows.len(), 3);
        assert_eq!(rows[0].cmd, "cargo build --release");
        assert_eq!(rows[0].ts, 1690000100);
        assert_eq!(rows[2].cmd, "plain --line");
        let _ = std::fs::remove_dir_all(&base);
    }
}
