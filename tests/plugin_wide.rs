//! A file name in a double-width script, drawn correctly and highlighted right.
//!
//! The filesystem answer is drawn as a menu, and the menu is laid out in
//! columns — characters for ASCII, two cells each for CJK and emoji. The
//! Screen model has to count columns the way the terminal does or its
//! assertions are about a terminal that does not exist.
//!
//! Ported from `tests/test_plugins_wide.py`.
//!
//! Adaptation: Python read `scr.grid[row]` directly — every cell's character
//! *and* attributes — but the Rust `Screen` keeps its grid private. The two
//! things this suite reads out of the grid are rebuilt here from the public
//! API: `column_of` reads the column a name starts at out of `lines()`
//! (continuation columns dropped, one character per character — the same view
//! Python's cell walk built), and `reverse_block` reconstructs the
//! reverse-video columns by realigning `styled()`'s runs against the row's
//! text. `styled` trims trailing whitespace and drops whitespace-only runs, so
//! the runs' positions cannot be accumulated run-to-run; realignment against
//! the full row text (entries are unique and in order) puts every run back on
//! its true columns. One known blind spot: a reverse-video run's *trailing
//! padding spaces* are invisible here, so the block's last column is the last
//! visible reversed character. The assertion that matters — no reverse cell at
//! or past the next name's column — is unaffected: the columns past a name are
//! never whitespace on a menu row, so any reverse cell there would show up
//! inside a reversed run.

mod support;
use support::*;
use support::screen::{ROWS, Screen};

use std::path::PathBuf;

// Tests in one binary share /tmp/tai, so they run one at a time.
static LOCK: std::sync::Mutex<()> = std::sync::Mutex::new(());

/// East-Asian Width W or F: the characters that take two columns.
///
/// A local copy of `screen.rs`'s private `is_wide`, so this suite can price a
/// line the way the Screen it asserts against does. Every character this suite
/// uses sits inside the same ranges both sides check: 日 U+65E5, 本 U+672C and
/// 語 U+8A9E in 0x4E00..=0x9FFF, 🎉 U+1F389 in 0x1F300..=0x1F64F — all `W` per
/// `unicodedata.east_asian_width`, and ñ/é are narrow on both sides.
fn is_wide(ch: char) -> bool {
    let c = ch as u32;
    matches!(c,
        0x1100..=0x115F
        | 0x2329..=0x232A
        | 0x2E80..=0x303E
        | 0x3041..=0x33FF
        | 0x3400..=0x4DBF
        | 0x4E00..=0x9FFF
        | 0xA000..=0xA4CF
        | 0xA960..=0xA97F
        | 0xAC00..=0xD7A3
        | 0xF900..=0xFAFF
        | 0xFE10..=0xFE19
        | 0xFE30..=0xFE6F
        | 0xFF00..=0xFF60
        | 0xFFE0..=0xFFE6
        | 0x1F300..=0x1F64F
        | 0x1F900..=0x1F9FF
        | 0x20000..=0x2FFFD
        | 0x30000..=0x3FFFD
    )
}

/// What a terminal spends on `text`: two columns per wide character.
fn columns(text: &str) -> usize {
    text.chars().map(|c| if is_wide(c) { 2 } else { 1 }).sum()
}

/// The column `text` starts at in `row`, counted the way a terminal counts.
///
/// Continuation columns are dropped first, because a name in a double-width
/// script is one character per cell *and* has a cell per column: searching the
/// grid by cell index would run out of slice half way through the name.
/// `lines()` has already dropped them, so the prefix's columns price the
/// position directly.
fn column_of(screen: &Screen, row: usize, text: &str) -> Option<usize> {
    let line = &screen.lines()[row];
    let at = line.find(text)?;
    Some(line[..at].chars().map(|c| if is_wide(c) { 2 } else { 1 }).sum())
}

/// The columns of the row's reverse-video cells, reconstructed from the styled
/// runs.
///
/// `styled` merges runs, trims trailing whitespace and drops whitespace-only
/// runs, so summing run widths run-to-run drifts left by every dropped space.
/// Instead each run's text is matched against the row's full text (which holds
/// every space) left to right, in order — the entries are unique and contain
/// no spaces, so the greedy match lands on the true position.
fn reverse_block(screen: &Screen, row: usize) -> Vec<usize> {
    let rows = screen.lines();
    let t: Vec<char> = rows[row].chars().collect();
    // Column at which each character of the row starts, plus the end.
    let mut col_of = vec![0usize; t.len() + 1];
    for i in 0..t.len() {
        col_of[i + 1] = col_of[i] + if is_wide(t[i]) { 2 } else { 1 };
    }
    let mut block = Vec::new();
    let mut from = 0usize;
    for (text, attr) in screen.styled(row) {
        let run: Vec<char> = text.chars().collect();
        let mut found = None;
        'outer: for i in from..=t.len().saturating_sub(run.len()) {
            for j in 0..run.len() {
                if t[i + j] != run[j] {
                    continue 'outer;
                }
            }
            found = Some(i);
            break;
        }
        let Some(i) = found else {
            panic!("run {text:?} not found in row {:?}", rows[row]);
        };
        if attr.3 {
            block.extend(col_of[i]..col_of[i + run.len()]);
        }
        from = i + run.len();
    }
    block
}

/// Backdate/forward-date a file the way `os.utime(path, (ts, ts))` did, so the
/// filesystem answer has a defined order.
fn set_mtime(path: &PathBuf, ts: i64) {
    let times = [
        libc::timeval { tv_sec: ts, tv_usec: 0 },
        libc::timeval { tv_sec: ts, tv_usec: 0 },
    ];
    let c = path.to_string_lossy().into_owned();
    unsafe {
        libc::utimes(c.as_ptr() as _, times.as_ptr());
    }
}

/// The reverse-video block covers the whole selected name — and stops short of
/// the next one on the same row.
///
/// Containment, not exact offsets: the column width is padded in characters
/// (ZLE counts regions in characters, and padding in columns would misplace
/// the selection), so how many columns of padding a cell has is the plugin's
/// business and not this test's. What has to hold is that the block covers the
/// whole selected name and stops before the next one — counted in columns,
/// which is what the terminal counts and what this Screen now models. Only
/// when the next name is on this row — the last entry of a row has nothing
/// beside it, and that is not a pass by default.
fn covers(scr: &Screen, name: &str, after: Option<&str>) {
    let rows = scr.lines();
    let row = (0..ROWS as usize)
        .find(|&r| rows[r].contains(name))
        .unwrap_or_else(|| panic!("{name} is not on screen"));
    let block = reverse_block(scr, row);
    assert!(!block.is_empty(), "no reverse video on the row holding {name}");
    let at = column_of(scr, row, name)
        .unwrap_or_else(|| panic!("{name} is not on screen"));
    assert!(
        block[0] <= at && *block.last().unwrap() >= at + columns(name) - 1,
        "the block covers all of {name}"
    );
    // …and no further: two cells on one row must not share a column, or the
    // second name is drawn inside the first one's selection.
    if let Some(after_col) = after.and_then(|a| column_of(scr, row, a)) {
        assert!(*block.last().unwrap() < after_col, "and stops short of the next name");
    }
}

#[test]
fn test_wide_file_names_are_measured_in_columns() {
    let _guard = LOCK.lock().unwrap_or_else(|e| e.into_inner());
    if zsh_bin().is_none() {
        return; // no zsh on this machine; the pty suite cannot run
    }
    setup();

    let wide = PathBuf::from("/tmp/tai/tai_wide_names");
    let _ = std::fs::remove_dir_all(&wide);
    std::fs::create_dir_all(&wide).unwrap();
    // A `wide-` prefix, because an empty word after `cat` answers from the
    // download directories as well as the current one, and what is newest on
    // this machine is not what this test wrote.
    let names = ["wide-日本語.txt", "wide-español.txt", "wide-🎉party.txt", "wide-ascii.txt"];
    for name in names {
        std::fs::write(wide.join(name), "").unwrap();
    }
    // The file list is newest first, and four files written in the same
    // instant have no order at all — so the mtimes are set, `names[0]` newest,
    // and the assertions can name an entry instead of reading whichever one
    // came first.
    for (i, name) in names.iter().rev().enumerate() {
        set_mtime(&wide.join(name), 1_700_000_000 + i as i64);
    }
    let mut s = Session::new("zsh");
    s.run(&format!("cd {}", wide.display()));
    s.send("cat wide-");
    s.write(CTRL_SPACE);
    s.settle();
    // `clear=false` between the keys and the screen: reading the line the
    // other way abandons it, and an abandoned line closes the menu.
    s.menu(false);
    let scr = screen_of(&s);
    assert_eq!(selected_entry(&scr), names[0], "the wide names are on screen");
    covers(&scr, names[0], Some(names[1]));
    for step in 1..names.len() {
        s.write(TAB);
        s.settle();
        s.menu(false);
        let scr = screen_of(&s);
        assert_eq!(selected_entry(&scr), names[step], "the mark moves to {}", names[step]);
        covers(&scr, names[step], names.get(step + 1).copied());
    }
    // The shared head, written in characters like everything else on this
    // path: `wid` is answered by four names that agree up to `wide-`, the
    // written forms of which diverge at the first escaped byte — the head is
    // what they all still start with.
    s.write("\x15");
    s.send("cat wid");
    s.write(TAB);
    s.settle();
    let m = s.menu(false);
    assert_eq!(m.line, "cat wide-", "the head of double-width names is written");
    assert_eq!(m.entries, names.len(), "and all four names are still listed");
    assert!(s.noise().is_empty(), "no noise: {:?}", s.noise());

    s.close();
    let _ = std::fs::remove_dir_all(&wide);
    check_fixture_intact("the run");
}
