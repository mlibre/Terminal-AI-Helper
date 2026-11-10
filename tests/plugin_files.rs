//! Lines that end in a file, answered from the filesystem rather than history.
//!
//! What you are about to use is by definition not in the history yet, so
//! frequency cannot answer it. These tests drive a real shell with a scratch
//! HOME and check that a line ending in a path sees the newest file there,
//! while a line that does not end in a file is left to the index.
//!
//! Ported from `tests/test_plugins_files.py`.
//!
//!     cargo test --release --test plugin_files

mod support;
use support::*;

use regex::Regex;
use std::fs;
use std::path::Path;
use std::sync::Mutex;
use std::time::{SystemTime, UNIX_EPOCH};

// Tests run in parallel and share /tmp/tai — serialize them.
static LOCK: Mutex<()> = Mutex::new(());

/// The per-root and roots caps, from `fixture_rules.py` (which mirrors
/// `src/fresh.rs`). The pins the three implementations must share.
const PER_ROOT: usize = 8;
const MAX_ROOTS: usize = 8;

fn now_secs() -> i64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap()
        .as_secs() as i64
}

fn chmod(path: &Path, mode: u32) {
    use std::os::unix::fs::PermissionsExt;
    fs::set_permissions(path, fs::Permissions::from_mode(mode)).unwrap();
}

/// Set both mtimes to a whole-second stamp, the way `os.utime` did.
fn set_mtime(path: &Path, secs: i64) {
    use std::fs::FileTimes;
    let f = fs::OpenOptions::new().append(true).open(path).unwrap();
    let t = UNIX_EPOCH + std::time::Duration::from_secs(secs.max(0) as u64);
    f.set_times(FileTimes::new().set_modified(t)).unwrap();
}

/// A download in its own ~/Downloads, and a directory to run from. Ported from
/// `plugin_env.py`'s `setup_files()`.
fn setup_files() {
    let _ = fs::remove_dir_all(file_home());
    let _ = fs::remove_dir_all(file_work());
    fs::create_dir_all(file_downloads()).unwrap();
    fs::create_dir_all(file_work()).unwrap();
    let fresh = file_downloads().join(DOWNLOADED);
    fs::write(&fresh, "#!/bin/sh\necho 'RAN downloaded'\n").unwrap();
    chmod(&fresh, 0o755);
    let old = file_work().join(WORK_OLD);
    fs::write(&old, "notes\n").unwrap();
    let now = now_secs();
    set_mtime(&fresh, now);
    set_mtime(&old, now - 86400);
}

/// The first half of every suggestion reading: ZLE's ghost text, before the
/// menu and the region highlight that ride along in the dump.
fn ghost(s: &mut Session) -> String {
    s.suggestion().split(" PD=").next().unwrap_or("").to_string()
}

/// COMPREPLY for `line`, which is the list readline would show or pick from
/// (bash only).
///
/// `_tai_complete` is called with COMP_LINE set by hand rather than through a
/// key, because readline only fills COMP_LINE for a function it is completing
/// — a `bind -x` command gets an empty one, which is a fact about bash rather
/// than about this plugin.
///
/// The count is written first, so the file is never empty and `wait_file` —
/// which waits on the file existing *with content* — is a real synchronisation
/// point. Without it, "no candidates" could not be told from "the command has
/// not run yet", which is the whole assertion for several of the lines.
fn offered(s: &mut Session, line: &str) -> Vec<String> {
    let reply = reply_path();
    let _ = fs::remove_file(&reply);
    s.run(&format!(
        "COMP_LINE='{line}'; COMP_POINT={}; COMPREPLY=(); _tai_complete; \
         printf \"%s\\n\" \"${{#COMPREPLY[@]}}\" \"${{COMPREPLY[@]}}\" > {}",
        line.len(),
        reply.display()
    ));
    s.wait_file(&reply, &format!("the completion list for {line:?}"));
    let got: Vec<String> = fs::read_to_string(&reply)
        .unwrap_or_default()
        .lines()
        .map(|l| l.to_string())
        .collect();
    match got.first() {
        Some(first) if !first.is_empty() && first.chars().all(|c| c.is_ascii_digit()) => {
            got[1..].to_vec()
        }
        _ => got,
    }
}

#[test]
fn test_zsh_file_arguments() {
    let _guard = LOCK.lock().unwrap_or_else(|e| e.into_inner());
    setup();
    setup_files();
    let home = file_home().to_string_lossy().into_owned();
    let roots = file_downloads().to_string_lossy().into_owned();
    let env = vec![("HOME", home.as_str()), ("TAI_FILE_ROOTS", roots.as_str())];
    let mut s = Session::new_with("zsh", &env, true);
    s.run(&format!("cd {}", file_work().display()));

    // The learned file exists here, so it stays the answer. `script` is written
    // into the working directory by this test for exactly this case, and the hint
    // is read after a fresh send each time: a keystroke does not always redraw,
    // so a hint read twice in a row can be the previous line's.
    let script = file_work().join("script");
    fs::write(&script, "#!/bin/sh\n").unwrap();
    chmod(&script, 0o755);
    s.send("chmod +x ");
    assert_eq!(
        ghost(&mut s),
        "SUG=[script]",
        "a learned file that still exists keeps the hint"
    );

    // Now it is gone — a file the history names, in a directory it is not in —
    // and the file that was actually downloaded is the answer. Written as
    // `~/Downloads/x` rather than an absolute path, so the suggestion is the line
    // a person would type and still works from another directory.
    fs::remove_file(&script).unwrap();
    s.send("chmod +x ");
    assert_eq!(
        ghost(&mut s),
        format!("SUG=[~/Downloads/{DOWNLOADED}]"),
        "and when it is not there, the newest file is"
    );

    // Part of the word narrows it. `chmod +x freeb` is looking for a file whose
    // name starts with `freeb`, and `~/Downloads/freebuff…` does not — the file is
    // *in* Downloads, it is not *called* Downloads. So a path from another root
    // cannot answer a word already being typed, which is the same rule the menu
    // has always applied to a candidate that does not extend the word.
    s.send("chmod +x freeb");
    assert_eq!(
        ghost(&mut s),
        "SUG=[]",
        "a partial word is not answered by a path from elsewhere"
    );
    // Type it the way the file would be reached from this directory, and the same
    // file answers — because now the word really is a prefix of it, and the `~` the
    // user typed is the `~` they get back rather than an expanded path.
    s.send("chmod +x ~/Downloads/freeb");
    assert_eq!(
        ghost(&mut s),
        format!("SUG=[uff-{}]", &DOWNLOADED["freebuff-".len()..]),
        "and does answer it once the word is a prefix"
    );
    // Typing the directory and stopping there is the shape this is really for:
    // the answer has to be built from what was typed in front of the word, not
    // appended after it, or the line comes out as `chmod +x ~/Downloads/f~/Down…`.
    s.send("chmod +x ~/Downloads/");
    assert_eq!(
        ghost(&mut s),
        format!("SUG=[{DOWNLOADED}]"),
        "and the answer continues the line, not the line twice"
    );
    s.write(CTRL_U);

    // Tab cycles the words the filesystem offers — it does not take the hint
    // the way the arrow does. The hint's own key still completes the line.
    s.send("chmod +x ");
    s.write(TAB);
    s.settle();
    let m = s.menu(true);
    assert!(
        m.line.starts_with("chmod +x "),
        "Tab lists the words rather than taking the hint"
    );
    assert!(m.entries > 1, "Tab lists the words rather than taking the hint");
    s.write(CTRL_U);
    s.send("chmod +x ");
    s.write(RIGHT);
    s.settle();
    let m = s.menu(true);
    assert_eq!(
        m.line,
        format!("chmod +x ~/Downloads/{DOWNLOADED}"),
        "the arrow completes it"
    );

    // The list shows the files and what tai learned — but only the learned
    // names that are still files here. `script` was unlinked above: a file slot
    // is a path slot, and a name the history remembers is not offered where
    // taking it would read "No such file or directory". The alive learned file
    // kept its place at the top of this test; this is the other half of the
    // same rule, the one `nano mo` listing a `Modelfile` that was not there
    // was reported about.
    s.send("chmod +x ");
    s.write(LIST);
    s.settle();
    let m = s.menu(false);
    let entries = menu_entries(&m.drawn);
    assert_eq!(
        entries[0],
        format!("~/Downloads/{DOWNLOADED}"),
        "the list puts the real file first"
    );
    assert!(
        !entries.iter().any(|e| e == "script"),
        "and a learned name that is gone is not offered"
    );
    assert_eq!(m.line, "chmod +x ", "leaving the line alone");
    s.write(CTRL_U);

    // Not every line ends in a file, and a line that does not must be untouched.
    // `git ` learns from the history, `ls ` is not a file argument at all, and
    // neither may be answered with whatever is on disk.
    s.send("git ");
    assert_eq!(
        ghost(&mut s),
        "SUG=[pull --rebase]",
        "a line that does not end in a file is unaffected"
    );
    s.send("ls ");
    assert_eq!(
        ghost(&mut s),
        "SUG=[-la]",
        "nor is a line tai has no reason to think is one"
    );

    // `cat` is marked as taking a file, from the fixture's `cat tzz_d` line. The
    // word `tzz_d` matches no file in this directory, so the answer is empty —
    // which is the case that must not become "the newest thing on disk".
    s.send("cat tzz_d");
    assert_eq!(
        ghost(&mut s),
        "SUG=[]",
        "a word that matches no file gets no answer"
    );

    let noise = s.noise();
    assert!(noise.is_empty(), "no noise from the file lookup: {noise:?}");
    s.close();
    check_fixture_intact("the run");
}

#[test]
fn test_bash_file_arguments() {
    let _guard = LOCK.lock().unwrap_or_else(|e| e.into_inner());
    setup();
    setup_files();
    // TAI_COMPLETE_ALL=1 so Tab reaches _tai_complete for `chmod` too. bash binds
    // completion per command name, and out of the box that is only the literal
    // `tai`; without this the Tab assertion below would be reading readline's
    // own filename completion rather than anything tai did.
    let home = file_home().to_string_lossy().into_owned();
    let roots = file_downloads().to_string_lossy().into_owned();
    let env = vec![
        ("HOME", home.as_str()),
        ("TAI_FILE_ROOTS", roots.as_str()),
        ("TAI_COMPLETE_ALL", "1"),
    ];
    let mut s = Session::new_with("bash", &env, true);
    s.run(&format!("cd {}", file_work().display()));

    assert!(
        offered(&mut s, "chmod +x ").contains(&format!("~/Downloads/{DOWNLOADED} ")),
        "the file that was just downloaded is offered"
    );
    assert_eq!(
        offered(&mut s, "chmod +x ")[0],
        format!("~/Downloads/{DOWNLOADED} "),
        "and it is offered first"
    );
    assert!(
        !offered(&mut s, "chmod +x ").iter().any(|c| c == "script "),
        "while a learned name that is gone is not offered"
    );
    assert!(
        offered(&mut s, "chmod +x freeb").is_empty(),
        "a partial word is not answered by a path from elsewhere"
    );
    assert!(
        offered(&mut s, "git p").iter().filter(|c| c.contains('/')).count() == 0,
        "nor is a line that does not end in a file"
    );
    assert!(
        offered(&mut s, "cat tzz_d").iter().filter(|c| c.contains('/')).count() == 0,
        "a word matching no file gets nothing from the filesystem"
    );

    // A learned file that still exists keeps the top place in bash as well,
    // which is what stops this from displacing every absolute path anyone uses.
    let script = file_work().join("script");
    fs::write(&script, "#!/bin/sh\n").unwrap();
    chmod(&script, 0o755);
    assert_eq!(
        offered(&mut s, "chmod +x ")[0],
        "script ",
        "and a learned file that still exists keeps its place"
    );

    // Through a real Tab, so the claim is one a user would recognise. The ambiguous
    // case is the interesting one — several files, so readline draws a listing
    // rather than choosing — and it is read off the terminal because that is the
    // only place a readline listing exists.
    //
    // The line and the key go in one write, and the mark is taken before it.
    // Splitting them — send the line, read the echo, *then* send Tab — puts the
    // mark in the middle of the echo, and how much of the echo has arrived by
    // then is a property of how fast the machine is: on a slow one the listing
    // lands after the mark and the assertion fails for a plugin that is working.
    // It was latent rather than broken, and only showed up once the completion
    // path grew a fork and the timing moved.
    let mark = s.seen_len();
    s.send(&format!("chmod +x {TAB}"));
    s.settle();
    assert!(
        ansi_re().replace_all(&s.raw()[mark..], "").contains(DOWNLOADED),
        "Tab lists the files through readline"
    );
    s.write(CTRL_U);
    s.settle();
    // And the bare partial word is left alone, as in zsh: `freebuff` is not a
    // prefix of `~/Downloads/freebuff…`, so that file is not an answer to it.
    s.send(&format!("chmod +x {}", &DOWNLOADED[..8]));
    s.write(TAB);
    s.settle();
    assert_eq!(
        s.line(),
        format!("chmod +x {}", &DOWNLOADED[..8]),
        "while a bare partial word is not completed from another root"
    );
    s.write(CTRL_U);
    s.settle();

    // And the accept path agrees with it. `_tai_query` is what Ctrl-F and the
    // right arrow use, so a rule that changed in one and not the other is how the
    // two drift apart — and the line it returns is the line that gets written.
    fs::remove_file(&script).unwrap();
    assert_eq!(
        offered(&mut s, "chmod +x ")[0],
        format!("~/Downloads/{DOWNLOADED} "),
        "the arrow key is given the same answer"
    );
    s.send(&format!("chmod +x {CTRL_F}"));
    s.settle();
    assert_eq!(
        s.line(),
        format!("chmod +x ~/Downloads/{DOWNLOADED}"),
        "and accepting writes that line"
    );
    // `git p` completes to `git pull --rebase`, so readline's word is `pull`; the
    // filesystem must contribute nothing to it, and this is the whole claim.
    assert!(
        offered(&mut s, "git p").iter().filter(|c| c.contains('/')).count() == 0,
        "while a line that does not end in a file is untouched by it"
    );

    let noise = s.noise();
    assert!(noise.is_empty(), "no noise from the file lookup: {noise:?}");
    s.close();
    check_fixture_intact("the run");
}

#[test]
fn test_learned_file_words_are_judged() {
    let _guard = LOCK.lock().unwrap_or_else(|e| e.into_inner());
    // A file slot's learned word is judged where it is offered, both shells.
    //
    // Two reported shapes, one rule. `cat .zh` hinted `cat .zhistory` in the
    // directory where the user had just read "No such file or directory" from
    // that exact line — a hidden file is unambiguously a path, the index marks
    // it, and the per-directory judgement has to reach it. `nano mo` listed
    // `Modelfile` beside the file that was actually there — a bare word the
    // shape test passes over, but the line it was recorded with ends in a file,
    // and that meaning is the judgement. The ghost, the menu and the completion
    // ask the same question about the same word, so the three answer together.
    setup();
    for shell in ["zsh", "bash"] {
        setup_files();
        // FILE_WORK holds notes.txt and no `.zshrc`: the fixture's `cat .zshrc`
        // was recorded somewhere it existed, and this is somewhere it does not.
        let home = file_home().to_string_lossy().into_owned();
        let roots = file_downloads().to_string_lossy().into_owned();
        let env = vec![("HOME", home.as_str()), ("TAI_FILE_ROOTS", roots.as_str())];
        let mut s = Session::new_with(shell, &env, true);
        s.run(&format!("cd {}", file_work().display()));

        if shell == "zsh" {
            s.send("cat .zsh");
            assert_eq!(
                ghost(&mut s),
                "SUG=[]",
                "a hidden file that is not here is not hinted"
            );
            // Reading the suggestion abandons the line (the dump widget clears
            // it), so the menu is asked for on a line sent for it.
            s.send("cat .zsh");
            s.write(LIST);
            s.settle();
            let m = s.menu(false);
            assert!(
                !menu_entries(&m.drawn).iter().any(|e| e.contains(".zshrc")),
                "and not listed either"
            );
            s.write(CTRL_U);
            s.send("cat .zsh");
            s.write(RIGHT);
            s.settle();
            assert_eq!(s.line(), "cat .zsh", "and the arrow takes nothing");
        } else {
            assert!(
                !offered(&mut s, "cat .zsh").iter().any(|c| c.contains(".zshrc")),
                "a hidden file that is not here is not offered by Tab"
            );
            s.send(&format!("cat .zsh{CTRL_F}"));
            s.settle();
            assert_eq!(s.line(), "cat .zsh", "and the accept key takes nothing");
        }

        // The other half of the rule, so the fix cannot be "never offer": where
        // the file exists, the same line is offered by every door. A second
        // hidden file makes the menu a list — a lone candidate is committed,
        // which is the one-entry promise, and is asserted right after. Their
        // mtimes are set explicitly, because the accept key answers with the
        // newest file when the learned one is not what the lookup ranks first,
        // and "newest" has to be a fact about the fixture and not about how
        // fast the machine wrote two files in one second.
        let zshenv = file_work().join(".zshenv");
        let zshrc = file_work().join(".zshrc");
        fs::write(&zshenv, "env\n").unwrap();
        fs::write(&zshrc, "history\n").unwrap();
        let now = now_secs();
        set_mtime(&zshenv, now - 60);
        set_mtime(&zshrc, now);
        if shell == "zsh" {
            s.send("cat .zsh");
            assert_eq!(
                ghost(&mut s),
                "SUG=[rc]",
                "where it exists, the hidden file is hinted"
            );
            s.send("cat .zsh");
            s.write(LIST);
            s.settle();
            let m = s.menu(false);
            assert_eq!(
                menu_entries(&m.drawn).iter().filter(|e| *e == ".zshrc").count(),
                1,
                "and listed"
            );
            s.write(CTRL_U);
        } else {
            assert_eq!(
                offered(&mut s, "cat .zsh")
                    .iter()
                    .filter(|c| c.starts_with(".zshrc"))
                    .map(|c| c.as_str())
                    .collect::<Vec<_>>(),
                vec![".zshrc "],
                "where it exists, Tab offers it"
            );
            s.send(&format!("cat .zsh{CTRL_F}"));
            s.settle();
            assert_eq!(s.line(), "cat .zshrc", "and the accept key writes it");
        }

        let noise = s.noise();
        assert!(
            noise.is_empty(),
            "no noise from the file-word judgement: {noise:?}"
        );
        s.close();
    }
    check_fixture_intact("the run");
}

#[test]
fn test_quoted_names() {
    let _guard = LOCK.lock().unwrap_or_else(|e| e.into_inner());
    // A name the shell has to be told about is written so the shell agrees.
    //
    // The reported case is not exotic: `cat My Document.pdf` completed to the
    // right *name* and the wrong *line*, because the name went in bare and the
    // shell then read two arguments. It looks like it worked — there is a
    // suggestion, the suggestion is the file, and the failure only appears when the
    // command runs.
    //
    // Both shells, and every key that writes: for bash that is Tab and the arrow,
    // and for zsh the arrow (Tab cycles its list instead of writing). bash quotes
    // through readline on the Tab path and has to do it itself on the other, which
    // is how the two disagreed about the same line.
    setup();
    for shell in ["zsh", "bash"] {
        setup_files();
        let mut s = Session::new(shell);
        s.run(&format!("cd {}", file_work().display()));
        fs::write(file_work().join("two words.txt"), "x").unwrap();
        // Ctrl-Space lists rather than writing in bash — readline has no menu, so
        // the line is left alone — so it is only asserted for the shell that has
        // one. Every key that *does* write has to write a name the shell can pass.
        let mut keys: Vec<(&str, &str)> = vec![(RIGHT, "the arrow")];
        if shell == "bash" {
            keys.push((TAB, "Tab"));
        }
        if shell == "zsh" {
            keys.push((CTRL_SPACE, "Ctrl-Space"));
        }
        for (key, label) in &keys {
            s.send("cat two");
            s.write(key);
            assert_eq!(
                s.line().trim_end(),
                "cat two\\ words.txt",
                "{shell} {label} writes a name a shell passes as one word"
            );
        }
        // For zsh there is a second case at the prompt line: Tab offers exactly
        // one file, and offering exactly one is taking it.
        if shell == "zsh" {
            s.send("cat two");
            s.write(TAB);
            assert_eq!(
                s.line().trim_end(),
                "cat two\\ words.txt",
                "zsh Tab takes a lone completion too"
            );
        }
        // And it is the file that is there, which is the only reason to care.
        fs::write(file_work().join("quote'name.txt"), "x").unwrap();
        s.send("cat quote");
        s.write(RIGHT);
        assert_eq!(
            s.line().trim_end(),
            "cat quote\\'name.txt",
            "{shell} quotes an apostrophe too"
        );
        s.close();
    }
    check_fixture_intact("the run");
}

/// The number a shell plugin holds for `_TAI_<name>`, read from its source.
fn plugin_limits(path: &Path, name: &str) -> usize {
    let text = fs::read_to_string(path).unwrap();
    let re = Regex::new(&format!(r"_TAI_{name}(?::=|=)(\d+)")).unwrap();
    let found = re
        .captures(&text)
        .unwrap_or_else(|| panic!("{} does not define _TAI_{name}", path.display()));
    found[1].parse().unwrap()
}

#[test]
fn test_the_three_implementations_hold_the_same_limits() {
    let _guard = LOCK.lock().unwrap_or_else(|e| e.into_inner());
    // `tai/fresh.py`, `files.zsh` and `files.bash` agree on the two limits.
    //
    // Three implementations of one rule is already too many; what makes it
    // survivable is that they are pinned to the same numbers, and the pins are
    // read out of the sources rather than restated here — a restatement is a
    // fourth copy, and a fourth copy is how the cap became alphabetical in all
    // three at once and stayed alphabetical in all three afterwards.
    setup();
    for part in ["plugins/zsh/index.zsh", "plugins/bash/files.bash"] {
        let path = Path::new(REPO).join(part);
        assert_eq!(
            plugin_limits(&path, "FILE_PER_ROOT"),
            PER_ROOT,
            "{} reads at most PER_ROOT files from one root",
            path.display()
        );
        assert_eq!(
            plugin_limits(&path, "FILE_ROOTS_MAX"),
            MAX_ROOTS,
            "{} reads at most MAX_ROOTS roots",
            path.display()
        );
    }
    check_fixture_intact("the run");
}

#[test]
fn test_the_cap_is_on_recency_in_both_shells() {
    let _guard = LOCK.lock().unwrap_or_else(|e| e.into_inner());
    // The eight offered are the eight *newest*, in zsh and in bash alike.
    //
    // The Python side has always asserted this — `sorted(glob(...))[:PER_ROOT]` is
    // alphabetical, so a downloads folder of more than eight files kept the
    // alphabetically first and dropped the file that had just arrived, which is the
    // one thing this feature exists to offer, and all three implementations did it
    // because all three globbed. Only the Python side was pinned, though: a shell
    // that lost its `(om)` or its `--zero -N` sort would have failed nothing here,
    // since every other assertion in this file looks at the newest file, which is
    // first under either ordering.
    //
    // So the name that decides it is `zzz…`: alphabetically last, newest by mtime.
    // Eight files are not enough — the cap has to bite — and each is written with
    // its own timestamp so recency is a fact and not an accident of the order the
    // test happened to create them in.
    setup();
    setup_files();
    let home = file_home().to_string_lossy().into_owned();
    let roots = file_downloads().to_string_lossy().into_owned();
    let env = vec![("HOME", home.as_str()), ("TAI_FILE_ROOTS", roots.as_str())];
    let mut names: Vec<String> =
        (0..10).map(|i| format!("cap-{i:02}-file.txt")).collect();
    names[PER_ROOT] = "zzz-just-arrived.txt".to_string(); // newest, and last by name
    let base = now_secs();
    for (i, name) in names.iter().enumerate() {
        let path = file_downloads().join(name);
        fs::write(&path, "x").unwrap();
        // Ascending with i, and the replacement written last, so `zzz…` is both
        // the newest and — by name — the one a sort would drop.
        set_mtime(&path, base + i as i64 * 60);
    }
    set_mtime(&file_downloads().join("zzz-just-arrived.txt"), base + 3600);
    for shell in ["zsh", "bash"] {
        // Two sessions: taking the answer writes to the line, and the list is
        // asked for on a line that still ends in a word.
        let mut s = Session::new_with(shell, &env, true);
        s.run(&format!("cd {}", file_work().display()));
        s.send("chmod +x ");
        let got = if shell == "zsh" {
            ghost(&mut s)
        } else {
            s.write(CTRL_F);
            s.settle();
            let line = s.line();
            format!("SUG=[{}]", line.get("chmod +x ".len()..).unwrap_or(""))
        };
        assert_eq!(
            got,
            "SUG=[~/Downloads/zzz-just-arrived.txt]",
            "{shell} offers the newest file, not the first by name"
        );
        let noise = s.noise();
        assert!(noise.is_empty(), "{shell} no noise: {noise:?}");
        s.close();

        let mut listed = Session::new_with(shell, &env, true);
        listed.run(&format!("cd {}", file_work().display()));
        // A word that matches several files *in the root being read*, so the
        // cap is what decides the list: the oldest is `cap-00` and it is
        // dropped, `cap-09` is the newest of them and it is not.
        if shell == "zsh" {
            listed.send("chmod +x ~/Downloads/cap-");
            listed.write(CTRL_SPACE);
            listed.settle();
            let shown = menu_entries(&listed.menu(false).drawn);
            let offered: Vec<String> =
                shown.into_iter().filter(|e| e.contains("cap-")).collect();
            assert_eq!(offered.len(), PER_ROOT, "zsh lists the capped files");
            assert_eq!(
                offered.first().map(|s| s.as_str()).unwrap_or(""),
                "cap-09-file.txt",
                "zsh and the newest is first"
            );
        } else {
            // bash: ask `_tai_complete` for COMPREPLY with COMP_LINE set by
            // hand, the way the existing test does — the cap lives in that
            // reply, before readline ever draws anything.
            let reply = Path::new("/tmp/tai/tai_cap_reply.txt").to_path_buf();
            let _ = fs::remove_file(&reply);
            listed.run(&format!(
                "COMP_LINE='chmod +x ~/Downloads/cap-'; \
                 COMP_POINT=${{#COMP_LINE}}; COMPREPLY=(); _tai_complete; \
                 printf '%s\\n' \"${{COMPREPLY[@]}}\" > {}",
                reply.display()
            ));
            listed.wait_file(&reply, "the capped reply list");
            let replies: Vec<String> = fs::read_to_string(&reply)
                .unwrap()
                .lines()
                .map(|l| l.to_string())
                .collect();
            assert_eq!(replies.len(), PER_ROOT, "bash offers the cap's worth");
            assert_eq!(
                replies.first().map(|s| s.as_str()).unwrap_or(""),
                "~/Downloads/cap-09-file.txt ",
                "bash and the newest of them is first"
            );
            assert!(
                !replies.iter().any(|r| r.trim_end().ends_with("cap-00-file.txt")),
                "bash and the oldest is dropped"
            );
        }
        let noise = listed.noise();
        assert!(noise.is_empty(), "{shell} no noise: {noise:?}");
        listed.close();
    }
    for name in &names {
        let _ = fs::remove_file(file_downloads().join(name));
    }
    check_fixture_intact("the run");
}
