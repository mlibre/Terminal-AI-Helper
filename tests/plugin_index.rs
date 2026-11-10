//! The index lifecycle: a rebuild the running shell picks up, and recording.
//!
//! Ported from `tests/test_plugins_index.py`. The index is written by a process
//! the shell does not wait for, so these are the tests for the two moments
//! where that shows up — a shell that was already open when the index changed,
//! and a command that has not been recorded yet.

mod support;
use support::*;

use std::path::PathBuf;
use std::time::Duration;

/// The tests share /tmp/tai, so they run one at a time.
static LOCK: std::sync::Mutex<()> = std::sync::Mutex::new(());

fn have(shell: &str) -> bool {
    shell_path(shell).is_some()
}

/// The suggestion line's `SUG=[…]` half — everything before the styled region,
/// which is terminal-dependent.
fn sug_pd(s: &mut Session) -> String {
    s.suggestion().split(" PD=").next().unwrap_or("").to_string()
}

/// The stamp file a plugin compares the index against: the index path with
/// `.stamp` appended (not `with_suffix`, which would replace `.zsh`).
fn stamp_of(index: &std::path::Path) -> PathBuf {
    PathBuf::from(format!("{}.stamp", index.display()))
}

#[test]
fn live_reload() {
    let _guard = LOCK.lock().unwrap_or_else(|e| e.into_inner());
    setup();
    if !have("zsh") {
        return;
    }
    let base: Vec<&str> = DEFAULT_COMMANDS.to_vec();
    write_index(Some(&base), None);
    // A shell that is already running when the index changes underneath it.
    let with_new: Vec<&str> =
        std::iter::once("docker ps -a").chain(base.iter().copied()).collect();
    write_index(Some(&with_new), None);
    let mut s = Session::new("zsh");
    s.send("docker");
    assert_eq!(sug_pd(&mut s), "SUG=[ ps -a]", "a new shell sees the new index");
    s.close();

    write_index(Some(&base), None);
    let mut s = Session::new("zsh");
    s.send("docker");
    assert_eq!(sug_pd(&mut s), "SUG=[ ps]", "before the rebuild");
    let stamp = stamp_of(&zsh_index());
    let with_compose: Vec<&str> =
        std::iter::once("docker compose up -d").chain(base.iter().copied()).collect();
    write_index(Some(&with_compose), Some(&stamp));
    s.send("docker");
    assert_eq!(sug_pd(&mut s), "SUG=[ ps]", "still the old index, no prompt yet");
    s.send("\n"); // run an empty line, get a fresh prompt
    s.send("docker");
    assert_eq!(
        sug_pd(&mut s),
        "SUG=[ compose up -d]",
        "the same shell sees it after one prompt"
    );
    let noise = s.noise();
    assert!(noise.is_empty(), "reload is clean: {noise:?}");

    // The other half, and the one that was broken: a rebuild that *removes* a
    // command. Re-sourcing overwrote the value of a key the new index still had,
    // but a key the new index no longer mentions was simply left where it was — so
    // `tai purge --stale` dropped a suggestion from the store, the next prompt
    // re-read the file, and the stale entry stayed installed for the life of the
    // shell. A shell that can only learn and never forget is not reading the file,
    // it is accumulating it.
    //
    // `git status` would not catch it: `git` is still in the reduced index, so its
    // key is overwritten and the lookup lands on the surviving candidate. What has
    // to go is a command whose *own* word key disappears with it — `docker ps`,
    // which nothing else in the fixture shares a key with and which no glob on
    // this machine can answer.
    let no_docker: Vec<&str> =
        base.iter().copied().filter(|c| !c.starts_with("docker ")).collect();
    write_index(Some(&no_docker), Some(&stamp));
    s.send("\n");
    s.send("docker p");
    assert_eq!(
        sug_pd(&mut s),
        "SUG=[]",
        "and it forgets what the rebuild removed"
    );
    s.close();
    write_index(None, None);

    // The same rule for a cached answer. The loose glimpse for `gst` is cached,
    // because a redraw without a keystroke must not re-scan every learned line —
    // so the cache has to be keyed on the index as well as on the prefix. A cache
    // that forgets keeps offering `git status` for a typo long after `git status`
    // was purged from the store: a suggestion the store no longer holds and the
    // file on disk no longer mentions.
    let mut s = Session::new("zsh");
    s.send("gst");
    let m = s.menu(false);
    assert_eq!(m.drawn, "\\n  git status", "a typo reaches the learned line");
    assert_eq!(m.entries, 1, "a typo reaches the learned line: N");
    assert_eq!(m.idx, 1, "a typo reaches the learned line: IDX");
    let no_git_status: Vec<&str> =
        base.iter().copied().filter(|c| *c != "git status").collect();
    write_index(Some(&no_git_status), Some(&stamp));
    s.send("\n"); // a fresh prompt is what reads the new file
    s.send("gst");
    let m = s.menu(false);
    assert_eq!(m.drawn, "", "and a rebuild retires the cached answer");
    assert_eq!(m.entries, 0, "and a rebuild retires the cached answer: N");
    assert_eq!(m.idx, 0, "and a rebuild retires the cached answer: IDX");
    s.close();
    write_index(None, None);
    check_fixture_intact("the run");
}

#[test]
fn live_reload_bash() {
    let _guard = LOCK.lock().unwrap_or_else(|e| e.into_inner());
    setup();
    if !have("bash") {
        return;
    }
    // The live-reload rule in bash — "keep a case in both suites".
    //
    // The zsh half above is the one the rule was born from; this is the same
    // question asked of the other implementation, because a rule that works in
    // one shell and not the other is how a split starts. Bash delivers its hint
    // by rewriting the readline buffer on the accept key, so each answer here is
    // read by pressing Ctrl-F and reading the line: an empty hint accepts
    // nothing, and a stale one accepts the stale line.
    let base: Vec<&str> = DEFAULT_COMMANDS.to_vec();
    let with_new: Vec<&str> =
        std::iter::once("docker ps -a").chain(base.iter().copied()).collect();
    write_index(Some(&with_new), None);
    let mut s = Session::new("bash");
    s.send("docker");
    s.write(CTRL_F);
    s.settle();
    assert_eq!(s.line(), "docker ps -a", "a new shell sees the new index");
    s.close();

    write_index(Some(&base), None);
    let mut s = Session::new("bash");
    s.send("docker");
    s.write(CTRL_F);
    s.settle();
    assert_eq!(s.line(), "docker ps", "before the rebuild");
    let stamp = stamp_of(&bash_index());
    let with_compose: Vec<&str> =
        std::iter::once("docker compose up -d").chain(base.iter().copied()).collect();
    write_index(Some(&with_compose), Some(&stamp));
    s.send("docker");
    s.write(CTRL_F);
    s.settle();
    assert_eq!(s.line(), "docker ps", "still the old index, no prompt yet");
    s.send("\n"); // run an empty line, get a fresh prompt
    s.send("docker");
    s.write(CTRL_F);
    s.settle();
    assert_eq!(
        s.line(),
        "docker compose up -d",
        "the same shell sees it after one prompt"
    );
    let noise = s.noise();
    assert!(noise.is_empty(), "reload is clean: {noise:?}");

    // The forgetting half: a rebuild that removes a command must remove it
    // from an open shell too. `docker ps` is the command whose own word key
    // disappears with the rebuild — nothing else in the fixture shares it.
    let no_docker: Vec<&str> =
        base.iter().copied().filter(|c| !c.starts_with("docker ")).collect();
    write_index(Some(&no_docker), Some(&stamp));
    s.send("\n");
    s.send("docker p");
    s.write(CTRL_F);
    s.settle();
    assert_eq!(
        s.line(),
        "docker p",
        "and it forgets what the rebuild removed"
    );
    s.close();
    write_index(None, None);
    check_fixture_intact("the bash run");
}

#[test]
fn recording() {
    let _guard = LOCK.lock().unwrap_or_else(|e| e.into_inner());
    setup();
    let typed = ["echo alpha-one", "echo beta-two"];
    for shell in ["bash", "zsh"] {
        if !have(shell) {
            continue;
        }
        for disabled in [false, true] {
            let tag = if disabled { "off" } else { "on" };
            // A database of its own per iteration, named from RECORD_DB above. The
            // disabled case asserts the *absence* of a write and the writes are
            // detached, so a shared file made it a question about the previous
            // session: its record processes were still in flight when this
            // iteration deleted the file, and a straggler landed in the fresh one.
            // A path no other shell was ever given is what makes "wrote nothing"
            // mean what it says.
            let db = record_db_path()
                .with_file_name(format!("tai_plugins_record-{shell}-{tag}.db"));
            // The spool, named just as singly: it is the shells' write side now,
            // and "wrote nothing" has to mean the spool never grew either.
            let spool = db
                .with_file_name(format!("tai_plugins_record-{shell}-{tag}-spool.db"));
            for suffix in ["", "-wal", "-shm"] {
                let mut p = db.clone().into_os_string();
                p.push(suffix);
                let _ = std::fs::remove_file(&p);
            }
            let _ = std::fs::remove_file(&spool);
            // The only test that records, so it also gets a throwaway index path:
            // a stored command can trigger the 100-command maintenance rebuild in
            // a detached process, and that process writes TAI_INDEX.
            // TAI_SPOOL_MAX=1 asks for the flush after every command — the same
            // one-record-at-a-time cadence the tests can wait on; the product's
            // default batches eight.
            let rec_idx = record_index();
            let env: Vec<(&str, &str)> = vec![
                (
                    "TAI_NO_AUTO_RECORD",
                    if disabled { "1" } else { "0" },
                ),
                ("TAI_INDEX", rec_idx.to_str().unwrap()),
                ("TAI_DB", db.to_str().unwrap()),
                ("TAI_SPOOL", spool.to_str().unwrap()),
                ("TAI_SPOOL_MAX", "1"),
                ("TAI_SPOOL_SECONDS", "1"),
            ];
            let mut s = Session::new_with(shell, &env, true);
            for cmd in typed {
                s.send(&format!("{cmd}\n")); // newline submits, so it really runs
            }
            // Recording runs detached, so the rows land some time after the
            // prompt returns — and the detached process is a child of this
            // session's terminal. Waiting for the rows while the session is
            // still open is what makes the assertion about recording and not
            // about the race between a flush startup and the pty going away:
            // close() tears down the master, and the kernel's SIGHUP to the
            // session's group is then free to kill a flush that has not
            // exec'd yet. A session that exits NORMALLY never sends that
            // SIGHUP, so the product never sees this race — only this
            // harness could lose it.
            if disabled {
                s.close();
                // Recording is off, so nothing will ever land: the absence
                // is checked after the session is gone, on the final state.
                let got = rows(&db);
                assert!(got.is_empty(), "{shell} writes nothing when disabled: {got:?}");
                assert!(!spool.exists(), "{shell} spools nothing when disabled");
            } else {
                let missing = await_rows(&db, &typed, Duration::from_secs(45));
                s.close();
                assert!(
                    missing.is_empty(),
                    "{shell} records when enabled: missing {missing:?}"
                );
                // Every record went through the spool, and every flush drains
                // it whole: a spool still holding frames at this point means a
                // record bypassed the gate or a flush lost one.
                let drained = !spool.exists()
                    || std::fs::read_to_string(&spool)
                        .unwrap_or_default()
                        .trim()
                        .is_empty();
                assert!(drained, "{shell} drained its spool");
            }
        }
    }
    check_fixture_intact("the recording test");
}

#[test]
fn exit_code_survives_earlier_hooks() {
    let _guard = LOCK.lock().unwrap_or_else(|e| e.into_inner());
    setup();
    // The recorded exit status is the command's, even with hooks before tai's.
    //
    // The report behind this one: `opencoe` — a typo — sat at the top of the
    // dashboard panel, and `openc` answered nothing in the shell. Both grew out
    // of the store: rows for refused words had been recorded as successes, the
    // phantom rule (a line whose every run was 127) could never fire, and the
    // typos ranked like real commands. Whatever put the wrong status in those
    // rows — a shell config that answers for not-found commands with its own
    // exit, an install old enough to predate one of the record paths — the
    // recording path's own invariant is what this test pins: the status that
    // lands in the store is the one the shell handed the prompt, even when a
    // prompt hook registered before tai's runs first. zsh hands every precmd
    // hook the command's own status and tai takes the first slot anyway; bash
    // flows $? from one PROMPT_COMMAND segment into the next and tai has
    // always prepended itself there.
    let typed = "tai_no_such_command_xyz";
    for shell in ["bash", "zsh"] {
        if !have(shell) {
            continue;
        }
        let db =
            record_db_path().with_file_name(format!("tai_plugins_record-{shell}-exit.db"));
        let spool =
            db.with_file_name(format!("tai_plugins_record-{shell}-exit-spool.db"));
        for suffix in ["", "-wal", "-shm"] {
            let mut p = db.clone().into_os_string();
            p.push(suffix);
            let _ = std::fs::remove_file(&p);
        }
        let _ = std::fs::remove_file(&spool);
        let rec_idx = record_index();
        let env: Vec<(&str, &str)> = vec![
            ("TAI_NO_AUTO_RECORD", "0"),
            ("TAI_INDEX", rec_idx.to_str().unwrap()),
            ("TAI_DB", db.to_str().unwrap()),
            ("TAI_SPOOL", spool.to_str().unwrap()),
            ("TAI_SPOOL_MAX", "1"),
            ("TAI_SPOOL_SECONDS", "1"),
        ];
        // source=False: the plugin is sourced from inside the session, AFTER
        // the stand-in theme has registered its hook — the order every real
        // setup has, and the one that decides which hook reads $? first. The
        // theme's body runs `true`, so the $? it leaves behind is never the
        // command's; only a hook that runs before it can see the 127.
        let mut s = Session::new_with(shell, &env, false);
        if shell == "zsh" {
            s.run(&format!(
                "_tai_theme_hook() {{ true; }}; \
                 precmd_functions=( _tai_theme_hook ); source {}",
                plugin_path("zsh").display()
            ));
        } else {
            // bash: tai is already first in PROMPT_COMMAND by its own doing,
            // and the theme appends — which is what a theme sourced later does.
            s.run(&format!(
                "_tai_theme_hook() {{ true; }}; source {}; \
                 PROMPT_COMMAND=\"${{PROMPT_COMMAND:-}};_tai_theme_hook\"",
                plugin_path("bash").display()
            ));
        }
        s.send(&format!("{typed}\n")); // newline submits: the shell refuses the word, 127
        let missing = await_rows(&db, &[typed], Duration::from_secs(45));
        s.close();
        assert!(
            missing.is_empty(),
            "{shell} records the refused command: missing {missing:?}"
        );
        let con = rusqlite::Connection::open(&db).unwrap();
        let mut stmt = con
            .prepare("select exit_code from commands where cmd=?1")
            .unwrap();
        let codes: Vec<i64> = stmt
            .query_map([typed], |r| r.get::<_, i64>(0))
            .unwrap()
            .filter_map(|r| r.ok())
            .collect();
        drop(stmt);
        drop(con);
        assert_eq!(
            codes,
            vec![127],
            "{shell} recorded the command's 127, not the theme's 0"
        );
    }
    check_fixture_intact("the exit-code test");
}
