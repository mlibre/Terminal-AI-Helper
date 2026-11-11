//! Port of `tests/test_plugins_isearch.py` — ^R is the shell's search, and for
//! its every keystroke tai's no-op.
//!
//! The reported hang: the user searches history with ^R and presses ^R again to
//! step to the older match, and on each press the shell visibly stalls — tai
//! "starting to work". The mechanism: tai's ghost rides the `line-pre-redraw`
//! hook, and ZLE fires that hook for the search's redraws too, so every search
//! step paid for a full `_tai_query_do` for a line nobody is editing. The
//! guard in the plugin stands down while the search owns the line, via the
//! isearch hook pair (`zle-isearch-update` / `zle-isearch-exit`) and the
//! widget wrap of `history-incremental-search-backward`, which raises the flag
//! before the builtin runs a single step.
//!
//! These tests instrument the same two points the guard does: every
//! `_tai_update` call is logged with the isearch flag it saw, and every
//! `_tai_query_do` — a miss, real work — is logged with the buffer it answered
//! for. Every read of the log happens after the session is closed, because the
//! log is written from inside the very hooks the keystrokes drive.

mod support;
use support::*;

use std::time::Duration;

static LOCK: std::sync::Mutex<()> = std::sync::Mutex::new(());

/// ^R: zsh's history-incremental-search-backward, and again.
const ISEARCH: &str = "\x12";
// The right arrow that exits the search, keeping the line it found, is the
// harness's `RIGHT` ("\x1b[C") — the same bytes the Python test binds locally.

/// The suite's scratch files. Named per process: the whole pty suite shares
/// /tmp/tai, other targets run against it at the same time, and a second copy
/// of this binary truncating the fixed `tai_isearch_log.txt` mid-run would
/// turn this test's evidence into another session's.
fn log_path() -> String {
    format!("/tmp/tai/tai_isearch_log_{}.txt", std::process::id())
}
/// The wrap's own footprint, read to a file because the terminal interleaves
/// its echoes with anything printed: after the plugin is sourced, the search
/// widget's name must carry tai's wrapper.
fn wrap_value_path() -> String {
    format!("/tmp/tai/tai_isearch_wrap_value_{}.txt", std::process::id())
}
/// This suite's own HISTFILE. The harness's shared one is deleted and rewritten
/// by every other suite's setup(), so a search here could be matching another
/// suite's seeded lines; the seeds below must be the only lines in the list.
fn hist_path() -> String {
    format!("/tmp/tai/tai_isearch_history_{}.txt", std::process::id())
}

/// The history the search looks through. `l` matches `lsof -i` and `s` narrows
/// to `ls /tmp`; the further ^R presses re-search on the same line. `lsof -i`
/// is the discriminator: typed nowhere in this test, it can only reach the
/// buffer as a match found by the search.
const HISTORY_SEED: &[&str] = &[
    "ls -la", "ls -l", "ls /tmp", "lsof -i", "docker ps", "git status", "cd tzz_dir",
];

fn seed_history() {
    std::fs::write(hist_path(), format!("{}\n", HISTORY_SEED.join("\n"))).unwrap();
}

/// A zsh session with this suite's own HISTFILE riding on the defaults.
fn isearch_session() -> Session {
    let hist = hist_path();
    Session::new_with("zsh", &[("HISTFILE", hist.as_str())], true)
}

/// The instruments, installed where the plugin's own hooks are: a log line per
/// redraw hook call, carrying the isearch flag the call ran under, and a log
/// line per query miss, carrying the buffer it worked on. The flag is the
/// plugin's own `_TAI_ISEARCH` — the exact value the guard reads.
fn setup_cmd() -> String {
    format!(
        "functions[_tai_update_orig]=${{functions[_tai_update]}}; \
         _tai_update() {{ print -r -- \"U ${{_TAI_ISEARCH:-0}}\" >> {log}; \
         _tai_update_orig \"$@\" }}; \
         functions[_tai_qdo_orig]=${{functions[_tai_query_do]}}; \
         _tai_query_do() {{ print -r -- \"Q $BUFFER\" >> {log}; \
         _tai_qdo_orig \"$@\" }}; \
         fc -R $HISTFILE; print seeded",
        log = log_path()
    )
}

/// The instruments, and the user's shell: the update hook is replaced with a
/// no-op, so the flag can rise only through the wrap. The exit hook stays live.
fn blind_setup_cmd() -> String {
    format!(
        "functions[_tai_update_orig]=${{functions[_tai_update]}}; \
         _tai_update() {{ print -r -- \"U ${{_TAI_ISEARCH:-0}}\" >> {log}; \
         _tai_update_orig \"$@\" }}; \
         functions[_tai_qdo_orig]=${{functions[_tai_query_do]}}; \
         _tai_query_do() {{ print -r -- \"Q $BUFFER\" >> {log}; \
         _tai_qdo_orig \"$@\" }}; \
         _tai_isearch_update() {{ :; }}; \
         print blind",
        log = log_path()
    )
}

/// The log, split into its two streams: the isearch flags the redraw hook ran
/// under, and the buffers the query misses answered for. Read only after the
/// session is closed.
fn read_log() -> (Vec<String>, Vec<String>) {
    let text = std::fs::read_to_string(log_path()).unwrap();
    let mut hook_flags = Vec::new();
    let mut queried = Vec::new();
    for line in text.lines() {
        if let Some(rest) = line.strip_prefix("U ") {
            hook_flags.push(rest.to_string());
        } else if let Some(rest) = line.strip_prefix("Q ") {
            queried.push(rest.to_string());
        }
    }
    (hook_flags, queried)
}

/// The Python suite skips when there is no zsh (`SHELLS["zsh"]`). The same
/// check here, retried briefly: this sandbox's filesystem has been seen to
/// fail a stat/access on the bundled binary for one whole process while every
/// other process saw it fine, and a silent skip on such a glitch would be a
/// false green.
fn zsh_available() -> bool {
    for attempt in 0..10 {
        if shell_path("zsh").is_some() {
            return true;
        }
        std::thread::sleep(Duration::from_millis(100 * (attempt + 1)));
    }
    false
}

#[test]
fn isearch_costs_nothing() {
    let _guard = LOCK.lock().unwrap_or_else(|e| e.into_inner());
    setup();
    if !zsh_available() {
        // No zsh on this machine; the Python suite skips silently.
        return;
    }
    seed_history();
    let mut s = isearch_session();
    s.run(&setup_cmd());
    s.run(&format!(": > {}", log_path())); // the setup's own typing has queried; start clean
    // The line the user had typed when ^R arrived — left in the buffer, ghost
    // drawn on it, exactly the state the report starts from.
    s.send("ls -l");
    let mark = s.seen_len();
    // The search itself: ^R, two pattern characters, ^R twice to step through
    // the matches — the presses the report hangs on — then the right arrow,
    // which ends the search and leaves the found line on the prompt to edit.
    // The wait after the arrow is one quiet window wider than usual, because
    // the arrow's first byte is the byte meta sequences start with, and a
    // shell that waits to hear more of the chord must be waited out too.
    s.write(ISEARCH);
    s.settle();
    s.write("l");
    s.settle();
    s.write("s");
    s.settle();
    s.write(ISEARCH);
    s.settle();
    s.write(ISEARCH);
    s.settle();
    s.write(RIGHT);
    s.settle_for(Duration::from_millis(500));
    // One deleted character on the found line — the user's own words for when
    // tai *should* work again.
    s.write(BACKSPACE);
    s.settle();
    let found = s.line();
    s.close();
    let raw = s.raw();
    let (hook_flags, queried) = read_log();

    assert!(
        raw[mark..].contains("bck-i-search"),
        "the search ran (zsh's own prompt came up)"
    );
    assert!(
        queried.iter().any(|q| q == "ls -l"),
        "typing reached tai (the instruments are alive)"
    );
    assert!(
        hook_flags.iter().any(|f| f == "1"),
        "the search's steps reached tai's hook"
    );
    assert!(
        !queried.iter().any(|q| q == "lsof -i"),
        "tai answered none of the search's found lines"
    );
    assert_eq!(
        queried.iter().filter(|q| *q == "ls /tmp").count(),
        1,
        "the found line's first draw asked tai once"
    );
    assert!(
        found.contains("/tmp"),
        "the found line was still there to edit"
    );
    assert!(s.noise().is_empty(), "the ^R session clean: {:?}", s.noise());
    check_fixture_intact("the ^R test");
}

#[test]
fn pattern_steps_cost_nothing_without_the_update_hook() {
    let _guard = LOCK.lock().unwrap_or_else(|e| e.into_inner());
    setup();
    if !zsh_available() {
        // No zsh on this machine; the Python suite skips silently.
        return;
    }
    // The second report: typing into the pattern, on a shell whose update
    // hook never raises the flag.
    //
    // With the update hook blind, the flag can only come from the wrap — the
    // search raised by its own widget at ^R's dispatch, before the search
    // draws anything. Measured on the bundled zsh with this exact blindness,
    // every match-changing pattern character paid for a full query before the
    // wrap: `d` and `o` each queried. The discriminator here is `cd tzz_dir`,
    // the newest seeded line holding a `d`, typed nowhere in this test.
    seed_history();
    let mut s = isearch_session();
    s.run(&blind_setup_cmd());
    s.run(&format!(
        "print -r -- \"${{widgets[history-incremental-search-backward]:-}}\" > {}",
        wrap_value_path()
    ));
    s.run(&format!(": > {}", log_path()));
    // A fresh history list, pushed last and by explicit filename, so the
    // search sees only the seeded lines: the harness's own commands otherwise
    // sit in the list — every run() line carries the done_N__ marker, a "do"
    // substring — and outrank the seeds as the newest matches. fc -p with no
    // arguments unsets HISTFILE, which is why the filename travels with it.
    // Nothing is typed after this, so the newest entries are the seeds.
    s.run(&format!(
        "fc -p {h} 30 30; fc -R {h}",
        h = hist_path()
    ));
    let mark = s.seen_len();
    // `d` lands on `cd tzz_dir`, `o` narrows to `docker ps`, `c` changes
    // nothing, and the arrow ends the search keeping the line it found —
    // whose first ordinary draw is tai working again, once.
    s.write(ISEARCH);
    s.settle();
    s.write("d");
    s.settle();
    s.write("o");
    s.settle();
    s.write("c");
    s.settle();
    s.write(RIGHT);
    s.settle_for(Duration::from_millis(500));
    let found = s.line();
    s.close();
    let raw = s.raw();
    let wrap = std::fs::read_to_string(wrap_value_path()).unwrap();
    let (hook_flags, queried) = read_log();

    assert!(
        raw[mark..].contains("bck-i-search"),
        "the search ran (zsh's own prompt came up)"
    );
    assert!(
        wrap.contains("tai-isearch-backward"),
        "the search's widget is tai's wrapper"
    );
    assert!(
        hook_flags.iter().any(|f| f == "1"),
        "the flag was up inside the search, hook or no hook"
    );
    assert!(
        !queried.iter().any(|q| q == "cd tzz_dir"),
        "no pattern step ran the query"
    );
    assert_eq!(
        queried.iter().filter(|q| *q == "docker ps").count(),
        1,
        "the found line's first draw asked tai once (queries: {queried:?}, flags: {hook_flags:?})"
    );
    assert_eq!(found, "docker ps", "the found line was still there");
    assert!(s.noise().is_empty(), "the ^R session clean: {:?}", s.noise());
    check_fixture_intact("the ^R test");
}
