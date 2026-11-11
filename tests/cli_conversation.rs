//! The CLI conversation: what each verb prints, what it costs the store, and
//! what it refuses to do — one process per question, the way a user asks.

use std::collections::HashMap;
use std::process::Command;

fn world(name: &str) -> (std::path::PathBuf, HashMap<&'static str, String>) {
    let base = std::env::temp_dir().join(format!("tai_cli_conv_{}", name));
    let _ = std::fs::remove_dir_all(&base);
    std::fs::create_dir_all(&base).unwrap();
    let mut env = HashMap::new();
    env.insert("HOME", base.to_string_lossy().to_string());
    env.insert("PATH", "/usr/bin:/bin".to_string());
    env.insert("TAI_DB", base.join("history.db").to_string_lossy().to_string());
    env.insert("TAI_INDEX", base.join("zsh-index.zsh").to_string_lossy().to_string());
    env.insert("TAI_SPOOL", base.join("spool.log").to_string_lossy().to_string());
    env.insert("TAI_NO_LEARN", "1".to_string());
    env.insert("TAI_NO_ENGINE_CACHE", "1".to_string());
    (base, env)
}

fn tai(env: &HashMap<&'static str, String>, args: &[&str]) -> (i32, String, String) {
    let out = Command::new(env!("CARGO_BIN_EXE_tai"))
        .args(args)
        .envs(env.iter().map(|(k, v)| (std::ffi::OsString::from(*k), std::ffi::OsString::from(v))))
        .current_dir(std::env::temp_dir())
        .output()
        .unwrap();
    (
        out.status.code().unwrap_or(-1),
        String::from_utf8_lossy(&out.stdout).to_string(),
        String::from_utf8_lossy(&out.stderr).to_string(),
    )
}

#[test]
fn record_says_whether_it_stored_and_why_not() {
    let (base, env) = world("record");
    let (code, out, _) = tai(&env, &["record", "--cwd", "/tmp", "echo boundary"]);
    assert_eq!(code, 0);
    assert_eq!(out, "✓ recorded\n");
    // A secret-shaped command is refused, and the refusal says why.
    let (code, out, _) = tai(&env, &["record", "echo password=hunter2"]);
    assert_eq!(code, 1);
    assert!(
        out.starts_with("tai: not recorded — '"),
        "the rejection names the rule: {out:?}"
    );
    assert!(out.contains("'echo password=hunter2' looks like"), "the shown command is Python-repr quoted: {out:?}");
    // Nothing at all is its own answer.
    let (code, out, _) = tai(&env, &["record", "   "]);
    assert_eq!(code, 1);
    assert_eq!(out, "tai: nothing to record — no command was given\n");
    // A long, secret-shaped command is shown cut, with the ellipsis saying
    // so: the cut is about the message, not the store bound.
    let long = format!("echo password={}", "y".repeat(60));
    let (code, out, _) = tai(&env, &["record", &long]);
    assert_eq!(code, 1);
    assert!(out.contains("…'"), "the shown command is cut at 40: {out:?}");
    let _ = std::fs::remove_dir_all(&base);
}

#[test]
fn flush_says_what_it_found() {
    let (base, env) = world("flush");
    // Nothing pending is a fact, not an error.
    let (code, out, _) = tai(&env, &["flush"]);
    assert_eq!(code, 0);
    assert_eq!(out, "✓ nothing pending\n");
    // Two frames land as one batch.
    let spool = base.join("spool.log");
    std::fs::write(
        &spool,
        format!("1700100000\u{1f}0\u{1f}/tmp\u{1f}echo one\u{1e}1700100100\u{1f}3\u{1f}\u{1f}echo two\u{1e}"),
    )
    .unwrap();
    let (code, out, _) = tai(&env, &["flush"]);
    assert_eq!(code, 0);
    assert_eq!(out, "✓ ingested 2\n");
    let _ = std::fs::remove_dir_all(&base);
}

#[test]
fn forget_speaks_in_the_users_words() {
    let (base, env) = world("forget");
    // No names is a usage question, and usage is exit 2.
    let (code, out, _) = tai(&env, &["forget"]);
    assert_eq!(code, 2);
    assert!(out.starts_with("usage: tai forget <command>"));
    // A name that matches nothing is visible rather than silent.
    let (code, out, _) = tai(&env, &["forget", "never-typed-anywhere"]);
    assert_eq!(code, 0);
    assert!(
        out.starts_with("nothing forgotten — no stored row matches `never-typed-anywhere`"),
        "{out:?}"
    );
    let _ = tai(&env, &["record", "git sta"]);
    let (code, out, _) = tai(&env, &["forget", "git sta"]);
    assert_eq!(code, 0);
    assert_eq!(out, "forgot 1 row for 1 command: `git sta`; index rebuilt\n");
    // The name is exact: forgetting `git` does not take `git sta`'s neighbours.
    let _ = tai(&env, &["record", "git push"]);
    let (code, out, _) = tai(&env, &["forget", "git"]);
    assert_eq!(code, 0);
    assert!(out.starts_with("nothing forgotten"));
    let _ = std::fs::remove_dir_all(&base);
}

#[test]
fn purge_says_what_it_purged_and_what_it_did_not() {
    let (base, env) = world("purge");
    let (code, out, _) = tai(&env, &["purge"]);
    assert_eq!(code, 0);
    assert_eq!(
        out,
        "nothing to purge\nrun 'tai purge --stale' to also drop commands whose paths are gone\n"
    );
    let _ = std::fs::remove_dir_all(&base);
}

#[test]
fn version_says_what_is_running_and_where() {
    let (base, env) = world("version");
    let (code, out, _) = tai(&env, &["version"]);
    assert_eq!(code, 0);
    let version = std::fs::read_to_string(env!("CARGO_MANIFEST_DIR").to_string() + "/VERSION")
        .unwrap()
        .trim()
        .to_string();
    assert!(out.starts_with(&format!("tai {}\n", version)), "{out:?}");
    assert!(out.contains("zsh plugin not wired"));
    assert!(out.contains("bash plugin not wired"));
    let _ = std::fs::remove_dir_all(&base);
}

#[test]
fn suggest_answers_json_and_lines() {
    let (base, env) = world("suggest");
    let _ = tai(&env, &["record", "--cwd", "/tmp", "git status"]);
    let _ = tai(&env, &["record", "--cwd", "/tmp", "git status"]);
    let (code, out, _) = tai(&env, &["suggest", "--json", "git st"]);
    assert_eq!(code, 0);
    let v: serde_json::Value = serde_json::from_str(out.trim()).unwrap();
    assert_eq!(v["choice"], "git status");
    assert_eq!(v["completion"], "atus");
    assert_eq!(v["type"], "choice");
    // A plain suggest prints the choice, with the newline a command
    // substitution strips anyway.
    let (code, out, _) = tai(&env, &["suggest", "git st"]);
    assert_eq!(code, 0);
    assert_eq!(out, "git status\n");
    // With a limit, one line per choice.
    let (code, out, _) = tai(&env, &["suggest", "--limit", "3", "git "]);
    assert_eq!(code, 0);
    let lines: Vec<&str> = out.trim_end().split('\n').collect();
    assert!((1..=3).contains(&lines.len()), "{out:?}");
    // A suggestion has to extend the line.
    let (code, out, _) = tai(&env, &["suggest", "git status"]);
    assert_eq!(code, 0);
    assert_eq!(out, "\n", "a whole command suggests nothing");
    let _ = std::fs::remove_dir_all(&base);
}

#[test]
fn unknown_commands_are_named_not_crashed() {
    let (base, env) = world("unknown");
    let (code, out, _) = tai(&env, &["nosuchverb"]);
    assert_eq!(code, 2);
    assert!(out.contains("unknown command 'nosuchverb'"));
    let (code, out, _) = tai(&env, &["--help"]);
    assert_eq!(code, 0);
    assert!(out.contains("usage: tai <command>"));
    assert!(out.contains("refresh   import new history rows"));
    let _ = std::fs::remove_dir_all(&base);
}

#[test]
fn refresh_imports_and_rebuilds() {
    let (base, mut env) = world("refresh");
    let hist = base.join("zsh_history");
    std::fs::write(&hist, ": 1690000100:0;cargo build --release\n: 1690000200:0;cargo test\n").unwrap();
    env.insert("TAI_HISTORY_FILES", hist.to_string_lossy().to_string());
    let (code, out, _) = tai(&env, &["refresh"]);
    assert_eq!(code, 0);
    assert!(
        out.starts_with("✓ ") && out.contains("commands indexed"),
        "{out:?}"
    );
    // The quiet flag prints nothing on success.
    let (code, out, _) = tai(&env, &["refresh", "--quiet"]);
    assert_eq!(code, 0);
    assert_eq!(out, "");
    // And the index files exist for the shells.
    assert!(base.join("zsh-index.zsh").exists());
    assert!(base.join("bash-index.bash").exists());
    let _ = std::fs::remove_dir_all(&base);
}
