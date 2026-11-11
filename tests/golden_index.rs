//! The golden index: the fixture the migration harness pinned, rebuilt by
//! this binary, byte-compared to the file the Python implementation wrote.
//!
//! The fixture lives in scripts/parity_check.py (the migration harness that
//! proved the port); the golden under tests/golden/ was written by the
//! Python implementation with the same PATH pinned to tests/fakebin, so the
//! installed-command bonus is decided by tools this checkout carries and the
//! bytes are the same on every machine.

use std::path::PathBuf;
use std::process::Command;

fn repo() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
}

fn world(name: &str) -> (PathBuf, std::collections::HashMap<&'static str, String>) {
    let base = std::env::temp_dir().join(format!("tai_golden_{}", name));
    let _ = std::fs::remove_dir_all(&base);
    std::fs::create_dir_all(&base).unwrap();
    let db = base.join("history.db");
    let con = rusqlite::Connection::open(&db).unwrap();
    con.execute_batch(
        "CREATE TABLE commands(
            id INTEGER PRIMARY KEY, cmd TEXT NOT NULL, cwd TEXT DEFAULT '',
            repo TEXT DEFAULT '', branch TEXT DEFAULT '',
            exit_code INTEGER DEFAULT 0, ts INTEGER DEFAULT 0);
        CREATE TABLE history_imports(
            source TEXT PRIMARY KEY, inode INTEGER NOT NULL, offset INTEGER NOT NULL);
        CREATE TABLE cli_tools(
            name TEXT PRIMARY KEY, data TEXT NOT NULL, updated INTEGER NOT NULL);",
    )
    .unwrap();
    // The same fixture rows scripts/parity_check.py seeds: every rule the
    // builder applies is exercised here.
    const FIXTURE: &[(&str, &str, &str, &str, i64, i64)] = &[
        ("git status", "/home/u/proj", "proj", "main", 0, 1700000100),
        ("git status", "/home/u/proj", "proj", "main", 0, 1700000200),
        ("git status", "/home/u/proj", "proj", "main", 1, 1700000300),
        ("git add -A", "/home/u/proj", "proj", "main", 0, 1700000400),
        ("git commit -m 'fix the thing'", "/home/u/proj", "proj", "main", 0, 1700000500),
        ("git push", "/home/u/proj", "proj", "main", 0, 1700000600),
        ("ls -la", "/home/u", "", "", 0, 1700000700),
        ("ls -la", "/home/u", "", "", 0, 1700000800),
        ("ls -l", "/home/u", "", "", 0, 1700000900),
        ("cd ~/projects/myapp", "/home/u", "", "", 0, 1700001000),
        ("cd ..", "/home/u/proj", "proj", "main", 0, 1700001100),
        ("cd -", "/home/u/proj", "proj", "main", 0, 1700001200),
        ("cd Data/INI", "/home/u/proj", "proj", "main", 0, 1700001300),
        ("cat .zhistory", "/home/u", "", "", 0, 1700001400),
        ("chmod +x script", "/home/u", "", "", 0, 1700001500),
        ("sudo systemctl restart nginx", "/home/u", "", "", 0, 1700001600),
        ("docker compose up -d", "/home/u/proj", "proj", "main", 0, 1700001700),
        ("docker compose down", "/home/u/proj", "proj", "main", 0, 1700001800),
        ("python3 -m pytest", "/home/u/proj", "proj", "main", 0, 1700001900),
        ("npm run dev", "/home/u/proj", "proj", "main", 0, 1700002000),
        ("opencoe --serve", "/home/u", "", "", 127, 1700002100),
        ("opencoe --serve", "/home/u", "", "", 127, 1700002101),
        ("tai unsintall", "/home/u", "", "", 0, 1700002200),
        ("tai uninstall", "/home/u", "", "", 0, 1700002300),
        ("tai uninstall", "/home/u", "", "", 0, 1700002400),
        ("tai uninstall", "/home/u", "", "", 0, 1700002500),
        ("grep -rn pattern src/", "/home/u/proj", "proj", "main", 0, 1700002600),
        ("echo 'export API_TOKEN=xyz'", "/home/u", "", "", 0, 1700002700),
        ("curl -sS https://example.com/api", "/home/u", "", "", 0, 1700002800),
        ("cd /nowhere/gone", "/home/u", "", "", 0, 1700002900),
        ("ls ~/Documents", "/home/u", "", "", 0, 1700003000),
        ("DSH_DELETE_SESSIONS_DEBUG=1 systemctl status", "/home/u", "", "", 0, 1700003100),
        ("systemctl --user restart didsh.service", "/home/u", "", "", 0, 1700003200),
        ("date 1/2/2024", "/home/u", "", "", 0, 1700003300),
        ("git diff ~main", "/home/u/proj", "proj", "main", 0, 1700003400),
        ("mkdir -p directory", "/home/u", "", "", 0, 1700003500),
        ("printf 'password hunter2'", "/home/u", "", "", 0, 1700003600),
    ];
    {
        use rusqlite::params;
        for (cmd, cwd, repo, branch, code, ts) in FIXTURE {
            con.execute(
                "INSERT INTO commands(cmd,cwd,repo,branch,exit_code,ts) VALUES(?1,?2,?3,?4,?5,?6)",
                params![cmd, cwd, repo, branch, code, ts],
            )
            .unwrap();
        }
    }
    con.close().unwrap();
    let hist = base.join("zsh_history");
    std::fs::write(
        &hist,
        ": 1690000100:0;cargo build --release\n\
         : 1690000200:0;cargo test\n\
         : 1690000300:0;cd ~/.cargo\n\
         : 1690000400:0;ls -la\n\
         : 1690000500:0;vim .zshrc\n\
         plain-command --flag value\n\
         : 1690000600:0;rm -rf /tmp/old-build\n\
         : 1690000700:0;# a comment is not a command\n\
         : 1690000800:0;git pull --rebase\n",
    )
    .unwrap();
    let spool = base.join("spool.log");
    // The spool the shells would have written by now — the same frames
    // scripts/parity_check.py writes. Every command drains it on its way in,
    // so the refresh below is the flush that takes these.
    std::fs::write(&spool, spool_frames()).unwrap();
    let mut env = std::collections::HashMap::new();
    env.insert("HOME", base.to_string_lossy().to_string());
    env.insert("PATH", repo().join("tests/fakebin").to_string_lossy().to_string());
    env.insert("TAI_DB", db.to_string_lossy().to_string());
    env.insert("TAI_INDEX", base.join("zsh-index.zsh").to_string_lossy().to_string());
    env.insert("TAI_SPOOL", spool.to_string_lossy().to_string());
    env.insert("TAI_HISTORY_FILES", hist.to_string_lossy().to_string());
    env.insert("TAI_NO_LEARN", "1".to_string());
    env.insert("XDG_DATA_HOME", base.join("data").to_string_lossy().to_string());
    (base, env)
}

fn spool_frames() -> String {
    // The same frames scripts/parity_check.py writes: two good rows, a
    // multiline frame the store must refuse, a secret-shaped frame, and a
    // two-character row that is still vocabulary.
    let mut spool_text = String::new();
    for (ts, code, cwd, cmd) in [
        (1700100000i64, 0i64, "/home/u/proj", "cargo build --release"),
        (1700100100, 2, "/home/u", "ls --color=auto -la"),
        (1700100200, 0, "/home/u", "echo with\ttab and\nnewline stays framed"),
        (1700100300, 0, "/home/u", "curl -sS https://x/Y -H 'Authorization: Bearer TOKEN'"),
        (1700100400, 0, "", "short"),
    ] {
        spool_text.push_str(&format!("{}\u{1f}{}\u{1f}{}\u{1f}{}\u{1e}", ts, code, cwd, cmd));
    }
    spool_text
}

fn run(env: &std::collections::HashMap<&'static str, String>, args: &[&str]) -> String {
    let out = Command::new(env!("CARGO_BIN_EXE_tai"))
        .args(args)
        .envs(env.iter().map(|(k, v)| (std::ffi::OsString::from(*k), std::ffi::OsString::from(v))))
        .current_dir(std::env::temp_dir())
        .output()
        .unwrap();
    assert!(out.status.success(), "tai {:?} failed: {}", args, String::from_utf8_lossy(&out.stderr));
    String::from_utf8_lossy(&out.stdout).to_string()
}

#[test]
fn the_index_the_plugins_source_is_the_python_bytes() {
    let (base, env) = world("index");
    let out = run(&env, &["refresh"]);
    assert!(out.starts_with("✓ "), "refresh says what it did: {out:?}");

    for name in ["zsh-index.zsh", "bash-index.bash"] {
        let got = std::fs::read(base.join(name)).unwrap();
        let want = std::fs::read(repo().join("tests/golden").join(name)).unwrap();
        assert_eq!(
            got, want,
            "{} must be byte-identical to the index the Python builder wrote",
            name
        );
    }
    let _ = std::fs::remove_dir_all(&base);
}

#[test]
fn the_store_after_one_flush_is_the_python_store() {
    let (base, mut env) = world("flush");
    let db = env["TAI_DB"].clone();
    env.insert("TAI_HISTORY_FILES", base.join("zsh_history").to_string_lossy().to_string());
    // The refresh drains the pending spool on its way in — ingest first,
    // maintain after, the same order `tai record` keeps.
    run(&env, &["refresh"]);
    // A second flush finds the spool already taken: rotation is the safety.
    let out = run(&env, &["flush"]);
    assert_eq!(out, "✓ nothing pending\n");

    // The rows the store now holds, read back straight from SQLite.
    let con = rusqlite::Connection::open(&db).unwrap();
    let rows: Vec<String> = {
        let mut stmt = con
            .prepare("SELECT cmd || '|' || cwd || '|' || repo || '|' || branch || '|' || exit_code || '|' || ts FROM commands ORDER BY id")
            .unwrap();
        stmt.query_map([], |r| r.get(0))
            .unwrap()
            .filter_map(|r| r.ok())
            .collect()
    };
    // The secret-shaped frame is absent, the multiline frame is absent, and
    // the three good frames carry their git context — empty for a directory
    // that has since been deleted.
    assert!(
        !rows.iter().any(|r| r.contains("Bearer TOKEN")),
        "a secret-shaped command is never learned"
    );
    assert!(
        !rows.iter().any(|r| r.contains("newline stays framed")),
        "a multiline frame is not one command, so it is not stored"
    );
    assert!(rows.iter().any(|r| r.starts_with("cargo build --release|/home/u/proj|")));
    assert!(rows.iter().any(|r| r.starts_with("ls --color=auto -la|/home/u|")));
    assert!(rows.iter().any(|r| r.starts_with("short||")));
    con.close().unwrap();
    let _ = std::fs::remove_dir_all(&base);
}
