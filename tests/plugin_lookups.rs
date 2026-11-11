//! Plugin lookup behaviour, driven through a real pty.
//!
//! Ported from `tests/test_plugins.py` — the "lookups" suite: what a typed
//! word completes to, what wrapping a command does to it, that each shell
//! reads its own index, and the prompt-time prophecy. The menu, the
//! files-on-the-end-of-a-line, and the index lifecycle have their own entry
//! points.
//!
//! Three rules keep these tests honest and fast (inherited from the harness):
//!
//!  * every run uses a scratch TAI_DB, so a test can never write into the
//!    developer's real history;
//!  * the default session does not record, so no background process can
//!    rebuild the index fixture these tests assert on;
//!  * every wait ends on something observable rather than on a stopwatch.

mod support;
use support::*;
use std::os::unix::fs::PermissionsExt;

use std::collections::BTreeMap;
use std::path::PathBuf;

/// The tests share /tmp/tai, so they run one at a time.
static LOCK: std::sync::Mutex<()> = std::sync::Mutex::new(());

fn have(shell: &str) -> bool {
    shell_path(shell).is_some()
}

/// The suggestion line's `SUG=[…] PD=[…]` half — everything before the styled
/// region, which is terminal-dependent.
fn sug_pd(s: &mut Session) -> String {
    s.suggestion().split(" PD=").next().unwrap_or("").to_string()
}

/// Same, cut before the region-highlight half.
fn sug_before_rh(s: &mut Session) -> String {
    s.suggestion().split(" RH=").next().unwrap_or("").to_string()
}

#[test]
fn bash() {
    let _guard = LOCK.lock().unwrap_or_else(|e| e.into_inner());
    setup();
    // Right arrow with the cursor mid-line must move, not accept.
    probe("bash", &format!("{CTRL_A}{RIGHT}X"), "git status", "gXit status");
    // Right arrow at end of line accepts the suggestion.
    probe("bash", &format!("{}X", RIGHT.repeat(12)), "git status", "git statusX");
    // Ctrl-F accepts.
    probe("bash", &format!("{CTRL_E}{CTRL_F}X"), "git status", "git statusX");
    // Nothing to suggest, cursor at end: stays put, no crash.
    probe("bash", &format!("{}X", RIGHT.repeat(3)), "zzzz", "zzzzX");
    // An empty prompt must not raise "bad array subscript". It stays empty here
    // because the fixture defines no _TAI_SEQ entries: with sequence data the
    // empty prompt would legitimately predict the next command, so this asserts
    // the no-crash invariant, not the absence of a prediction.
    probe("bash", &RIGHT.repeat(4), "", "");
    // Mid-line with no suggestion still moves exactly one character.
    probe("bash", &format!("{CTRL_A}{}X", RIGHT.repeat(2)), "abcd", "abXcd");
    // Same arrow, application cursor-key mode: ESC O C, not ESC [ C. zsh had
    // this miss; bash now binds both byte strings, and both must move mid-line.
    probe("bash", RIGHT_APP, "docker", "docker ps");
    probe("bash", &format!("{CTRL_A}{}X", RIGHT_APP.repeat(2)), "abcd", "abXcd");
    // One word of the suggestion, on the key the hand already makes for "next
    // word". `git ` hints `git pull --rebase`, so one word is `git pull` — the
    // same answer zsh gives, and the only way the two shells can be said to
    // agree about it.
    probe("bash", &format!("{CTRL_A}{CTRL_RIGHT}"), "git ", "git pull");
    // With no suggestion, bash's `bind -x` cannot move the cursor on its own —
    // readline ignores a bare READLINE_POINT from a bound function — so the key
    // rewrites nothing and Alt-F, which is readline's own `forward-word`, is the
    // one that moves. Asserted as what it is: the key does not invent an answer.
    probe("bash", &format!("{CTRL_A}{CTRL_RIGHT}X"), "abcd", "aXbcd");
    check_fixture_intact("the run");
}

#[test]
fn zsh() {
    let _guard = LOCK.lock().unwrap_or_else(|e| e.into_inner());
    setup();
    if !have("zsh") {
        return;
    }
    // Read ZLE's own view of the suggestion from inside a widget. Asserting on
    // the bytes ZLE paints is unreliable, because how the post-display region
    // is drawn depends on the terminal.
    let mut s = Session::new("zsh");
    s.send("docker");
    // "docker" is 6 characters and the hint is " ps", so the styled region is
    // 6..9 and covers the hint exactly.
    assert_eq!(
        s.suggestion(),
        "SUG=[ ps] PD=[ ps] RH=[6 9 fg=8,bold]",
        "zsh styles the ghost via region_highlight"
    );
    let noise = s.noise();
    assert!(noise.is_empty(), "zsh has no hook errors: {noise:?}");
    s.close();

    probe("zsh", &format!("{CTRL_E}{CTRL_F}X"), "docker", "docker psX");
    probe("zsh", &format!("{CTRL_E}{CTRL_F}"), "docker", "docker ps");
    probe("zsh", &RIGHT.repeat(3), "", "");
    // Right arrow takes the whole hint at end of line, and steps one character
    // when the cursor is not at the end. The dump reads the buffer, not the
    // cursor, so a cursor move shows up as the text being left alone — which is
    // the assertion: with the arrow bound straight to accept, these two would
    // have pulled " ps" into the line.
    probe("zsh", RIGHT, "docker", "docker ps");
    probe("zsh", &format!("{CTRL_A}{RIGHT}"), "docker", "docker");
    probe("zsh", &format!("{CTRL_A}{}", RIGHT.repeat(2)), "docker", "docker");
    // The same arrow as a terminal actually sends it. ZLE switches the terminal
    // into application-cursor-key mode at line-init (`smkx`), and from then on the
    // arrow is ESC O C, not ESC [ C — a widget bound to the normal-mode form only
    // is never reached, and the key that takes the hint does nothing at the end of
    // the line. Sending the bytes a terminal sends is the only way to see this:
    // every other test in this file writes the normal-mode sequence and so passes
    // a plugin that no user could use.
    probe("zsh", RIGHT_APP, "docker", "docker ps");
    probe("zsh", &format!("{CTRL_A}{RIGHT_APP}"), "docker", "docker");
    // A fresh line must not carry a stale ghost.
    probe("zsh", CTRL_E, "docker", "docker");
    // Alt-F accepts a single word, matching the documented behaviour: the full
    // suggestion for "git " is "git pull --rebase", but only "git pull" lands.
    probe("zsh", &format!("{CTRL_E}{ALT_F}"), "git ", "git pull");
    // Nothing to accept leaves the line alone.
    probe("zsh", &format!("{CTRL_E}{ALT_F}"), "git status", "git status");
    check_fixture_intact("the run");
}

#[test]
fn prefix_lookups() {
    let _guard = LOCK.lock().unwrap_or_else(|e| e.into_inner());
    setup();
    if !have("zsh") {
        return;
    }
    let mut g = Ghosts::new();
    assert_eq!(g.of("ls -l"), "a", "`ls -l` extends to `ls -la`");
    assert_eq!(g.of("ls -"), "la", "`ls -` extends to `ls -la`");
    assert_eq!(g.of("ls -la"), "", "`ls -la` has nothing left to add");
    assert_eq!(
        g.of(INSTALLED),
        " --help",
        "installed `{INSTALLED}` offers --help"
    );
    assert_eq!(
        g.of(&format!("{INSTALLED} ")),
        "--help",
        "installed `{INSTALLED} ` offers --help"
    );
    assert_eq!(g.of(NOT_INSTALLED), "", "uninstalled name stays empty");
    assert_eq!(
        g.of(&format!("{NOT_INSTALLED} ")),
        "",
        "uninstalled name with space stays empty"
    );
    assert_eq!(
        g.of(&NOT_INSTALLED[..9]),
        "",
        "partial unknown name stays empty"
    );
    // A line of nothing but spaces has no complete word to look up, and bash
    // treats an empty associative-array subscript as a hard error. This is the
    // input that would print "bad array subscript" on every single prompt.
    assert_eq!(g.of("   "), "", "whitespace-only line is clean");
    g.close();

    if !have("bash") {
        return;
    }
    probe("bash", &format!("{CTRL_E}{CTRL_F}"), "ls -l", "ls -la");
    probe(
        "bash",
        &format!("{CTRL_E}{CTRL_F}"),
        INSTALLED,
        &format!("{INSTALLED} --help"),
    );
    probe(
        "bash",
        &format!("{CTRL_E}{CTRL_F}"),
        &format!("{INSTALLED} "),
        &format!("{INSTALLED} --help"),
    );
    probe(
        "bash",
        &format!("{CTRL_E}{CTRL_F}X"),
        &format!("{NOT_INSTALLED} "),
        &format!("{NOT_INSTALLED} X"),
    );
    probe("bash", &format!("{CTRL_E}{CTRL_F}X"), "   ", "   X");
    check_fixture_intact("the run");
}

#[test]
fn half_typed_command() {
    let _guard = LOCK.lock().unwrap_or_else(|e| e.into_inner());
    setup();
    if !have("zsh") {
        return;
    }
    // An empty directory, so nothing can answer from the filesystem and every
    // answer below is the index's or nothing's. The one file here is the one
    // the fixture's `cat tzz_d` names: a file-argument line is judged where it
    // is offered now, and this test is about the name scan, not the judgement —
    // the judgement has its own suite (test_plugins_files). Put the file where
    // the test stands, and the scan is all that is under test.
    std::fs::write(empty_dir().join("tzz_d"), "").unwrap();
    let mut s = Session::new("zsh");
    s.run(&format!("cd {}", empty_dir().display()));
    for (prefix, want) in [
        ("doc", "ker ps"),       // the `god` case
        ("gi", "t pull --rebase"),
        ("ca", "t tzz_d"),
        ("l", "s -la"),
        ("cat", " tzz_d"),       // the exact name still wins
        ("cdt", ""),             // nothing begins with it
        (&NOT_INSTALLED[..9], ""), // and it stays a non-answer
        ("c*t", ""),             // a glob is not a prefix
    ] {
        s.send(prefix);
        assert_eq!(
            sug_pd(&mut s),
            format!("SUG=[{want}]"),
            "zsh {prefix:?} hints"
        );
    }
    let _ = std::fs::remove_file(empty_dir().join("tzz_d"));
    // The arrow takes what the hint says, which is the point of it being a hint.
    s.send("doc");
    s.write(RIGHT);
    s.settle();
    assert_eq!(s.line(), "docker ps", "and the arrow takes it");
    s.close();

    if !have("bash") {
        return;
    }
    // Tab is the shell's own first-word completion here — tai sits on Tab only
    // under TAI_COMPLETE_ALL=1, which the menu suite covers — so what this
    // probe answers with is the machine's executable vocabulary, and it used
    // to be whatever that happened to be. A machine that gained a `docx2txt`
    // — a pip tool nothing in this repository ever asked for — saw the matches'
    // common prefix fall from `docker` back to `doc`, readline lists instead of
    // inserting then, and the assertion was measuring the machine's binary zoo
    // rather than anything tai does. The PATH below is the test's own: two
    // `doc*` executables that share the `docker` prefix, so the answer is the
    // prefix, unambiguous per what was authored, and nothing else.
    let stub = PathBuf::from("/tmp/tai/tai_stub_bin");
    std::fs::create_dir_all(&stub).unwrap();
    for name in ["docker", "dockerd"] {
        let shim = stub.join(name);
        if !shim.exists() {
            std::fs::write(&shim, "#!/bin/sh\nexit 0\n").unwrap();
            std::fs::set_permissions(&shim, std::fs::Permissions::from_mode(0o755)).unwrap();
        }
    }
    // The plugin locates its own directory with `dirname` at source time and
    // sorts the index keys and the learned lines with `sort` — the very path
    // the accept key below reads — and the harness's own line-dump editor runs
    // `cp`, so the stub carries those real binaries and nothing else.
    for name in ["cp", "dirname", "sort"] {
        if let Some(real) = which_bin(name) {
            let link = stub.join(name);
            if !link.exists() {
                std::os::unix::fs::symlink(&real, &link).unwrap();
            }
        }
    }
    let hermetic = [("PATH", stub.to_str().unwrap())];
    probe_env("bash", TAB, "doc", "docker", &hermetic);
    probe_env("bash", &format!("{CTRL_E}{CTRL_F}"), "doc", "docker ps", &hermetic);
    probe_env("bash", &format!("{CTRL_E}{CTRL_F}"), "cdt", "cdt", &hermetic);
    check_fixture_intact("the run");
}

/// The generator rules (fixture_rules.py): the WORD keys a real index build
/// writes for the fixture commands, with their candidate lists in order.
fn real_word_rows() -> BTreeMap<String, Vec<String>> {
    let mut map: BTreeMap<String, Vec<String>> = BTreeMap::new();
    for cmd in DEFAULT_COMMANDS {
        let parts: Vec<&str> = cmd.split(' ').collect();
        for i in 2..=parts.len().min(WORD_KEY_MAX_DEPTH) {
            let key = parts[..i].join(" ");
            map.entry(key).or_default().push(cmd.to_string());
        }
    }
    map
}

/// The WORD rows actually on disk in the fixture index. Each entry is
/// `_TAI_WORD+=('key' 'cand1\ncand2')` — and the fixture writer cannot quote,
/// so no value ever holds a quote and the parse below is exact.
fn fixture_word_rows(fixture: &str) -> BTreeMap<String, Vec<String>> {
    let mut map = BTreeMap::new();
    let mut rest = fixture;
    while let Some(i) = rest.find("_TAI_WORD+=(") {
        rest = &rest[i + "_TAI_WORD+=(".len()..];
        let end = rest.find("')").expect("unterminated _TAI_WORD entry");
        let body = rest[..end].strip_prefix('\'').expect("entry is quoted");
        let (key, value) = body.split_once("' '").expect("entry is a pair");
        map.insert(
            key.to_string(),
            value.split('\n').map(|c| c.to_string()).collect(),
        );
        rest = &rest[end + 2..];
    }
    map
}

#[test]
fn fixture_matches_generator() {
    let _guard = LOCK.lock().unwrap_or_else(|e| e.into_inner());
    setup();
    // The assertion is deliberately not "the tests pass". It is that the two
    // writers agree on which keys exist, which is the only property that makes
    // the rest of the suite mean anything. The fixture side is read out of the
    // index file `write_index` put on disk, the generator side is computed
    // from the same rules `tai/index.py` uses.
    let real = real_word_rows();
    let fixture_text = std::fs::read_to_string(zsh_index()).unwrap();
    let fixture = fixture_word_rows(&fixture_text);

    let real_keys: Vec<&str> = real.keys().map(|k| k.as_str()).collect();
    let fixture_keys: Vec<&str> = fixture.keys().map(|k| k.as_str()).collect();
    assert_eq!(fixture_keys, real_keys, "the fixture's word keys are the generator's");
    assert_eq!(fixture, real, "and so are their candidate lists");

    // The specific shape the wrapper rule depends on, asserted directly so the
    // failure names the cause rather than "the fixture drifted".
    for key in ["git", "git "] {
        assert!(
            !fixture.contains_key(key),
            "no {key:?} word key, exactly as the generator writes it"
        );
    }
    check_fixture_intact("the run");
}

#[test]
fn wrappers() {
    let _guard = LOCK.lock().unwrap_or_else(|e| e.into_inner());
    setup();
    if !have("zsh") {
        return;
    }
    let mut g = Ghosts::new();
    assert_eq!(
        g.of("sudo git "),
        "pull --rebase",
        "`sudo git ` completes from `git ...`"
    );
    assert_eq!(g.of("sudo git s"), "tatus", "`sudo git s` completes too");
    assert_eq!(g.of("sudo ls -l"), "a", "`sudo ls -l` still extends");
    assert_eq!(g.of("doas docker "), "ps", "`doas docker ` works");
    assert_eq!(g.of("time git "), "pull --rebase", "`time git ` works");
    // `env` and `xargs` change what follows them enough that re-attaching the
    // wrapper would be a lie, and a wrapper carrying assignments is not a
    // transparent prefix at all.
    assert_eq!(g.of("env git "), "", "`env git ` stays empty");
    assert_eq!(g.of("xargs git "), "", "`xargs git ` stays empty");
    assert_eq!(g.of("sudo FOO=1 git "), "", "`sudo FOO=1 git ` stays empty");
    assert_eq!(
        g.of("sudo zzznope "),
        "",
        "an unknown wrapped command stays empty"
    );
    // With no `sudo` history there is nothing behind the wrapper to rank, and the
    // honest answer for a name that exists is its own --help. What must never
    // happen is an invented continuation.
    assert_eq!(g.of("sudo "), "--help", "`sudo ` with no history offers --help");
    g.close();

    if !have("bash") {
        return;
    }
    probe(
        "bash",
        &format!("{CTRL_E}{CTRL_F}"),
        "sudo git ",
        "sudo git pull --rebase",
    );
    probe("bash", &format!("{CTRL_E}{CTRL_F}"), "sudo ls -l", "sudo ls -la");
    probe(
        "bash",
        &format!("{CTRL_E}{CTRL_F}X"),
        "env git ",
        "env git X",
    );
    check_fixture_intact("the run");
}

#[test]
fn each_shell_reads_its_own_index() {
    let _guard = LOCK.lock().unwrap_or_else(|e| e.into_inner());
    setup();
    for (shell, want) in [("zsh", zsh_index()), ("bash", bash_index())] {
        // A machine without one of the shells skips its half rather than
        // failing here; the other shell's assertions still run.
        if !have(shell) {
            continue;
        }
        // A live session, and the file it actually resolved. Read out of the
        // running shell rather than re-deriving it here, so a plugin that
        // resolved the wrong path cannot agree with this test by construction.
        // The session stands in MENU_DIR, because the answer it reads is a
        // *destination* — `cd tzz_dir` — and a destination is judged from where
        // the user stands: from the repository root it is dead, and the plugin
        // is right to stay silent about it.
        let mut s = Session::new(shell);
        s.run(&format!("cd {}", menu_dir().display()));
        let scratch = PathBuf::from(format!("/tmp/tai/tai_index_name_{}.txt", std::process::id()));
        let _ = std::fs::remove_file(&scratch);
        s.run(&format!("basename \"$_TAI_INDEX_FILE\" > {}", scratch.display()));
        let got = std::fs::read_to_string(&scratch).unwrap().trim().to_string();
        assert_eq!(
            got,
            want.file_name().unwrap().to_string_lossy(),
            "{shell} reads its own index"
        );
        // And it holds the answers, not merely the name. Each shell delivers
        // its hint its own way — zsh as POSTDISPLAY, which is only the text
        // that extends the line, and bash by rewriting the readline buffer on
        // the accept key — so each is read the way that shell gives it.
        s.send("cd ");
        if shell == "zsh" {
            assert_eq!(sug_pd(&mut s), "SUG=[tzz_dir]", "{shell} answers from that file");
        } else {
            s.write(CTRL_F);
            s.settle();
            assert_eq!(s.line(), "cd tzz_dir", "{shell} answers from that file");
        }
        s.close();
    }
    // The two files really are different files, so the check above has teeth.
    assert_ne!(
        std::fs::read_to_string(zsh_index()).unwrap(),
        std::fs::read_to_string(bash_index()).unwrap(),
        "the two fixtures are not the same file"
    );
    check_fixture_intact("the run");
}

#[test]
fn a_foreign_index_is_not_executed() {
    let _guard = LOCK.lock().unwrap_or_else(|e| e.into_inner());
    setup();
    let good_zsh = std::fs::read_to_string(zsh_index()).unwrap();
    let good_bash = std::fs::read_to_string(bash_index()).unwrap();
    for (shell, path, good) in [
        ("zsh", zsh_index(), good_zsh.clone()),
        ("bash", bash_index(), good_bash.clone()),
    ] {
        // Same deal as everywhere else: a missing shell skips its half.
        if !have(shell) {
            continue;
        }
        for (label, text) in [
            ("the wrong file entirely", "hello world\n_TAI_SCORE=()\n".to_string()),
            (
                "a header with something else under it",
                format!("{}\nnot an index at all\n", good.lines().next().unwrap()),
            ),
            ("a file that is empty", String::new()),
        ] {
            std::fs::write(&path, text).unwrap();
            let mut s = Session::new(shell);
            s.run("true");
            let said = s.raw();
            assert!(said.contains("tai refresh"), "{shell} refuses {label}");
            let noise = s.noise();
            assert!(
                noise.is_empty(),
                "{shell} is not left printing errors after {label}: {noise:?}"
            );
            // …and it still works: the installed-command fallback answers, which
            // needs no index at all. zsh delivers it as ghost text and bash by
            // rewriting the line on the accept key, so each is read its way.
            s.send("git");
            if shell == "zsh" {
                assert_eq!(
                    sug_pd(&mut s),
                    "SUG=[ --help]",
                    "{shell} still answers after {label}"
                );
            } else {
                s.write(CTRL_F);
                s.settle();
                assert_eq!(
                    s.line(),
                    "git --help",
                    "{shell} still answers after {label}"
                );
            }
            s.close();
        }
        std::fs::write(&path, &good).unwrap();
    }
    check_fixture_intact("the run");
}

// The prophecy: the gray line on a fresh prompt is what the history saw follow
// the command that just ran. Its fixture is its own for the same reason the
// live-words fixture is: the shared one deliberately carries no _TAI_SEQ
// entries, which is what keeps every other empty-prompt assertion exact. The
// follower of the liveness pair is a relative path, so the line is judged where
// the prompt stands — the v2.2.0 rule, asked of the prophecy too.
fn prophecy_dir() -> PathBuf {
    PathBuf::from("/tmp/tai/tai_prophecy_cwd")
}
fn prophecy_with() -> PathBuf {
    prophecy_dir().join("with")
}
fn prophecy_without() -> PathBuf {
    prophecy_dir().join("without")
}
fn prophecy_zsh_path() -> PathBuf {
    PathBuf::from("/tmp/tai/tai_test_prophecy_index/zsh-index.zsh")
}
fn prophecy_bash_path() -> PathBuf {
    PathBuf::from("/tmp/tai/tai_test_prophecy_index/bash-index.bash")
}

/// Two learned pairs: one plain, one whose follower is a relative path.
fn write_prophecy_index() {
    std::fs::create_dir_all(prophecy_with().join("Data").join("INI")).unwrap();
    std::fs::create_dir_all(prophecy_without()).unwrap();
    std::fs::create_dir_all(prophecy_zsh_path().parent().unwrap()).unwrap();
    std::fs::write(
        prophecy_zsh_path(),
        concat!(
            "# tai zsh index; test fixture, not generated\n",
            "typeset -gA _TAI_SCORE _TAI_FIRST _TAI_WORD _TAI_SEQ _TAI_FILE _TAI_LIVE\n",
            "_TAI_SEQ+=('echo prophecy-before' 'echo prophecy-after')\n",
            "_TAI_SEQ+=('echo gone' 'ls Data/INI')\n",
            "_TAI_LIVE+=('Data/INI' 1)\n",
        ),
    )
    .unwrap();
    std::fs::write(
        prophecy_bash_path(),
        concat!(
            "# tai bash index; test fixture, not generated\n",
            "declare -gA _TAI_SCORE _TAI_FIRST _TAI_WORD _TAI_SEQ _TAI_FILE _TAI_LIVE\n",
            "_TAI_SEQ['echo prophecy-before']='echo prophecy-after'\n",
            "_TAI_SEQ['echo gone']='ls Data/INI'\n",
            "_TAI_LIVE['Data/INI']=1\n",
        ),
    )
    .unwrap();
}

/// Rows on screen naming the prophecy — by index, so "everything but the live
/// prompt's own row" is expressible.
fn prophecy_rows(s: &Session) -> Vec<usize> {
    screen_of(s)
        .lines()
        .iter()
        .enumerate()
        .filter(|(_, l)| l.contains("prophecy-after"))
        .map(|(r, _)| r)
        .collect()
}

/// Every prophecy row except the last — the last is the live prompt's own, and
/// a residue row is a row that reads as a command the user never ran.
fn assert_no_gray_trace(rows: Vec<usize>, what: &str) {
    let got: Vec<usize> = if rows.is_empty() {
        Vec::new()
    } else {
        rows[..rows.len() - 1].to_vec()
    };
    assert_eq!(got, Vec::<usize>::new(), "{what}");
}

#[test]
fn prophecy_zsh() {
    let _guard = LOCK.lock().unwrap_or_else(|e| e.into_inner());
    setup();
    if !have("zsh") {
        return;
    }
    write_prophecy_index();
    let idx = prophecy_zsh_path().to_string_lossy().into_owned();
    let mut s = Session::new_with("zsh", &[("TAI_INDEX", idx.as_str())], true);
    // A shell that has run nothing says nothing: the prophecy is learned, and
    // "by default" is exactly what it must never be.
    assert_eq!(
        sug_before_rh(&mut s),
        "SUG=[] PD=[]",
        "a shell that just opened says nothing"
    );
    s.send(&format!("echo prophecy-before{ENTER}"));
    s.settle();
    // Painted gray, on the empty prompt, before any keystroke: the whole
    // remainder is the suggestion, the styled region covers it from 0.
    assert_eq!(
        s.suggestion(),
        "SUG=[echo prophecy-after] PD=[echo prophecy-after] RH=[0 19 fg=8,bold]",
        "the fresh prompt carries the learned next command"
    );
    // No list rides on it — no menu rows over the prophecy, and the
    // post-display is one line, not a screen of did-you-mean.
    let m = s.menu(false);
    assert_eq!(m.entries, 0, "no menu stands over the prophecy");
    assert!(!m.drawn.contains('\n'), "the prophecy is a single line, not a list");
    // And the key that takes a hint takes it.
    s.write(RIGHT);
    s.settle();
    assert_eq!(s.line(), "echo prophecy-after", "the arrow takes the prophecy");
    // The line after that is fresh again, and typing replaces the prophecy
    // with the ordinary lookup — it is a suggestion, not a mode.
    s.send("zz");
    s.settle();
    assert_eq!(sug_before_rh(&mut s), "SUG=[] PD=[]", "typing replaces the prophecy");
    // The v2.2.0 rule, asked of the prophecy: the follower is judged where the
    // prompt stands, and a dead relative path is not an answer here.
    s.run(&format!("cd {}", prophecy_without().display()));
    s.send(&format!("echo gone{ENTER}"));
    s.settle();
    assert_eq!(
        sug_before_rh(&mut s),
        "SUG=[] PD=[]",
        "a prophecy with a dead relative path is not offered"
    );
    s.run(&format!("cd {}", prophecy_with().display()));
    s.send(&format!("echo gone{ENTER}"));
    s.settle();
    assert_eq!(
        sug_before_rh(&mut s),
        "SUG=[ls Data/INI] PD=[ls Data/INI]",
        "the same prophecy where the path exists"
    );
    let noise = s.noise();
    assert!(noise.is_empty(), "no noise from the prophecy: {noise:?}");
    s.close();
    check_fixture_intact("the run");
}

#[test]
fn prophecy_bash() {
    let _guard = LOCK.lock().unwrap_or_else(|e| e.into_inner());
    setup();
    if !have("bash") {
        return;
    }
    write_prophecy_index();
    let idx = prophecy_zsh_path().to_string_lossy().into_owned();
    let mut s = Session::new_with("bash", &[("TAI_INDEX", idx.as_str())], true);
    // Nothing has run: the arrow has nothing to fill.
    s.write(RIGHT);
    s.settle();
    assert_eq!(s.line(), "", "a shell that just opened fills nothing");
    s.send(&format!("echo prophecy-before{ENTER}"));
    s.settle();
    // bash paints no ghost, by its own design; the prophecy reaches the line
    // through the arrow that accepts a hint, exactly as before — and the
    // liveness judgement rides along, because the head of the list is judged
    // the same way the ghost's would be.
    s.write(RIGHT);
    s.settle();
    assert_eq!(
        s.line(),
        "echo prophecy-after",
        "the arrow fills the learned next command"
    );
    let noise = s.noise();
    assert!(noise.is_empty(), "no noise from the prophecy: {noise:?}");
    s.close();
    check_fixture_intact("the run");
}

#[test]
fn enter_runs_what_was_typed() {
    let _guard = LOCK.lock().unwrap_or_else(|e| e.into_inner());
    setup();
    if !have("zsh") {
        return;
    }
    write_prophecy_index();
    let idx = prophecy_zsh_path().to_string_lossy().into_owned();

    // The report: the fresh prompt carries the learned next command in gray,
    // Enter pressed without taking it runs an empty line, and the gray text
    // stays painted on the row the shell just left — three Enters stacked
    // three rows that read as if the command had run each time. The accept
    // echo re-renders the line as it will run, and tai's own redraw hook
    // answered that render with a fresh query, painting the prophecy right
    // back. Accept is the one redraw that paints nothing: the echo shows
    // what was typed, and what tai drew goes down with the line.
    let mut s = Session::new_with("zsh", &[("TAI_INDEX", idx.as_str())], true);
    s.send(&format!("echo prophecy-before{ENTER}"));
    s.settle();
    for i in 1..=3 {
        s.write(ENTER);
        s.settle();
        // The empty line the shell ran says nothing; the only row naming the
        // prophecy is the live prompt's own.
        assert_no_gray_trace(
            prophecy_rows(&s),
            &format!("Enter #{i} leaves no gray trace on the row it left"),
        );
    }
    // And the prophecy on the fresh prompt still works: the arrow takes it.
    s.write(RIGHT);
    s.settle();
    assert_eq!(
        s.line(),
        "echo prophecy-after",
        "the arrow still takes the prophecy after Enters"
    );
    s.close();

    // The ordinary ghost answers to the same rule: `echo prop` hinted, Enter
    // runs the typed line, and the part nobody typed is nowhere on screen. A
    // fresh session, so the row the arrow's acceptance legitimately painted
    // above is not here to answer for it.
    let mut s = Session::new_with("zsh", &[("TAI_INDEX", idx.as_str())], true);
    s.send("echo prop");
    s.settle();
    s.write(ENTER);
    s.settle();
    let after: Vec<String> = screen_of(&s)
        .lines()
        .into_iter()
        .filter(|l| l.contains("prophecy-after"))
        .collect();
    assert!(after.is_empty(), "the hinted remainder is not on the executed row");
    let typed: Vec<String> = screen_of(&s)
        .lines()
        .into_iter()
        .filter(|l| l.trim() == "prop")
        .collect();
    assert_eq!(typed, ["prop".to_string()], "the typed line is what ran");
    s.close();

    // TAI_NO_MENU=1 leaves Enter as stock accept-line — the same echo, and
    // the same paint-back through the redraw hook. The wrapper answers it.
    let mut s = Session::new_with(
        "zsh",
        &[("TAI_INDEX", idx.as_str()), ("TAI_NO_MENU", "1")],
        true,
    );
    s.send(&format!("echo prophecy-before{ENTER}"));
    s.settle();
    for i in 1..=2 {
        s.write(ENTER);
        s.settle();
        assert_no_gray_trace(prophecy_rows(&s), &format!("no-menu Enter #{i} leaves no gray trace"));
    }
    let noise = s.noise();
    assert!(noise.is_empty(), "no noise from the accept path: {noise:?}");
    s.close();
    check_fixture_intact("the run");
}

/// A limited `which` for the stub-PATH test: the real binary a symlink should
/// carry into the hermetic PATH.
fn which_bin(name: &str) -> Option<PathBuf> {
    let path = std::env::var("PATH").ok()?;
    std::env::split_paths(&path)
        .map(|dir| dir.join(name))
        .find(|c| c.is_file())
}
