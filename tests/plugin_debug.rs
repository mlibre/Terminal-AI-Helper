//! The realtime trail, and the command that reads it back.
//!
//!     cargo test --release --test plugin_debug
//!
//! The bug that asked for this facility kills the shell, so the contract is
//! about the disk: a keystroke's trail line exists the moment the keystroke
//! happened, and `tai debug` prints the file whole. The tests here check both
//! halves — the zsh side writes `upd` lines per redraw with the buffer in
//! them, a heartbeat once a second, a `shell gone` when the shell dies; the
//! binary side writes its argv, begin/end with the exit code, and answers a
//! `tai debug` run with the report and the trail in one output. The trail is
//! off by default, and an off trail writes nothing at all.

mod support;

use std::fs;
use std::path::PathBuf;
use std::process::Command;

use support::Session;

// Tests in one binary share /tmp/tai, so they run one at a time — the same
// rule plugin_menu and the other pty suites run under.
static LOCK: std::sync::Mutex<()> = std::sync::Mutex::new(());

fn trail(tag: &str) -> PathBuf {
    PathBuf::from(format!("/tmp/tai/tai_debug_{}_{}.log", tag, std::process::id()))
}

fn tai_bin() -> PathBuf {
    let release = support::repo_path("target/release/tai");
    if release.exists() {
        release
    } else {
        support::repo_path("target/debug/tai")
    }
}

#[test]
fn zsh_keystrokes_leave_a_realtime_trail() {
    if support::zsh_bin().is_none() {
        return; // the pty suites' rule: no zsh, no check
    }
    let _guard = LOCK.lock().unwrap_or_else(|e| e.into_inner());
    support::setup();
    let log = trail("zsh");
    let _ = fs::remove_file(&log);
    let mut s = Session::new_with(
        "zsh",
        &[
            ("TAI_DEBUG", "1"),
            ("TAI_DEBUG_FILE", log.to_str().unwrap()),
        ],
        true,
    );
    // One character per send, the way a hand types. Every redraw hook run is
    // one `upd` line naming the widget and the buffer as it stood.
    for ch in ["e", "c", "h", "o"] {
        s.send(ch);
    }
    s.settle();
    let text = fs::read_to_string(&log).unwrap_or_default();
    assert!(
        text.contains("upd: widget=self-insert"),
        "each keystroke's redraw is on the trail: {text}"
    );
    assert!(
        text.contains("buffer=ech"),
        "the trail records the buffer as it stood at each redraw: {text}"
    );
    assert!(
        text.contains("query: prefix="),
        "the lookup is on the trail: {text}"
    );
    assert!(
        text.contains("tai zsh pid="),
        "every line names its writer: {text}"
    );
    // The heartbeat. One second's wait buys the first pulse; the shell is
    // alive here, so what follows must be `alive`, not `shell gone`.
    std::thread::sleep(std::time::Duration::from_millis(1400));
    let text = fs::read_to_string(&log).unwrap_or_default();
    assert!(text.contains(" alive"), "the pulse is on the trail: {text}");
    assert!(
        !text.contains("shell gone"),
        "a living shell is never reported gone: {text}"
    );
    s.close();
    // After the shell dies the heartbeat writes one last line. This is the
    // line that tells a crash from a silence, and the test that pins it is
    // the test that makes `tai debug` able to answer "did the shell die?".
    for _ in 0..40 {
        let text = fs::read_to_string(&log).unwrap_or_default();
        if text.contains("shell gone") {
            break;
        }
        std::thread::sleep(std::time::Duration::from_millis(250));
    }
    let text = fs::read_to_string(&log).unwrap_or_default();
    assert!(text.contains("shell gone"), "a dead shell is reported: {text}");
}

#[test]
fn the_trail_stays_off_by_default() {
    if support::zsh_bin().is_none() {
        return;
    }
    let _guard = LOCK.lock().unwrap_or_else(|e| e.into_inner());
    support::setup();
    let log = trail("off");
    let _ = fs::remove_file(&log);
    let mut s = Session::new_with(
        "zsh",
        &[("TAI_DEBUG_FILE", log.to_str().unwrap())],
        true,
    );
    s.send("e");
    s.send("c");
    s.settle();
    s.close();
    std::thread::sleep(std::time::Duration::from_millis(1200));
    assert!(
        !log.exists(),
        "with TAI_DEBUG unset the plugin writes no trail at all"
    );
}

#[test]
fn tai_debug_arms_the_trail_mid_session() {
    if support::zsh_bin().is_none() {
        return;
    }
    let _guard = LOCK.lock().unwrap_or_else(|e| e.into_inner());
    support::setup();
    let log = trail("arm");
    let _ = fs::remove_file(&log);
    let mut s = Session::new_with(
        "zsh",
        &[("TAI_DEBUG_FILE", log.to_str().unwrap())],
        true,
    );
    // Arm first, type after: a keystroke that precedes the arm would ride
    // into the run line's own buffer and mangle the command.
    s.run("tai-debug");
    s.send("e");
    s.send("c");
    s.settle();
    let text = fs::read_to_string(&log).unwrap_or_default();
    assert!(
        text.contains("debug armed mid-session by tai-debug"),
        "the arm is on the trail: {text}"
    );
    assert!(
        text.contains("buffer=ec") || text.contains("buffer=c"),
        "keystrokes after the arm are on the trail: {text}"
    );
    s.close();
}

#[test]
fn binary_invocations_leave_a_trail() {
    let _guard = LOCK.lock().unwrap_or_else(|e| e.into_inner());
    support::setup();
    let log = trail("bin");
    let _ = fs::remove_file(&log);
    let out = Command::new(tai_bin())
        .arg("doctor")
        .env("TAI_DEBUG", "1")
        .env("TAI_DEBUG_FILE", &log)
        .env("HOME", "/tmp/tai")
        .env("XDG_DATA_HOME", "/tmp/tai/data")
        .env("XDG_CACHE_HOME", "/tmp/tai/cache")
        .env("TAI_DB", "/tmp/tai/tai_debug_bin.db")
        .env("TAI_SPOOL", "/tmp/tai/tai_debug_bin_spool.log")
        .output()
        .expect("tai doctor runs");
    assert!(out.status.success());
    let text = fs::read_to_string(&log).unwrap_or_default();
    assert!(
        text.contains("bin pid=") && text.contains("cli: argv=[\"doctor\"]"),
        "the invocation is on the trail with its argv: {text}"
    );
    assert!(
        text.contains("begin") && text.contains("end code="),
        "the command's begin and exit code are on the trail: {text}"
    );
}

#[test]
fn tai_debug_prints_the_whole_trail() {
    let _guard = LOCK.lock().unwrap_or_else(|e| e.into_inner());
    support::setup();
    let log = trail("dump");
    let _ = fs::remove_file(&log);
    for arg in ["version", "doctor"] {
        Command::new(tai_bin())
            .arg(arg)
            .env("TAI_DEBUG", "1")
            .env("TAI_DEBUG_FILE", &log)
            .env("HOME", "/tmp/tai")
            .env("XDG_DATA_HOME", "/tmp/tai/data")
            .env("TAI_DB", "/tmp/tai/tai_debug_dump.db")
            .output()
            .expect("tai runs");
    }
    let out = Command::new(tai_bin())
        .arg("debug")
        .env("TAI_DEBUG_FILE", &log)
        .env("HOME", "/tmp/tai")
        .env("XDG_DATA_HOME", "/tmp/tai/data")
        .env("TAI_SPOOL", "/tmp/tai/tai_debug_dump_spool.log")
        .output()
        .expect("tai debug runs");
    assert!(out.status.success());
    let text = String::from_utf8_lossy(&out.stdout).to_string();
    assert!(text.contains("tai debug report — "), "the report header: {text}");
    assert!(text.contains("binary      : "), "the binary line: {text}");
    assert!(text.contains("environment (what is set):"), "the knobs: {text}");
    assert!(text.contains("files (name, bytes, modified):"), "the inventory: {text}");
    // The whole trail, oldest first: both invocations, in order.
    let v = text.find("argv=[\"version\"]").expect("the version run is on the report");
    let d = text.find("argv=[\"doctor\"]").expect("the doctor run is on the report");
    assert!(v < d, "the trail prints oldest first");
    assert!(
        text.contains("end code=0"),
        "the command ends are on the report: {text}"
    );
}

#[test]
fn tai_debug_tail_and_clear() {
    let _guard = LOCK.lock().unwrap_or_else(|e| e.into_inner());
    support::setup();
    let log = trail("tail");
    let _ = fs::remove_file(&log);
    for _ in 0..3 {
        Command::new(tai_bin())
            .arg("version")
            .env("TAI_DEBUG", "1")
            .env("TAI_DEBUG_FILE", &log)
            .env("HOME", "/tmp/tai")
            .env("XDG_DATA_HOME", "/tmp/tai/data")
            .env("TAI_DB", "/tmp/tai/tai_debug_tail.db")
            .output()
            .expect("tai version runs");
    }
    let out = Command::new(tai_bin())
        .arg("debug")
        .arg("--tail")
        .arg("2")
        .env("TAI_DEBUG_FILE", &log)
        .env("HOME", "/tmp/tai")
        .env("XDG_DATA_HOME", "/tmp/tai/data")
        .env("TAI_SPOOL", "/tmp/tai/tai_debug_tail_spool.log")
        .output()
        .expect("tai debug runs");
    let text = String::from_utf8_lossy(&out.stdout).to_string();
    assert_eq!(
        text.matches("cli: argv=[\"version\"]").count(),
        2,
        "--tail keeps the last two: {text}"
    );
    assert!(
        text.contains("earlier lines are in the file"),
        "the note names the lines left out: {text}"
    );
    // --clear empties the file; the next run starts from nothing.
    let out = Command::new(tai_bin())
        .arg("debug")
        .arg("--clear")
        .env("TAI_DEBUG_FILE", &log)
        .env("HOME", "/tmp/tai")
        .env("XDG_DATA_HOME", "/tmp/tai/data")
        .env("TAI_SPOOL", "/tmp/tai/tai_debug_tail_spool.log")
        .output()
        .expect("tai debug --clear runs");
    assert!(out.status.success());
    assert!(
        fs::read_to_string(&log).unwrap().trim().is_empty(),
        "the trail is empty after --clear"
    );
}

#[test]
fn a_crashed_shells_trail_reads_back() {
    let _guard = LOCK.lock().unwrap_or_else(|e| e.into_inner());
    support::setup();
    let log = trail("panic");
    let _ = fs::remove_file(&log);
    // The shape `tai debug` must handle after the bug it exists for: a shell
    // wrote its keystrokes, then died — its last line is a redraw with the
    // buffer in it, the heartbeat's "shell gone" right behind, and no exit
    // hook line anywhere. The report prints those lines exactly as they were
    // written; nothing in the reader needs the writer alive.
    fs::write(
        &log,
        "1750000000.000001 tai zsh pid=999 upd: widget=self-insert buffer=systemctl --user s\n\
         1750000000.500000 tai zsh pid=999 shell gone\n",
    )
    .unwrap();
    let out = Command::new(tai_bin())
        .arg("debug")
        .env("TAI_DEBUG_FILE", &log)
        .env("HOME", "/tmp/tai")
        .env("XDG_DATA_HOME", "/tmp/tai/data")
        .env("TAI_SPOOL", "/tmp/tai/tai_debug_panic_spool.log")
        .output()
        .expect("tai debug runs");
    let text = String::from_utf8_lossy(&out.stdout).to_string();
    assert!(
        text.contains("shell gone"),
        "a hand-written trail reads back through tai debug: {text}"
    );
}
