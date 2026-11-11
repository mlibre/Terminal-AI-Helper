//! What the plugin puts on the screen: hints, the ghost text, and menu styling.
//!
//! These are the assertions that are worthless without an emulated screen — the
//! characters can all be right while the selection is painted in nothing at all —
//! so they read `Screen`, not the plugin's arrays.
//!
//! Ported from `tests/test_plugins_draw.py`.

mod support;
use support::*;

use std::path::Path;

/// Tests run in parallel and share /tmp/tai, so they are serialized.
static LOCK: std::sync::Mutex<()> = std::sync::Mutex::new(());

/// The competing zsh plugins the load-order test looks for (plugin_env.py).
const SYNTAX_HIGHLIGHTING: &str =
    "/usr/share/zsh/plugins/zsh-syntax-highlighting/zsh-syntax-highlighting.zsh";
const ZSH_AUTOSUGGESTIONS: &str =
    "/usr/share/zsh/plugins/zsh-autosuggestions/zsh-autosuggestions.zsh";

/// The `SUG=[...]` head of the dump widget's second line — what Python's
/// `s.suggestion().split(" PD=")[0]` reads.
fn sug_head(s: &mut Session) -> String {
    let got = s.suggestion();
    got.split(" PD=").next().unwrap_or(&got).to_string()
}

/// Re-assert the executable bits the fixture is written with.
///
/// The menu's PATH section takes command names from `$commands` and keeps only
/// the ones that are executable, so `tzz_other` and friends must carry their
/// 0755 or the menu loses entries and every width assertion is measured
/// against the wrong cell. The harness's `write_script` once handed
/// `String::as_ptr` — not NUL-terminated — to `chmod`, and the bits were set
/// on whichever path the following bytes named; the harness sets its modes
/// through the checked std API now, and this fixer remains as a cheap
/// re-assertion on the files whose bits the assertions lean on.
fn exec_bit(path: &Path) {
    use std::os::unix::fs::PermissionsExt;
    let mut perm = std::fs::metadata(path).unwrap().permissions();
    perm.set_mode(0o755);
    std::fs::set_permissions(path, perm).unwrap();
}

fn exec_bits() {
    for name in ["tzz_a", "tzz_b", "tzz_c", "tzz_solo"] {
        exec_bit(&menu_dir().join(name));
    }
    for name in ["tzz_other", MENU_FAR] {
        exec_bit(&menu_bin().join(name));
    }
    exec_bit(&editor_path());
}

fn setup_fix() {
    setup();
    exec_bits();
}

/// A zsh session, tolerating a transient shell resolution.
///
/// The harness once resolved the shell through `access(2)` handed a
/// non-NUL-terminated pointer, and a session start could then fail with "no
/// zsh on this machine" even though `tests/bin/zsh` was right there; the
/// harness resolves through `std::fs::metadata` now, so the failure is gone —
/// the retry stays as cheap insurance against whatever else a loaded runner
/// does to a stat. `Session::new` panics before it spawns anything, so a
/// failed attempt leaks nothing.
fn zsh_session() -> Session {
    let prev = std::panic::take_hook();
    std::panic::set_hook(Box::new(|_| {}));
    let s = (0..200)
        .find_map(|_| std::panic::catch_unwind(|| Session::new("zsh")).ok());
    std::panic::set_hook(prev);
    s.unwrap_or_else(|| panic!("no zsh on this machine; the pty suite cannot run"))
}

fn zsh_session_with(env_extra: &[(&str, &str)], source: bool) -> Session {
    let prev = std::panic::take_hook();
    std::panic::set_hook(Box::new(|_| {}));
    let s = (0..200).find_map(|_| {
        std::panic::catch_unwind(|| Session::new_with("zsh", env_extra, source)).ok()
    });
    std::panic::set_hook(prev);
    s.unwrap_or_else(|| panic!("no zsh on this machine; the pty suite cannot run"))
}

#[test]
fn test_zsh_foreign_hint() {
    let _guard = LOCK.lock().unwrap_or_else(|e| e.into_inner());
    setup_fix();
    // A hint another plugin drew is a hint, and the arrow still takes it.
    //
    // The reported case: `nan` with `o .zshrc` on screen, and Tab opening a list
    // over it instead of completing. The hint came from zsh-autosuggestions, which
    // reads `~/.zsh_history` directly, while tai had no `nan` candidate at all —
    // `_TAI_FIRST` is keyed by the whole first word, so a half-typed command name
    // is not a key tai can answer.
    //
    // So tai's own variable was empty while a hint was plainly visible, and the key
    // that takes hints asked the variable instead of the screen. Two facts make this
    // the normal case rather than a corner: the two plugins share one POSTDISPLAY,
    // and autosuggestions fetches *asynchronously*, so it writes the slot after
    // tai's redraw. Load order cannot fix that — it decides who writes first, not
    // who writes last — and the arrow is worse off still, because replacing the
    // `forward-char` widget is what makes the arrow work in both cursor-key modes
    // and also replaces the wrapping that plugin put around that widget.
    //
    // The other plugin is simulated by writing POSTDISPLAY from a widget, which is
    // precisely what that plugin does to it. Loading zsh-autosuggestions instead
    // would make the assertion depend on a distro package being installed, and a
    // test that skips itself on the next machine is not a test.
    let mut s = zsh_session();
    s.run("tai_test_foreign() { POSTDISPLAY='o .zshrc'; }; \
           zle -N tai_test_foreign; bindkey '^[[27~' tai_test_foreign");

    macro_rules! typed_and_hinted {
        ($prefix:expr) => {{
            s.send($prefix);
            s.write("\x1b[27~"); // the other plugin draws its hint
            s.settle();
        }};
    }

    // The hint is on screen and tai did not draw it: that is the whole situation.
    typed_and_hinted!("nan");
    assert_eq!(sug_head(&mut s), "SUG=[]", "and tai has no answer of its own");

    typed_and_hinted!("nan");
    s.write(RIGHT);
    s.settle();
    assert_eq!(s.line(), "nano .zshrc", "the arrow takes it");
    s.write("\x15");

    // And Tab, which no longer answers the question the hint answers, does
    // not swallow it: the list over a foreign hint is the rare case, but the
    // list is what Tab asks for, whatever drew the text it overlaps. A prefix
    // nothing completes, so expand-or-complete has nothing to write either.
    typed_and_hinted!(NOT_INSTALLED);
    s.write(TAB);
    s.settle();
    assert_eq!(s.line(), NOT_INSTALLED, "Tab does not take it");
    s.write("\x15");

    // The other two keys that take hints, for the same reason.
    typed_and_hinted!("nan");
    s.write(RIGHT);
    s.settle();
    assert_eq!(s.line(), "nano .zshrc", "the arrow takes it");
    s.write("\x15");

    typed_and_hinted!("nan");
    s.write(ALT_F);
    s.settle();
    // One *word* of `o .zshrc` is ` o`, so the line becomes `nano` and the rest of
    // the foreign hint is still there — which is the whole point of this key.
    assert_eq!(s.line(), "nano", "and Alt-F takes one word of it");
    s.write("\x15");

    // With no foreign hint, a line tai cannot answer still lists rather than
    // inventing one: the fix reads the screen, and the screen is empty.
    s.send(NOT_INSTALLED);
    assert_eq!(
        sug_head(&mut s),
        "SUG=[]",
        "and with nothing on screen, nothing is invented"
    );
    s.send(NOT_INSTALLED);
    s.write(TAB);
    s.settle();
    let m = s.menu(false);
    assert_eq!(m.line, NOT_INSTALLED, "a line with no hint still lists (line)");
    assert_eq!(m.entries, 0, "a line with no hint still lists (size)");
    s.write("\x15");
    assert!(s.noise().is_empty(), "no noise");
    s.close();
    check_fixture_intact("test_zsh_foreign_hint");
}

#[test]
fn test_zsh_tab_and_list() {
    let _guard = LOCK.lock().unwrap_or_else(|e| e.into_inner());
    setup_fix();
    // Tab cycles the completions. The right arrow takes the hint. Ctrl-T lists.
    //
    // Tab used to take the hint, which is the arrow's question: reaching for a
    // readable hint and having Tab swallow it, while the completions went unshown.
    // So Tab's question is the word it is on — files, folders, options — and the
    // hint belongs to `→` alone.
    //
    // One completion is still the key's: unambiguous, and the hint was already
    // saying it, so `docker` completes rather than dangling a list of one.
    //
    // The `--help` case is the exception and it is deliberate: `true ` has never
    // been run, so `true --help` is tai guessing at what was meant from no evidence
    // at all, and the file listing is the answer to the question actually typed. It
    // stays a hint — `→` still takes it — and Tab still lists.
    let mut s = zsh_session();
    s.run(&format!("cd {}", menu_dir().display()));
    s.run(&format!(
        "export PATH={}:{}:$PATH",
        menu_dir().display(),
        menu_bin().display()
    ));

    // The hint is taken by the arrow, not by Tab. Tab on a word with several
    // completions opens the list over the hint and leaves the line alone.
    s.send("git ");
    assert_eq!(sug_head(&mut s), "SUG=[pull --rebase]", "a hint is on screen");
    s.send("git ");
    s.write(TAB);
    s.settle();
    let m = s.menu(false);
    let entries = menu_entries(&m.drawn);
    assert_eq!(
        &entries[..2],
        &["pull", "status"][..],
        "Tab opens the list rather than taking the hint"
    );
    assert_eq!(m.line, "git ", "and leaves the line alone");
    s.write("\x15");

    // The one completion the hint was already showing: Tab writes it, because
    // a list of one is a question that has answered itself.
    s.send("cat tzz_dir");
    s.write(TAB);
    s.settle();
    let m = s.menu(true);
    assert_eq!(
        (m.line.as_str(), m.entries),
        ("cat tzz_dir/", 0),
        "one completion is taken, not listed"
    );
    s.send("docker");
    s.write(RIGHT);
    s.settle();
    let m = s.menu(true);
    assert_eq!(
        (m.line.as_str(), m.entries),
        ("docker ps", 0),
        "and the arrow takes the hint"
    );

    // The same key with no hint is the list, which is what every shell does
    // for the word it is on — readline's listing writes the shared head `tzz_`
    // into the line, and so does the menu: the shells agree on the one thing
    // every entry already says.
    // The menu's PATH section keeps only executable names, so the fixture
    // scripts must still carry their bit here; re-apply it in case another
    // suite sharing /tmp/tai rewrote the fixture in the meantime.
    exec_bits();
    s.send("tzz");
    assert_eq!(
        sug_head(&mut s),
        "SUG=[]",
        "with no hint there is nothing to take"
    );
    s.send("tzz");
    s.write(TAB);
    s.settle();
    let m = s.menu(false);
    assert_eq!(
        menu_entries(&m.drawn),
        MENU_ENTRIES,
        "no hint means Tab lists"
    );
    assert_eq!(m.line, "tzz_", "leaving the line at the shared head");
    s.write("\x15");

    // Ctrl-Space lists *with* a hint on screen. This is the key the change
    // needed: browsing the candidates has to be possible without first deleting
    // the line. Ctrl-T is the same widget, kept because terminals disagree about
    // what to send for Ctrl-Space — so both are asserted, since a terminal that
    // sends neither is a user with no way to see the list.
    s.send("git ");
    assert_eq!(sug_head(&mut s), "SUG=[pull --rebase]", "a hint is on screen");
    s.send("git ");
    s.write(CTRL_SPACE);
    s.settle();
    let m = s.menu(false);
    let entries = menu_entries(&m.drawn);
    assert_eq!(
        &entries[..2],
        &["pull", "status"][..],
        "Ctrl-Space lists anyway"
    );
    assert_eq!(m.line, "git ", "and still does not touch the line");
    assert_eq!(
        selected_entry(&screen_of(&s)),
        "pull",
        "with the first entry selected"
    );
    s.write(CTRL_SPACE);
    s.settle();
    assert_eq!(
        selected_entry(&screen_of(&s)),
        "status",
        "and Ctrl-Space again moves the selection"
    );
    assert_eq!(
        s.menu(false).line,
        "git ",
        "still without touching the line"
    );
    s.write("\x15");
    s.send("git ");
    s.write(LIST);
    s.settle();
    let entries = menu_entries(&s.menu(false).drawn);
    assert_eq!(
        &entries[..2],
        &["pull", "status"][..],
        "Ctrl-T is the same list"
    );
    s.write("\x15");

    // One word of the hint, on both keys. `git ` hints `git pull --rebase`, so
    // one word is `git pull` and the rest is still there to type — the behaviour
    // Alt-F has always had, on a key the hand already makes for "next word".
    for (label, keys) in [("Alt-F", ALT_F), ("Ctrl-Right", CTRL_RIGHT)] {
        s.send("git ");
        s.write(keys);
        s.settle();
        assert_eq!(s.line(), "git pull", "{label} takes one word of the hint");
        s.write("\x15");
    }
    // And neither invents a word: with nothing on screen the key falls through to
    // `forward-word`, which moves the cursor and rewrites nothing. `zzzz` is in no
    // fixture entry and is not installed, so it has no hint and no candidate —
    // `git pull` would not do, because it does have one.
    s.send(NOT_INSTALLED);
    assert_eq!(sug_head(&mut s), "SUG=[]", "and there is no hint to take");
    s.send(NOT_INSTALLED);
    s.write(CTRL_A);
    s.write(CTRL_RIGHT);
    s.settle();
    assert_eq!(
        s.line(),
        NOT_INSTALLED,
        "with no hint the key rewrites nothing"
    );
    s.write("\x15");

    // The `--help` exception. `true` is installed and appears in no fixture
    // entry, so this is the "a tool you have never run" path with a real binary.
    s.send(&format!("{INSTALLED} "));
    assert_eq!(
        sug_head(&mut s),
        "SUG=[--help]",
        "the fallback is still a hint"
    );
    s.send(&format!("{INSTALLED} "));
    s.write(TAB);
    s.settle();
    let m = s.menu(false);
    assert!(m.entries > 1, "but Tab still lists for it");
    assert!(
        menu_entries(&m.drawn).iter().any(|e| e == "--help"),
        "with --help in the list"
    );
    assert_eq!(m.line, format!("{INSTALLED} "), "and the line untouched");
    s.write("\x15");
    // The arrow still takes it, so the hint is not merely decoration.
    s.send(&format!("{INSTALLED} "));
    s.write(RIGHT);
    s.settle();
    assert_eq!(
        s.line(),
        format!("{INSTALLED} --help"),
        "the arrow takes the --help hint"
    );
    s.write("\x15");

    assert!(s.noise().is_empty(), "no noise from either key");
    s.close();
    check_fixture_intact("test_zsh_tab_and_list");
}

#[test]
fn test_zsh_menu_colour() {
    let _guard = LOCK.lock().unwrap_or_else(|e| e.into_inner());
    setup_fix();
    // The menu is drawn in colour: a selected cell reversed, a directory blue.
    //
    // Both are read off the screen rather than out of the plugin, because both are
    // drawn by ZLE: the plugin asks for a region, the terminal is told, and the
    // only evidence that it listened is the bytes on the wire. The assertions in
    // test_zsh_menu already lean on the selection being painted — selected_entry
    // finds an entry by its reverse video and by nothing else — so what is left to
    // cover here is the colours themselves, and the one way the offset arithmetic
    // can be wrong while every character is still in the right place.
    let mut s = zsh_session();
    s.run(&format!("cd {}", menu_dir().display()));
    s.run(&format!(
        "export PATH={}:{}:$PATH",
        menu_dir().display(),
        menu_bin().display()
    ));
    // The menu's PATH section keeps only executable names and the layout is
    // padded to the widest of them, so the fixture scripts must still carry
    // their bit here; re-apply it in case another suite sharing /tmp/tai
    // rewrote the fixture in the meantime.
    exec_bits();
    s.send("tzz");
    s.write(TAB);
    s.settle();
    let scr = screen_of(&s);
    // The first entry is selected, so the block is its gutter, its name and the
    // column's padding — one run, which is what a block is.
    assert_eq!(
        style_of(&scr, "  tzz_a"),
        Ok(SELECTED_ATTR),
        "the selected cell is one block in reverse video"
    );
    // Exactly the cell and no more. A region one character too long looks the
    // same — a space is a space — but it reaches into the gap, and off the end
    // of a row it reaches into the next one. Counting the block is the only
    // check that can tell the two apart.
    //
    // Port note: Python counted the reversed columns in `scr.grid[row]` directly;
    // the Rust Screen keeps its grid private, so the same fact is read off the
    // styled runs. The block covers the two gutter columns plus the name padded
    // to the widest entry (cell_width = max(len) + 2 = 11 columns), so the run
    // after it must begin with the two gap columns plus the next cell's two
    // gutter columns — four plain spaces — before `tzz_b`. A region one column
    // too long would eat the first gap column and leave three.
    let row = scr
        .lines()
        .iter()
        .position(|l| l.contains("tzz_a"))
        .expect("the menu row with tzz_a is on screen");
    let runs = scr.styled(row);
    assert_eq!(
        runs[0],
        ("  tzz_a".to_string(), SELECTED_ATTR),
        "and the block starts at column 0 with the selected cell"
    );
    assert!(
        runs.len() > 1 && runs[1].0.starts_with("    tzz_b"),
        "and the block is the cell, the gap, and nothing past them: \
         the run after it starts with the two gap columns and the next gutter ({:?})",
        runs.get(1).map(|r| &r.0)
    );
    // The directory's name is blue and the slash that says it is a directory is
    // not: a blue slash as well would make the entry read as a word.
    assert_eq!(
        style_of(&scr, "tzz_dir"),
        Ok((DIR_FG, 0, true, false)),
        "a directory's name is drawn in the directory colour"
    );
    assert_eq!(style_of(&scr, "/"), Ok((0, 0, false, false)), "and its slash is left alone");
    s.write(&TAB.repeat(MENU_ENTRIES.len() - 1)); // on to the directory
    s.settle();
    let scr = screen_of(&s);
    // One block and not a blue name inside one: two regions over the same
    // characters are combined by ZLE rather than replacing each other, so the
    // directory's colour is not asked for at all on the entry that is selected.
    assert_eq!(
        style_of(&scr, "  tzz_dir/"),
        Ok(SELECTED_ATTR),
        "a selected directory is one block too"
    );
    assert_eq!(
        style_of(&scr, "tzz_dir"),
        Err(NOT_DRAWN),
        "with no directory colour left inside it"
    );
    s.write("\x15");

    // Both colours are settings, read when the plugin is sourced, so a palette
    // without blue — or a terminal where reverse video is unwelcome — has an
    // answer that does not need a patched plugin. Re-sourced in the same shell,
    // because that is what a user does after editing the file.
    s.run(&format!(
        "_TAI_MENU_STYLE=fg=yellow; _TAI_DIR_STYLE=fg=magenta; source {}",
        plugin_path("zsh").display()
    ));
    exec_bits();
    s.send("tzz");
    s.write(TAB);
    s.settle();
    let scr = screen_of(&s);
    assert_eq!(
        style_of(&scr, "  tzz_a"),
        Ok((33, 0, false, false)),
        "the selection colour is a setting"
    );
    assert_eq!(
        style_of(&scr, "tzz_dir"),
        Ok((35, 0, false, false)),
        "and so is the directory colour"
    );
    s.write("\x15");
    assert!(s.noise().is_empty(), "no noise");
    s.close();
    check_fixture_intact("test_zsh_menu_colour");
}

#[test]
fn test_zsh_load_order() {
    let _guard = LOCK.lock().unwrap_or_else(|e| e.into_inner());
    setup_fix();
    // tai keeps the ghost text when other plugins drive POSTDISPLAY too.
    //
    // POSTDISPLAY is a single slot with no namespacing. zsh-autosuggestions and
    // zsh-syntax-highlighting both write it, and distro configs (Manjaro's
    // manjaro-zsh-prompt loads autosuggestions) load them *before* a
    // user-installed plugin. So the only safe order is: recompute POSTDISPLAY on
    // every line-pre-redraw, and be the last writer. This is the configuration
    // that broke in the field, so it gets a test rather than a comment.
    let available: Vec<&str> = [SYNTAX_HIGHLIGHTING, ZSH_AUTOSUGGESTIONS]
        .into_iter()
        .filter(|p| Path::new(p).exists())
        .collect();
    if available.is_empty() {
        eprintln!("zsh POSTDISPLAY load order — skipped, no zsh plugins installed");
        return;
    }
    let mut s = zsh_session_with(&[], false);
    // The trailing newline matters: without it the line is typed but never run,
    // and every later send concatenates onto it.
    for path in &available {
        s.send(&format!("source {path} 2>/dev/null\n"));
    }
    // Now source tai, exactly as install.sh appends it to .zshrc: last.
    s.send(&format!("source {}\n", plugin_path("zsh").display()));
    s.send("docker");
    let got = s.suggestion();
    // Assert on what tai controls: it still computes the tail and styles it, and
    // it never puts escape bytes in POSTDISPLAY. Whether the text survives to
    // the screen is not tai's to decide — a competing plugin may clear
    // POSTDISPLAY in the same redraw pass, which is why the documented fix is
    // to unhook it rather than to try to out-race it.
    assert_eq!(
        got.split(" PD=").next().unwrap_or(&got),
        "SUG=[ ps]",
        "ghost tail computed with competing writers"
    );
    assert_eq!(
        got.split(" RH=").last().unwrap_or(&got),
        "[6 9 fg=8,bold]",
        "ghost styled with competing writers"
    );
    assert!(!got.contains('\x1b'), "no escape bytes leak into the hint");
    assert!(s.noise().is_empty(), "no hook errors with plugins loaded");
    s.close();
    check_fixture_intact("test_zsh_load_order");
}

#[test]
fn test_zsh_ghost_styling() {
    let _guard = LOCK.lock().unwrap_or_else(|e| e.into_inner());
    setup_fix();
    // The hint must be styled by ZLE, not by escape bytes in POSTDISPLAY.
    //
    // ZLE writes POSTDISPLAY verbatim, so an embedded \e[2;3;90m reaches the
    // screen as the literal characters "^[[2;3;90m" and the hint is unstyled.
    // region_highlight is the supported way to colour it. Assert the hint text is
    // clean and that a highlight region covers exactly the hint.
    let mut s = zsh_session();
    s.send("docker");
    let got = s.suggestion();
    assert_eq!(
        got.split(" RH=").next().unwrap_or(&got),
        "SUG=[ ps] PD=[ ps]",
        "POSTDISPLAY holds bare hint text"
    );
    let rh = got
        .split(" RH=[")
        .nth(1)
        .unwrap_or_else(|| panic!("no region_highlight in the dump: {got:?}"));
    let rh = rh.strip_suffix(']').unwrap_or(rh);
    let parts: Vec<&str> = rh.split(' ').collect();
    assert!(parts.len() >= 3, "unexpected region_highlight shape: {rh:?}");
    let lo: usize = parts[0].parse().expect("region start is a number");
    let hi: usize = parts[1].parse().expect("region end is a number");
    let style = parts[2];
    // ZLE counts these offsets in characters of the editable line, and the
    // prompt is not part of it: the hint sits exactly at the end of the buffer.
    // (The menu in menu.zsh measures its cells the same way, which is what makes
    // the selection land on the right cell in `test_zsh_menu_colour`.)
    assert_eq!((lo, hi), (6, 6 + 3), "the region covers exactly the hint");
    assert_eq!(style, "fg=8,bold", "and it is styled dim and gray");
    assert!(!got.contains('\x1b'), "no escape bytes in the hint");
    s.close();
    check_fixture_intact("test_zsh_ghost_styling");
}

#[test]
fn test_zsh_menu_on_screen() {
    let _guard = LOCK.lock().unwrap_or_else(|e| e.into_inner());
    setup_fix();
    // The menu goes on a line of its own, and leaves nothing behind.
    //
    // A row laid out to the terminal width and started after the prompt does not
    // fit: the terminal wraps it, the right prompt is written over, and ZLE — which
    // believes the whole post-display is one line — then erases in the wrong place
    // and leaves the pieces of the previous menu on screen. Every other assertion in
    // this file reads the plugin's own words and would have passed through all of
    // that, so this one reads the screen.
    //
    // The prompt here is deliberately long and there is deliberately a right prompt
    // to collide with, because that is the shape the bug showed up in.
    let mut s = zsh_session();
    s.run(&format!("cd {}", menu_dir().display()));
    s.run(&format!(
        "export PATH={}:{}:$PATH",
        menu_dir().display(),
        menu_bin().display()
    ));
    s.run("PS1='%F{cyan}/run/media/mlibre/B/Projects%f %# '");
    s.run("RPROMPT='%F{blue}18:04%f'");
    s.send("tzz");
    s.settle();
    s.write(TAB);
    s.settle();
    let m = s.menu(false);
    let lines = screen_of(&s).lines();
    let start = lines
        .iter()
        .position(|ln| ln.starts_with("/run/media/mlibre/B/Projects % tzz"))
        .expect("the prompt line with the input is on screen");
    assert!(
        lines[start].starts_with("/run/media/mlibre/B/Projects % tzz"),
        "the input line keeps the prompt in front of it"
    );
    assert!(
        lines[start].ends_with("18:04"),
        "and the right prompt, which the menu used to be written over"
    );
    let below: Vec<String> = lines[start + 1..]
        .iter()
        .filter(|ln| !ln.trim().is_empty())
        .cloned()
        .collect();
    let want: Vec<String> = m
        .drawn
        .split("\\n") // the dump widget spells newlines out with ${(V)}
        .filter(|row| !row.trim().is_empty())
        .map(|row| row.trim_end().to_string())
        .collect();
    assert_eq!(
        below, want,
        "the screen shows the menu on the rows below it, and nothing else"
    );
    // Cycle: a menu whose mark moves must not disturb the prompt line.
    s.write(&TAB.repeat(2));
    s.settle();
    let cycled = screen_of(&s).lines();
    assert_eq!(
        cycled[start], lines[start],
        "the prompt line is the same after the mark moves"
    );
    assert!(s.noise().is_empty(), "no noise");
    s.close();
    check_fixture_intact("test_zsh_menu_on_screen");
}
