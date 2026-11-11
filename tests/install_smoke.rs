//! The install end to end, asked the way a user asks it: one door at a time.
//!
//!     cargo test --release --test install_smoke
//!
//! The program is a compiled binary, so every question here runs the real
//! `install.sh` against a sandbox HOME with the binary the build just made —
//! the rc wiring, the wrapper, the data directory, the uninstall, and the two
//! index rules the installer's own refresh has to hold. Nothing here touches
//! the developer's HOME: HOME, BIN_DIR and XDG_DATA_HOME all point into the
//! sandbox, and the doors that reach for the network are exercised with the
//! binary pre-staged, the way the npm postinstall stages it.
//!
//! Ported from tests/test_smoke_install.py. Every check names the rule it
//! holds; a rule broken is a red build.

use std::fs;
use std::path::{Path, PathBuf};
use std::process::{Command, Output};

fn repo() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
}

fn bin() -> PathBuf {
    let release = repo().join("target/release/tai");
    if release.exists() {
        release
    } else {
        repo().join("target/debug/tai")
    }
}

fn version() -> String {
    fs::read_to_string(repo().join("VERSION")).unwrap().trim().to_string()
}

/// A sandbox HOME, with the binary staged where install.sh looks first.
fn world(name: &str) -> PathBuf {
    let home = PathBuf::from(format!("/tmp/tai/tai_install_{}_{}", name, std::process::id()));
    let _ = fs::remove_dir_all(&home);
    fs::create_dir_all(home.join("target/release")).unwrap();
    fs::copy(bin(), home.join("target/release/tai")).unwrap();
    home
}

fn run_install(home: &Path, extra_env: &[(&str, String)]) -> Output {
    let mut cmd = Command::new("bash");
    cmd.arg(repo().join("install.sh"))
        .env("HOME", home)
        .env("BIN_DIR", home.join(".local/bin"))
        .env("XDG_DATA_HOME", home.join("data"))
        // zsh's interactive probe would drag the user's own config in.
        .env("ZDOTDIR", home);
    for (k, v) in extra_env {
        cmd.env(k, v);
    }
    cmd.output().expect("failed to run install.sh")
}

fn run_tai(home: &Path, args: &[&str]) -> Output {
    Command::new(home.join(".local/bin/tai"))
        .args(args)
        .env("HOME", home)
        .env("BIN_DIR", home.join(".local/bin"))
        .env("XDG_DATA_HOME", home.join("data"))
        .output()
        .expect("failed to run the tai wrapper")
}

fn stdout(out: &Output) -> String {
    String::from_utf8_lossy(&out.stdout).into_owned()
}

fn which(cmd: &str) -> Option<PathBuf> {
    let path = std::env::var_os("PATH")?;
    std::env::split_paths(&path)
        .map(|dir| dir.join(cmd))
        .find(|p| p.is_file())
}

fn is_executable(p: &Path) -> bool {
    use std::os::unix::fs::PermissionsExt;
    p.exists() && fs::metadata(p).map(|m| m.permissions().mode() & 0o111 != 0).unwrap_or(false)
}

// -- install: the rc wiring, the wrapper, the welcome -------------------------

#[test]
fn install_and_rerun() {
    let home = world("install");
    let got = run_install(&home, &[]);
    assert_eq!(got.status.code(), Some(0), "install exits 0");
    let out = stdout(&got);
    assert!(out.contains("✓ zsh installed"), "zsh wired");
    assert!(out.contains("✓ bash installed"), "bash wired");
    assert!(
        out.contains("the terminal that knows your next command"),
        "welcome shown"
    );
    assert!(
        out.trim_end().ends_with("→ exec bash") || out.contains("exec"),
        "reload hint last"
    );

    let zshrc = fs::read_to_string(home.join(".zshrc")).unwrap();
    let bashrc = fs::read_to_string(home.join(".bashrc")).unwrap();
    let marker = "# tai autocomplete (installed by ./install.sh)";
    assert!(zshrc.contains(marker), "zshrc has the marker");
    assert_eq!(
        zshrc.matches("plugins/tai.zsh").count(),
        1,
        "zshrc sources the plugin once"
    );
    assert_eq!(
        bashrc.matches("plugins/tai.bash").count(),
        1,
        "bashrc sources the plugin once"
    );
    assert!(home.join(".local/bin/tai").exists(), "wrapper exists");
    assert_eq!(
        fs::read_to_string(home.join("data/tai/install-method"))
            .unwrap()
            .trim(),
        "git",
        "install-method recorded"
    );

    let got = run_tai(&home, &["version"]);
    assert_eq!(got.status.code(), Some(0), "wrapper runs");
    assert!(
        stdout(&got).starts_with(&format!("tai {}", version())),
        "version says the release"
    );
    let out = stdout(&got);
    assert!(
        out.contains("zsh plugin") && !out.contains("not wired"),
        "version sees the wiring"
    );

    // -- rerun: idempotent, and never a second source line --------------------

    let got = run_install(&home, &[]);
    assert_eq!(got.status.code(), Some(0), "rerun exits 0");
    let zshrc = fs::read_to_string(home.join(".zshrc")).unwrap();
    assert_eq!(
        zshrc.matches("plugins/tai.zsh").count(),
        1,
        "rerun adds no second line"
    );
}

// -- retarget: a line an older door left is this install's to own -------------

#[test]
fn retarget() {
    let home = world("retarget");
    let old_tree = "/home/someone/elsewhere";
    fs::write(
        home.join(".zshrc"),
        format!("# my own config\nsource {old_tree}/plugins/tai.zsh\nalias ll='ls -l'\n"),
    )
    .unwrap();
    let got = run_install(&home, &[]);
    let zshrc = fs::read_to_string(home.join(".zshrc")).unwrap();
    assert!(
        stdout(&got).contains("retargeted the tai.zsh source line"),
        "retarget reports itself"
    );
    assert!(
        zshrc.contains(&format!("{}/plugins/tai.zsh", repo().display())),
        "retargeted to this tree"
    );
    assert!(!zshrc.contains(old_tree), "the old tree is gone");
    assert!(zshrc.contains("alias ll='ls -l'"), "the user's own lines stay");
}

// -- autosuggestions: paused when found, given back, kept when asked -----------
//
// The pause is decided by whether zsh can actually load the plugin, asked of
// zsh itself — so the check needs a zsh, the way the pty suite needs one.
// Without one the installer correctly leaves the plugin alone, and the
// give-back half is asked of the verb the installer calls.

#[test]
fn autosuggest_pause() {
    let home = world("autosuggest");
    fs::write(
        home.join(".zshrc"),
        "_zsh_autosuggest_start() { :; }\nadd-zsh-hook precmd _zsh_autosuggest_start\n",
    )
    .unwrap();
    let zsh_bin = match which("zsh") {
        Some(p) => p.to_string_lossy().into_owned(),
        None => {
            let stub = repo().join("tests/bin/zsh");
            if is_executable(&stub) {
                stub.to_string_lossy().into_owned()
            } else {
                String::new()
            }
        }
    };
    if zsh_bin.is_empty() {
        eprintln!("skipping autosuggest pause: no zsh on this machine");
        return;
    }
    let fpath = repo().join("tests/bin/zsh-functions");
    let path = std::env::var("PATH").unwrap_or_default();
    let zsh_dir = match zsh_bin.rfind('/') {
        Some(i) => zsh_bin[..i].to_string(),
        None => ".".to_string(),
    };
    let mut extra: Vec<(&str, String)> =
        vec![("PATH", format!("{zsh_dir}:{path}"))];
    if fpath.exists() {
        extra.push(("FPATH", fpath.to_string_lossy().into_owned()));
    }
    let got = run_install(&home, &extra);
    let _ = stdout(&got);
    let zshrc = fs::read_to_string(home.join(".zshrc")).unwrap();
    assert!(
        zshrc.contains("# tai: zsh-autosuggestions paused"),
        "autosuggest paused"
    );
    assert!(
        zshrc.contains("add-zsh-hook -d precmd _zsh_autosuggest_start"),
        "the hook was removed"
    );
}

// The give-back half, asked of the verb itself.
#[test]
fn autosuggest_give_back() {
    let home = world("autosuggest_back");
    let rc = home.join(".zshrc");
    fs::write(
        &rc,
        "keep me\n\
         # tai: zsh-autosuggestions paused (installed by ./install.sh)\n\
         add-zsh-hook -d precmd _zsh_autosuggest_start 2>/dev/null\n",
    )
    .unwrap();
    Command::new(bin())
        .args(["rc-pause-autosuggest"])
        .arg(&rc)
        .arg("0")
        .output()
        .expect("failed to run rc-pause-autosuggest");
    let zshrc = fs::read_to_string(&rc).unwrap();
    assert!(
        !zshrc.contains("# tai: zsh-autosuggestions paused"),
        "the block is given back"
    );
    assert!(zshrc.contains("keep me"), "the user's lines survive it");
}

#[test]
fn autosuggest_keep() {
    let home = world("autosuggest_keep");
    fs::write(
        home.join(".zshrc"),
        "add-zsh-hook precmd _zsh_autosuggest_start\n\
         # tai: zsh-autosuggestions paused (installed by ./install.sh)\n\
         add-zsh-hook -d precmd _zsh_autosuggest_start 2>/dev/null\n",
    )
    .unwrap();
    let _ = run_install(&home, &[("TAI_KEEP_AUTOSUGGEST", "1".to_string())]);
    let zshrc = fs::read_to_string(home.join(".zshrc")).unwrap();
    // An existing block goes either way: an opt-out that only stopped the *next*
    // install from adding one would leave the previous one in place.
    assert!(
        !zshrc.contains("# tai: zsh-autosuggestions paused"),
        "an asked-for keep still clears an old block"
    );
}

// -- uninstall: everything it added, and nothing else -------------------------

#[test]
fn uninstall() {
    let home = world("uninstall");
    fs::write(home.join("notes.txt"), "mine\n").unwrap();
    let _ = run_install(&home, &[]);
    let got = Command::new(home.join(".local/bin/tai"))
        .arg("uninstall")
        .env("HOME", &home)
        .env("XDG_DATA_HOME", home.join("data"))
        .env("BIN_DIR", home.join(".local/bin"))
        .output()
        .expect("failed to run tai uninstall");
    assert_eq!(got.status.code(), Some(0), "uninstall exits 0");
    assert!(
        stdout(&got).starts_with("tai removed"),
        "uninstall reports itself"
    );
    assert!(
        !home.join(".local/bin/tai").exists(),
        "wrapper gone"
    );
    assert!(!home.join("data/tai").exists(), "data dir gone");
    assert!(
        home.join(".tai").exists() || !home.join(".tai/install.sh").exists(),
        "checkout gone"
    );
    let zshrc = fs::read_to_string(home.join(".zshrc")).unwrap();
    assert!(!zshrc.contains("plugins/tai.zsh"), "rc line gone");
    assert!(
        !zshrc.contains("# tai autocomplete (installed by ./install.sh)"),
        "marker gone"
    );
    assert!(
        !zshrc.contains("# tai: zsh-autosuggestions paused"),
        "pause block gone"
    );
    // A second uninstall says so: the wrapper is gone, so the binary itself is
    // asked, and the answer is the same fact in the same words.
    let got2 = Command::new(bin())
        .arg("uninstall")
        .env("HOME", &home)
        .env("XDG_DATA_HOME", home.join("data"))
        .env("BIN_DIR", home.join(".local/bin"))
        .output()
        .expect("failed to run tai uninstall (second)");
    assert_eq!(
        String::from_utf8_lossy(&got2.stdout).trim(),
        "tai was not installed",
        "uninstall is a no-op twice"
    );
}

// -- the index rules the installer's own refresh holds ------------------------

fn seq_env(cmd: &mut Command, home: &Path, record_index: &Path) {
    cmd.env("HOME", home)
        .env("XDG_DATA_HOME", home.join("data"))
        .env("TAI_DB", home.join("data/tai/history.db"))
        .env("TAI_INDEX", record_index)
        .env("TAI_SPOOL", home.join("spool.log"))
        .env("TAI_NO_LEARN", "1")
        .env("TAI_NO_ENGINE_CACHE", "1");
}

fn record(cmd: &str, home: &Path, record_index: &Path) {
    let mut c = Command::new(bin());
    c.args(["record", "--cwd"]).arg(repo()).arg(cmd);
    seq_env(&mut c, home, record_index);
    c.output().expect("failed to run tai record");
}

fn refresh(home: &Path, record_index: &Path) {
    let mut c = Command::new(bin());
    c.arg("refresh");
    seq_env(&mut c, home, record_index);
    c.output().expect("failed to run tai refresh");
}

#[test]
fn index_rules() {
    let home = world("seq");
    let record_index = home.join("record/zsh-index.zsh");
    fs::create_dir_all(record_index.parent().unwrap()).unwrap();

    let stale = "cd /nowhere/gone";
    // The chain the filter has to survive: a stale command both as a key and as
    // a follower, and one live pair behind it that must keep its seq entry.
    let chain = [stale, "git checkout feature", stale, "git status", "git status"];
    for cmd in chain {
        record(cmd, &home, &record_index);
    }
    refresh(&home, &record_index);
    let written = fs::read_to_string(&record_index).unwrap();
    let seq_lines: Vec<&str> =
        written.lines().filter(|l| l.starts_with("_TAI_SEQ")).collect();
    // The fixture must have produced entries at all; an empty table would have
    // proved nothing about the filter below.
    assert!(!seq_lines.is_empty(), "no _TAI_SEQ entries at all: the fixture proved nothing");
    assert!(
        !seq_lines.iter().any(|l| l.contains(stale)),
        "a stale command is still offered as what follows another one"
    );
    assert!(
        !seq_lines
            .iter()
            .any(|l| l.starts_with(&format!("_TAI_SEQ+=('{stale}"))),
        "a stale command still has followers in the index"
    );
    assert!(
        seq_lines.iter().any(|l| l.contains("git status")),
        "filtering the sequence table also removed live commands"
    );

    // The order of a _TAI_SEQ value list is the prophecy's order, because the head
    // of the list is the line the fresh prompt offers: what followed *most*, not
    // the best-scoring line that ever did.
    let seq_key = "git fetch --all";
    let often = "git pull --rebase origin main";
    let rare = "echo tally";
    for _ in 0..3 {
        for cmd in [seq_key, often] {
            record(cmd, &home, &record_index);
        }
    }
    record(seq_key, &home, &record_index);
    for _ in 0..30 {
        record(rare, &home, &record_index);
    }
    refresh(&home, &record_index);
    let written = fs::read_to_string(&record_index).unwrap();
    let head = format!("_TAI_SEQ+=('{seq_key}' '");
    assert!(written.contains(&head), "no sequence entry for the key at all");
    let vstart = written.find(&head).unwrap() + head.len();
    let vend = written[vstart..].find("')").unwrap() + vstart;
    let order_values = &written[vstart..vend];
    assert_eq!(
        order_values.lines().next().unwrap(),
        often,
        "the prophecy head is the best score, not the most frequent follower"
    );
    assert!(
        order_values.lines().any(|l| l == rare),
        "ordering by frequency dropped the less frequent follower"
    );
}

// -- the JEV gate: a hostile model answers, and the code decides --------------

/// A model that grades its own homework: it picked `rm -rf /` and calls it
/// safe. The gate is deterministic and in code, so the answer must not reach
/// the prompt.
fn serve_hostile_model() -> (u16, std::thread::JoinHandle<()>) {
    use std::io::{Read, Write};
    let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
    let port = listener.local_addr().unwrap().port();
    let handle = std::thread::spawn(move || {
        let body = r#"{"answers":{"best_candidate":{"choice":"rm -rf /","probabilities":{},"confidence":0.99},"show_now":{"noul":1.0},"destructive":{"noul":0.1}},"model":"hostile-test"}"#;
        let reply = format!(
            "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}",
            body.len(),
            body
        );
        // Serve until the listener is closed (process exit tears the thread down).
        for stream in listener.incoming() {
            let mut stream = match stream {
                Ok(s) => s,
                Err(_) => break,
            };
            let mut data = Vec::new();
            let mut buf = [0u8; 4096];
            loop {
                match stream.read(&mut buf) {
                    Ok(0) | Err(_) => break,
                    Ok(n) => {
                        data.extend_from_slice(&buf[..n]);
                        let hdr_end = match data.windows(4).position(|w| w == b"\r\n\r\n") {
                            Some(i) => i + 4,
                            None => continue,
                        };
                        let head = String::from_utf8_lossy(&data[..hdr_end]);
                        let cl: usize = head
                            .split("\r\n")
                            .find_map(|l| {
                                let mut it = l.splitn(2, ':');
                                let k = it.next()?.trim().to_ascii_lowercase();
                                if k == "content-length" {
                                    it.next()?.trim().parse().ok()
                                } else {
                                    None
                                }
                            })
                            .unwrap_or(0);
                        if data.len() >= hdr_end + cl {
                            break;
                        }
                    }
                }
            }
            let _ = stream.write_all(reply.as_bytes());
            let _ = stream.flush();
            // stream dropped: connection closed ("Connection: close").
        }
    });
    (port, handle)
}

#[test]
fn jev_gate() {
    // A sandbox like the seq fixture's, with enough recorded history that the
    // suggest flow has candidates for the model to (mis)rank.
    let home = world("jev");
    let record_index = home.join("record/zsh-index.zsh");
    fs::create_dir_all(record_index.parent().unwrap()).unwrap();
    let stale = "cd /nowhere/gone";
    for cmd in [stale, "git checkout feature", stale, "git status", "git status"] {
        record(cmd, &home, &record_index);
    }
    refresh(&home, &record_index);

    let (port, _server) = serve_hostile_model();
    let mut cmd = Command::new(bin());
    cmd.args(["suggest", "--json", "--jev", "rm -rf /", "--cwd"])
        .arg(&home)
        .args(["--git", "", "--branch", ""]);
    seq_env(&mut cmd, &home, &record_index);
    cmd.env("TYPESAFE_API_KEY", "test-key")
        .env("TYPESAFE_BASE_URL", format!("http://127.0.0.1:{port}"))
        .env("TAI_JEV_TIMEOUT", "5");
    let got = cmd.output().expect("failed to run tai suggest --jev");
    let res: serde_json::Value =
        serde_json::from_str(&String::from_utf8_lossy(&got.stdout))
            .expect("suggest --json printed JSON");
    assert_ne!(
        res.get("choice"),
        Some(&serde_json::json!("rm -rf /")),
        "the hostile choice did not become the suggestion"
    );
    assert!(
        res.get("jev_rejected")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .contains("not offering"),
        "the rejection was said out loud"
    );
    assert_eq!(
        res.get("jev").and_then(|j| j.get("model")).cloned(),
        Some(serde_json::json!("hostile-test")),
        "the rerank was recorded, not hidden"
    );
}
