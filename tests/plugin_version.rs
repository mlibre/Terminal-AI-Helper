//! The plugin self-report: the release a running shell sourced, left where
//! `tai version` reads it.
//!
//!     cargo test --release --test plugin_version
//!
//! The rc file names a tree, but a shell started before an update keeps the
//! old code in memory for its whole life — the report that started this was a
//! freeze the current release had already fixed, still reproducible in a
//! session that had never re-sourced, with `tai version` reporting the new
//! release and nothing able to say otherwise. The plugin now leaves one line
//! ("zsh 3.1.1") in the data directory at source time, and `tai version`
//! turns a mismatch into the one sentence that matters: open a new shell.

use std::fs;
use std::path::PathBuf;
use std::process::Command;

fn repo() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
}

fn version() -> String {
    fs::read_to_string(repo().join("VERSION")).unwrap().trim().to_string()
}

/// Source one plugin entry file in its own shell and return the marker line
/// it left, or the failure that says why there is none.
fn marker_from(shell: &str, entry: &str) -> Result<String, String> {
    let home = PathBuf::from(format!("/tmp/tai/plugin_version_{}", std::process::id()));
    let _ = fs::remove_dir_all(&home);
    fs::create_dir_all(home.join(".local/share/tai")).unwrap();
    let data = home.join(".local/share");
    let (cmd, args): (&str, Vec<&str>) = match shell {
        "zsh" => ("zsh", vec!["-f", "-c", "source \"$0\""]),
        _ => ("bash", vec!["--noprofile", "--norc", "-c", "source \"$0\""]),
    };
    let mut command = Command::new(cmd);
    command
        .args(&args)
        .arg(repo().join(entry))
        .env("HOME", &home)
        .env("XDG_DATA_HOME", &data)
        .env("TAI_BIN", repo().join("target/release/tai"));
    let out = command.output().map_err(|e| format!("failed to run {shell}: {e}"))?;
    let stderr = String::from_utf8_lossy(&out.stderr).to_string();
    let path = data.join("tai/plugin-version");
    let text = fs::read_to_string(&path)
        .map_err(|_| format!("{shell} left no marker (stderr: {})", stderr.trim()))?;
    Ok(text.trim().to_string())
}

#[test]
fn zsh_plugin_leaves_its_version() {
    if !Command::new("zsh").arg("--version").output().is_ok() {
        return; // the pty suites' rule: no zsh, no check
    }
    let marker = marker_from("zsh", "plugins/tai.zsh").expect("zsh marker");
    assert_eq!(
        marker,
        format!("zsh {}", version()),
        "the marker names the shell and the release the plugin tree carries"
    );
}

#[test]
fn bash_plugin_leaves_its_version() {
    let marker = marker_from("bash", "plugins/tai.bash").expect("bash marker");
    assert_eq!(
        marker,
        format!("bash {}", version()),
        "the marker names the shell and the release the plugin tree carries"
    );
}

#[test]
fn version_command_reports_a_stale_shell() {
    // The binary is built from this tree; a marker naming an older release is
    // the running-shell-still-old shape, and `tai version` says so.
    let home = PathBuf::from(format!("/tmp/tai/plugin_version_cmd_{}", std::process::id()));
    let _ = fs::remove_dir_all(&home);
    fs::create_dir_all(home.join(".local/share/tai")).unwrap();
    fs::write(home.join(".local/share/tai/plugin-version"), "zsh 3.0.0\n").unwrap();
    let out = Command::new(repo().join("target/release/tai"))
        .arg("version")
        .env("HOME", &home)
        .env("XDG_DATA_HOME", home.join(".local/share"))
        .env_remove("TAI_DATA_DIR")
        .output()
        .expect("failed to run tai version");
    let text = String::from_utf8_lossy(&out.stdout).to_string();
    assert!(
        text.contains("still holds plugin 3.0.0"),
        "a stale shell is named: {text}"
    );
    assert!(text.contains("exec zsh"), "the advice is the new shell: {text}");

    // The current release leaves no such line: a matching marker is silent.
    fs::write(
        home.join(".local/share/tai/plugin-version"),
        format!("zsh {}\n", version()),
    )
    .unwrap();
    let out = Command::new(repo().join("target/release/tai"))
        .arg("version")
        .env("HOME", &home)
        .env("XDG_DATA_HOME", home.join(".local/share"))
        .output()
        .expect("failed to run tai version");
    let text = String::from_utf8_lossy(&out.stdout).to_string();
    assert!(
        !text.contains("still holds"),
        "a current shell says nothing: {text}"
    );
}
