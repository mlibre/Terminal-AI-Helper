//! systemctl unit completion, driven through a real pty.
//!
//! Ported from `tests/test_plugins_units.py`. `sudo systemctl restart <Tab>` is
//! supposed to cycle the real service units — Manjaro's own completion does,
//! and a shell without it feels broken the moment you type one. tai answers the
//! same question from a cached unit list: one `systemctl` call per day, one
//! file per scope, and after that no process and no I/O on the keystroke path.
//!
//! The sessions here get a fake `systemctl` on PATH and a scratch cache
//! directory, so the assertions are about these units on any machine, and the
//! cache the test writes or the loader forks is never the developer's.

mod support;
use support::*;

use regex::Regex;
use std::collections::BTreeSet;
use std::os::unix::fs::PermissionsExt;
use std::path::{Path, PathBuf};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

/// The tests share /tmp/tai, so they run one at a time.
static LOCK: std::sync::Mutex<()> = std::sync::Mutex::new(());

fn have(shell: &str) -> bool {
    shell_path(shell).is_some()
}

fn units_bin() -> PathBuf {
    PathBuf::from("/tmp/tai/tai_units_bin")
}
fn units_cache() -> PathBuf {
    PathBuf::from("/tmp/tai/tai_units_cache")
}

const SYSTEM_UNITS: &[&str] = &[
    "docker.service",
    "herm.service",
    "herm2.service",
    "sshd.service",
    "tmp.mount",
];
#[allow(dead_code)]
const USER_UNITS: &[&str] = &["user-a.service", "user-b.timer"];

const FAKE_SYSTEMCTL: &str = r#"#!/bin/sh
if [ "$1" = "--user" ]; then
  printf '%s enabled\n' user-a.service user-b.timer
else
  printf '%s enabled\n' sshd.service herm.service docker.service herm2.service tmp.mount
fi
"#;

fn write_executable(path: &Path, body: &str) {
    std::fs::write(path, body).unwrap();
    std::fs::set_permissions(path, std::fs::Permissions::from_mode(0o755)).unwrap();
}

fn setup_units() {
    let bin = units_bin();
    let cache = units_cache();
    let _ = std::fs::remove_dir_all(&bin);
    let _ = std::fs::remove_dir_all(&cache);
    std::fs::create_dir_all(&bin).unwrap();
    std::fs::create_dir_all(&cache).unwrap();
    write_executable(&bin.join("systemctl"), FAKE_SYSTEMCTL);
}

/// The environment every units session runs in: the fake systemctl first on
/// PATH, and a scratch XDG_CACHE_HOME the real cache never touches.
fn units_env() -> Vec<(String, String)> {
    vec![
        (
            "PATH".to_string(),
            format!("{}:{}", units_bin().display(), std::env::var("PATH").unwrap_or_default()),
        ),
        ("XDG_CACHE_HOME".to_string(), units_cache().to_string_lossy().into_owned()),
    ]
}

fn env_refs(env: &[(String, String)]) -> Vec<(&str, &str)> {
    env.iter().map(|(k, v)| (k.as_str(), v.as_str())).collect()
}

fn cache_text(scope: &str) -> String {
    let path = units_cache().join("tai").join(format!("units-{scope}.txt"));
    std::fs::read_to_string(path).unwrap_or_default()
}

fn now_secs() -> i64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap()
        .as_secs() as i64
}

/// The suggestion line's `SUG=[…]` half — everything before the styled region,
/// which is terminal-dependent.
fn sug_pd(s: &mut Session) -> String {
    s.suggestion().split(" PD=").next().unwrap_or("").to_string()
}

#[test]
fn zsh_units() {
    let _guard = LOCK.lock().unwrap_or_else(|e| e.into_inner());
    setup();
    setup_units();
    if !have("zsh") {
        return;
    }
    let env = units_env();
    let mut s = Session::new_with("zsh", &env_refs(&env), true);
    // Load the cache deterministically: the ghost text must not fork, so a cold
    // cache answers nothing there by design. This is what the prompt hook's
    // preload does in real use, minus the timing race.
    s.run("_tai_units_load 0 >/dev/null 2>&1");
    s.send("sudo systemctl restart her");
    assert_eq!(
        sug_pd(&mut s),
        "SUG=[m.service]",
        "zsh hints the unit that extends the word"
    );
    s.write(CTRL_U);

    // The list, asked for directly: the units ARE the vocabulary on this line —
    // files, command names and the directory listing would be noise around them.
    s.send("systemctl restart ");
    s.write(LIST);
    s.settle();
    let m = s.menu(false);
    let listed: Vec<&str> = ["herm.service", "sshd.service"]
        .iter()
        .copied()
        .filter(|u| m.drawn.contains(u))
        .collect();
    assert_eq!(
        listed,
        vec!["herm.service", "sshd.service"],
        "zsh lists the units for an opened argument"
    );
    assert!(
        !m.drawn.contains("tzz_a"),
        "zsh does not list files on a unit line"
    );
    s.write(CTRL_U);

    // The shared head is written before the list draws, on this vocabulary like
    // on every other: `her` is answered by two units that agree up to `herm`,
    // so the line takes `herm` and the list stands under it. Enter then lays
    // the whole unit over the span the head fills — the `.service` written
    // once, the head not doubled.
    s.send("sudo systemctl restart her");
    s.write(TAB);
    s.settle();
    let m = s.menu(false);
    assert_eq!(
        m.line,
        "sudo systemctl restart herm",
        "the units menu writes the head both units share"
    );
    assert_eq!(
        (menu_entries(&m.drawn), m.entries),
        (vec!["herm.service".to_string(), "herm2.service".to_string()], 2),
        "and still lists both units"
    );
    s.write(ENTER);
    s.settle();
    let m = s.menu(false);
    // The unit is one word, so the commit rule writes the space after it — the
    // next argument (or Enter) is ready without a keystroke of preparation.
    assert_eq!(
        (m.line, m.entries),
        ("sudo systemctl restart herm.service ".to_string(), 0),
        "Enter takes a unit over the advanced line"
    );
    s.write(CTRL_U);

    // A verb still being typed is not a unit argument: `resta` may yet grow into
    // `restart`, and a unit after it would be a guess.
    s.send("systemctl resta");
    assert_eq!(
        sug_pd(&mut s),
        "SUG=[]",
        "zsh answers nothing while the verb is half-typed"
    );
    s.write(CTRL_U);

    // --user completes from the user's own units, not the system's.
    s.run("_tai_units_load 1 >/dev/null 2>&1");
    s.send("systemctl --user restart use");
    assert_eq!(sug_pd(&mut s), "SUG=[r-a.service]", "zsh hints a user unit on --user");
    s.write(CTRL_U);

    // One fork, one file: the cache exists, and the loader reads it — the
    // freshness line first, the units after. (The Python check compared the
    // cache's first line to itself — a tautology that only ever proved the
    // read did not panic; the non-emptiness below is the live part of it, and
    // the units themselves are checked next.)
    let body = cache_text("system");
    assert!(!body.is_empty(), "the system cache was written");
    assert!(
        SYSTEM_UNITS.iter().all(|u| body.contains(u)),
        "the cache holds the units"
    );
    s.close();
    let noise = s.noise();
    assert!(noise.is_empty(), "zsh units clean: {noise:?}");
}

#[test]
fn bash_units() {
    let _guard = LOCK.lock().unwrap_or_else(|e| e.into_inner());
    setup();
    setup_units();
    if !have("bash") {
        return;
    }
    let env = units_env();
    let mut s = Session::new_with("bash", &env_refs(&env), true);
    s.run("_tai_units_load 0 >/dev/null 2>&1");
    // bash has no `${(j)}` — read COMPREPLY back the bash way.
    s.run(
        r##"COMP_LINE='sudo systemctl restart her'; COMP_POINT=${#COMP_LINE}; _tai_complete; printf 'UNITS=[%s]\n' "${COMPREPLY[*]}""##,
    );
    let raw = s.raw().replace('\r', "");
    let got = raw
        .lines()
        .filter(|l| l.contains("UNITS=["))
        .collect::<Vec<_>>()
        .join("\n");
    assert!(
        got.contains("herm.service") && got.contains("herm2.service"),
        "bash completes the unit: {got:?}"
    );
    s.run(
        r##"COMP_LINE='systemctl cat ssh'; COMP_POINT=${#COMP_LINE}; _tai_complete; printf 'UNITS2=[%s]\n' "${COMPREPLY[*]}""##,
    );
    let raw = s.raw().replace('\r', "");
    let got = raw
        .lines()
        .filter(|l| l.contains("UNITS2=["))
        .collect::<Vec<_>>()
        .join("\n");
    assert!(got.contains("sshd.service"), "bash completes through `cat`: {got:?}");
    s.close();
    let noise = s.noise();
    assert!(noise.is_empty(), "bash units clean: {noise:?}");
}

#[test]
fn units_cache_is_reused() {
    let _guard = LOCK.lock().unwrap_or_else(|e| e.into_inner());
    setup();
    setup_units();
    if !have("zsh") {
        return;
    }
    // A fresh cache written with a *current* stamp must be read back, not
    // refetched: point the loader at a systemctl that fails, and a fresh cache
    // still fills the array.
    let body = format!("{}\n{}\n", now_secs() - 10, SYSTEM_UNITS.join("\n"));
    std::fs::create_dir_all(units_cache().join("tai")).unwrap();
    std::fs::write(units_cache().join("tai").join("units-system.txt"), body).unwrap();
    let broken = units_bin().join("brokenctl");
    write_executable(&broken, "#!/bin/sh\nexit 3\n");
    // No system PATH at all — nothing but the fake bin can answer, so a
    // refetch would be visible as a failure.
    let env = vec![
        ("PATH".to_string(), format!("{}:", units_bin().display())),
        ("XDG_CACHE_HOME".to_string(), units_cache().to_string_lossy().into_owned()),
    ];
    let mut s = Session::new_with("zsh", &env_refs(&env), true);
    s.run("_tai_units_load 0 >/dev/null 2>&1");
    s.send("sudo systemctl restart her");
    assert_eq!(
        sug_pd(&mut s),
        "SUG=[m.service]",
        "a fresh cache answers with the units it holds"
    );
    s.close();
    let noise = s.noise();
    assert!(noise.is_empty(), "cache reuse clean: {noise:?}");
}

#[test]
fn preload_makes_no_job_noise() {
    let _guard = LOCK.lock().unwrap_or_else(|e| e.into_inner());
    setup();
    setup_units();
    // The prompt hook's preload is nobody's job, so nothing announces it.
    //
    // A plain `&` in the prompt hook made the loader a job of the interactive
    // shell, and the shell reported it: bash printed `[N] pid` at spawn and
    // `[N]+ Done` before the next prompt, zsh the same in its own words — the
    // user saw both around every systemctl command. The parenthesised fork
    // (the rule the flush and the record fallback always followed) makes the
    // loader the grandchild of a subshell that exits at once. The work still
    // happens — both cache files land — and the terminal stays silent.
    for shell in ["bash", "zsh"] {
        if !have(shell) {
            continue;
        }
        let env = units_env();
        let mut s = Session::new_with(shell, &env_refs(&env), true);
        let mark = s.seen_len();
        // A command that mentions systemctl, at the prompt: the prompt hook
        // answers it with the background preload. Both cache files appearing
        // is the observable end of that work.
        s.run("systemctl --user disable nothing.service");
        let end = Instant::now() + Duration::from_secs(15);
        while (cache_text("system").is_empty() || cache_text("user").is_empty())
            && Instant::now() < end
        {
            std::thread::sleep(Duration::from_millis(10));
        }
        assert!(
            cache_text("system").contains("herm.service"),
            "{shell} preload wrote the system cache"
        );
        assert!(
            cache_text("user").contains("user-a.service"),
            "{shell} preload wrote the user cache"
        );
        // Job-state reports are printed before the next prompt, so give the
        // shell one more prompt to speak in.
        s.run("true");
        s.close();
        let seen = s.raw()[mark..].replace('\r', "");
        let re = Regex::new(r"\[\d+\]\s*(?:\d+|\+)").unwrap();
        let notes: Vec<String> =
            re.find_iter(&seen).map(|m| m.as_str().to_string()).collect::<BTreeSet<_>>().into_iter().collect();
        assert_eq!(
            notes,
            Vec::<String>::new(),
            "{shell} preload leaves no job announcements\n       terminal tail:\n{}",
            indent(&tail(&s.raw(), 400), "       ")
        );
    }
}

#[test]
fn units_cache_survives_corruption() {
    let _guard = LOCK.lock().unwrap_or_else(|e| e.into_inner());
    setup();
    setup_units();
    // A torn, stale-in-the-future, or gutted cache refetches, never sticks.
    //
    // The cache is one file a shell reads back; anything can happen to it — a
    // truncated write, a clock that jumped, a stamp with no units under it. The
    // loader's answer in every one of those cases has to be the same: fork the
    // one `systemctl` call, rewrite the file, answer with the real units. A
    // cache that could answer "no units" for a day because of one bad write
    // would be a completion that lies politely.
    for shell in ["bash", "zsh"] {
        if !have(shell) {
            continue;
        }
        let cache_dir = units_cache().join("tai");
        std::fs::create_dir_all(&cache_dir).unwrap();
        let env = units_env();
        let mut s = Session::new_with(shell, &env_refs(&env), true);
        for (name, body) in [
            ("a garbage stamp", "not-a-number\njunk\n".to_string()),
            ("a stamp with no units", format!("{}\n", now_secs() - 10)),
            (
                "a stamp from the future",
                format!("{}\nherm.service\n", now_secs() + 90_000),
            ),
        ] {
            std::fs::write(cache_dir.join("units-system.txt"), &body).unwrap();
            s.run("_tai_units_load 0 >/dev/null 2>&1");
            // The accept key, not the ghost: bash paints no hint while a line
            // is being typed — its suggestion arrives when a key takes it —
            // so the answer is read the way a user reads it, in both shells.
            s.send("sudo systemctl restart her");
            s.write(RIGHT);
            s.settle();
            assert_eq!(
                s.line(),
                "sudo systemctl restart herm.service",
                "{shell}: {name} refetches rather than answering"
            );
            s.write(CTRL_U);
            let body_now = cache_text("system");
            let first = body_now.lines().next().unwrap_or("");
            assert!(
                !first.is_empty()
                    && first.chars().all(|c| c.is_ascii_digit())
                    && body_now.contains("herm.service"),
                "{shell}: {name} left a whole cache behind: {body_now:?}"
            );
        }
        s.close();
        let noise = s.noise();
        assert!(noise.is_empty(), "{shell} corrupt-cache runs clean: {noise:?}");
    }
}
