//! A small terminal-screen model, so the tests can read what was drawn.
//!
//! The pty tells you what bytes the shell wrote; a menu test needs to know
//! which cell was selected and in what colour, which is a question about a
//! screen rather than about a stream. So feed the stream into a `Screen` and
//! ask it. Ported from `plugin_screen.py`.

use super::{ansi_re, Attr, Session};

/// The second column of a double-width character; never drawn.
const WIDE_TAIL: char = '\x00';

/// A run that is not plain is something the plugin asked ZLE to draw that way.
const PLAIN: Attr = (0, 0, false, false);

pub fn tail(text: &str, n: usize) -> String {
    let clean = ansi_re().replace_all(text, "");
    let start = clean.len().saturating_sub(n);
    // Slice on a char boundary.
    let mut start = start;
    while !clean.is_char_boundary(start) {
        start += 1;
    }
    clean[start..].to_string()
}

pub fn indent(text: &str, prefix: &str) -> String {
    text.lines().map(|l| format!("{prefix}{l}")).collect::<Vec<_>>().join("\n")
}

/// The menu as the user reads it: the entries, in order.
///
/// Columns are padded to the widest name in the list, so the spacing between
/// entries is not a fixed number of characters and cannot be asserted on. The
/// entries themselves never contain a space — a menu completes one word —
/// which is what makes a run of spaces a reliable separator here. The one
/// exception is the first-word menu's learned rows, which are whole lines and
/// are asserted as drawn text, not through this split.
pub fn menu_entries(drawn: &str) -> Vec<String> {
    let flat = drawn.replace("\\n", " ");
    flat.split_whitespace().map(|s| s.to_string()).collect()
}

/// The terminal as a grid, so "what the screen shows" can be asserted.
///
/// ZLE's redraw is incremental, so a run of escape bytes is unreadable as
/// text: splitting it on newlines shows the two cells that moved and none of
/// the line they moved on. Feeding the bytes to a grid is the only way to
/// catch a menu that lands on the prompt line or leaves the pieces of the last
/// one behind.
///
/// Each cell keeps its attributes as well as its character. A menu can be
/// right in every character and wrong in every colour, and the only way to see
/// that is to keep what the terminal was told about each one.
///
/// Only the escapes zsh's redraw actually emits are handled. Anything else is
/// dropped rather than guessed at, so an unhandled sequence shows up as a
/// wrong screen instead of quietly passing.
pub struct Screen {
    grid: Vec<Vec<(char, Attr)>>,
    row: usize,
    col: usize,
    attr: Attr,
}

pub const COLS: u16 = 80;
pub const ROWS: u16 = 24;

impl Default for Screen {
    fn default() -> Self {
        Self::new()
    }
}

impl Screen {
    pub fn new() -> Screen {
        Screen {
            grid: vec![vec![(' ', PLAIN); COLS as usize]; ROWS as usize],
            row: 0,
            col: 0,
            attr: PLAIN,
        }
    }

    fn put(&mut self, ch: char) {
        if self.col >= COLS as usize {
            // a terminal wraps at the edge
            self.col = 0;
            self.row += 1;
        }
        if self.row < ROWS as usize {
            self.grid[self.row][self.col] = (ch, self.attr);
        }
        // A character in a double-width script — Japanese, Chinese, Korean,
        // and most emoji — takes two columns, and a model that gives it one
        // shifts every column after it by one. The second column is a
        // continuation: occupied, so a region that reaches it is seen, and
        // invisible in `lines()`, because the character to its left is the
        // whole story.
        let width = if is_wide(ch) { 2 } else { 1 };
        if width == 2 && self.col + 1 < COLS as usize && self.row < ROWS as usize {
            self.grid[self.row][self.col + 1] = (WIDE_TAIL, self.attr);
        }
        self.col += width;
    }

    fn blank(&mut self, row: usize, start: usize, stop: usize) {
        if row < ROWS as usize {
            for c in start..stop.min(COLS as usize) {
                self.grid[row][c] = (' ', PLAIN);
            }
        }
    }

    pub fn feed(&mut self, data: &str) {
        let chars: Vec<char> = data.chars().collect();
        let mut i = 0;
        while i < chars.len() {
            let ch = chars[i];
            if ch == '\x1b' && i + 1 < chars.len() && chars[i + 1] == '[' {
                match parse_csi(&chars, i) {
                    Some((args, cmd, end)) => {
                        self.escape(&args, cmd);
                        i = end;
                        continue;
                    }
                    None => {
                        i += 1;
                        continue;
                    }
                }
            }
            match ch {
                '\r' => self.col = 0,
                '\n' => {
                    self.row += 1;
                    if self.row >= ROWS as usize {
                        // the grid scrolls
                        self.grid.remove(0);
                        self.grid.push(vec![(' ', PLAIN); COLS as usize]);
                        self.row = ROWS as usize - 1;
                    }
                }
                '\x08' => self.col = self.col.saturating_sub(1),
                '\t' => {
                    self.col = ((self.col / 8 + 1) * 8).min(COLS as usize - 1);
                }
                c if c >= ' ' => self.put(c), // printable, and ^X for a control
                _ => {}
            }
            i += 1;
        }
    }

    fn escape(&mut self, args: &str, cmd: char) {
        let nums: Vec<usize> = args
            .split(';')
            .filter_map(|a| a.parse::<usize>().ok())
            .collect();
        let first = nums.first().copied().unwrap_or(0);
        match cmd {
            'm' => self.sgr(if nums.is_empty() { vec![0] } else { nums }),
            'C' => self.col = (self.col + first.max(1)).min(COLS as usize - 1),
            'D' => self.col = self.col.saturating_sub(first.max(1)),
            'A' => self.row = self.row.saturating_sub(first.max(1)),
            'B' => self.row = (self.row + first.max(1)).min(ROWS as usize - 1),
            'K' => {
                let (start, stop) = match args {
                    "" | "0" => (self.col, COLS as usize),
                    "1" => (0, self.col + 1),
                    _ => (0, COLS as usize),
                };
                self.blank(self.row, start, stop);
            }
            'J' => {
                if args.is_empty() || args == "0" {
                    self.blank(self.row, self.col, COLS as usize);
                    for r in self.row + 1..ROWS as usize {
                        self.grid[r] = vec![(' ', PLAIN); COLS as usize];
                    }
                } else {
                    for r in 0..self.row {
                        self.grid[r] = vec![(' ', PLAIN); COLS as usize];
                    }
                    self.blank(self.row, 0, self.col + 1);
                }
            }
            _ => {}
        }
    }

    /// Apply SGR parameters. Only the ones a redraw actually emits.
    fn sgr(&mut self, nums: Vec<usize>) {
        let (mut fg, mut bg, mut bold, mut reverse) = self.attr;
        for n in nums {
            match n {
                0 => {
                    fg = 0;
                    bg = 0;
                    bold = false;
                    reverse = false;
                }
                1 => bold = true,
                7 => reverse = true,
                22 => bold = false, // normal intensity
                27 => reverse = false,
                39 => fg = 0,
                49 => bg = 0,
                30..=37 | 90..=97 => fg = n as i32,
                40..=47 | 100..=107 => bg = n as i32,
                // 24 (underline off) is not tracked: nothing here draws
                // underlined text, and carrying an attribute no assertion
                // reads is noise.
                _ => {}
            }
        }
        self.attr = (fg, bg, bold, reverse);
    }

    /// The screen as text lines. Continuation columns are dropped: the
    /// character to their left is the one the terminal draws, so a line read
    /// as text has one character per character and not one per column.
    pub fn lines(&self) -> Vec<String> {
        self.grid
            .iter()
            .map(|row| {
                let line: String = row
                    .iter()
                    .filter(|(ch, _)| *ch != WIDE_TAIL)
                    .map(|(ch, _)| ch)
                    .collect();
                line.trim_end().to_string()
            })
            .collect()
    }

    /// The last `count` rows that have something on them.
    pub fn bottom(&self, count: usize) -> Vec<String> {
        let lines: Vec<String> = self.lines().into_iter().filter(|l| !l.is_empty()).collect();
        let start = lines.len().saturating_sub(count);
        lines[start..].to_vec()
    }

    /// A row as (text, attributes) runs, trailing blanks dropped.
    ///
    /// A run that is not plain is something the plugin asked ZLE to draw that
    /// way, and the text it holds is what was drawn that way — which is how
    /// "is the selection highlighted" and "is that a directory blue" are
    /// answered without reading the plugin's own arrays.
    pub fn styled(&self, row: usize) -> Vec<(String, Attr)> {
        let mut runs: Vec<(String, Attr)> = Vec::new();
        for (ch, attr) in &self.grid[row] {
            if *ch == WIDE_TAIL {
                continue; // part of the character to its left
            }
            if let Some(last) = runs.last_mut() {
                if last.1 == *attr {
                    last.0.push(*ch);
                    continue;
                }
            }
            runs.push((ch.to_string(), *attr));
        }
        runs.into_iter()
            .filter(|(text, _)| text.trim().len() > 0)
            .map(|(text, attr)| (text.trim_end().to_string(), attr))
            .collect()
    }

    /// Every styled run on the screen, top to bottom — the runs `styled`
    /// reports, gathered across all rows.
    pub fn styled_rows(&self) -> Vec<(String, Attr)> {
        let mut out = Vec::new();
        for row in 0..ROWS as usize {
            out.extend(self.styled(row));
        }
        out
    }
}

/// Parse a CSI sequence starting at `chars[i] == ESC`. Returns the parameter
/// text, the final byte, and the index just past the sequence.
fn parse_csi(chars: &[char], i: usize) -> Option<(String, char, usize)> {
    debug_assert_eq!(chars[i], '\x1b');
    let mut j = i + 2; // skip ESC [
    let mut args = String::new();
    while j < chars.len() {
        let c = chars[j];
        if c.is_ascii_digit() || c == ';' || c == '?' {
            args.push(c);
            j += 1;
        } else if ('@'..='~').contains(&c) {
            return Some((args, c, j + 1));
        } else {
            return None;
        }
    }
    None
}

/// East-Asian Width W or F: the characters that take two columns.
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

/// The entry the screen shows as selected — the one Enter would take.
///
/// Read off the screen rather than out of the plugin's arrays, because the
/// selection is now drawn by ZLE and not by a character in the menu text:
/// there is no marker in the text any more, and the only evidence that an
/// entry is selected is that its cell was painted in reverse video.
pub fn selected_entry(screen: &Screen) -> String {
    for row in (0..ROWS as usize).rev() {
        for (text, attr) in screen.styled(row) {
            if attr.3 {
                return text.trim().to_string();
            }
        }
    }
    String::new()
}

/// The attributes a piece of text is drawn in, wherever it sits on screen.
///
/// A whole row is one run, so the text of a run is only a single entry when
/// the run is that entry — which is exactly the case the menu's colours are
/// about.
pub fn style_of(screen: &Screen, text: &str) -> Result<Attr, &'static str> {
    for row in 0..ROWS as usize {
        for (got, attr) in screen.styled(row) {
            if got == text {
                return Ok(attr);
            }
        }
    }
    Err(NOT_DRAWN)
}

pub const NOT_DRAWN: &str = "not on screen";

/// The screen a session has drawn so far.
pub fn screen_of(session: &Session) -> Screen {
    let mut screen = Screen::new();
    screen.feed(&session.raw());
    screen
}
