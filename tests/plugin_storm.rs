//! Port of `tests/test_plugins_storm.py` — the rapid-edit storm: a word typed
//! and taken apart letter by letter.
//!
//! The report: typing a nonsense word and then removing or changing characters
//! quickly hung the prompt and took /bin/zsh down. The mechanics: every delete
//! key lands on a prefix the typing already answered, and the loose scan's
//! parent shortcut only serves a prefix that *extends* its parent — a deletion
//! never does — so every delete key paid the full three-tier scan again. The
//! fix is the memo (a bounded ring keyed on prefix and index generation) plus
//! a flood bound on the two tier loops; this suite pins the mechanism and the
//! behaviour, not the clock — a clock here would flake on a loaded CI runner
//! and pass on a regression small enough to still sting.
//!
//! The state reader is a widget, not a probe command typed at the prompt:
//! typing anything at the prompt is itself a storm — every character is a
//! prefix the hook scans — and a probe would both smash `_TAI_LOOSE_PAR` with
//! its own lowercased line and clear the buffer it meant to inspect. Its
//! output is one field per line, because every other shape — one line,
//! space-separated — shatters on the first value that holds a space, and
//! every interesting value here is a typed line.

mod support;
use support::*;

use regex::Regex;
use std::collections::HashMap;

static LOCK: std::sync::Mutex<()> = std::sync::Mutex::new(());

// BS is the harness's BACKSPACE ("\x7f").
/// ^O: the stock accept-and-hold, rebound here to the reader.
const STATE_KEY: &str = "\x0f";

/// The suite's state file, named per process: the whole pty suite shares
/// /tmp/tai and other targets run against it at the same time.
fn state_path() -> String {
    format!("/tmp/tai/tai_storm_state_{}.txt", std::process::id())
}

/// The widget definition runs once per session, before any storm; after that
/// one keypress writes the memo's state without moving the buffer. The two
/// membership probes are this suite's own keys, compared exactly — the ring
/// stores the user's text as data, and this is how it is asked about.
fn state_widget() -> String {
    format!(
        "tai_storm_state() {{ local k has_full=0 has_del=0; \
         for k in \"${{_TAI_LOOSE_MEM_KEYS[@]}}\"; do \
         [[ \"$k\" == \"pull --qqzzx|$_TAI_LOOSE_GEN\" ]] && has_full=1; \
         [[ \"$k\" == \"pull --qq|$_TAI_LOOSE_GEN\" ]] && has_del=1; \
         done; \
         print -r -- \"GEN=[$_TAI_LOOSE_GEN]\" > \"{st}\"; \
         print -r -- \"PAR=[$_TAI_LOOSE_PAR]\" >> \"{st}\"; \
         print -r -- \"MEM=[${{_TAI_LOOSE_MEM_N}}]\" >> \"{st}\"; \
         print -r -- \"HASFULL=[$has_full]\" >> \"{st}\"; \
         print -r -- \"HASDEL=[$has_del]\" >> \"{st}\" \
         }}; zle -N tai_storm_state; bindkey \"^O\" tai_storm_state",
        st = state_path()
    )
}

/// The memo's state, read through the ^O widget.
fn storm_state(s: &mut Session) -> HashMap<String, String> {
    let state = state_path();
    let _ = std::fs::remove_file(&state);
    s.write(STATE_KEY);
    s.settle();
    let text = std::fs::read_to_string(&state).unwrap();
    let re = Regex::new(r"^([A-Z]+)=\[(.*)\]$").unwrap();
    let mut out = HashMap::new();
    for line in text.lines() {
        if let Some(c) = re.captures(line) {
            out.insert(c[1].to_string(), c[2].to_string());
        }
    }
    out
}

/// One `NAME=[value]` field out of a state file, matched with the Python
/// suite's value pattern (`\d+`, `\d`, or `.*`) — greedy `.*` everywhere would
/// swallow the fields that share the line, which the Python regexes could not.
fn field(text: &str, name: &str, pat: &str) -> String {
    let re = Regex::new(&format!(r"{name}=\[({pat})\]")).unwrap();
    match re.captures(text) {
        Some(c) => c[1].to_string(),
        None => panic!("no {name} in the state file: {text:?}"),
    }
}

/// The MENU= line of the dump, exactly as the widget wrote it — the shape the
/// Python suite compared `got[2]` against.
fn menu_str(m: &MenuRead) -> String {
    format!("MENU=[{}] N=[{}] IDX=[{}]", m.drawn, m.entries, m.idx)
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
        std::thread::sleep(std::time::Duration::from_millis(100 * (attempt + 1)));
    }
    false
}

#[test]
fn delete_storm_answers_from_the_memo() {
    let _guard = LOCK.lock().unwrap_or_else(|e| e.into_inner());
    setup();
    if !zsh_available() {
        // No zsh on this machine; the Python suite skips silently.
        return;
    }
    let mut s = Session::new("zsh");
    s.run(&state_widget());
    let _st = storm_state(&mut s);
    // A word that is dead at every prefix: no learned line holds its tail or
    // anything one edit away from it, so the glimpse answers nothing and the
    // memo fills on every keystroke. This is the reported shape — the
    // nonsense word, then the backspace run back down it.
    s.send("pull --qqzzx");
    let got = s.menu(false);
    assert_eq!(menu_str(&got), "MENU=[] N=[0] IDX=[0]", "dead word previews nothing");
    let st = storm_state(&mut s);
    assert_eq!(st["HASFULL"], "1", "the storm's last prefix is in the memo");
    for _ in 0..3 {
        s.write(BACKSPACE);
        s.settle();
    }
    let got = s.menu(false);
    assert_eq!(got.line, "pull --qq", "line after the storm");
    assert_eq!(menu_str(&got), "MENU=[] N=[0] IDX=[0]", "still no preview on the way back");
    // The memo hit restored the parent state: only a hit sets PAR to the
    // prefix it answered, and the delete keys asked for prefixes the typing
    // had already scanned.
    let st = storm_state(&mut s);
    assert_eq!(st["PAR"], "pull --qq", "the memo restored the parent state");
    assert!(s.noise().is_empty(), "the storm left no shell noise: {:?}", s.noise());
    s.close();

    // The alive storm: the word is answered on the way up and on the way
    // back down, and every delete key is a revisit of a scanned prefix.
    let mut s = Session::new("zsh");
    s.run(&state_widget());
    s.send("pull --rebasex");
    for _ in 0..3 {
        s.write(BACKSPACE);
        s.settle();
    }
    let got = s.menu(false);
    assert_eq!(got.line, "pull --reba", "line after the alive storm");
    assert_eq!(
        menu_str(&got),
        "MENU=[\\n  git pull --rebase] N=[1] IDX=[1]",
        "memo-hit answer equals the fresh-scan answer"
    );
    assert_eq!(
        storm_state(&mut s)["PAR"],
        "pull --reba",
        "the memo restored the parent state"
    );
    s.close();

    // The same prefix typed cold, in a shell whose memo is empty: the two
    // roads must agree, or a revisit would answer differently from a first
    // ask — the drift the one-deep cache existed to prevent, now bounded.
    let mut s2 = Session::new("zsh");
    s2.send("pull --reba");
    let got2 = s2.menu(false);
    assert_eq!(menu_str(&got2), menu_str(&got), "fresh-typed prefix previews the same");
    s2.close();
    check_fixture_intact("the run");
}

#[test]
fn memo_is_bounded() {
    let _guard = LOCK.lock().unwrap_or_else(|e| e.into_inner());
    setup();
    if !zsh_available() {
        // No zsh on this machine; the Python suite skips silently.
        return;
    }
    let mut s = Session::new("zsh");
    // Direct scans, three prefixes against a cap of two: the first answer is
    // overwritten, the two latest stand. A memo that only ever grew would
    // hold every prefix a session ever typed, which is the index's job, not
    // the memo's. Membership, not position: which slot a survivor landed in
    // is the ring's business.
    s.run(&format!(
        "_TAI_LOOSE_MEM_MAX=2; _tai_loose \"aaa-xx\"; _tai_loose \"bbb-xx\"; \
         _tai_loose \"ccc-xx\"; \
         local k has_a=0 has_b=0 has_c=0; \
         for k in \"${{_TAI_LOOSE_MEM_KEYS[@]}}\"; do \
         [[ \"$k\" == \"aaa-xx|\"* ]] && has_a=1; \
         [[ \"$k\" == \"bbb-xx|\"* ]] && has_b=1; \
         [[ \"$k\" == \"ccc-xx|\"* ]] && has_c=1; \
         done; \
         print -r -- \"MEM=[${{_TAI_LOOSE_MEM_N}}] \
         HASA=[$has_a] HASB=[$has_b] HAS=[$has_c]\" > \"{st}\"",
        st = state_path()
    ));
    let text = std::fs::read_to_string(state_path()).unwrap();
    let mem: usize = field(&text, "MEM", r"\d+").parse().unwrap();
    assert_eq!(mem, 2, "bounded");
    assert_eq!(field(&text, "HASA", r"\d"), "0", "oldest overwritten");
    assert_eq!(
        (field(&text, "HASB", r"\d"), field(&text, "HAS", r"\d")),
        ("1".to_string(), "1".to_string()),
        "the two latest stand"
    );
    s.close();
    check_fixture_intact("the run");
}

#[test]
fn flood_bound_keeps_the_answer() {
    let _guard = LOCK.lock().unwrap_or_else(|e| e.into_inner());
    setup();
    if !zsh_available() {
        // No zsh on this machine; the Python suite skips silently.
        return;
    }
    let mut s = Session::new("zsh");
    // `git stats` matches nothing verbatim; its one-edit spelling `git stat`
    // occurs in `git status`. With the loop's input capped at one line, the
    // one survivor is still tested and the answer stands — the cap bounds the
    // flood, it does not gate the tier.
    s.run("_TAI_LOOSE_FLOOD=1");
    s.send("git stats");
    let got = s.menu(false);
    assert_eq!(
        menu_str(&got),
        "MENU=[\\n  git status] N=[1] IDX=[1]",
        "one-edit answer survives the cap"
    );
    assert!(s.noise().is_empty(), "the storm left no shell noise: {:?}", s.noise());
    s.close();
    check_fixture_intact("the run");
}

#[test]
fn reported_dead_line_is_bounded() {
    let _guard = LOCK.lock().unwrap_or_else(|e| e.into_inner());
    setup();
    if !zsh_available() {
        // No zsh on this machine; the Python suite skips silently.
        return;
    }
    let mut s = Session::new("zsh");
    // The report: typing `DSH_DELETE_SESSIONS_DEBUG=1` stood the prompt still.
    // Twenty-seven letters, nothing in the history holds them, so every fuzzy
    // tier ran its widest filter over the whole index on every keystroke. The
    // answer is still nothing — the line is dead and must stay dead — and the
    // mechanism pin is on the two bounds that keep it cheap: the one-edit
    // filter is the word's own first and last letter pair (two literals, one
    // pass, however long the word), and a word past the gap cap never builds
    // the letters-in-order filter at all.
    s.run(&format!(
        "local out=\"\" g=\"\"; \
         _tai_word_variants_re \"dsh_delete_sessions_debug=1\"; \
         g=\"$_TAI_GLOB_OUT\"; \
         _tai_loose \"dsh_delete_sessions_debug=1\"; \
         print -r -- \"LOOSE=[${{#_TAI_LOOSE_LINES}}] GLOB=[$g]\" > \"{st}\"",
        st = state_path()
    ));
    let text = std::fs::read_to_string(state_path()).unwrap();
    assert_eq!(field(&text, "LOOSE", r"\d+"), "0", "the dead line stays dead");
    assert_eq!(
        field(&text, "GLOB", ".*"),
        r"(ds|\=1)",
        "one-edit filter is the edge pair, not the variant alternation"
    );
    // And a flood-level prefix cannot hand the next letter more than the cap:
    // the parent set is what a delete run and the next keystroke rescan.
    s.run(&format!(
        "local n=0; _tai_loose \"s\"; \
         print -r -- \"PARLOW=[${{#_TAI_LOOSE_PAR_LOW}}] MAX=[${{_TAI_LOOSE_PAR_MAX}}]\" \
         > \"{st}\"",
        st = state_path()
    ));
    let text = std::fs::read_to_string(state_path()).unwrap();
    let parlow: usize = field(&text, "PARLOW", r"\d+").parse().unwrap();
    let parmax: usize = field(&text, "MAX", r"\d+").parse().unwrap();
    assert!(parlow <= parmax, "parent scan set is capped");
    s.close();
    check_fixture_intact("the run");
}
