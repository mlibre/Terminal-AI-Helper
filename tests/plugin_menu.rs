//! The Tab menu: what it lists, what Enter takes, and when it opens.
//!
//! Ported from `tests/test_plugins_menu.py`. Menu behaviour is the part of the
//! plugin most likely to be *nearly* right — the entries are correct, the line
//! advances only by what every entry shares, and the selection is never drawn —
//! so these tests assert on an emulated screen rather than on the plugin's
//! arrays.
//!
//! One structural adaptation: Python reached `s._dump(clear=False)` directly to
//! read `region_highlight` ("standout") and the whole `MENU=[…] N=[…] IDX=[…]`
//! line for the loose-menu tests. The Rust `Session` keeps `dump` private, so
//! this file binds its own dump widget per zsh session (`bind_raw_dump`) that
//! writes the same three lines — buffer, SUG/PD/RH, MENU/N/IDX — to a
//! test-owned path, and `raw_dump` reads it the same way.

mod support;
use support::*;
use support::screen::COLS;

use std::fs;
use std::path::PathBuf;
use std::process::Command;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::time::{Duration, Instant};

// Tests in one binary share /tmp/tai, so they run one at a time.
static LOCK: std::sync::Mutex<()> = std::sync::Mutex::new(());
static RAW_N: AtomicUsize = AtomicUsize::new(0);

fn own(list: &[&str]) -> Vec<String> {
    list.iter().map(|s| s.to_string()).collect()
}

/// The index quoting, as `_zq` in plugin_env.py. The fixture values used here
/// never hold a single quote.
fn zq(value: &str) -> String {
    assert!(!value.contains('\''), "test fixture cannot quote {value:?}");
    format!("'{value}'")
}

/// Bind a raw dump widget — the support harness's `tai_test_dump`, aimed at a
/// path this test owns and a key the tests never otherwise press (^W).
/// Returns the path the widget writes.
fn bind_raw_dump(s: &mut Session) -> PathBuf {
    let n = RAW_N.fetch_add(1, Ordering::SeqCst);
    let path = PathBuf::from(format!("/tmp/tai/tai_raw_{}_{n}.txt", std::process::id()));
    let p = path.display();
    s.run(&format!(
        "tai_raw_dump() {{ print -r -- \"$BUFFER\" > {p}; \
         print -r -- \"SUG=[$_TAI_SUGGESTION] PD=[${{(V)POSTDISPLAY}}] \
         RH=[${{(j: :)region_highlight}}]\" >> {p}; \
         print -r -- \"MENU=[${{(V)POSTDISPLAY}}] N=[${{#_TAI_MENU}}] \
         IDX=[$_TAI_MENU_IDX]\" >> {p}; }}; \
         zle -N tai_raw_dump; bindkey '^W' tai_raw_dump"
    ));
    path
}

/// The three dump lines, read the way `s._dump(clear=False)` did.
fn raw_dump(s: &mut Session, path: &std::path::Path, clear: bool) -> Vec<String> {
    let _ = fs::remove_file(path);
    s.write("\x17"); // the bound tai_raw_dump key
    s.wait_file(path, "the raw dump");
    if clear {
        s.write(CTRL_C); // zsh: the widget only copies, so abandon the line
    }
    s.settle();
    let text = fs::read_to_string(path).unwrap_or_default();
    let _ = fs::remove_file(path);
    text.trim_end_matches('\n').split('\n').map(|l| l.to_string()).collect()
}

fn dump_line(got: &[String], i: usize) -> String {
    got.get(i).cloned().unwrap_or_default()
}

/// The ghost hint, the way the Python file read it: `suggestion()` up to PD=.
fn hint(s: &mut Session) -> String {
    s.suggestion().split(" PD=").next().unwrap_or("").to_string()
}

/// Make the fixture scripts executable.
///
/// `plugin_env.setup()` chmods every fixture script 0o755, and the menu's
/// behaviour depends on those bits: a PATH name is offered only when it can be
/// run (`[[ -x ]]` in _tai_menu_open), readline colours an executable in the
/// listing, and Enter proves which entry it took by running the line. The
/// harness's `write_script` records the mode it was given, but the bits have
/// been seen not to stick under this runner — so they are re-asserted here,
/// after `setup()`, exactly as the Python fixture writes them.
fn make_fixture_runnable() {
    use std::os::unix::fs::PermissionsExt;
    let runnable: Vec<PathBuf> = ["tzz_a", "tzz_b", "tzz_c", "tzz_solo"]
        .iter()
        .map(|n| menu_dir().join(n))
        .chain(["tzz_other", MENU_FAR].iter().map(|n| menu_bin().join(n)))
        .collect();
    for path in runnable {
        // This runner's filesystem has been seen to drop a freshly chmod'ed
        // mode bit, so the mode is re-asserted — and the metadata read, which
        // has been seen to fail transiently, retries before giving up.
        let mut perm = None;
        for _ in 0..50 {
            match fs::metadata(&path) {
                Ok(m) => {
                    perm = Some(m.permissions());
                    break;
                }
                Err(_) => std::thread::sleep(Duration::from_millis(100)),
            }
        }
        let mut perm = perm
            .unwrap_or_else(|| panic!("fixture script {} missing", path.display()));
        perm.set_mode(0o755);
        fs::set_permissions(&path, perm).unwrap();
    }
}

// ---------------------------------------------------------------------------

/// bash cannot draw a list below the line; it can list without choosing.
///
/// readline has no menu of its own. What readline does have is
/// `show-all-if-ambiguous`, and that is the half that matters: the first Tab
/// shows what matches, and Tab still completes the moment the word is no
/// longer ambiguous. Both halves are asserted, because a setting that made Tab
/// only ever list would be worse than no setting at all.
#[test]
fn bash_menu() {
    let _guard = LOCK.lock().unwrap_or_else(|e| e.into_inner());
    setup();
    make_fixture_runnable();

    let mut s = Session::new("bash");
    s.run(&format!("cd {}", menu_dir().display()));
    s.send("cat tzz");
    let mark = s.seen_len();
    s.write(TAB);
    s.settle();
    assert!(s.raw()[mark..].contains("tzz_a"), "the first Tab lists the matches");
    let line = s.line();
    // readline also inserts the longest common prefix of the matches — once
    // the one thing it did that a zsh listing did not. What must not happen is
    // a whole match being chosen — the line being rewritten before the list
    // has even been read — and a common prefix is not that.
    let chosen: Vec<&str> = MENU_ENTRIES.iter().copied().filter(|n| line.contains(n)).collect();
    assert!(chosen.is_empty(), "and no match is chosen");
    assert!(s.noise().is_empty(), "no noise from the listing");
    s.send(CTRL_U);
    s.send("cat tzz_s");
    s.write(TAB);
    s.settle();
    assert_eq!(s.line(), "cat tzz_solo ", "Tab still completes a unique word");
    s.close();
    assert_eq!(tabs_to_list(&[]), 1, "the first Tab lists");
    assert_eq!(
        tabs_to_list(&[("TAI_NO_MENU", "1")]),
        3,
        "TAI_NO_MENU leaves readline to list in its own time"
    );

    // The listing is coloured from the user's LS_COLORS: a directory's name in
    // the directory colour, its trailing slash left in the default one. It is
    // read off an emulated screen because that is the only place the colour
    // exists — the plugin sets a readline variable and readline does the
    // drawing. LS_COLORS is set here rather than left to the environment
    // because a shell started with --norc has no colour scheme; with nothing
    // to paint, readline emits nothing, which is the same as a user who has
    // set no colours.
    let mut s = Session::new("bash");
    s.run(&format!("cd {}", menu_dir().display()));
    s.run("LS_COLORS='di=01;34:*.txt=01;32'; export LS_COLORS");
    s.send("ls tzz");
    let mark = s.seen_len();
    s.write(TAB);
    s.settle();
    let mut scr = Screen::new();
    scr.feed(&s.raw()[mark..]);
    assert_eq!(
        style_of(&scr, "tzz_dir"),
        Ok((34, 0, true, false)),
        "bash colours a directory's name from LS_COLORS"
    );
    // The slash stays in the default colour. The run it lands in can be the
    // slash alone or the slash merged with the plain text that follows it —
    // that is readline's per-version padding — but whichever run carries it is
    // a plain run, and the directory's blue run ends before the slash.
    {
        let blue_run = scr
            .styled_rows()
            .into_iter()
            .find(|(text, attr)| *attr == (34, 0, true, false) && text.contains("tzz_dir"));
        assert!(
            blue_run.is_some(),
            "bash colours a directory's name from LS_COLORS"
        );
        assert!(
            !blue_run.unwrap().0.contains('/'),
            "and leaves the trailing slash alone"
        );
        assert!(
            scr.styled_rows()
                .into_iter()
                .any(|(text, attr)| attr == (0, 0, false, false) && text.contains('/')),
            "and the slash itself is drawn plain"
        );
    }
    assert!(s.noise().is_empty(), "no noise from the coloured listing");
    s.close();

    // A directory argument is answered by directories and by nothing else: the
    // shell's own `compgen -d` list, learned words tested for liveness, and no
    // file anywhere in it — `cd tzz_a` is an error the user would have to
    // notice and retype. TAI_COMPLETE_ALL=1 is what puts tai on the default
    // completion, which is where a `cd ` Tab lands.
    let mut s = Session::new_with("bash", &[("TAI_COMPLETE_ALL", "1")], true);
    s.run(&format!("cd {}", menu_dir().display()));
    s.send("cd ");
    let mark = s.seen_len();
    s.write(TAB);
    s.settle();
    let raw = s.raw()[mark..].to_string();
    assert!(raw.contains("stemroot/"), "bash offers the directories after cd");
    assert!(raw.contains("tzz_dir"), "and the learned destination");
    assert!(
        !raw.contains("tzz_a") && !raw.contains("tzz_solo"),
        "but no file, however fresh"
    );
    assert!(s.noise().is_empty(), "no noise from the cd listing");
    s.close();
    check_fixture_intact("the run");
}

/// Tab lists the completions, Tab moves the mark, Enter takes one.
///
/// The rule is that the line moves only by what every entry shares: the press
/// that opens the list first writes the head all candidates start with, and
/// after that the line stands while the selection moves — the opposite of
/// zsh's own menu completion, which rewrites the line with the entry each Tab
/// lands on, leaving nothing to choose between.
#[test]
fn zsh_menu() {
    let _guard = LOCK.lock().unwrap_or_else(|e| e.into_inner());
    setup();
    make_fixture_runnable();

    let mut s = Session::new("zsh");
    s.run(&format!("cd {}", menu_dir().display()));
    // The names are on PATH so the chosen one can actually be run by Enter,
    // which is the only way a test can see which entry was taken: the line is
    // gone by then. The same names then arrive from the command list as well
    // as from the directory, and have to appear once. The second directory on
    // PATH is here for the names in it that cannot be run.
    s.run(&format!(
        "export PATH={}:{}:$PATH",
        menu_dir().display(),
        menu_bin().display()
    ));

    s.send("tzz");
    s.write(TAB);
    s.settle();
    let m = s.menu(false);
    assert_eq!(menu_entries(&m.drawn), own(MENU_ENTRIES), "Tab lists every match");
    assert!(
        menu_entries(&m.drawn).iter().any(|e| e == "tzz_dir/"),
        "a directory is listed with its slash"
    );
    let unrunnable: Vec<&str> = MENU_UNRUNNABLE
        .iter()
        .copied()
        .filter(|n| menu_entries(&m.drawn).iter().any(|e| e == n))
        .collect();
    assert!(unrunnable.is_empty(), "a name on PATH that cannot be run is not offered");
    // The head every entry shares — `tzz_` — is in the line before the list
    // draws, the same thing readline writes on this key (see bash_menu).
    assert_eq!(m.line, "tzz_", "Tab writes the head every entry shares");
    assert_eq!(
        selected_entry(&screen_of(&s)),
        MENU_ENTRIES[0],
        "the first entry is selected"
    );

    for (i, want) in MENU_ENTRIES[1..].iter().enumerate() {
        let step = i + 2;
        s.write(TAB);
        s.settle();
        let m = s.menu(false);
        assert_eq!(
            selected_entry(&screen_of(&s)),
            *want,
            "Tab {step} selects the next entry"
        );
        assert_eq!(m.line, "tzz_", "Tab {step} still leaves the line at the head");
        assert_eq!(m.entries, MENU_ENTRIES.len(), "Tab {step} reports the same menu");
    }

    s.write(TAB);
    s.settle();
    let m = s.menu(false);
    assert_eq!(
        selected_entry(&screen_of(&s)),
        MENU_ENTRIES[0],
        "the selection wraps round"
    );
    s.write(CTRL_U);
    // `tzz_d` is both a file in that directory and a prefix of the directory
    // next to it. Offering `tzz_d` for `tzz_d` would be a menu with one entry
    // that says nothing the line does not already say. What is left is one
    // entry, and one entry is not a menu: the key takes it.
    s.send("tzz_d");
    s.write(TAB);
    s.settle();
    let m = s.menu(false);
    assert_eq!(
        (m.line, m.entries),
        ("tzz_dir/".to_string(), 0),
        "one entry is taken, not offered"
    );
    s.write(CTRL_U);

    // The same single entry, but a name that exists only because it matched on
    // PATH: PATH alone says *it exists*, and one entry is one entry.
    s.send(&MENU_FAR[..3]);
    assert_eq!(hint(&mut s), "SUG=[]", "and nothing is hinted for it");
    s.send(&MENU_FAR[..3]);
    s.write(TAB);
    s.settle();
    let m = s.menu(false);
    assert_eq!(
        (m.line, m.entries),
        (format!("{MENU_FAR} "), 0),
        "one entry is taken even when only PATH knows it"
    );
    s.write(CTRL_U);

    // A learned name and the directory of the same name are one completion,
    // not two. The learned form wins, so the menu agrees with the hint.
    s.send("cd tzz_di");
    assert_eq!(hint(&mut s), "SUG=[r]", "and the hint was already saying it");
    s.send("cd tzz_di");
    s.write(TAB);
    s.settle();
    let m = s.menu(false);
    assert_eq!(
        (m.line, m.entries),
        ("cd tzz_dir ".to_string(), 0),
        "a learned name and the path of the same name are one entry"
    );
    s.write(CTRL_U);

    // Every `cd …` line in the history offers its next word, so `cd ..` offers
    // `..` and `cd -` offers `-`. Neither starts with `t`, so neither
    // completes `cd t`, and a menu listing them is noise with nothing behind
    // it. The hint has always filtered on "extends the line" inside
    // _tai_best, so the menu has to ask the same question.
    s.send("cd t");
    s.write(TAB);
    s.settle();
    let m = s.menu(false);
    assert_eq!(
        (m.line, m.entries),
        ("cd tzz_dir ".to_string(), 0),
        "a cd word with one live directory completes to it"
    );
    s.write(CTRL_U);
    s.send("cd t");
    assert_eq!(hint(&mut s), "SUG=[zz_dir]", "and the hint agrees on what the word can be");
    s.write(CTRL_U);

    // The reported case was `cd ` answering `cd ..`, so the index now ranks a
    // real destination above a relative move. What has to survive that is the
    // move itself: a fix that dropped `cd ..` from the index would stop
    // `cd .` completing to it.
    s.send("cd ");
    assert_eq!(
        hint(&mut s),
        "SUG=[tzz_dir]",
        "a bare `cd ` is answered with a destination, not `cd ..`"
    );
    s.write(CTRL_U);
    // Asked for as a *word*: `cd .` can only be completed by `..`, which is
    // the assertion that the demotion ranked the move rather than dropping it
    // from the index.
    s.send("cd .");
    assert_eq!(
        hint(&mut s),
        "SUG=[.]",
        "while `cd .` still completes to it — ranked, not removed"
    );
    // Re-sent because reading the hint ends the line in zsh. Tab then *takes*
    // the completion rather than opening a list: the rule demoted `cd ..` in
    // the ranking, it did not take it off the menu.
    s.send("cd .");
    s.write(TAB);
    s.settle();
    let m = s.menu(false);
    assert_eq!(
        (m.line, m.entries),
        ("cd .. ".to_string(), 0),
        "and the key that takes a completion still writes it"
    );
    s.write(CTRL_U);

    // A key that was not Tab puts the menu away: the entries belonged to a line
    // that is no longer the one being typed. What the dump may hold instead is
    // the loose glimpse, which the edited word `tzz_X` is one deletion from
    // earning — a reader's list, not the menu that was open.
    s.send("tzz");
    s.write(TAB);
    s.settle();
    s.send("X");
    let m = s.menu(false);
    assert!(!m.drawn.contains("tzz_a"), "typing closes the menu");
    assert_eq!(m.line, "tzz_X", "and the character is in the line");
    s.write(CTRL_U);

    // A name the directory does not have, and no command has either.
    s.send(NOT_INSTALLED);
    s.write(TAB);
    s.settle();
    let m = s.menu(false);
    assert_eq!(
        (m.entries, m.line),
        (0, NOT_INSTALLED.to_string()),
        "nothing to complete means no menu"
    );
    s.write(CTRL_U);

    // A word the cursor is not on. The index is keyed on whole lines, so there
    // is no history behind half a line, and a menu that marked an entry here
    // would be marking a word the user is not looking at. Only the menu is
    // asserted: from there Tab is zsh's own completion again.
    s.send("tzz");
    s.write(CTRL_A);
    s.write(TAB);
    s.settle();
    let m = s.menu(false);
    assert_eq!((m.entries, m.idx), (0, 0), "a cursor off the end gets the shell's own completion");
    s.write(CTRL_U);

    // What tai learned, in the order it ranked it, and the wrapper rule with
    // it: the menu reads the same lookup as the ghost text, so `sudo git `
    // offers the git the user has run rather than nothing at all.
    s.send("git ");
    s.write(LIST);
    s.settle();
    let m = s.menu(false);
    assert_eq!(
        menu_entries(&m.drawn).into_iter().take(2).collect::<Vec<_>>(),
        own(&["pull", "status"]),
        "the menu offers what tai learned"
    );
    s.write(CTRL_U);

    // And the same lookup, asked about a word: `git p` has one answer, so the
    // key takes it — which is also what the hint was saying all along.
    s.send("git p");
    s.write(LIST);
    s.settle();
    let m = s.menu(false);
    assert_eq!(
        (m.line, m.entries),
        ("git pull ".to_string(), 0),
        "one learned answer completes rather than listing"
    );
    s.write(CTRL_U);
    s.send("git p");
    assert_eq!(hint(&mut s), "SUG=[ull --rebase]", "and the hint was completing to the same word");
    s.write(CTRL_U);
    s.send("sudo git ");
    s.write(LIST);
    s.settle();
    let m = s.menu(false);
    assert_eq!(
        menu_entries(&m.drawn).into_iter().take(2).collect::<Vec<_>>(),
        own(&["pull", "status"]),
        "a wrapper does not hide the menu"
    );
    s.write(CTRL_U);

    // An installed command the history has never seen still gets its one
    // honest completion, exactly as the ghost text gives it.
    s.send(&format!("{INSTALLED} "));
    s.write(TAB);
    s.settle();
    let m = s.menu(false);
    assert_eq!(
        selected_entry(&screen_of(&s)),
        "--help",
        "an unknown tool still offers --help"
    );
    s.write(CTRL_U);
    assert!(s.noise().is_empty(), "no noise from the menu");
    s.close();

    // Enter takes the selected entry and stops there. It must not run the
    // line: filling in a directory you picked out of a menu is not a request
    // to go there. So this checks both halves — the entry was filled, the
    // first one was not, and nothing ran until the next Enter.
    let mut s = Session::new("zsh");
    s.run(&format!("cd {}", menu_dir().display()));
    s.run(&format!(
        "export PATH={}:{}:$PATH",
        menu_dir().display(),
        menu_bin().display()
    ));
    s.send("tzz");
    s.write("\t\t");
    s.settle();
    s.write(ENTER);
    s.settle();
    let m = s.menu(false);
    assert_eq!(m.line, "tzz_b ", "Enter fills in the selected entry");
    assert_eq!((m.entries, m.idx), (0, 0), "and closes the menu");
    assert!(!m.line.starts_with("tzz_a"), "Enter did not take the first entry");
    assert!(!s.raw().contains("RAN tzz_b"), "and Enter did not run it");
    // The second Enter is the one that runs, which is the whole point of the
    // first one not doing it.
    s.write(ENTER);
    s.wait_re("RAN tzz_b", "the line to run on the second Enter");
    assert!(s.noise().is_empty(), "the second Enter runs the line");
    s.close();

    // TAI_NO_MENU=1 puts Tab back to what it did before the menu existed.
    probe_env("zsh", TAB, "docker", "docker ps", &[("TAI_NO_MENU", "1")]);

    // Down steps the *normal* menu as well — not through its own binding, but
    // because the same key sequences ask the plugin to drive the selection it
    // already owns.
    let mut s = Session::new("zsh");
    s.run(&format!("cd {}", menu_dir().display()));
    s.send("tzz");
    s.write(LIST);
    s.settle();
    s.write(DOWN);
    s.settle();
    s.write(ENTER);
    s.settle();
    let m = s.menu(false);
    assert_eq!(m.line, "tzz_b ", "Down steps a normal armed menu");
    assert!(!s.raw().contains("RAN tzz_b"), "and nothing ran it");
    assert!(s.noise().is_empty(), "no noise while stepping");
    s.close();
    check_fixture_intact("the run");
}

/// Tab writes the head every candidate already shares, then lists.
///
/// The reported case: `cat .zs` answered with `.zshrc` and `.zsh/` — two
/// candidates that are the same word until the `h` — and the menu drew them
/// under a line still reading `.zs`. So the press that opens the list first
/// advances the line to the longest head every entry starts with, and the list
/// draws under the advanced line.
///
/// Four ways to be wrong, each asserted: writing it twice (the second Tab
/// cycling must not append the head again); writing it when the word already
/// IS the head; writing a head the typed word does not start with (the
/// case-folded head takes the candidates' case, and only folds from the word
/// onward); and writing anything at all when the word is empty.
#[test]
fn zsh_menu_prefix() {
    let _guard = LOCK.lock().unwrap_or_else(|e| e.into_inner());
    setup();
    make_fixture_runnable();

    let mut s = Session::new("zsh");
    s.run(&format!("cd {}", prefix_dir().display()));

    // The reported pair. The file answer answers `.zs` with `.zshrc` from the
    // disk's (#i) fallback — a directory is not a file, so `.zsh/` arrives
    // from the directory listing — and the head of the two written forms is
    // `.zsh`.
    s.send("cat .zs");
    s.write(TAB);
    s.settle();
    let m = s.menu(false);
    assert_eq!(m.line, "cat .zsh", "the line takes the head both candidates share");
    let entries = menu_entries(&m.drawn);
    assert!(
        entries.iter().any(|e| e == ".zshrc") && entries.iter().any(|e| e == ".zsh/"),
        "and the list shows both candidates"
    );
    assert_eq!(selected_entry(&screen_of(&s)), ".zshrc", "with the first one selected");

    // The next press is the choosing press it always was: the selection moves
    // and the line stands. A head written twice would read `.zsh_` here.
    s.write(TAB);
    s.settle();
    let m = s.menu(false);
    assert_eq!(
        (m.line, selected_entry(&screen_of(&s))),
        ("cat .zsh".to_string(), ".zsh/".to_string()),
        "the next Tab cycles without writing again"
    );

    // Enter lays the whole entry over the span the head fills — head included,
    // exactly once.
    s.write(ENTER);
    s.settle();
    let m = s.menu(false);
    assert_eq!(
        (m.line, m.entries),
        ("cat .zsh/".to_string(), 0),
        "Enter takes an entry over the advanced line"
    );
    s.write(CTRL_U);

    // A word that already IS the head: nothing to write, the menu just lists.
    s.send("cat .zsh");
    s.write(TAB);
    s.settle();
    let m = s.menu(false);
    assert_eq!(
        (m.line, m.entries),
        ("cat .zsh".to_string(), 2),
        "a word that is the head is not extended"
    );
    s.write(CTRL_U);

    // The head folds case, from the word onward: `down` is answered by two
    // files whose shared head is `Downloads`, and the line takes the head's
    // case — the rule every accept key already keeps.
    s.send("cat down");
    s.write(TAB);
    s.settle();
    let m = s.menu(false);
    assert_eq!(
        m.line,
        "cat Downloads",
        "a head reached through the case fold is written in the candidates' case"
    );
    let entries = menu_entries(&m.drawn);
    assert!(
        entries.iter().any(|e| e == "Downloads.zip") && entries.iter().any(|e| e == "Downloads2.txt"),
        "and both folded files are on the list"
    );
    s.write(CTRL_U);

    // A head that only exists quoted. Both candidates are `My Docs…`, so the
    // head of the *written* forms is `My\ Docs` — one shell word, safe to
    // stand in the line. The rows are drawn raw, and read as names — a
    // containment assertion on the drawn text, because menu_entries reads the
    // space inside the name as a column gap.
    s.send("cat My");
    s.write(TAB);
    s.settle();
    let m = s.menu(false);
    assert_eq!(m.line, "cat My\\ Docs", "a head with a space in it is written quoted");
    let flat = m.drawn.replace("\\n", " ");
    assert!(
        flat.contains("My Docs2.txt") && flat.contains("My Docs/"),
        "and the rows are drawn as the names they are"
    );
    // Cycle to the directory and take it: the entry replaces the head — no
    // doubled `Docs`, no half a quote left behind.
    s.write(TAB);
    s.settle();
    s.write(ENTER);
    s.settle();
    let m = s.menu(false);
    assert_eq!(
        (m.line, m.entries),
        ("cat My\\ Docs/".to_string(), 0),
        "Enter replaces the quoted head with the whole entry"
    );
    s.write(CTRL_U);

    // An empty word is nothing to extend: the line ends in a space, the menu
    // is the listing, and nothing is typed on the user's behalf.
    s.send("cat ");
    s.write(TAB);
    s.settle();
    let m = s.menu(false);
    assert_eq!(
        (m.line, m.entries > 0),
        ("cat ".to_string(), true),
        "an empty word is listed, not filled in"
    );
    s.write(CTRL_U);
    assert!(s.noise().is_empty(), "no noise from the shared head");
    s.close();

    // readline has been completing the certain part first for as long as it
    // has existed; the point of the half above is that the shells now agree.
    let mut b = Session::new("bash");
    b.run(&format!("cd {}", prefix_dir().display()));
    b.send("cat .zs");
    b.write(TAB);
    b.settle();
    assert_eq!(b.line(), "cat .zsh", "readline completes the shared head too");
    assert!(b.noise().is_empty(), "no noise from the bash head");
    b.close();
    check_fixture_intact("the run");
}

/// A menu shows what an entry adds, not what every entry shares.
///
/// The reported case: a Tab drew ten directories, each with the typed
/// directory in front of it. The line above the menu already said that, so ten
/// rows spent most of the screen on eight words — and the longest entry set
/// the width of every cell.
///
/// Three things are asserted, because each is its own way of being wrong: the
/// rows lose the stem; `Enter` still writes the **whole** word (a menu that
/// inserted the suffix it drew would quietly truncate the line); and what the
/// entries share is only dropped when it is a whole word — `tzz_` is not a
/// word, and cutting there would leave `a` and `b`.
#[test]
fn zsh_menu_stem() {
    let _guard = LOCK.lock().unwrap_or_else(|e| e.into_inner());
    setup();
    make_fixture_runnable();

    let mut s = Session::new("zsh");
    s.run(&format!("cd {}", menu_dir().display()));

    // Four entries, not five: `stemroot/` holds a directory argument's menu,
    // and `stem_zeta.iso` is a file — the shell answers `cd stem_zeta.iso`
    // with "not a directory", so the menu does not offer it. The long
    // directory name still sets the width of every cell.
    let stem_dirs: Vec<&str> = STEM_NAMES.iter().copied().filter(|n| n.ends_with('/')).collect();
    s.send("cd stemroot/");
    s.write(TAB);
    s.settle();
    let m = s.menu(false);
    assert_eq!(
        menu_entries(&m.drawn),
        own(&stem_dirs),
        "the stem is drawn once, by the line, and not on every row"
    );
    assert_eq!(m.line, "cd stemroot/", "the line is untouched");
    assert_eq!(m.entries, stem_dirs.len(), "every entry is there");
    assert!(
        !menu_entries(&m.drawn).iter().any(|e| e == "stem_zeta.iso"),
        "a file is not offered after cd"
    );
    assert_eq!(
        selected_entry(&screen_of(&s)),
        stem_dirs[0],
        "the first entry is selected"
    );
    s.write(ENTER);
    s.settle();
    let m = s.menu(false);
    // The whole word, stem and all: this is the half that would corrupt the
    // line.
    assert_eq!(
        m.line,
        format!("cd stemroot/{}", STEM_NAMES[0]),
        "Enter writes the whole word, not the drawn row"
    );
    assert_eq!((m.entries, m.idx), (0, 0), "and closes the menu");
    s.write(CTRL_U);

    // Two file names that share a whole *word* — `wombat book.txt` and
    // `wombat cards.txt` under a word typed as `womb`. The head of the two
    // written forms is `wombat\ ` — one shell word — and Tab writes it before
    // the list draws. The rows are then drawn whole: the stem clamp cannot cut
    // a shared word into the line when the line holds it quoted and the rows
    // are raw.
    s.send("womb");
    s.write(TAB);
    s.settle();
    let m = s.menu(false);
    // menu_entries reads a run of spaces as a column gap, and these names
    // hold a space of their own, so the assertion is on the drawn text itself.
    let flat = m.drawn.replace("\\n", " ");
    assert!(
        flat.contains("wombat book.txt") && flat.contains("wombat cards.txt"),
        "entries sharing a word past the word are drawn whole"
    );
    assert_eq!(m.line, "wombat\\ ", "and the line took the quoted head they share");
    s.write(ENTER);
    s.settle();
    let m = s.menu(false);
    // The whole name, and quoted: the bare name in the line is two words and
    // `cat wombat book.txt` is a command that cannot work. The row is still
    // drawn without the quoting — the drawing shows the file, the line holds
    // the text a shell passes as one word.
    assert_eq!(m.line, "wombat\\ book.txt", "Enter writes the whole name");
    s.write(CTRL_U);

    // A shared *fragment* is not a stem, however long it is. These seven share
    // `tzz_`, and `tzz_` is not a word — cutting there would leave every name
    // reduced to its own tail, which is a menu answering a different question.
    s.send("tzz_");
    s.write(LIST);
    s.settle();
    let m = s.menu(false);
    // Six of them, not seven: `tzz_other` is on PATH, and this session did not
    // put MENU_BIN there. The names here are the ones next to the cursor,
    // which is where the files are.
    assert_eq!(
        menu_entries(&m.drawn),
        own(&["tzz_a", "tzz_b", "tzz_c", "tzz_d", "tzz_dir/", "tzz_solo"]),
        "a shared fragment is not a stem"
    );
    assert_eq!(m.line, "tzz_", "and the line is still the whole word");
    s.write(CTRL_U);
    assert!(s.noise().is_empty(), "no noise from the stem");
    s.close();
    check_fixture_intact("the run");
}

/// The loose menu: previewed while typing, armed by the down arrow, kept after
/// Enter until a second one runs it. Also the key-sequence wiring: an rc file
/// that bound the arrows somewhere else still gets the armed list from the
/// same key.
#[test]
fn loose_menu() {
    let _guard = LOCK.lock().unwrap_or_else(|e| e.into_inner());
    setup();
    make_fixture_runnable();

    // Words that no learned line starts with, but that learned lines do
    // contain: the glimpse comes up by itself, and Down steps into it.
    let mut s = Session::new("zsh");
    let raw = bind_raw_dump(&mut s);
    s.send("pull --rebase");
    let got = raw_dump(&mut s, &raw, false);
    assert_eq!(
        dump_line(&got, 2),
        "MENU=[\\n  git pull --rebase] N=[1] IDX=[1]",
        "loose lines glimpsed while typing"
    );
    assert!(!dump_line(&got, 1).contains("standout"), "disarmed: no selection highlight yet");
    s.write(DOWN);
    let got = raw_dump(&mut s, &raw, false);
    assert!(dump_line(&got, 1).contains("standout"), "Down arms the glimpse");
    s.write(ENTER);
    let got = raw_dump(&mut s, &raw, false);
    assert_eq!(dump_line(&got, 0), "git pull --rebase", "Enter fills in the loose match");
    assert!(!s.raw().contains("RAN git"), "and did not run it");
    assert_eq!(dump_line(&got, 2), "MENU=[] N=[0] IDX=[0]", "and the menu closed");
    s.close();

    // Tab never writes a loose row, however few of them there are. The loose
    // list answers the line nothing completes with the lines the history
    // merely mentions the typed letters in — `cat z` by `cat tzz_d`, for the z
    // inside the name — and every one of its rows replaces the whole typed
    // line. A completion is a word that extends the word being typed; a loose
    // row is not one, so the key arms the row it finds instead of writing it,
    // and Enter is the press that means it.
    let mut s = Session::new("zsh");
    let raw = bind_raw_dump(&mut s);
    s.run(&format!("cd {}", menu_dir().display()));
    s.send("cat z");
    let got = raw_dump(&mut s, &raw, false);
    assert_eq!(
        dump_line(&got, 2),
        "MENU=[\\n  cat tzz_d] N=[1] IDX=[1]",
        "a loose line the letters merely reach is glimpsed"
    );
    assert!(!dump_line(&got, 1).contains("standout"), "a preview is drawn unmarked");
    s.write(TAB);
    let got = raw_dump(&mut s, &raw, false);
    assert_eq!(dump_line(&got, 0), "cat z", "Tab leaves the typed line alone");
    assert!(dump_line(&got, 1).contains("standout"), "Tab arms the row instead of writing it");
    assert_eq!(
        dump_line(&got, 2),
        "MENU=[\\n  cat tzz_d] N=[1] IDX=[1]",
        "and the list still holds the row"
    );
    s.write(TAB);
    let got = raw_dump(&mut s, &raw, false);
    assert_eq!(dump_line(&got, 0), "cat z", "the next Tab cycles, still nothing written");
    s.write(ENTER);
    let got = raw_dump(&mut s, &raw, false);
    assert_eq!(dump_line(&got, 0), "cat tzz_d", "Enter takes the armed row");
    assert_eq!(dump_line(&got, 2), "MENU=[] N=[0] IDX=[0]", "and the menu closed with it");
    assert!(!s.raw().contains("RAN "), "and one Enter did not run it");
    s.close();

    // Same glimpse in the key mode the terminal actually uses once it has
    // emitted smkx: application cursor keys.
    let mut s = Session::new("zsh");
    let raw = bind_raw_dump(&mut s);
    s.send("pull --rebase");
    s.write("\x1bOB");
    let got = raw_dump(&mut s, &raw, false);
    assert!(dump_line(&got, 1).contains("standout"), "Down in application cursor mode also arms");
    s.close();

    // Up from a glimpse nothing marked yet puts the list away *for that line*:
    // nothing re-opens on the same text, and a new line gets a fresh glimpse.
    let mut s = Session::new("zsh");
    let raw = bind_raw_dump(&mut s);
    s.send("pull --rebase");
    s.write(UP);
    let got = raw_dump(&mut s, &raw, false);
    assert_eq!(dump_line(&got, 2), "MENU=[] N=[0] IDX=[0]", "Up closes the glimpse");
    s.write(CTRL_E); // ^E: cursor to the end, line unchanged
    let got = raw_dump(&mut s, &raw, false);
    assert_eq!(dump_line(&got, 2), "MENU=[] N=[0] IDX=[0]", "nothing re-opened on the same line");
    s.write(CTRL_U);
    s.send("status");
    let got = raw_dump(&mut s, &raw, false);
    assert_eq!(
        dump_line(&got, 2),
        "MENU=[\\n  git status] N=[1] IDX=[1]",
        "a new line re-opens the glimpse"
    );
    s.close();

    // A stock startup that binds the arrows somewhere else — the way
    // manjaro-zsh-config hands them to zsh-history-substring-search. The down
    // arrow has to step into the loose list *anyway*, and Enter takes it.
    if std::path::Path::new("/usr/share/zsh/manjaro-zsh-config").exists() {
        let mut s = Session::new("zsh");
        let raw = bind_raw_dump(&mut s);
        s.run("source /usr/share/zsh/manjaro-zsh-config");
        s.run(&format!("source {}", plugin_path("zsh").display()));
        s.send("pull --rebase");
        s.write(DOWN);
        let got = raw_dump(&mut s, &raw, false);
        assert!(
            dump_line(&got, 1).contains("standout"),
            "Down steps into the loose list even when the rc bound it elsewhere"
        );
        s.write(ENTER);
        let got = raw_dump(&mut s, &raw, false);
        assert_eq!(
            dump_line(&got, 0),
            "git pull --rebase",
            "and Enter writes the armed loose match into the line"
        );
        assert!(s.noise().is_empty(), "no zle mishap from the stash wiring");
        s.close();
    }

    // Enter with a *disarmed* glimpse does not swallow the line: the typed
    // text runs as typed.
    let mut s = Session::new("zsh");
    s.send("pull --rebase");
    s.write(ENTER);
    s.settle();
    assert_eq!(s.line(), "", "Enter on a disarmed glimpse runs the typed line");
    s.close();

    // Typos: when no line exactly continues the text, the loose glimpse still
    // finds a line whose letters, in order, hold every letter of the typed
    // word — `gst` is a tap away from `git status`.
    let mut s = Session::new("zsh");
    let raw = bind_raw_dump(&mut s);
    s.send("gst");
    let got = raw_dump(&mut s, &raw, false);
    assert_eq!(
        dump_line(&got, 2),
        "MENU=[\\n  git status] N=[1] IDX=[1]",
        "a short typo still reaches the learned line"
    );
    // And the two clauses that keep that from being arithmetic: too short to
    // be fuzzy, and nothing to be found. `ts` is not a command on this
    // machine, so nothing else can answer it — `gs`, which is one, gets the
    // `--help` fallback instead and is checked in zsh_menu.
    s.write(CTRL_U);
    s.send("ts");
    let got = raw_dump(&mut s, &raw, false);
    assert_eq!(dump_line(&got, 2), "MENU=[] N=[0] IDX=[0]", "but two letters are not a typo");
    s.write(CTRL_U);
    s.send("zqx");
    let got = raw_dump(&mut s, &raw, false);
    assert_eq!(
        dump_line(&got, 2),
        "MENU=[] N=[0] IDX=[0]",
        "and letters nothing shares stay silent"
    );
    s.close();

    // The same prefix twice is one answer, not two scans: ZLE redraws without
    // the buffer changing (cursor moves, a menu repaint, a resize), and the
    // glimpse has to still be on screen for the second one. A cache that skips
    // the scan but forgets to hand the answer back loses the menu on the
    // redraw that follows, which is the same bug seen one step later.
    let mut s = Session::new("zsh");
    let raw = bind_raw_dump(&mut s);
    s.send("gst");
    let first = raw_dump(&mut s, &raw, false);
    s.write("\x01\x1b[C"); // ^A, Right: move, redraw, buffer unchanged
    let again = raw_dump(&mut s, &raw, false);
    assert_eq!(dump_line(&again, 2), dump_line(&first, 2), "the redraw keeps the same glimpse");
    s.close();

    // Very long learned lines must not smear the whole screen: the loose list
    // trims its cells to the terminal width instead of letting one curl
    // monster row overflow every column.
    let long_cmd = format!(
        "LONG_MARKER curl https://example.com/{} --compressed -H UA:abcd",
        "verylong".repeat(80)
    );
    let text = fs::read_to_string(zsh_index()).unwrap();
    fs::write(
        zsh_index(),
        format!(
            "{text}_TAI_SCORE+=({} 50)\n_TAI_FIRST+=({} {})\n_TAI_WORD+=({} {})\n",
            zq(&long_cmd),
            zq("curl"),
            zq(&long_cmd),
            zq("curl"),
            zq(&long_cmd)
        ),
    )
    .unwrap();
    let mut s = Session::new("zsh");
    s.send("LONG_MARKER");
    // The rows are read from the drawn menu, not from the raw dump line: the
    // dump appends `] N=[…] IDX=[…]` to the same string, and counting that
    // bookkeeping as part of a row would make a row that fits look like one
    // that does not.
    let m = s.menu(false);
    let rows: Vec<&str> = m.drawn.split("\\n").filter(|r| !r.is_empty()).collect();
    assert!(
        rows.iter().any(|r| r.contains("LONG_MARKER")),
        "loose list shows the long line"
    );
    assert!(
        rows.iter().all(|r| r.chars().count() <= COLS as usize),
        "but each row fits the screen"
    );
    s.close();
    write_index(None, None);
    check_fixture_intact("the run");
}

/// A paste is not a half-remembered command, and the scan has to say so before
/// it reads a single learned line.
///
/// `_tai_loose` turns each typed word into a pattern of its letters joined by
/// `*` and runs that against every line the index holds — a cost that grows
/// with the length of the word in a way nothing about typing suggests. So a
/// pasted token is matched as a plain substring instead of becoming a pattern,
/// and a pasted *line* is refused before the scan starts at all.
///
/// Matched rather than dropped is the other half of the rule, and it is the
/// half that has to be tested: a word that leaves the question takes its veto
/// with it, so `cd` followed by a pasted path would only ask whether a line
/// mentions `cd`. Both bounds change what the glimpse answers, so they are
/// asserted as answers rather than left as a comment, and the cost is asserted
/// as a bound rather than as a benchmark.
#[test]
fn pasted_text_is_not_a_typo() {
    let _guard = LOCK.lock().unwrap_or_else(|e| e.into_inner());
    setup();
    make_fixture_runnable();

    // A learned line holding one word longer than `_TAI_LOOSE_MAX_WORD`, and
    // short enough that the line bound cannot be what answers for it.
    let line = "curl https://example.com/aaabbbccddeeffgg theend";
    let verbatim = "https://example.com/aaabbbccddeeffgg"; // 36 chars, in `line`
    // The same word with the space taken out: the line holds its letters *in
    // order* and not the word itself, which is what an unbounded fuzzy
    // pattern would match and a substring never can.
    let in_order = "curlhttps://example.com/aaabbbccddeeffgg"; // 40 chars
    assert!(line.contains(verbatim) && !line.contains(in_order));
    let text = fs::read_to_string(zsh_index()).unwrap();
    fs::write(
        zsh_index(),
        format!(
            "{text}_TAI_SCORE+=({} 9)\n_TAI_FIRST+=({} {})\n_TAI_WORD+=({} {})\n",
            zq(line),
            zq("curl"),
            zq(line),
            zq("curl"),
            zq(line)
        ),
    )
    .unwrap();

    let mut s = Session::new("zsh");
    let raw = bind_raw_dump(&mut s);
    // A paste does not open the list on its own. It arrives wrapped in the
    // bracketed-paste envelope — what a terminal sends when the user pastes —
    // and nobody has read a line that arrived at once. The learned line IS
    // here (the pasted fragment is its verbatim text), so the empty menu below
    // is the paste rule answering, not the scan finding nothing.
    s.send(&format!("{PASTE_START}pull --re{PASTE_END}"));
    let got = raw_dump(&mut s, &raw, false);
    assert_eq!(
        dump_line(&got, 2),
        "MENU=[] N=[0] IDX=[0]",
        "a paste does not open the list on its own"
    );
    // …and one real keystroke re-arms the glimpse: the buffer changed by a
    // character, which is typing, which is what the list is for.
    s.write(BACKSPACE);
    let got = raw_dump(&mut s, &raw, false);
    assert_eq!(
        dump_line(&got, 2),
        "MENU=[\\n  git pull --rebase] N=[1] IDX=[1]",
        "and removing a character brings it back"
    );
    // A pasted token that is not the text of any line is not fuzzy-matched
    // either: the letters-in-order rule is the tail of the same scan, and
    // re-arming it does not invent an answer the scan does not have. (36 and
    // 40 characters: each also crosses the raw-arrival bound, so both rules —
    // the envelope and the length — answer the same way.)
    s.write(CTRL_U);
    s.send(&format!("{PASTE_START}{in_order}{PASTE_END}"));
    let got = raw_dump(&mut s, &raw, false);
    assert_eq!(
        dump_line(&got, 2),
        "MENU=[] N=[0] IDX=[0]",
        "a pasted token that is not text is not fuzzy-matched either"
    );
    s.write(BACKSPACE);
    let got = raw_dump(&mut s, &raw, false);
    assert_eq!(
        dump_line(&got, 2),
        "MENU=[] N=[0] IDX=[0]",
        "and re-arming it does not invent an answer either"
    );
    // A line past `_TAI_LOOSE_MAX_LINE` is refused outright, before the scan
    // starts at all — and a keystroke after it does not change that.
    s.write(CTRL_U);
    s.send(&format!("{PASTE_START}curl {}{PASTE_END}", "z".repeat(220)));
    let got = raw_dump(&mut s, &raw, false);
    assert_eq!(dump_line(&got, 2), "MENU=[] N=[0] IDX=[0]", "a pasted line is refused");
    s.write(BACKSPACE);
    let got = raw_dump(&mut s, &raw, false);
    assert_eq!(
        dump_line(&got, 2),
        "MENU=[] N=[0] IDX=[0]",
        "and stays refused once the character is removed"
    );
    s.close();
    write_index(None, None);

    // The bound as a cost. 2000 learned lines, because the pathology is the
    // product of the pattern's length and the number of lines it is run
    // against — a fixture of ten lines cannot see it. The token is a real
    // URL's worth of hex, which is what makes it expensive. Unbounded, the
    // same call is 8.9s on this fixture, so 5 seconds separates the bound from
    // the machine.
    let unit = "https://release-assets.githubusercontent.com/github-production/\
                3f5d6f8-f335-4b20-b2cc-cd45fbbea0a4?sp=r&sv=2018-11-09&sr=b&\
                spr=https&se=2026-04-05T20%3A20%3A35Z";
    let repeated = unit.repeat(4);
    let word: String = repeated.chars().take(6000).collect();
    let cmds: Vec<String> = (0..2000).map(|i| format!("echo line{i} filler{i} tail{i}")).collect();
    let refs: Vec<&str> = cmds.iter().map(|c| c.as_str()).collect();
    write_index(Some(&refs), None);

    // zsh_bin() may prepend the bundled zsh's directory to PATH and set FPATH;
    // resolve the shell first so the child inherits those.
    let zsh = shell_path("zsh").expect("no zsh for the loose cost probe");
    let mut cmd = Command::new(&zsh);
    cmd.args(["-f", "-c"])
        .arg(format!(
            "source {}\n_tai_loose '{}'",
            plugin_path("zsh").display(),
            word
        ))
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null());
    cmd.env_clear();
    for (k, v) in std::env::vars() {
        cmd.env(k, v);
    }
    cmd.env("TAI_INDEX", zsh_index())
        .env("TAI_DB", db_path())
        .env("TAI_HISTORY_FILES", "")
        .env("TAI_DATA_DIR", "/tmp/tai/tai_loose");
    let mut child = cmd.spawn().expect("spawn zsh for the loose cost probe");
    let deadline = Instant::now() + Duration::from_secs(5);
    loop {
        match child.try_wait().expect("wait for the loose probe") {
            Some(_) => break,
            None => {
                if Instant::now() > deadline {
                    let _ = child.kill();
                    panic!("a pasted token does not cost a redraw");
                }
                std::thread::sleep(Duration::from_millis(10));
            }
        }
    }
    write_index(None, None);
    check_fixture_intact("the run");
}

/// A paste delivered without the envelope is still one paste.
///
/// A paste that a terminal delivers as a burst of small insertions — no
/// envelope, which is what some ssh transports, VNC clients and terminals with
/// the mode off produce — lands between one keystroke and an arrival on every
/// chunk, and each chunk's redraw then paid a full query for a line nobody had
/// finished reading.
///
/// So chunks are timed, not measured: an insertion that arrives within a
/// fiftieth of a second of the previous one is a terminal, not a hand. The
/// quiet engages on the first chunk that follows another closely enough, the
/// arrival redraw answers nothing, and the answer the line owes is paid at the
/// next redraw that is not itself an arrival — here, the keystroke that
/// re-arms the glimpse, exactly as the envelope paste does on the same
/// keystroke.
#[test]
fn paste_chunks_are_a_paste_too() {
    let _guard = LOCK.lock().unwrap_or_else(|e| e.into_inner());
    setup();
    make_fixture_runnable();

    // The fixture this test answers from, pinned on purpose. The one line the
    // re-arm answers from is named here; the default list goes back at the
    // end. (Python had to save the ambient COMMANDS because write_index()
    // rebinds it; the Rust write_index takes the list per call.)
    write_index(Some(&["git pull --rebase"]), None);

    // A unique path for this session's paste-state dump: the session's own
    // TAI_TEST_DUMP is private to the harness.
    let n = RAW_N.fetch_add(1, Ordering::SeqCst);
    let pf = PathBuf::from(format!("/tmp/tai/tai_paste_{}_{n}.txt", std::process::id()));

    let paste_flags = |s: &mut Session| -> String {
        // The two flags the chunk rule owns, read inside the editor. ^O is
        // accept-line-and-down in emacs mode and bound by nobody else in this
        // session; the widget writes the file at widget time, so the values
        // are the ones the paste left, not the next redraw's.
        let _ = fs::remove_file(&pf);
        s.write("\x0f");
        s.wait_file(&pf, "paste state");
        let got = fs::read_to_string(&pf).unwrap_or_default();
        let _ = fs::remove_file(&pf);
        got.trim().to_string()
    };

    let mut s = Session::new("zsh");
    // Bound before anything else happens; s.run supplies the newline that
    // commits the definition (without it the paste below merges into it).
    let pfd = pf.display();
    s.run(&format!(
        "tai_paste_dump() {{ print -r -- \"QUIET=[$_TAI_PASTE_QUIET] \
         OWED=[$_TAI_Q_OWED]\" > {pfd}; }}; \
         zle -N tai_paste_dump; bindkey '^O' tai_paste_dump"
    ));
    // The raw dump widget is bound now, before any paste, because binding it
    // types a command — and once the pasted text is in the buffer, a command
    // line would merge into it.
    let raw = bind_raw_dump(&mut s);

    // First paste: long enough that the chunks cannot arrive as one read,
    // ending in a chunk wider than a keystroke so the quiet it raised is still
    // up when the settle ends. 23 characters, 3 at a time: eight chunks, every
    // one of them between the keystroke and the jump.
    let long_paste = "pull --rebase --onto xy";
    assert!(long_paste.len() % 3 == 2 && long_paste.len() > 25 / 2);
    let bytes = long_paste.as_bytes();
    let mut i = 0;
    while i < bytes.len() {
        let end = (i + 3).min(bytes.len());
        s.write(&long_paste[i..end]);
        std::thread::sleep(Duration::from_millis(10));
        i = end;
    }
    s.settle();
    assert_eq!(paste_flags(&mut s), "QUIET=[1] OWED=[1]", "a chunked paste raises the quiet");

    // The line it left answers nothing yet: the arrival redraws owed their
    // query, and the loose list — the thing that costs — never ran for a line
    // nobody has read.
    let got = raw_dump(&mut s, &raw, false);
    assert_eq!(dump_line(&got, 2), "MENU=[] N=[0] IDX=[0]", "and it opens no list on its own");

    // Now the shape the envelope test ends on: the same buffer, pasted the
    // same way, then one keystroke. The debt is paid there — one query for the
    // finished line — and the glimpse answers exactly as it does after an
    // envelope paste.
    s.write(CTRL_U);
    s.settle();
    let text = "pull --re";
    let bytes = text.as_bytes();
    let mut i = 0;
    while i < bytes.len() {
        let end = (i + 3).min(bytes.len());
        s.write(&text[i..end]);
        std::thread::sleep(Duration::from_millis(10));
        i = end;
    }
    s.settle();
    assert_eq!(paste_flags(&mut s), "QUIET=[1] OWED=[1]", "a shorter chunked paste is quiet too");
    // The keystroke that re-arms the glimpse is a hand's keystroke: a pause
    // after the dump key's own redraw, so the paste rule reads it at the speed
    // it arrives at and ends the quiet, the way it ends for the user who
    // typed it.
    std::thread::sleep(Duration::from_millis(50));
    s.write(BACKSPACE);
    s.settle();
    let got = raw_dump(&mut s, &raw, false);
    assert_eq!(
        dump_line(&got, 2),
        "MENU=[\\n  git pull --rebase] N=[1] IDX=[1]",
        "and one keystroke re-arms the glimpse"
    );
    assert_eq!(
        paste_flags(&mut s),
        "QUIET=[0] OWED=[0]",
        "with the debt paid where the line could be read"
    );
    s.close();
    write_index(None, None);
    check_fixture_intact("the run");
}

/// A learned row with raw control bytes never reaches the terminal.
///
/// The history held two rows that were really bracketed-paste envelopes, and
/// the index inherited them: one began with ESC, the other with SYN. Painted
/// from either into POSTDISPLAY, those bytes went to the terminal unescaped
/// and were escape sequences, not text. This rides the one path a user hits —
/// a glimpse that would have drawn one — and asserts nothing is drawn at all.
#[test]
fn unpaintable_rows_are_never_drawn() {
    let _guard = LOCK.lock().unwrap_or_else(|e| e.into_inner());
    setup();
    make_fixture_runnable();

    let text = fs::read_to_string(zsh_index()).unwrap();
    let dirty = "\x1b[200~zqx run~";
    fs::write(
        zsh_index(),
        format!(
            "{text}_TAI_SCORE+=({} 9)\n_TAI_FIRST+=({} {})\n_TAI_WORD+=({} {})\n",
            zq(dirty),
            zq("zqx"),
            zq(dirty),
            zq("zqx run"),
            zq(dirty)
        ),
    )
    .unwrap();

    let mut s = Session::new("zsh");
    let raw = bind_raw_dump(&mut s);
    s.send("zqx");
    // The loose list was the only answer: the row that would have fed it is
    // filtered, so nothing may appear — before, the raw ESC line was.
    let got = raw_dump(&mut s, &raw, false);
    assert_eq!(dump_line(&got, 2), "MENU=[] N=[0] IDX=[0]", "no raw bytes are glimpsed");
    s.close();
    write_index(None, None);
    check_fixture_intact("the run");
}

/// The control-byte rule's bash half — the candidate filter in bash.
///
/// The zsh twin above rides the glimpse; bash delivers the same row through
/// the ghost hint, accepted by rewriting the readline buffer on Ctrl-F. The
/// dirty row is the only answer the fixture holds for `zqx`, so an unfiltered
/// list would put raw escape bytes into the buffer, and the line after the
/// accept would be the paste envelope instead of what was typed.
#[test]
fn unpaintable_rows_are_never_drawn_bash() {
    let _guard = LOCK.lock().unwrap_or_else(|e| e.into_inner());
    setup();
    make_fixture_runnable();

    let text = fs::read_to_string(bash_index()).unwrap();
    let dirty = "\x1b[200~zqx run~";
    fs::write(
        bash_index(),
        format!(
            "{text}_TAI_SCORE[{}]=9\n_TAI_FIRST[{}]={}\n_TAI_WORD[{}]={}\n",
            zq(dirty),
            zq("zqx"),
            zq(dirty),
            zq("zqx run"),
            zq(dirty)
        ),
    )
    .unwrap();

    let mut s = Session::new("bash");
    s.send("zqx");
    s.write(CTRL_F);
    s.settle();
    // An empty hint accepts nothing: the line must still be what was typed,
    // never the envelope that arrived raw from an old store.
    assert_eq!(s.line(), "zqx", "no raw bytes are hinted in bash");
    assert!(s.noise().is_empty(), "and no noise from it");
    s.close();
    write_index(None, None);
    check_fixture_intact("the run");
}

/// A stem may only cut what the line already shows.
///
/// The reported case: `cd tm` in a home directory where every learned
/// destination sits under `tmp/`. The menu held entries whose every member
/// shared `tmp/`, the boundary cut handed that back as the stem, and the rows
/// were drawn as bare names under a line reading `cd tm` — completions of
/// `tmvllm`, names nobody typed and nothing offers. The stem is a reminder of
/// text that is already on screen; anything longer than the typed word is
/// information taken away.
///
/// Two halves are asserted: a prefix the user did *not* type is written into
/// the line by the shared-head rule, and the rows then carry only what it
/// adds. And a prefix the user *did* type is still cut, because that is the
/// half the original stem rule exists for.
#[test]
fn stem_never_cuts_untyped_text() {
    let _guard = LOCK.lock().unwrap_or_else(|e| e.into_inner());
    setup();
    make_fixture_runnable();

    let tmp_dir = menu_dir().join("tmp");
    let _ = fs::remove_dir_all(&tmp_dir);
    fs::create_dir_all(tmp_dir.join("vllm")).unwrap();
    fs::create_dir_all(tmp_dir.join("x")).unwrap();
    let mut cmds: Vec<&str> = DEFAULT_COMMANDS.to_vec();
    cmds.extend(["cd tmp/vllm", "cd tmp/x", "cd tmp/"]);
    write_index(Some(&cmds), None);

    let mut s = Session::new("zsh");
    s.run(&format!("cd {}", menu_dir().display()));

    s.send("cd tm");
    s.write(LIST);
    s.settle();
    let m = s.menu(false);
    let entries = menu_entries(&m.drawn);
    // The head every candidate shares is now in the line — `tmp/`, which the
    // user had not typed — and the rows carry the tails alone.
    assert_eq!(m.line, "cd tmp/", "the untyped root moves into the line");
    assert!(entries.iter().any(|e| e == "vllm"), "the rows carry what it adds");
    assert!(!entries.iter().any(|e| e == "tmp/vllm"), "and nothing is drawn twice");
    s.write(CTRL_U);

    // The half the stem rule is for: the prefix the user typed is drawn once,
    // by the line, and the rows carry only what it adds.
    s.send("cd tmp/");
    s.write(LIST);
    s.settle();
    let m = s.menu(false);
    let entries = menu_entries(&m.drawn);
    assert!(entries.iter().any(|e| e == "vllm"), "a typed stem is still cut from the rows");
    assert_eq!(m.line, "cd tmp/", "and the line still says what was typed");
    s.write(CTRL_U);
    assert!(s.noise().is_empty(), "no noise from the clamped stem");
    s.close();

    let _ = fs::remove_dir_all(&tmp_dir);
    write_index(None, None);
    check_fixture_intact("the run");
}

/// The glimpse ranks what the history holds verbatim, then one edit away.
///
/// The reported case: `forest` answered with the lines that mention it, and
/// `forestt` — one keystroke of typo — answered with aria2c URLs, because the
/// old gap matcher let the letters be *anywhere* in a line, in order. The
/// tiers now read: verbatim first, one deletion away second, and the
/// bounded-gap matcher only when both of those are silent.
#[test]
fn loose_tiers() {
    let _guard = LOCK.lock().unwrap_or_else(|e| e.into_inner());
    setup();
    make_fixture_runnable();

    let junk = format!(
        "aria2c -x 15 https://files.example.com/{}",
        "a".repeat(40)
    );
    // The forest line is a `cd` to a relative path, and a relative path is
    // judged where it is offered — the fixture writes `_TAI_LIVE` now, so the
    // line is only offerable from a directory that has the tree. The test is
    // about the tiers, not the judgement, so the tree exists where the shell
    // stands and the line is alive while the matchers are under test.
    let forest = menu_dir().join("tmp").join("fun-game").join("beauty-forest");
    fs::create_dir_all(&forest).unwrap();
    let mut cmds: Vec<String> = DEFAULT_COMMANDS.iter().map(|c| c.to_string()).collect();
    cmds.push("cd tmp/fun-game/beauty-forest".to_string());
    cmds.push(junk);
    let refs: Vec<&str> = cmds.iter().map(|c| c.as_str()).collect();
    write_index(Some(&refs), None);

    let mut s = Session::new("zsh");
    s.run(&format!("cd {}", menu_dir().display()));

    // A typo of a word the history holds: the one-edit tier answers with the
    // forest line, and the scatter matcher is not allowed to argue.
    s.send("forestt");
    let m = s.menu(false);
    assert!(m.drawn.contains("beauty-forest"), "a typo is answered by the line it is one letter from");
    assert!(!m.drawn.contains("aria2c"), "a typo is not answered by scattered letters");
    s.write(CTRL_U);

    // A word the history holds verbatim: the exact tier, and nothing guessed
    // underneath it.
    s.send("forest");
    let m = s.menu(false);
    assert!(m.drawn.contains("beauty-forest"), "the verbatim word is the answer");
    assert!(!m.drawn.contains("aria2c"), "verbatim leaves no room for guesses");
    s.write(CTRL_U);

    // The gap matcher still reads across the words a person remembers: g, s,
    // t sit in `git status` a few characters apart.
    s.send("gst");
    let m = s.menu(false);
    assert!(m.drawn.contains("git status"), "in-order letters still find git status");
    s.write(CTRL_U);
    assert!(s.noise().is_empty(), "no noise from the tiers");
    s.close();

    let _ = fs::remove_dir_all(menu_dir().join("tmp"));
    write_index(None, None);
    check_fixture_intact("the run");
}

/// Up through the history and Down again is history, not a menu.
///
/// The reported case: a few Ups to read previous commands, a Down or two to
/// come back, and somewhere in the middle the loose list opened — armed on a
/// line nobody typed. Down on a buffer that arrived from the history means the
/// next history entry; the list is for a line the user is composing.
#[test]
fn history_browsing_opens_no_list() {
    let _guard = LOCK.lock().unwrap_or_else(|e| e.into_inner());
    setup();
    make_fixture_runnable();

    let mut s = Session::new("zsh");
    s.run(&format!("cd {}", menu_dir().display()));
    // Three lines in the history, typed the way a user types them. The
    // harness's `run` wraps its command in a done-marker, and Up would recall
    // the wrapper rather than the command — so the lines enter the history as
    // keystrokes, newline and all.
    s.send(&format!("git status{ENTER}"));
    s.send(&format!("cd {}{ENTER}", menu_dir().display()));
    // A builtin line, not `docker ps`: this line is EXECUTED as it is typed,
    // and the noise check below reads everything the session printed. A
    // command that may not exist on the machine running the suite would print
    // "command not found" here and be noise the walk itself never caused.
    s.send(&format!("print tzz-walk-end{ENTER}"));
    s.write(UP);
    s.settle();
    s.write(UP);
    s.settle();
    s.write(UP);
    s.settle();
    let m = s.menu(false);
    assert_eq!(m.line, "git status", "Up still recalls history");
    s.write(DOWN);
    let m = s.menu(false);
    assert_eq!(m.entries, 0, "Down over a history line does not open the list");
    assert_eq!(
        m.line,
        format!("cd {}", menu_dir().display()),
        "and the history moved instead"
    );
    s.write(DOWN);
    let m = s.menu(false);
    assert_eq!(m.entries, 0, "and still no list");
    assert_eq!(m.line, "print tzz-walk-end", "until the walk leaves the oldest line");
    s.write(DOWN);
    let m = s.menu(false);
    assert_eq!(m.line, "", "and Down still walks out of the history");
    // That Down still opens the list for a line the user *is* composing is
    // loose_menu's whole subject; this test is about the history half.
    assert!(s.noise().is_empty(), "no noise from the history walk");
    s.close();
    check_fixture_intact("the run");
}

/// `cd` is answered by directories, and only by ones that exist here.
///
/// The reported case: `cd ` listed the files of the current directory, because
/// the directory glob offered every entry and the file answer answers any
/// path-shaped word. Enter on any of it answers "not a directory".
#[test]
fn cd_answers_directories() {
    let _guard = LOCK.lock().unwrap_or_else(|e| e.into_inner());
    setup();
    make_fixture_runnable();

    let mut s = Session::new("zsh");
    s.run(&format!("cd {}", menu_dir().display()));
    s.send("cd ");
    s.write(LIST);
    s.settle();
    let m = s.menu(false);
    let entries = menu_entries(&m.drawn);
    assert!(m.entries > 0, "the menu opened");
    let files: Vec<&String> = entries
        .iter()
        .filter(|e| ["tzz_d", "tzz_a", "tzz_solo"].contains(&e.as_str()))
        .collect();
    assert!(files.is_empty(), "no file is offered after cd");
    assert_eq!(
        entries.iter().filter(|e| *e == "tzz_dir" || *e == "tzz_dir/").count(),
        1,
        "the learned destination is, once"
    );
    assert!(
        entries.iter().any(|e| e == "stemroot/"),
        "the shell's own directories are, marked"
    );
    s.write(CTRL_U);
    assert!(s.noise().is_empty(), "no noise from the cd menu");
    s.close();
    check_fixture_intact("the run");
}

// A learned relative path is a suggestion where it exists, and nowhere else.
// The fixture index is its own files rather than the shared one, because the
// marks under test are the shared fixture's deliberate absence: every other
// menu test runs with an empty _TAI_LIVE and proves nothing changed for a
// history without relative paths in it.
fn live_dir() -> PathBuf {
    PathBuf::from("/tmp/tai/tai_live_cwd")
}
fn live_index() -> PathBuf {
    PathBuf::from("/tmp/tai/tai_test_live_index")
}
fn live_zsh() -> PathBuf {
    live_index().join("zsh-index.zsh")
}
fn live_bash() -> PathBuf {
    live_index().join("bash-index.bash")
}

/// The generated shape, small enough to read: three `ls` rows, one marked.
///
/// `Data/INI` is a relative path, so the generator marks it and the shells
/// judge it where they stand. `-la` is a flag and `/usr` is absolute: neither
/// is ever marked, and both are offered in every directory as they always
/// were.
fn write_live_index() {
    let with = live_dir().join("with");
    let without = live_dir().join("without");
    fs::create_dir_all(with.join("Data").join("INI")).unwrap();
    fs::create_dir_all(&without).unwrap();
    fs::create_dir_all(live_index()).unwrap();
    let zsh = [
        "# tai zsh index; test fixture, not generated".to_string(),
        "typeset -gA _TAI_SCORE _TAI_FIRST _TAI_WORD _TAI_SEQ _TAI_FILE _TAI_LIVE".to_string(),
        "_TAI_SCORE+=(ls\\ -la 50)".to_string(),
        "_TAI_SCORE+=(ls\\ /usr 49)".to_string(),
        "_TAI_SCORE+=(ls\\ Data/INI 48)".to_string(),
        "_TAI_FIRST+=(ls 'ls -la\nls /usr\nls Data/INI')".to_string(),
        "_TAI_LIVE+=(Data/INI 1)".to_string(),
    ];
    fs::write(live_zsh(), zsh.join("\n") + "\n").unwrap();
    let bash = [
        "# tai bash index; test fixture, not generated".to_string(),
        "declare -gA _TAI_SCORE _TAI_FIRST _TAI_WORD _TAI_SEQ _TAI_FILE _TAI_LIVE".to_string(),
        "_TAI_SCORE[ls\\ -la]=50".to_string(),
        "_TAI_SCORE[ls\\ /usr]=49".to_string(),
        "_TAI_SCORE[ls\\ Data/INI]=48".to_string(),
        "_TAI_FIRST[ls]=$'ls -la\\nls /usr\\nls Data/INI'".to_string(),
        "_TAI_LIVE[Data/INI]=1".to_string(),
    ];
    fs::write(live_bash(), bash.join("\n") + "\n").unwrap();
}

/// The word the menu completes is judged where the menu stands.
#[test]
fn live_words_zsh() {
    let _guard = LOCK.lock().unwrap_or_else(|e| e.into_inner());
    setup();
    make_fixture_runnable();
    write_live_index();

    let mut s = Session::new_with(
        "zsh",
        &[("TAI_INDEX", live_zsh().to_string_lossy().as_ref())],
        true,
    );
    s.run(&format!("cd {}", live_dir().join("without").display()));
    s.send("ls ");
    s.write(TAB);
    s.settle();
    let m = s.menu(false);
    let entries = menu_entries(&m.drawn);
    assert!(m.entries > 0, "the menu opened");
    assert!(entries.iter().any(|e| e == "-la"), "a flag is offered, unjudged");
    assert!(entries.iter().any(|e| e == "/usr"), "an absolute path is offered, unjudged");
    assert!(
        !entries.iter().any(|e| e == "Data/INI"),
        "a relative path that is not here is not offered"
    );
    s.write(CTRL_U);
    s.run(&format!("cd {}", live_dir().join("with").display()));
    s.send("ls ");
    s.write(TAB);
    s.settle();
    let m = s.menu(false);
    let entries = menu_entries(&m.drawn);
    assert!(
        entries.iter().any(|e| e == "Data/INI"),
        "the same word is offered where it exists"
    );
    assert!(s.noise().is_empty(), "no noise from the live menus");
    s.write(CTRL_U);

    // A first word commits whole lines, so the line is the claim under test:
    // `ls Data/INI` offered on `l` in the wrong directory writes an error, and
    // the whole-line test answers the same question the word test does.
    s.run(&format!("cd {}", live_dir().join("without").display()));
    s.send("l");
    s.write(TAB);
    s.settle();
    let m = s.menu(false);
    // A first-word menu's rows are whole lines, and the entries splitter is a
    // word splitter — the drawn text is the assertion here, the same way
    // first_word_menu reads its rows.
    assert!(
        !m.drawn.contains("ls Data/INI"),
        "the whole line is not offered where the path is not"
    );
    assert!(m.drawn.contains("ls -la"), "the other whole lines are");
    s.write(CTRL_U);
    s.run(&format!("cd {}", live_dir().join("with").display()));
    s.send("l");
    s.write(TAB);
    s.settle();
    let m = s.menu(false);
    assert!(m.drawn.contains("ls Data/INI"), "the whole line is offered where it exists");
    assert!(s.noise().is_empty(), "no noise from the whole-line menus");
    s.write(CTRL_U);
    s.close();
    check_fixture_intact("the run");
}

/// The same judgement in the other shell, by its own doors.
#[test]
fn live_words_bash() {
    let _guard = LOCK.lock().unwrap_or_else(|e| e.into_inner());
    setup();
    make_fixture_runnable();
    write_live_index();

    let mut s = Session::new_with(
        "bash",
        &[
            ("TAI_INDEX", live_zsh().to_string_lossy().as_ref()),
            ("TAI_COMPLETE_ALL", "1"),
        ],
        true,
    );
    s.run(&format!("cd {}", live_dir().join("without").display()));
    s.send("ls ");
    s.write(TAB);
    s.settle();
    let raw = s.raw();
    assert!(raw.contains("-la"), "a flag is offered, unjudged");
    assert!(raw.contains("/usr"), "an absolute path is offered, unjudged");
    assert!(
        !raw.contains("Data/INI"),
        "a relative path that is not here is not offered"
    );
    s.write(CTRL_U);
    s.run(&format!("cd {}", live_dir().join("with").display()));
    s.send("ls ");
    s.write(TAB);
    s.settle();
    assert!(s.raw().contains("Data/INI"), "the same word is offered where it exists");
    assert!(s.noise().is_empty(), "no noise from the live menus");
    s.write(CTRL_U);
    s.close();
    check_fixture_intact("the run");
}

/// Tab on a one-word line offers the learned lines first, whole.
///
/// The report: typing `g` hinted one learned line, Tab replaced it with a
/// screen of installed command names, and the first suggestion was not the
/// panel's winner. The menu's learned source looks the index up by the text
/// *in front of* the word, and a first word has none — so the learned lines
/// were never offered. Now the first-word menu opens with the learned whole
/// lines, merged by score across their keys, and the installed names keep
/// their place below.
///
/// Both shells are asserted, because the two implementations of the rule are
/// where the drift would start.
///
/// The report that returned here: the same Tab also echoed one `d=N` line per
/// matching key above the menu — a `local` declaration inside the key loop,
/// and zsh's `local`, applied to a parameter that already exists, prints its
/// value. The press's own bytes are the assertion, and the function is asked
/// for its stdout directly.
#[test]
fn first_word_menu() {
    let _guard = LOCK.lock().unwrap_or_else(|e| e.into_inner());
    setup();
    make_fixture_runnable();

    let fixture = ["go mod vendor", "go mod download", "godot .", "got log", "goose web", "git status"];
    let learned = ["go mod vendor", "go mod download", "godot .", "got log", "goose web"];
    write_index(Some(&fixture), None);

    // zsh first-word menu
    let mut s = Session::new("zsh");
    s.run(&format!("cd {}", menu_dir().display()));
    s.send("g");
    assert_eq!(hint(&mut s), "SUG=[o mod vendor]", "the ghost hints the engine's winner");
    // The dump above abandons the line, so the word is typed again.
    s.send("g");
    let mark = s.seen_len();
    s.write(TAB);
    s.settle();
    let var_re = regex::Regex::new(r"^[A-Za-z_][A-Za-z_0-9]*=").unwrap();
    let ansi = ansi_re();
    let stray: Vec<String> = s.raw()[mark..]
        .replace('\r', "\n")
        .lines()
        .map(|l| ansi.replace_all(l, "").trim().to_string())
        .filter(|l| var_re.is_match(l))
        .collect();
    assert!(stray.is_empty(), "the Tab press echoes no variable assignment");
    let m = s.menu(false);
    // Whole learned lines are the rows here, and menu_entries' space split
    // would tear them into words; the rows are asserted as the text the user
    // reads, in the order it is painted.
    let at: Vec<i64> = learned
        .iter()
        .map(|row| m.drawn.find(row).map(|p| p as i64).unwrap_or(-1))
        .collect();
    let mut sorted_at = at.clone();
    sorted_at.sort_unstable();
    assert!(
        at.iter().all(|&a| a >= 0) && at == sorted_at,
        "Tab opens with the learned lines, ranked like the panel"
    );
    assert!(
        m.drawn.split("\\n").filter(|e| !e.is_empty()).any(|e| !learned.contains(&e)),
        "the installed names still follow"
    );
    assert_eq!(m.line, "g", "the line is untouched while the menu stands");
    assert!(s.noise().is_empty(), "no noise from the first-word menu");
    // The selection is armed on an opened menu; Enter takes the row and writes
    // the whole line, the panel's promise.
    s.write(ENTER);
    s.settle();
    assert_eq!(s.line(), "go mod vendor", "Enter takes the whole line");
    s.write(CTRL_U);
    // The same net at the source, where the screen cannot blur it: the
    // function's stdout, captured, must be empty. Any future typeset echo
    // inside it lands in this substitution verbatim.
    s.run("tai_stray=$(_tai_menu_first_lines g); print -r -- \"STRAY[$tai_stray]\"");
    assert!(s.raw().contains("STRAY[]"), "_tai_menu_first_lines prints nothing");
    s.close();

    // bash first-word completion
    let mut s = Session::new_with("bash", &[("TAI_COMPLETE_ALL", "1")], true);
    s.run(&format!("cd {}", menu_dir().display()));

    let offered = |s: &mut Session, line: &str| -> Vec<String> {
        let reply = reply_path();
        let _ = fs::remove_file(&reply);
        s.run(&format!(
            "COMP_LINE={}; COMP_POINT={}; COMPREPLY=(); _tai_complete; \
             printf \"%s\\n\" \"${{#COMPREPLY[@]}}\" \"${{COMPREPLY[@]}}\" > {}",
            zq(line),
            line.len(),
            reply.display()
        ));
        s.wait_file(&reply, &format!("the completion list for {line:?}"));
        let got: Vec<String> = fs::read_to_string(&reply)
            .unwrap_or_default()
            .lines()
            .map(|l| l.to_string())
            .collect();
        let counted = got
            .first()
            .map(|l| !l.is_empty() && l.chars().all(|c| c.is_ascii_digit()))
            .unwrap_or(false);
        if counted {
            got[1..].to_vec()
        } else {
            got
        }
    };

    let got = offered(&mut s, "g");
    assert_eq!(
        &got[..learned.len().min(got.len())],
        &own(&learned)[..],
        "bash offers the learned lines first, ranked"
    );
    assert!(got.len() > learned.len(), "and the installed names after");
    let got = offered(&mut s, "go");
    assert_eq!(
        &got[..learned.len().min(got.len())],
        &own(&learned)[..],
        "a complete word keeps its lines first"
    );
    assert!(s.noise().is_empty(), "no noise from the bash listing");
    s.close();
    write_index(None, None);
    check_fixture_intact("the run");
}

/// A word the shell would refuse to compile is a quiet no-op, not an error.
///
/// `_tai_globable` exists because a word can be a pattern the shell refuses —
/// `foo[bar` is an unclosed class — and the report that found it was Tab
/// printing `bad pattern: foo[bar*(N)` from inside the widget. The guard sits
/// on the ghost's path and on the menu's, and this pins the menu's half, in a
/// directory where nothing else could answer either: no menu, the key handed
/// to zsh's own completion, and nothing on the terminal but the prompt.
#[test]
fn glob_words_are_not_patterns() {
    let _guard = LOCK.lock().unwrap_or_else(|e| e.into_inner());
    setup();
    make_fixture_runnable();

    let mut s = Session::new("zsh");
    s.run(&format!("cd {}", empty_dir().display()));
    for word in ["cat foo[bar", "cat foo(bar", "cat foo<bar"] {
        s.send(word);
        s.write(TAB);
        s.settle();
        let m = s.menu(false);
        assert_eq!((m.entries, m.idx), (0, 0), "{word:?} opens no menu");
        s.write(CTRL_U);
    }
    assert!(s.noise().is_empty(), "and no word became a pattern error");
    s.close();
    check_fixture_intact("the run");
}

/// Tab on an empty line answers with the directory, not with the history.
///
/// The report: Tab on an empty prompt filled the first rows of the menu with
/// whichever entries the recency of a typo had put at the top, out of
/// thousands — the menu's learned source looks the index up by the first word,
/// and an empty word is every key in it.
///
/// An empty line asks nothing, so no ranking of the whole history can be the
/// answer. Two sources of this plugin had already decided exactly that, and
/// the menu now agrees with them. What Tab answers with is the one listing an
/// empty line has that is not arbitrary — the current directory.
#[test]
fn empty_line_menu() {
    let _guard = LOCK.lock().unwrap_or_else(|e| e.into_inner());
    setup();
    make_fixture_runnable();

    let fixture = ["go mod vendor", "go mod download", "godot .", "got log", "git status", "y", "n", ")", "ca"];
    let learned = ["go mod vendor", "go mod download", "godot .", "got log", "git status"];
    let junk = ["y", "n", ")", "ca"];
    write_index(Some(&fixture), None);

    // zsh empty-line menu
    let mut s = Session::new("zsh");
    s.run(&format!("cd {}", menu_dir().display()));
    s.write(TAB);
    s.settle();
    let m = s.menu(false);
    assert!(
        m.entries > 0 && m.drawn.contains("tzz_dir/"),
        "the empty line's menu is the directory listing"
    );
    assert_eq!(m.line, "", "the line it stood on is still empty");
    let standing: Vec<&str> = learned.iter().copied().filter(|r| m.drawn.contains(r)).collect();
    assert!(standing.is_empty(), "no learned line stands in it");
    let entries = menu_entries(&m.drawn);
    let junky: Vec<&String> = entries.iter().filter(|e| junk.contains(&e.as_str())).collect();
    assert!(junky.is_empty(), "no one-key entry from the history either");
    assert!(s.noise().is_empty(), "no noise from the empty-line menu");
    s.close();

    // bash empty-line completion
    let mut s = Session::new("bash");
    s.run(&format!("cd {}", menu_dir().display()));
    let reply = reply_path();
    let _ = fs::remove_file(&reply);
    s.run(&format!(
        "COMP_LINE=\"\"; COMP_POINT=0; COMPREPLY=(); _tai_complete; \
         printf \"%s\\n\" \"${{#COMPREPLY[@]}}\" \"${{COMPREPLY[@]}}\" > {}",
        reply.display()
    ));
    s.wait_file(&reply, "the empty-line completion list");
    let got: Vec<String> = fs::read_to_string(&reply)
        .unwrap_or_default()
        .lines()
        .map(|l| l.to_string())
        .collect();
    let counted = got
        .first()
        .map(|l| !l.is_empty() && l.chars().all(|c| c.is_ascii_digit()))
        .unwrap_or(false);
    let got = if counted { got[1..].to_vec() } else { got };
    assert!(got.is_empty(), "bash declines an empty line outright");
    s.close();
    write_index(None, None);
    check_fixture_intact("the run");
}

/// A key that holds only the word itself must not shadow its siblings.
///
/// The reported world: `openc` sat in the index — Enters on a half-typed line
/// had recorded it — and the ghost for `openc` answered nothing at all, while
/// `ope` and `op` still hinted `opencode`. Why: the exact-key hit in
/// _tai_first_values returned the bare word and never reached the keys that
/// begin with the same letters, and the bare word extends nothing. The hit now
/// peels word-equal heads and falls through to the sibling scan — in both
/// shells, the two implementations of the lookup being where the drift would
/// start.
#[test]
fn junk_first_word_key() {
    let _guard = LOCK.lock().unwrap_or_else(|e| e.into_inner());
    setup();
    make_fixture_runnable();

    let fixture = ["openc", "opencode", "opencode web", "git status"];
    write_index(Some(&fixture), None);

    // zsh ghost past a junk first-word key
    let mut g = Ghosts::new();
    assert_eq!(g.of("openc"), "ode", "the ghost for the junk word comes from its sibling");
    assert_eq!(g.of("opencode"), " web", "the full word still hints what follows it");
    g.close();

    // zsh menu past a junk first-word key
    let mut s = Session::new("zsh");
    s.send("openc");
    s.write(TAB);
    s.settle();
    let m = s.menu(false);
    assert!(m.drawn.contains("opencode"), "the menu lists the sibling's lines");
    let entries = menu_entries(&m.drawn);
    let junk: Vec<&String> = entries.iter().filter(|e| *e == "openc").collect();
    assert!(junk.is_empty(), "the junk word itself is not an entry");
    assert_eq!(m.line, "openc", "the line is untouched while the menu stands");
    assert!(s.noise().is_empty(), "no noise from the menu");
    s.close();

    // bash lookup past a junk first-word key
    let mut s = Session::new("bash");
    let out = PathBuf::from("/tmp/tai/tai_lookup_out.txt");
    s.run(&format!(
        "_tai_first_values openc; printf \"%s|%s\\n\" \"$_TAI_VALUES\" \"$_TAI_VALUES_ONE\" > {}",
        out.display()
    ));
    s.wait_file(&out, "the lookup answer for openc");
    let text = fs::read_to_string(&out).unwrap_or_default();
    let (vals, one) = text.trim_end_matches('\n').split_once('|').unwrap();
    assert_eq!(
        vals.split_whitespace().collect::<Vec<_>>(),
        vec!["openc", "opencode"],
        "the junk key falls through to its siblings"
    );
    assert_eq!(one, "0", "one head per key, so the ranking loop runs");
    s.run(&format!(
        "_tai_first_values opencode; printf \"%s|%s\\n\" \"$_TAI_VALUES\" \"$_TAI_VALUES_ONE\" > {}",
        out.display()
    ));
    s.wait_file(&out, "the lookup answer for opencode");
    let text = fs::read_to_string(&out).unwrap_or_default();
    let (vals, one) = text.trim_end_matches('\n').split_once('|').unwrap();
    assert_eq!(vals, "opencode web", "the full word keeps its own extension");
    assert_eq!(one, "1", "and answers from its own list");
    // The completion list a Tab would offer, through the real entry point: the
    // learned lines first, the sibling's among them.
    let reply = reply_path();
    let _ = fs::remove_file(&reply);
    s.run(&format!(
        "COMP_LINE=\"openc\"; COMP_POINT=5; COMPREPLY=(); _tai_complete; \
         printf \"%s\\n\" \"${{#COMPREPLY[@]}}\" \"${{COMPREPLY[@]}}\" > {}",
        reply.display()
    ));
    s.wait_file(&reply, "the completion list for openc");
    let got: Vec<String> = fs::read_to_string(&reply)
        .unwrap_or_default()
        .lines()
        .map(|l| l.to_string())
        .collect();
    let counted = got
        .first()
        .map(|l| !l.is_empty() && l.chars().all(|c| c.is_ascii_digit()))
        .unwrap_or(false);
    let got = if counted { got[1..].to_vec() } else { got };
    // The sibling's bare line is one word, so it carries the space the commit
    // rule writes; the `opencode web` line is whole and does not.
    assert!(got.iter().any(|l| l == "opencode "), "Tab lists the sibling's lines");
    assert!(s.noise().is_empty(), "no noise from the bash lookups");
    s.close();
    write_index(None, None);
    check_fixture_intact("the run");
}

/// Choosing a completion that leaves the line one bare word writes the space.
///
/// The report: choosing `nano` out of the menu put `nano` in the line, and the
/// user typed the space between a command and its arguments by hand — once per
/// line, every time. Every shell's own completion writes that space, so the
/// menu and tai's bash completion write it too.
///
/// Three boundaries keep it honest: a directory's slash is the path still
/// being typed; a whole learned line is the line the user ran, and a space
/// after it would ride the next Enter into the history as a row of its own;
/// and a name that holds a space is already more than one shell word, and the
/// quoting carries it.
#[test]
fn menu_commit_writes_the_space() {
    let _guard = LOCK.lock().unwrap_or_else(|e| e.into_inner());
    setup();
    make_fixture_runnable();

    // zsh menu commit writes the space
    let mut s = Session::new("zsh");
    s.run(&format!("cd {}", menu_dir().display()));
    s.run(&format!(
        "export PATH={}:{}:$PATH",
        menu_dir().display(),
        menu_bin().display()
    ));
    // The user's case: one word on the line, one completion for it, and the
    // key that takes it — `far` has exactly one answer anywhere, and it is a
    // command the menu reaches only through PATH.
    s.send("far");
    s.write(TAB);
    s.settle();
    assert_eq!(
        s.line(),
        format!("{MENU_FAR} "),
        "zsh a lone command completion takes the space with it"
    );
    s.write(CTRL_U);
    // An armed menu, Enter on a command name.
    s.send("tzz");
    s.write("\t\t");
    s.settle();
    s.write(ENTER);
    s.settle();
    assert_eq!(s.line(), "tzz_b ", "zsh Enter on a command name writes the space");
    s.write(CTRL_U);
    // A file: the same rule, because the next argument is the next word.
    s.send("cat tzz_s");
    s.write(TAB);
    s.settle();
    assert_eq!(s.line(), "cat tzz_solo ", "zsh a lone file completion takes the space with it");
    s.write(CTRL_U);
    // The boundary: a directory offered with its slash, and no space after it
    // — the slash is where the path keeps going.
    s.send("cat tzz_di");
    s.write(TAB);
    s.settle();
    assert_eq!(
        s.line(),
        "cat tzz_dir/",
        "zsh a directory's slash is written, and no space after it"
    );
    s.write(CTRL_U);
    assert!(s.noise().is_empty(), "no noise from the menu space");
    s.close();

    // bash completion writes the space
    let mut s = Session::new_with("bash", &[("TAI_COMPLETE_ALL", "1")], true);
    s.run(&format!("cd {}", menu_dir().display()));
    // A file: the entry carries the space, readline (told `nospace`) writes
    // the match verbatim, and the line is ready for the next word.
    s.send("cat tzz_s");
    s.write(TAB);
    s.settle();
    assert_eq!(s.line(), "cat tzz_solo ", "bash a lone file completion takes the space with it");
    s.write(CTRL_U);
    // The boundary: a directory is offered with its slash, and no space.
    // (`cd tzz` is the learned word's own case — the word and the directory
    // are one completion said twice; `stem` has no learned line behind it, so
    // the disk's slash form is the only answer.)
    s.send("cd stem");
    s.write(TAB);
    s.settle();
    assert_eq!(
        s.line(),
        "cd stemroot/",
        "bash a directory's slash is written, and no space after it"
    );
    s.write(CTRL_U);
    // A whole learned line is offered whole — the match carries no trailing
    // space, because for a first word the completion and the line are one
    // span. The installed name beside it carries the space the rule writes.
    let reply = reply_path();
    let _ = fs::remove_file(&reply);
    s.run(&format!(
        "COMP_LINE=\"g\"; COMP_POINT=1; COMPREPLY=(); _tai_complete; \
         printf \"%s\\n\" \"${{COMPREPLY[@]}}\" > {}",
        reply.display()
    ));
    s.wait_file(&reply, "the first-word completion list");
    let got: Vec<String> = fs::read_to_string(&reply)
        .unwrap_or_default()
        .lines()
        .filter(|l| !l.is_empty())
        .map(|l| l.to_string())
        .collect();
    assert_eq!(
        got.iter().filter(|l| *l == "git status").collect::<Vec<_>>(),
        vec!["git status"],
        "bash a whole learned line carries no trailing space"
    );
    assert_eq!(
        got.iter().filter(|l| *l == "git ").collect::<Vec<_>>(),
        vec!["git "],
        "bash an installed name beside it carries the space"
    );
    assert!(s.noise().is_empty(), "no noise from the bash space");
    s.close();
    check_fixture_intact("the run");
}
