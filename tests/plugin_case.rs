//! The case a command was recorded with is not a spelling the user remembers.
//!
//! `ls down` must reach `ls Downloads` — as a ghost hint, as an accepted line,
//! as a menu row, and as a file answer from the disk itself. Every half that
//! used to compare bytes is pinned here end to end, through a real bash and
//! zsh: the plugin lookups, the ghost's paint-and-strip gate, the menu's word
//! filter, the file answer's snapshot filter and its fallback glob, and the
//! systemctl unit match (`Net` reaching NetworkManager.service). The ghost's
//! own length cap is pinned here too, because it guards the same redraw.
//!
//! Ported from `tests/test_plugins_case.py`.

mod support;
use support::*;
use std::os::unix::fs::PermissionsExt;

use std::path::PathBuf;

const CASE_DIR: &str = "/tmp/tai/tai_case_dir";
const CASE_CD_DIR: &str = "/tmp/tai/tai_case_cd";
const CASE_UNITS_BIN: &str = "/tmp/tai/tai_case_units_bin";
const CASE_UNITS_CACHE: &str = "/tmp/tai/tai_case_units_cache";

/// The tests share /tmp/tai, so they run one at a time.
static LOCK: std::sync::Mutex<()> = std::sync::Mutex::new(());

fn p(path: &str) -> PathBuf {
    PathBuf::from(path)
}

fn setup_case() {
    std::fs::create_dir_all(CASE_DIR).unwrap();
    std::fs::write(p(CASE_DIR).join("Downloads"), "x\n").unwrap();
}

fn case_units_env() -> Vec<(String, String)> {
    let _ = std::fs::remove_dir_all(CASE_UNITS_BIN);
    let _ = std::fs::remove_dir_all(CASE_UNITS_CACHE);
    std::fs::create_dir_all(CASE_UNITS_BIN).unwrap();
    std::fs::create_dir_all(CASE_UNITS_CACHE).unwrap();
    let ctl = p(CASE_UNITS_BIN).join("systemctl");
    // A mixed-case unit name is the whole point: `Net` has to reach it.
    std::fs::write(
        &ctl,
        "#!/bin/sh\nprintf '%s enabled\\n' NetworkManager.service sshd.service\n",
    )
    .unwrap();
    std::fs::set_permissions(&ctl, std::fs::Permissions::from_mode(0o755)).unwrap();
    let path = std::env::var("PATH").unwrap_or_default();
    let env = vec![
        (
            "PATH".to_string(),
            format!(
                "{}:{}:{}:{}",
                CASE_UNITS_BIN,
                repo_path("tests/bin").display(),
                repo_path("tests/bin/zsh-functions").display(),
                path
            ),
        ),
        ("XDG_CACHE_HOME".to_string(), CASE_UNITS_CACHE.to_string()),
    ];
    env
}

fn sug_of(dump: &str) -> String {
    // The stored suggestion (the whole answer, taken by the accept keys).
    dump.split(" PD=").next().unwrap_or("").to_string()
}

fn sug(s: &mut Session) -> String {
    // The stored suggestion, off one dump.
    sug_of(&s.suggestion())
}

fn pd_of(dump: &str) -> String {
    // What is actually PAINTED: the dump's PD=[${(V)POSTDISPLAY}] field.
    //
    // SUG= is _TAI_SUGGESTION — the whole answer, held back for the key that
    // takes it — while PD= is the ellipsised text on screen. The cap is a rule
    // about the paint, so the paint is what this reads.
    let got = regex::Regex::new(r" PD=\[(.*)\] RH=")
        .unwrap()
        .captures(dump)
        .map(|c| c[1].to_string())
        .unwrap_or_default();
    if got.len() >= 2 && got.starts_with('\'') && got.ends_with('\'') {
        got[1..got.len() - 1].to_string()
    } else {
        got
    }
}

#[test]
fn test_zsh_ghost_case() {
    let _guard = LOCK.lock().unwrap_or_else(|e| e.into_inner());
    if shell_path("zsh").is_none() {
        return;
    }
    setup();
    write_index(Some(&["ls Downloads", "git status"]), None);
    let mut s = Session::new("zsh");
    s.send("ls down");
    assert_eq!(sug(&mut s), "SUG=[loads]", "zsh ghost folds case");
    s.send("ls D");
    assert_eq!(sug(&mut s), "SUG=[ownloads]", "and the exact case still answers");
    s.close();
    assert!(s.noise().is_empty(), "zsh ghost clean: {:?}", s.noise());
    write_index(None, None);
}

#[test]
fn test_bash_accept_case() {
    let _guard = LOCK.lock().unwrap_or_else(|e| e.into_inner());
    if shell_path("bash").is_none() {
        return;
    }
    setup();
    write_index(Some(&["ls Downloads", "git status"]), None);
    probe("bash", RIGHT, "ls down", "ls Downloads");
    probe("bash", CTRL_F, "ls down", "ls Downloads");
    write_index(None, None);
}

#[test]
fn test_zsh_menu_case() {
    let _guard = LOCK.lock().unwrap_or_else(|e| e.into_inner());
    if shell_path("zsh").is_none() {
        return;
    }
    setup();
    write_index(Some(&["cd Downloads", "cd Desktop"]), None);
    // A `cd` destination is judged for liveness from where the user stands —
    // the rule that keeps a learned directory two directories away from
    // answering — so the destinations have to exist from here.
    let _ = std::fs::remove_dir_all(CASE_CD_DIR);
    std::fs::create_dir_all(CASE_CD_DIR).unwrap();
    std::fs::create_dir_all(p(CASE_CD_DIR).join("Downloads")).unwrap();
    std::fs::create_dir_all(p(CASE_CD_DIR).join("Desktop")).unwrap();
    let mut s = Session::new("zsh");
    s.run(&format!("cd {}", CASE_CD_DIR));
    s.send("cd D");
    s.write(TAB);
    s.settle();
    let m = s.menu(true);
    assert_eq!(m.line, "cd D", "zsh menu line untouched");
    assert!(
        m.entries >= 1 && m.drawn.contains("Downloads") && m.drawn.contains("Desktop"),
        "zsh menu folds the word's case (n={}, drawn={:?})",
        m.entries,
        m.drawn
    );
    s.close();
    assert!(s.noise().is_empty(), "zsh menu clean: {:?}", s.noise());
    write_index(None, None);
}

#[test]
fn test_file_case() {
    let _guard = LOCK.lock().unwrap_or_else(|e| e.into_inner());
    if shell_path("zsh").is_none() && shell_path("bash").is_none() {
        return;
    }
    setup();
    setup_case();
    // A FILE named Downloads: the fresh-file answer reads files (`-f`), and the
    // learned-argument gate below needs the name to exist too.
    // Learned case: the history says `cat Downloads`, the user typed `down`.
    // The learned argument that still exists keeps the hint, case folded.
    write_index(Some(&["cat Downloads"]), None);
    let mut s = Session::new("zsh");
    s.run(&format!("cd {}", CASE_DIR));
    s.send("cat down");
    assert_eq!(sug(&mut s), "SUG=[loads]", "zsh learned file folds case");
    s.close();
    assert!(s.noise().is_empty(), "zsh learned-file clean: {:?}", s.noise());
    // Disk case: nothing learned that extends `down`, the file is simply there
    // — and `cat` is a known file-argument line, so tai answers, not readline.
    write_index(Some(&["cat notes.txt"]), None);
    let mut s = Session::new("zsh");
    s.run(&format!("cd {}", CASE_DIR));
    s.send("cat down");
    assert_eq!(sug(&mut s), "SUG=[loads]", "zsh disk answer folds case");
    s.close();
    assert!(s.noise().is_empty(), "zsh disk-file clean: {:?}", s.noise());
    if shell_path("bash").is_some() {
        // TAI_COMPLETE_ALL is what puts tai's own completion behind Tab for
        // every line; without it readline's own (case-sensitive) completion
        // answers instead — which is exactly the behaviour this file exists to
        // fold away.
        let mut s = Session::new_with("bash", &[("TAI_COMPLETE_ALL", "1")], true);
        s.run(&format!("cd {}", CASE_DIR));
        s.send("cat down");
        s.write(TAB);
        s.settle();
        assert_eq!(s.line(), "cat Downloads ", "bash disk answer folds case");
        s.close();
        assert!(s.noise().is_empty(), "bash disk-file clean: {:?}", s.noise());
    }
    write_index(None, None);
}

#[test]
fn test_units_case() {
    let _guard = LOCK.lock().unwrap_or_else(|e| e.into_inner());
    if shell_path("zsh").is_none() && shell_path("bash").is_none() {
        return;
    }
    setup();
    write_index(Some(&["git status"]), None);
    let env_strings = case_units_env();
    let env: Vec<(&str, &str)> = env_strings
        .iter()
        .map(|(k, v)| (k.as_str(), v.as_str()))
        .collect();
    if shell_path("zsh").is_some() {
        let mut s = Session::new_with("zsh", &env, true);
        // The ghost must not fork: the cache is preloaded the way the prompt
        // hook preloads it in real use, minus the timing race.
        s.run("_tai_units_load 0 >/dev/null 2>&1");
        s.send("systemctl restart Net");
        assert_eq!(
            sug(&mut s),
            "SUG=[workManager.service]",
            "zsh unit ghost folds case"
        );
        s.close();
        assert!(s.noise().is_empty(), "zsh units clean: {:?}", s.noise());
    }
    if shell_path("bash").is_some() {
        let mut s = Session::new_with("bash", &env, true);
        s.run("_tai_units_load 0 >/dev/null 2>&1");
        // Read COMPREPLY back the bash way — a completion press in the line
        // editor would list, and a listing is not what this check is about.
        s.run(
            "COMP_LINE='systemctl restart Net'; COMP_POINT=${#COMP_LINE}; \
             _tai_complete; printf 'UNITS=[%s]\\n' \"${COMPREPLY[*]}\"",
        );
        let raw = s.raw().replace('\r', "");
        let got = raw
            .lines()
            .filter(|l| l.contains("UNITS=["))
            .collect::<Vec<_>>()
            .join("\n");
        assert!(
            got.contains("NetworkManager.service"),
            "bash unit completion folds case: {got:?}"
        );
        s.close();
        assert!(s.noise().is_empty(), "bash units clean: {:?}", s.noise());
    }
    write_index(None, None);
}

#[test]
fn test_ghost_cap() {
    // A long hint paints 200 characters and an ellipsis — and only paints.
    //
    // The store refuses lines over MAX_CMD_LEN now; this line is under it, the
    // way a person's worst command is, and the cap is what keeps even that from
    // wrapping the prompt. Painted short, taken whole: the painted field holds
    // the ellipsised glance, and the stored suggestion behind it stays whole,
    // because the key that takes the hint appends all of it.
    let _guard = LOCK.lock().unwrap_or_else(|e| e.into_inner());
    if shell_path("zsh").is_none() {
        return;
    }
    setup();
    let cmd = format!("ls {}", "x".repeat(900));
    write_index(Some(&[cmd.as_str()]), None);
    let mut s = Session::new("zsh");
    s.send("ls ");
    let dump = s.suggestion(); // one dump: a second one reads a cleared line
    assert_eq!(
        pd_of(&dump),
        format!("{}…", "x".repeat(200)),
        "zsh ghost paints short"
    );
    assert_eq!(
        sug_of(&dump),
        format!("SUG=[{}]", "x".repeat(900)),
        "and is still taken whole"
    );
    s.close();
    assert!(s.noise().is_empty(), "zsh cap clean: {:?}", s.noise());
    write_index(None, None);
}
