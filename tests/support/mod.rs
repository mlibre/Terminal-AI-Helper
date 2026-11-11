//! The pty harness, ported from `plugin_env.py`, `plugin_pty.py` and
//! `fixture_rules.py` — the last Python left in the tree, now gone.
//!
//! Every wait here ends on something observable — a marker the shell printed, a
//! file the editor wrote, or output that stopped arriving — with a timeout only
//! as a backstop that fails loudly instead of hanging. A fixed pause costs that
//! pause on every interaction whether or not it was needed, and is still wrong
//! on a loaded machine.
//!
//! The screen model lives in the [`screen`] submodule. Test binaries pull this
//! in with `mod support;`.
#![allow(dead_code)]

use std::os::unix::process::CommandExt;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::time::{Duration, Instant};

pub mod screen;
pub use screen::{
    indent, menu_entries, screen_of, selected_entry, style_of, tail, Screen, NOT_DRAWN,
};

use regex::Regex;
use std::sync::OnceLock;

// ---------------------------------------------------------------------------
// Paths and fixture constants (plugin_env.py)
// ---------------------------------------------------------------------------

/// The repository root — the manifest dir, so the suite finds the plugins and
/// the CLI the same way whatever directory it is run from.
pub const REPO: &str = env!("CARGO_MANIFEST_DIR");

pub fn repo_path(rel: &str) -> PathBuf {
    Path::new(REPO).join(rel)
}

pub fn plugin_path(shell: &str) -> PathBuf {
    repo_path(if shell == "bash" {
        "plugins/tai.bash"
    } else {
        "plugins/tai.zsh"
    })
}

/// The zsh this suite runs: TAI_ZSH, then the system's, then the bundled one.
///
/// `tests/bin/` carries a compiled, ready-to-use zsh, so the whole pty suite
/// runs on a machine with no zsh installed. The system zsh wins when there is
/// one: it is the shell the product actually ships to. The bundled copy is the
/// floor, not the ceiling.
pub fn zsh_bin() -> Option<PathBuf> {
    // Resolved once: the lookup stat()s the bundled binary, and this sandbox's
    // filesystem has been seen to fail that stat transiently — a cached answer
    // keeps one flaky stat from failing a whole suite.
    static CACHE: OnceLock<Option<PathBuf>> = OnceLock::new();
    CACHE
        .get_or_init(|| {
            if let Ok(custom) = std::env::var("TAI_ZSH") {
                let custom = custom.trim().to_string();
                if !custom.is_empty() && is_executable(Path::new(&custom)) {
                    return Some(PathBuf::from(custom));
                }
            }
            if let Some(found) = which("zsh") {
                return Some(found);
            }
            let bundled = repo_path("tests/bin/zsh");
            // The bundled binary is the floor. A stat that fails transiently
            // (seen on overlay filesystems under load) retries for a moment
            // before the suite gives up and says so.
            let mut ok = bundled.is_file() && is_executable(&bundled);
            for _ in 0..50 {
                if ok {
                    break;
                }
                std::thread::sleep(Duration::from_millis(100));
                ok = bundled.is_file() && is_executable(&bundled);
            }
            if !ok {
                return None;
            }
            // Prepend so install.sh's own `command -v zsh` and the index
            // zcompile see the same shell this suite answers to.
            let parent = bundled.parent().unwrap().to_string_lossy().to_string();
            let path = std::env::var("PATH").unwrap_or_default();
            std::env::set_var("PATH", format!("{parent}:{path}"));
            // The two hook helpers the plugins autoload ship beside the
            // binary; a bundled zsh with no system around it has nowhere else
            // to find them.
            let fns = repo_path("tests/bin/zsh-functions");
            if fns.is_dir() {
                std::env::set_var("FPATH", &fns);
            }
            Some(bundled)
        })
        .clone()
}

fn is_executable(path: &Path) -> bool {
    // Through the checked std API, not `access(2)`: the raw call wants a
    // NUL-terminated C string, and a `String::as_ptr` is not one — a pointer
    // read past its end answers whichever byte follows the path in memory,
    // which made "is this executable" a coin toss on a churned heap.
    use std::os::unix::fs::PermissionsExt;
    std::fs::metadata(path)
        .map(|m| m.permissions().mode() & 0o111 != 0)
        .unwrap_or(false)
}

fn which(name: &str) -> Option<PathBuf> {
    let path = std::env::var("PATH").ok()?;
    for dir in path.split(':') {
        if dir.is_empty() {
            continue;
        }
        let candidate = Path::new(dir).join(name);
        if is_executable(&candidate) {
            return Some(candidate);
        }
    }
    None
}

pub fn shell_path(shell: &str) -> Option<PathBuf> {
    match shell {
        "bash" => which("bash"),
        "zsh" => zsh_bin(),
        other => panic!("unknown shell {other}"),
    }
}

pub fn db_path() -> PathBuf {
    PathBuf::from("/tmp/tai/tai_plugins_test.db")
}
pub fn record_db_path() -> PathBuf {
    PathBuf::from("/tmp/tai/tai_plugins_record.db")
}
pub fn editor_path() -> PathBuf {
    PathBuf::from("/tmp/tai/tai_dump_line.sh")
}
/// Where a bash test reads COMPREPLY back from. Its own file, not the dump
/// editor's, because sharing one file would make one assertion's output
/// another one's synchronisation point.
pub fn reply_path() -> PathBuf {
    PathBuf::from("/tmp/tai/tai_reply.txt")
}
pub fn index_dir() -> PathBuf {
    PathBuf::from("/tmp/tai/tai_test_index")
}
pub fn zsh_index() -> PathBuf {
    index_dir().join("zsh-index.zsh")
}
pub fn bash_index() -> PathBuf {
    index_dir().join("bash-index.bash")
}
/// Where the record subprocess is allowed to write an index. Recording
/// triggers a background rebuild every 100 stored commands, and that rebuild
/// writes whatever TAI_INDEX points at. Pointing it at the fixture would
/// replace the hand-written index.
pub fn record_index() -> PathBuf {
    index_dir().join("record-index.zsh")
}
pub fn histfile() -> PathBuf {
    PathBuf::from("/tmp/tai/tai_test_history")
}
pub fn menu_dir() -> PathBuf {
    PathBuf::from("/tmp/tai/tai_menu_cwd")
}
pub fn menu_bin() -> PathBuf {
    PathBuf::from("/tmp/tai/tai_menu_bin")
}
pub const MENU_UNRUNNABLE: &[&str] = &["tzz_nodir", "tzz_nope"];
pub const MENU_FAR: &str = "far_side_cmd";
pub const MENU_ENTRIES: &[&str] =
    &["tzz_a", "tzz_b", "tzz_c", "tzz_other", "tzz_solo", "tzz_d", "tzz_dir/"];
pub fn stem_root() -> PathBuf {
    menu_dir().join("stemroot")
}
pub const STEM_NAMES: &[&str] = &[
    "aaa_stem_a_very_long_directory_name/",
    "stem_alpha/",
    "stem_beta/",
    "stem_gamma/",
    "stem_zeta.iso",
];
pub const STEM_FILES: &[&str] = &["wombat book.txt", "wombat cards.txt"];
pub fn prefix_dir() -> PathBuf {
    PathBuf::from("/tmp/tai/tai_prefix_cwd")
}
/// The colours a menu is drawn in: the selected cell (reverse video, no colour
/// of its own) and a directory's name (zsh's `fg=blue`).
pub type Attr = (i32, i32, bool, bool);
pub const SELECTED_ATTR: Attr = (0, 0, false, true);
pub const DIR_FG: i32 = 34;

pub fn file_home() -> PathBuf {
    PathBuf::from("/tmp/tai/tai_file_home")
}
pub fn file_downloads() -> PathBuf {
    file_home().join("Downloads")
}
pub fn file_work() -> PathBuf {
    PathBuf::from("/tmp/tai/tai_file_work")
}
pub const DOWNLOADED: &str = "freebuff-0.0.154-linux-x86_64.AppImage";
pub const WORK_OLD: &str = "notes.txt";
pub fn empty_dir() -> PathBuf {
    PathBuf::from("/tmp/tai/tai_empty_cwd")
}

/// A command that exists on any Linux and appears in no fixture entry, so the
/// "installed but unknown" path can be exercised without inventing a binary.
pub const INSTALLED: &str = "true";
pub const NOT_INSTALLED: &str = "zzznotarealcommand";

// -- keys --------------------------------------------------------------------

pub const RIGHT: &str = "\x1b[C";
/// Right arrow in application cursor-key mode: what konsole and every
/// xterm-compatible terminal send once ZLE has enabled it with smkx.
pub const RIGHT_APP: &str = "\x1bOC";
pub const UP: &str = "\x1b[A";
pub const DOWN: &str = "\x1b[B";
pub const TAB: &str = "\t";
/// What a terminal sends for Enter; zsh reads it as ^M.
pub const ENTER: &str = "\r";
pub const CTRL_F: &str = "\x06";
/// What a terminal sends when the user pastes: the bracketed-paste envelope.
pub const PASTE_START: &str = "\x1b[200~";
pub const PASTE_END: &str = "\x1b[201~";
pub const BACKSPACE: &str = "\x7f";
/// Ctrl-T: the list, asked for directly.
pub const LIST: &str = "\x14";
pub const CTRL_SPACE: &str = "\x00";
pub const CTRL_RIGHT: &str = "\x1b[1;5C";
pub const CTRL_A: &str = "\x01";
pub const CTRL_E: &str = "\x05";
pub const CTRL_K: &str = "\x0b";
pub const CTRL_U: &str = "\x15";
pub const CTRL_X_CTRL_E: &str = "\x18\x05";
/// Not ^X: that is a prefix in zsh's emacs keymap, so a widget bound to it
/// waits out KEYTIMEOUT (0.4s) on every press.
pub const DUMP_KEY_ZSH: &str = "\x19";
pub const CTRL_C: &str = "\x03";
pub const ALT_F: &str = "\x1bf";

pub fn ansi_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"\x1b\[[0-9;?]*[a-zA-Z]").unwrap())
}
pub fn noise_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| {
        Regex::new(
            "bad array subscript|unbound variable|No such file|\
             command not found|: line \\d+:|function definition file not found|\
             No such widget|nested function level",
        )
        .unwrap()
    })
}

// ---------------------------------------------------------------------------
// The fixture rules (fixture_rules.py — the mirror of src/paths.rs)
// ---------------------------------------------------------------------------

pub const WORD_CANDIDATE_CAP: usize = 20;
pub const WORD_KEY_MAX_DEPTH: usize = 8;

const FILE_LAST: &[&str] = &["chmod", "chown", "chgrp", "cp", "mv", "ln", "install", "."];
const FILE_FLAGGED: &[&str] = &[
    "bat", "cat", "code", "diff", "du", "file", "head", "less", "md5sum", "more", "nano", "nvim",
    "readlink", "rm", "sha256sum", "sort", "stat", "tail", "tar", "unzip", "vim", "wc", "xdg-open",
    "zip",
];

fn unquote(token: &str) -> &str {
    let b = token.as_bytes();
    if b.len() >= 2 && b[0] == b[b.len() - 1] && (b[0] == b'"' || b[0] == b'\'') {
        &token[1..token.len() - 1]
    } else {
        token
    }
}

fn unanalysable(ch: char) -> bool {
    "$`*?[]{}<>|;&()!#\\\n\t\"'".contains(ch)
}

fn is_path_like(token: &str) -> bool {
    if token.is_empty() || token.starts_with('-') || token.contains('=') {
        return false;
    }
    if token.chars().any(unanalysable) {
        return false;
    }
    if token.contains("://") {
        return false;
    }
    if let Some((host, rest)) = token.split_once(':') {
        if !rest.is_empty() && !host.contains('/') {
            return false;
        }
    }
    if !token.is_empty()
        && token
            .trim_start_matches(['+', '-'])
            .chars()
            .all(|c| c.is_ascii_digit())
    {
        return false;
    }
    if token == "." || token == ".." {
        return true;
    }
    if token.starts_with('~') {
        return token == "~" || token.starts_with("~/");
    }
    if token.starts_with('.') {
        return !token
            .trim_start_matches('.')
            .chars()
            .all(|c| c.is_ascii_digit() || c == '.');
    }
    if !token.contains('/') {
        return false;
    }
    let head = token.trim_start_matches(['~', '/']).split('/').next().unwrap_or("");
    if !head.is_empty()
        && head.chars().all(|c| c.is_ascii_digit() || ".+-:".contains(c))
        && head.chars().any(|c| c.is_ascii_digit())
    {
        return false;
    }
    true
}

pub fn takes_file(cmd: &str) -> bool {
    let tokens: Vec<&str> = cmd.split_whitespace().collect();
    if tokens.len() < 2 {
        return false;
    }
    let last = unquote(tokens[tokens.len() - 1]);
    if last.starts_with('-') || last.chars().any(unanalysable) {
        return false;
    }
    let head = tokens[0];
    if FILE_LAST.contains(&head) {
        return true;
    }
    if !FILE_FLAGGED.contains(&head) {
        return false;
    }
    let mut previous = head;
    for token in &tokens[1..tokens.len() - 1] {
        if !token.starts_with('-') && !previous.starts_with('-') {
            return false;
        }
        previous = token;
    }
    true
}

pub fn relative_word_set(cmds: &[&str]) -> Vec<String> {
    let mut out: Vec<String> = Vec::new();
    for cmd in cmds {
        for token in cmd.split_whitespace() {
            let token = unquote(token);
            if token.starts_with('~') || token.starts_with('/') {
                continue;
            }
            if is_path_like(token) && !out.iter().any(|w| w == token) {
                out.push(token.to_string());
            }
        }
    }
    out.sort();
    out
}

// ---------------------------------------------------------------------------
// The fixture index (plugin_env.py)
// ---------------------------------------------------------------------------

/// The hand-written index, deliberately not a built one. Order matters:
/// `_tai_best` takes the highest `_TAI_SCORE`, so the first entry is the winner
/// for any prefix it matches. `cat .zshrc` sits last, so it is the best answer
/// for no prefix the other entries answer: the line exists so the file-word
/// rule has a hidden file to judge.
pub const DEFAULT_COMMANDS: &[&str] = &[
    "git pull --rebase",
    "git status",
    "docker ps",
    "ls -la",
    "ls -l",
    "cd tzz_dir",
    "cd /tmp",
    "cd ..",
    "cd -",
    "chmod +x script",
    "cat tzz_d",
    "cat .zshrc",
];

fn zq(value: &str) -> String {
    if value.contains('\'') {
        panic!("index fixture cannot quote {value:?}");
    }
    format!("'{value}'")
}

/// Build the (kind, key, newline-joined values) fixture rows. Mirrors the real
/// generator exactly: no single-word `_TAI_WORD` key (the command-name list
/// lives once, in `_TAI_FIRST`), no trailing-space key, the candidate cap and
/// the key depth from the same constants.
fn index_rows(commands: &[&str]) -> Vec<(String, String, String)> {
    let mut rows: Vec<(String, String, String)> = Vec::new();
    for (i, cmd) in commands.iter().enumerate() {
        rows.push(("SCORE".into(), cmd.to_string(), (commands.len() - i).to_string()));
    }
    let mut first: Vec<(String, Vec<String>)> = Vec::new();
    let mut words: Vec<(String, Vec<String>)> = Vec::new();
    for cmd in commands {
        let parts: Vec<&str> = cmd.split(' ').collect();
        match first.iter_mut().find(|(k, _)| *k == parts[0]) {
            Some(entry) => entry.1.push(cmd.to_string()),
            None => first.push((parts[0].to_string(), vec![cmd.to_string()])),
        }
        for i in 2..=parts.len().min(WORD_KEY_MAX_DEPTH) {
            let key = parts[..i].join(" ");
            match words.iter_mut().find(|(k, _)| *k == key) {
                Some(entry) => entry.1.push(cmd.to_string()),
                None => words.push((key, vec![cmd.to_string()])),
            }
        }
    }
    first.sort_by(|a, b| a.0.cmp(&b.0));
    for (key, values) in first {
        rows.push(("FIRST".into(), key, values.join("\n")));
    }
    words.sort_by(|a, b| a.0.cmp(&b.0));
    for (key, values) in words {
        rows.push((
            "WORD".into(),
            key,
            values[..values.len().min(WORD_CANDIDATE_CAP)].join("\n"),
        ));
    }
    // The file-argument marks, from the same `takes_file` the generator uses;
    // head keys are a set, as the builder's are.
    let mut file_heads: Vec<String> = Vec::new();
    for cmd in commands {
        if takes_file(cmd) {
            if let Some((head, _)) = cmd.rsplit_once(' ') {
                if !head.is_empty() && !file_heads.iter().any(|h| h == head) {
                    file_heads.push(head.to_string());
                }
            }
        }
    }
    file_heads.sort();
    for head in file_heads {
        rows.push(("FILE".into(), head, "1".into()));
    }
    // The relative-path words, from the same `relative_word_set` the generator
    // uses — the other map the shells judge a candidate by.
    for word in relative_word_set(commands) {
        rows.push(("LIVE".into(), word, "1".into()));
    }
    rows
}

fn backdate(path: &Path) {
    // `-nt` compares whole seconds in both shells, so "the file moved on" has
    // to be more than a second apart to be visible: the index keeps its natural
    // "just written" mtime and the stamp is moved into the past.
    // `File::set_times`, not `utimes(2)`: the raw call has been seen to fail
    // silently, and a backdate that did not happen is a test that sleeps.
    use std::fs::FileTimes;
    if !path.exists() {
        std::fs::write(path, "").unwrap();
    }
    let old = std::time::UNIX_EPOCH
        + std::time::Duration::from_secs((now_secs() - 30).max(0) as u64);
    let f = std::fs::OpenOptions::new().append(true).open(path).unwrap();
    f.set_times(FileTimes::new().set_accessed(old).set_modified(old)).unwrap();
}

fn now_secs() -> i64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap()
        .as_secs() as i64
}

/// Write both plugin index snapshots from the fixed fixture. `commands` of
/// `None` means the default fixture; `new_enough_than` back-dates a stamp file
/// instead of the test sleeping.
pub fn write_index(commands: Option<&[&str]>, new_enough_than: Option<&Path>) {
    let commands: Vec<&str> = commands.unwrap_or(DEFAULT_COMMANDS).to_vec();
    let dir = index_dir();
    std::fs::create_dir_all(&dir).unwrap();
    let rows = index_rows(&commands);

    // The zsh snapshot is written in the same shape the real generator uses —
    // a pair appended per entry — so the tests load the format that ships.
    let mut zsh = String::from(
        "# tai zsh index; test fixture, not generated\n\
         typeset -gA _TAI_SCORE _TAI_FIRST _TAI_WORD _TAI_SEQ _TAI_FILE _TAI_LIVE\n",
    );
    for (kind, key, value) in &rows {
        zsh.push_str(&format!("_TAI_{kind}+=({} {})\n", zq(key), zq(value)));
    }
    std::fs::write(zsh_index(), zsh).unwrap();

    let mut bash = String::from(
        "# tai bash index; test fixture, not generated\n\
         declare -gA _TAI_SCORE _TAI_FIRST _TAI_WORD _TAI_SEQ _TAI_FILE _TAI_LIVE\n",
    );
    for (kind, key, value) in &rows {
        bash.push_str(&format!("_TAI_{kind}[{}]={}\n", zq(key), zq(value)));
    }
    std::fs::write(bash_index(), bash).unwrap();

    if let Some(stamp) = new_enough_than {
        backdate(stamp);
    }
}

fn write_script(path: &Path, body: &str, mode: u32) {
    std::fs::write(path, body).unwrap();
    // Through the checked std API, not `chmod(2)`: the raw call wants a
    // NUL-terminated C string, and a `String::as_ptr` is not one — the mode
    // was set on whichever path the bytes after the string happened to name,
    // which is how a dump editor the shell may not exec and menu scripts
    // that came out 0644 were both possible from the same writer.
    use std::os::unix::fs::PermissionsExt;
    let mut perm = std::fs::metadata(path).unwrap().permissions();
    perm.set_mode(mode);
    std::fs::set_permissions(path, perm).unwrap();
}

/// Build the scratch world: the fixture index, the menu directories, the fake
/// editor, a scratch HISTFILE. Every entry point calls this before its first
/// session.
pub fn setup() {
    std::fs::create_dir_all("/tmp/tai").unwrap();
    for suffix in ["", "-wal", "-shm"] {
        let mut p = db_path().into_os_string();
        p.push(suffix);
        let _ = std::fs::remove_file(&p);
    }
    write_index(None, None);
    let _ = std::fs::remove_dir_all(menu_dir());
    std::fs::create_dir_all(menu_dir()).unwrap();
    for name in ["tzz_a", "tzz_b", "tzz_c", "tzz_solo"] {
        write_script(
            &menu_dir().join(name),
            &format!("#!/bin/sh\necho 'RAN {name}'\n"),
            0o755,
        );
    }
    std::fs::write(menu_dir().join("tzz_d"), "").unwrap();
    std::fs::create_dir_all(menu_dir().join("tzz_dir")).unwrap();

    let _ = std::fs::remove_dir_all(prefix_dir());
    std::fs::create_dir_all(prefix_dir()).unwrap();
    std::fs::create_dir_all(prefix_dir().join(".zsh")).unwrap();
    std::fs::write(prefix_dir().join(".zshrc"), "").unwrap();
    std::fs::create_dir_all(prefix_dir().join("Downloads")).unwrap();
    std::fs::write(prefix_dir().join("Downloads.zip"), "").unwrap();
    std::fs::write(prefix_dir().join("Downloads2.txt"), "").unwrap();
    std::fs::create_dir_all(prefix_dir().join("My Docs")).unwrap();
    std::fs::write(prefix_dir().join("My Docs2.txt"), "").unwrap();
    std::fs::write(prefix_dir().join("readme.txt"), "").unwrap();

    std::fs::create_dir_all(stem_root()).unwrap();
    for name in STEM_NAMES {
        if let Some(dir) = name.strip_suffix('/') {
            std::fs::create_dir_all(stem_root().join(dir)).unwrap();
        } else {
            std::fs::write(stem_root().join(name), "").unwrap();
        }
    }
    for name in STEM_FILES {
        std::fs::write(menu_dir().join(name), "").unwrap();
    }

    let _ = std::fs::remove_dir_all(empty_dir());
    std::fs::create_dir_all(empty_dir()).unwrap();

    let _ = std::fs::remove_dir_all(menu_bin());
    std::fs::create_dir_all(menu_bin()).unwrap();
    write_script(
        &menu_bin().join("tzz_other"),
        "#!/bin/sh\necho 'RAN tzz_other'\n",
        0o755,
    );
    write_script(
        &menu_bin().join(MENU_FAR),
        &format!("#!/bin/sh\necho 'RAN {MENU_FAR}'\n"),
        0o755,
    );
    std::fs::write(menu_bin().join("tzz_nope"), "").unwrap();
    std::fs::create_dir_all(menu_bin().join("tzz_nodir")).unwrap();

    // A scratch HISTFILE is not optional. bash and zsh both append every line
    // they read to $HISTFILE, including lines the test typed and then
    // abandoned; without this the harness appends its own keystrokes to the
    // developer's real history.
    let _ = std::fs::remove_file(histfile());

    // C-x C-e hands the editing buffer to $EDITOR. Copying it out and exiting
    // non-zero is the only reliable way to observe what a key sequence left in
    // the line, and the non-zero exit stops the command from running. The
    // destination is $TAI_TEST_DUMP, which every session sets to a file of its
    // own — a single shared path is a shared mailbox.
    write_script(
        &editor_path(),
        "#!/bin/sh\ncp \"$1\" \"$TAI_TEST_DUMP\" 2>/dev/null\nexit 1\n",
        0o755,
    );
}

// ---------------------------------------------------------------------------
// The session (plugin_pty.py)
// ---------------------------------------------------------------------------

/// Milliseconds of silence that mean "the shell caught up".
const QUIET: Duration = Duration::from_millis(20);
/// Backstop for a single wait.
const DEADLINE: Duration = Duration::from_secs(20);

/// An interactive shell on a pty with the tai plugin sourced.
///
/// Every wait here ends on evidence rather than on a guess. `run` waits for a
/// marker the shell itself printed, `send` waits for the terminal to go quiet,
/// and `dump` waits for the editor to write the file. Timeouts are backstops
/// that panic with the terminal tail attached, so a hang becomes a failure
/// with a diagnosis instead of a stall.
pub struct Session {
    fd: i32,
    pid: i32,
    shell: String,
    chunks: Vec<String>,
    seen: String,
    closed: bool,
    quiet: bool,
    marker: usize,
    buf: PathBuf,
}

thread_local! {
    static NEXT_BUF: std::cell::Cell<usize> = const { std::cell::Cell::new(0) };
}

fn reap(pid: i32) {
    unsafe {
        let mut status = 0;
        let mut waited = false;
        // Give the shell a moment to leave on its own; then make sure.
        for _ in 0..200 {
            let r = libc::waitpid(pid, &mut status, libc::WNOHANG);
            if r == pid {
                waited = true;
                break;
            }
            if r < 0 && *libc::__errno_location() == libc::ECHILD {
                return;
            }
            std::thread::sleep(Duration::from_millis(5));
        }
        if !waited {
            libc::kill(pid, libc::SIGKILL);
            libc::waitpid(pid, &mut status, 0);
        }
    }
}

impl Session {
    /// A session with the default environment, plugin sourced.
    pub fn new(shell: &str) -> Session {
        Session::new_with(shell, &[], true)
    }

    /// A session with extra environment riding on top of the defaults.
    /// `source=false` skips sourcing the plugin but still binds the dump key.
    pub fn new_with(shell: &str, env_extra: &[(&str, &str)], source: bool) -> Session {
        let n = NEXT_BUF.with(|c| {
            c.set(c.get() + 1);
            c.get()
        });
        // This session's own dump file, named so two shells — in this run or in
        // a run that was killed and left one behind — cannot answer each
        // other's questions.
        let buf = PathBuf::from(format!("/tmp/tai/tai_line_{}_{}.txt", std::process::id(), n));
        let bin = {
            let release = repo_path("target/release/tai");
            if release.exists() {
                release
            } else {
                repo_path("target/debug/tai")
            }
        };
        // Resolve the shell binary first: zsh_bin() may prepend the bundled
        // zsh's directory to PATH and set FPATH, and the child must inherit
        // those — so this happens before the environment is collected.
        let shell_exe = shell_path(shell)
            .unwrap_or_else(|| panic!("no {shell} on this machine; the pty suite cannot run"))
            .to_string_lossy()
            .into_owned();
        let mut env: Vec<(String, String)> = std::env::vars().collect();
        let shell = shell.to_string();
        let overrides: Vec<(String, String)> = vec![
            ("TAI_DB".into(), db_path().to_string_lossy().into()),
            ("PS1".into(), "P> ".into()),
            ("TERM".into(), "xterm-256color".into()),
            ("TAI_TEST_DUMP".into(), buf.to_string_lossy().into()),
            ("TAI_BIN".into(), bin.to_string_lossy().into()),
            ("EDITOR".into(), editor_path().to_string_lossy().into()),
            ("HISTFILE".into(), histfile().to_string_lossy().into()),
            ("TAI_INDEX".into(), zsh_index().to_string_lossy().into()),
            // Keep the run hermetic: no learning on use, no recording — either
            // would rebuild the fixture index mid-run and the test that breaks
            // is whichever one happens to be reading the index at the time.
            // Only the recording test turns these back on.
            ("TAI_NO_LEARN".into(), "1".into()),
            ("TAI_NO_AUTO_RECORD".into(), "1".into()),
        ];
        for (k, v) in overrides {
            env.retain(|(ek, _)| ek != &k);
            env.push((k, v));
        }
        for (k, v) in env_extra {
            env.retain(|(ek, _)| ek != k);
            env.push((k.to_string(), v.to_string()));
        }

        // A pty pair. The child becomes a session leader with the slave as its
        // controlling terminal; the parent keeps the master.
        let mut master: i32 = -1;
        let mut slave: i32 = -1;
        unsafe {
            assert_eq!(
                libc::openpty(
                    &mut master,
                    &mut slave,
                    std::ptr::null_mut(),
                    std::ptr::null(),
                    std::ptr::null_mut()
                ),
                0
            );
        }
        let mut cmd = Command::new(&shell_exe);
        match shell.as_str() {
            "bash" => cmd.args(["--norc", "-i"]),
            _ => cmd.args(["-f", "-i"]),
        };
        cmd.env_clear();
        for (k, v) in &env {
            cmd.env(k, v);
        }
        use std::os::unix::io::FromRawFd;
        // Three dups, one for each stdio slot; std dup2s them onto 0/1/2.
        let s0 = unsafe { std::process::Stdio::from_raw_fd(libc::dup(slave)) };
        let s1 = unsafe { std::process::Stdio::from_raw_fd(libc::dup(slave)) };
        let s2 = unsafe { std::process::Stdio::from_raw_fd(libc::dup(slave)) };
        cmd.stdin(s0).stdout(s1).stderr(s2);
        let hook_slave = unsafe { libc::dup(slave) };
        unsafe {
            cmd.pre_exec(move || {
                libc::setsid();
                // The slave is this child's controlling terminal, so the shell
                // is a real interactive session: job control, line discipline,
                // the works.
                libc::ioctl(hook_slave, libc::TIOCSCTTY, 0 as libc::c_ulong);
                libc::close(hook_slave);
                Ok(())
            });
        }
        let child = cmd.spawn().expect("failed to spawn the shell on a pty");
        unsafe {
            libc::close(slave);
        }
        // Give the pty the size the screen assertions read. A winsize of zero
        // makes zsh hand ZLE a COLUMNS of its own invention rather than the
        // width the terminal really has — so a row painted to fit that number
        // wraps a screen of 80 and every width assertion is measured against a
        // fiction. The screen this module emulates is COLS wide; the pty now
        // says so too.
        let ws = libc::winsize {
            ws_row: screen::ROWS,
            ws_col: screen::COLS,
            ws_xpixel: 0,
            ws_ypixel: 0,
        };
        unsafe {
            libc::ioctl(master, libc::TIOCSWINSZ, &ws as *const libc::winsize);
        }

        let mut session = Session {
            fd: master,
            pid: child.id() as i32,
            shell,
            chunks: Vec::new(),
            seen: String::new(),
            closed: false,
            quiet: false,
            marker: 0,
            buf,
        };
        // One write, one wait. The shell reads the whole chunk and runs the
        // lines in order, so the marker on the last line proves the plugin is
        // sourced and the dump key is bound. Asking for each of those
        // separately cost four round trips per session.
        let lines = setup_lines(&session.shell, source);
        session.run(&lines.join("\n"));
        session
    }

    // -- waiting ----------------------------------------------------------

    /// Read whatever has arrived. True when something did.
    ///
    /// `quiet` afterwards says the pty then went silent for a whole window —
    /// the one piece of evidence `settle` exists to collect. Leaving by the
    /// other door — the shell being gone — is not silence and must not look
    /// like it. The flag is reset first so it describes this read and never
    /// the one before it.
    fn read_once(&mut self, timeout: Duration) -> bool {
        self.quiet = false;
        let mut got: Vec<u8> = Vec::new();
        loop {
            let mut pfd =
                libc::pollfd { fd: self.fd, events: libc::POLLIN, revents: 0 };
            let r = unsafe { libc::poll(&mut pfd, 1, timeout.as_millis() as i32) };
            if r < 0 {
                if std::io::Error::last_os_error().raw_os_error() == Some(libc::EINTR) {
                    continue;
                }
                break;
            }
            if r == 0 {
                self.quiet = true; // nothing arrived inside the window
                break;
            }
            let mut chunk = [0u8; 65536];
            let n = unsafe {
                libc::read(self.fd, chunk.as_mut_ptr() as *mut libc::c_void, chunk.len())
            };
            if n <= 0 {
                // 0 is EOF; -1 with EIO is what Linux gives when the child is
                // gone. Either way: the shell is gone, which is not silence.
                self.closed = true;
                break;
            }
            got.extend_from_slice(&chunk[..n as usize]);
        }
        if got.is_empty() {
            return false;
        }
        let text = String::from_utf8_lossy(&got).into_owned();
        self.chunks.push(text.clone());
        self.seen.push_str(&text);
        true
    }

    /// Read until the shell has stopped talking.
    ///
    /// This is the honest way to know a keystroke was dealt with: the echo and
    /// the redraw are the shell answering. One window, not two — a read that
    /// ended on a timed-out poll has already waited out a full window in
    /// silence, so asking once more buys the same answer.
    pub fn settle(&mut self) {
        self.settle_for(QUIET);
    }

    pub fn settle_for(&mut self, quiet: Duration) {
        while self.read_once(quiet) {
            if self.quiet {
                return;
            }
        }
    }

    /// Block until `pattern` shows up in the terminal output.
    pub fn wait_re(&mut self, pattern: &str, what: &str) {
        self.wait_re_since(pattern, what, 0);
    }

    /// Block until `pattern` shows up in output produced after `since`, so a
    /// pattern that also appears in earlier output — the session's own setup
    /// lines — cannot match by accident.
    pub fn wait_re_since(&mut self, pattern: &str, what: &str, since: usize) {
        let rx = Regex::new(pattern).unwrap();
        let end = Instant::now() + DEADLINE;
        while !rx.is_match(&self.seen[since..]) {
            if Instant::now() >= end {
                panic!(
                    "timed out waiting for {what}\n--- terminal tail ---\n{}",
                    tail(&self.seen, 700)
                );
            }
            self.read_once(Duration::from_millis(10).min(end.saturating_duration_since(Instant::now())));
        }
    }

    /// Block until `path` exists and has content.
    pub fn wait_file(&mut self, path: &Path, what: &str) {
        let end = Instant::now() + DEADLINE;
        loop {
            if let Ok(meta) = std::fs::metadata(path) {
                if meta.len() > 0 {
                    return;
                }
            }
            if Instant::now() > end {
                panic!(
                    "timed out waiting for {what} ({})\n--- terminal tail ---\n{}",
                    path.display(),
                    tail(&self.seen, 700)
                );
            }
            self.read_once(Duration::from_millis(5));
        }
    }

    // -- writing ----------------------------------------------------------

    /// Write bytes and return immediately, with no waiting attached.
    pub fn write(&mut self, data: &str) {
        let bytes = data.as_bytes();
        let mut done = 0;
        while done < bytes.len() {
            let n =
                unsafe { libc::write(self.fd, bytes[done..].as_ptr() as _, bytes.len() - done) };
            if n < 0 {
                if std::io::Error::last_os_error().raw_os_error() == Some(libc::EINTR) {
                    continue;
                }
                panic!("write to the pty failed: {}", std::io::Error::last_os_error());
            }
            done += n as usize;
        }
    }

    /// Press `data` like a person, and wait for the shell to catch up.
    ///
    /// The wait ends on the shell going quiet, not on a fixed pause. Control
    /// keys echo nothing, so there is nothing to match on; printable text is no
    /// better, because back-to-back writes arrive as one read and the line
    /// editor then redraws once at the end instead of per character. Waiting
    /// for the *last* burst of output to end is the only signal that is true in
    /// both cases.
    ///
    /// One character per press, thirty milliseconds between them — slower than
    /// the plugin's fiftieth-of-a-second paste bound, faster than the
    /// wait-each-echo style. Control bytes keep no pause, and neither does the
    /// byte that ends an escape sequence. Text a test wants to arrive as a
    /// *paste* goes through `write` — that is the terminal speaking.
    pub fn send(&mut self, data: &str) {
        self.send_pause(data, 30);
    }

    pub fn send_pause(&mut self, data: &str, pause_ms: u64) {
        let chars: Vec<char> = data.chars().collect();
        for (i, ch) in chars.iter().enumerate() {
            self.write(&ch.to_string());
            let nxt = chars.get(i + 1).copied().unwrap_or('\0');
            if *ch > ' ' && *ch != '\x7f' && nxt > ' ' && nxt != '\x7f' {
                std::thread::sleep(Duration::from_millis(pause_ms));
            }
        }
        self.settle();
    }

    /// Run `cmd` and wait for it to finish.
    ///
    /// `cmd` may be several lines; the shell runs them in order, so a marker on
    /// the last line is proof the whole block ran. The marker is assembled
    /// inside the shell, because the terminal echoes the command before running
    /// it: a marker typed literally appears on screen twice, and the first is
    /// the echo. `;` rather than `&&`, so a failing command still answers
    /// instead of hanging the harness.
    pub fn run(&mut self, cmd: &str) {
        self.marker += 1;
        let marker = format!("__tai_done_{}__", self.marker);
        if !cmd.contains('\n') {
            self.write(&format!(
                "_tai_m=\"__tai_\"; _tai_m+=\"done_{}__\"; {cmd}; echo \"$_tai_m\"\n",
                self.marker
            ));
        } else {
            self.write(&format!(
                "_tai_m=\"__tai_\"; _tai_m+=\"done_{}__\"\n{cmd}\necho \"$_tai_m\"\n",
                self.marker
            ));
        }
        self.wait_re(&regex::escape(&marker), "a command to finish");
    }

    pub fn raw(&self) -> String {
        self.chunks.join("")
    }

    /// How much output has been seen so far — the `since` marks.
    pub fn seen_len(&self) -> usize {
        self.seen.len()
    }

    fn dump(&mut self, clear: bool) -> Vec<String> {
        let _ = std::fs::remove_file(&self.buf);
        // C-x C-e in bash aborts the line, so the buffer is safe to read once.
        // The zsh widget only copies it, so clear the line afterwards.
        if self.shell == "bash" {
            self.write(CTRL_X_CTRL_E);
        } else {
            self.write(DUMP_KEY_ZSH);
        }
        // Both shells end by writing the dump file: bash through $EDITOR, zsh
        // from the widget itself. That file appearing is the synchronisation
        // point, so there is nothing left to guess here.
        let buf = self.buf.clone();
        self.wait_file(&buf, "the line dump");
        if self.shell == "zsh" && clear {
            self.write(CTRL_C);
        }
        self.settle();
        let text = std::fs::read_to_string(&self.buf).unwrap_or_default();
        let _ = std::fs::remove_file(&self.buf);
        text.trim_end_matches('\n').split('\n').map(|s| s.to_string()).collect()
    }

    /// Read the current editing buffer, then abandon the line.
    pub fn line(&mut self) -> String {
        self.dump(true).into_iter().next().unwrap_or_default()
    }

    /// ZLE's current ghost text for the buffer (zsh only).
    pub fn suggestion(&mut self) -> String {
        self.dump(true).get(1).cloned().unwrap_or_default()
    }

    /// Read the line and the menu standing on it, in one pass.
    ///
    /// Returns the buffer, the menu as drawn, how many entries it holds, and
    /// which one is marked. The menu is read the way a user reads it — out of
    /// POSTDISPLAY, with the newlines spelled out — rather than by reaching
    /// into the plugin's arrays: a menu with the right entries in the right
    /// array and the wrong mark on screen is still wrong.
    ///
    /// `clear=false` leaves the line alone, which is what a test that walks
    /// the selection with Tab needs. Abandoning the line closes the menu, and
    /// the next Tab would then open a fresh one at the first entry.
    pub fn menu(&mut self, clear: bool) -> MenuRead {
        let got = self.dump(clear);
        let line3 = got.get(2).cloned().unwrap_or_default();
        let rx = Regex::new(r"MENU=\[(.*)\] N=\[(\d+)\] IDX=\[(\d+)\]").unwrap();
        match rx.captures(&line3) {
            Some(c) => MenuRead {
                line: got.first().cloned().unwrap_or_default(),
                drawn: c[1].to_string(),
                entries: c[2].parse().unwrap(),
                idx: c[3].parse().unwrap(),
            },
            None => MenuRead {
                line: got.first().cloned().unwrap_or_default(),
                drawn: String::new(),
                entries: 0,
                idx: 0,
            },
        }
    }

    /// The stray stderr lines the session produced, if any.
    pub fn noise(&self) -> Vec<String> {
        let mut out = Vec::new();
        for raw in self.raw().replace('\r', "\n").lines() {
            let line = ansi_re().replace_all(raw, "").trim().to_string();
            if !line.is_empty() && noise_re().is_match(&line) && !line.contains("source") {
                out.push(line);
            }
        }
        out
    }

    pub fn close(&mut self) {
        // Clear the line before exiting — `exit\n` alone assumes an empty
        // prompt, and a session that left a partial one behind appended `exit`
        // to it instead: bash ran `cat tzz_exit`, reported no such file, and
        // sat there. One Ctrl-U, which both readline and ZLE bind to discard
        // the line, and the shell leaves the moment it is asked to.
        self.write("\x15");
        self.write("exit\n");
        let end = Instant::now() + Duration::from_secs(5);
        while !self.closed && Instant::now() < end {
            self.read_once(Duration::from_millis(50));
        }
        unsafe {
            libc::close(self.fd);
        }
        self.closed = true;
        reap(self.pid);
    }
}

impl Drop for Session {
    fn drop(&mut self) {
        if !self.closed {
            self.close();
        } else {
            unsafe {
                libc::close(self.fd);
            }
        }
    }
}

fn setup_lines(shell: &str, source: bool) -> Vec<String> {
    let mut lines = Vec::new();
    if source {
        lines.push(format!("source {}", plugin_path(shell).display()));
    }
    if shell == "zsh" {
        // The widget writes the buffer on line 1, ZLE's own view of the ghost
        // text on line 2, and the Tab menu on line 3. Lines 2 and 3 go through
        // ${(V)}, which renders a newline as a literal \n: a menu is a
        // multi-line POSTDISPLAY, and without that its first line would spill
        // into the next and push every later reading of the dump out by one.
        lines.push(
            "tai_test_dump() { print -r -- \"$BUFFER\" > \"$TAI_TEST_DUMP\"; \
             print -r -- \"SUG=[$_TAI_SUGGESTION] PD=[${(V)POSTDISPLAY}] \
             RH=[${(j: :)region_highlight}]\" >> \"$TAI_TEST_DUMP\"; \
             print -r -- \"MENU=[${(V)POSTDISPLAY}] N=[${#_TAI_MENU}] \
             IDX=[$_TAI_MENU_IDX]\" >> \"$TAI_TEST_DUMP\"; }; \
             zle -N tai_test_dump; bindkey '^Y' tai_test_dump"
                .to_string(),
        );
    } else {
        lines.push(format!("export EDITOR={}", editor_path().display()));
    }
    lines
}

/// What `Session::menu` hands back.
pub struct MenuRead {
    pub line: String,
    pub drawn: String,
    pub entries: usize,
    pub idx: usize,
}

/// Type `prefix` + `keys` in a fresh shell and check the resulting buffer.
///
/// The prefix and the keys go in as **one** send — nothing is asserted between
/// them, and the line editor reads the whole chunk in order anyway, so the
/// prefix is dealt with before the keys are looked up. The noise check runs
/// after `close()`, which reads to EOF, so every byte the session produced is
/// in hand; checking earlier means a quiet-looking run.
pub fn probe(shell: &str, keys: &str, prefix: &str, want: &str) {
    probe_env(shell, keys, prefix, want, &[]);
}

pub fn probe_env(shell: &str, keys: &str, prefix: &str, want: &str, env: &[(&str, &str)]) {
    let mut s = Session::new_with(shell, env, true);
    s.send(&format!("{prefix}{keys}"));
    let got = s.line();
    s.close();
    let noise = s.noise();
    assert_eq!(got, want, "{shell} {prefix:?}+{keys:?}");
    assert!(
        noise.is_empty(),
        "{shell} {keys:?} was not clean: {noise:?}\n       terminal tail:\n{}",
        indent(&tail(&s.raw(), 400), "         ")
    );
}

/// How many Tab presses it takes before the matches are on screen. Ends on the
/// thing being asserted: the listing arriving. A fixed number of presses would
/// pass whether or not the listing was there.
pub fn tabs_to_list(env: &[(&str, &str)]) -> usize {
    let mut s = Session::new_with("bash", env, true);
    s.run(&format!("cd {}", menu_dir().display()));
    s.send("cat tzz");
    let mut presses = 0;
    for p in 1..=3 {
        presses = p;
        let mark = s.seen_len();
        s.write(TAB);
        s.settle();
        if s.raw()[mark..].contains("tzz_a") {
            break;
        }
    }
    s.close();
    presses
}

/// One zsh session, asked for many prefixes.
///
/// The dump widget copies the buffer out instead of running it, and `dump`
/// clears the line afterwards, so a session survives being read. That matters
/// because a session costs a shell fork and a plugin load: asking for the
/// ghost text of one prefix per shell paid that for every single assertion.
pub struct Ghosts {
    s: Session,
}

impl Ghosts {
    pub fn new() -> Ghosts {
        Ghosts { s: Session::new("zsh") }
    }

    pub fn of(&mut self, prefix: &str) -> String {
        let mark = self.s.seen_len();
        self.s.send(prefix);
        // Wait for the shell to echo the prefix before asking for the ghost.
        // Sending the dump key straight after the prefix raced ZLE's redraw:
        // the widget could run before the line had been redrawn for the new
        // text, and the answer was the *previous* prefix's suggestion. The
        // echo is the observable event: it cannot happen before ZLE has
        // displayed the line, and the ghost text is recomputed on that same
        // redraw.
        if !prefix.is_empty() {
            let last = prefix.chars().last().unwrap();
            self.s
                .wait_re_since(&regex::escape(&last.to_string()), "the prefix echo", mark);
        }
        let got = self.s.suggestion();
        let got = got.split(" PD=").next().unwrap_or("").to_string();
        if !got.starts_with("SUG=[") {
            return format!("<no suggestion: {:?}>", self.s.noise());
        }
        got["SUG=[".len()..got.len() - 1].to_string()
    }

    pub fn close(&mut self) {
        self.s.close();
    }
}

impl Default for Ghosts {
    fn default() -> Self {
        Self::new()
    }
}

// -- the store ---------------------------------------------------------------

/// The recorded rows of a store, in id order.
pub fn rows(db: &Path) -> Vec<String> {
    let con = match rusqlite::Connection::open(db) {
        Ok(con) => con,
        Err(_) => return Vec::new(),
    };
    let mut stmt = match con.prepare("select cmd from commands order by id") {
        Ok(stmt) => stmt,
        Err(_) => return Vec::new(),
    };
    stmt.query_map([], |r| r.get::<_, String>(0))
        .map(|rs| rs.filter_map(|r| r.ok()).collect())
        .unwrap_or_default()
}

/// The typed commands that have *not* been recorded yet, once they land.
///
/// The plugin's record runs in the background, so the rows appear some time
/// after the prompt returns. Polling for them is both faster than a fixed
/// pause and honest: it is the only wait that ends on the thing being
/// asserted. The ceiling is the runner's, not the product's.
pub fn await_rows(db: &Path, expected: &[&str], timeout: Duration) -> Vec<String> {
    let end = Instant::now() + timeout;
    loop {
        let got = rows(db);
        let missing: Vec<String> = expected
            .iter()
            .filter(|c| !got.iter().any(|g| g == *c))
            .map(|c| c.to_string())
            .collect();
        if missing.is_empty() || Instant::now() > end {
            return missing;
        }
        std::thread::sleep(Duration::from_millis(10));
    }
}

/// Fail loudly if something replaced the hand-written index.
///
/// Every assertion in the pty suite depends on the fixture, and the plugin
/// re-reads it from disk. Anything that writes it — the 100-command maintenance
/// rebuild, a learn-on-use rebuild — turns a later assertion into a mystery,
/// because the test that actually broke ran several tests ago. Naming the
/// overwrite is worth more than the assertion it costs.
pub fn check_fixture_intact(where_: &str) {
    for path in [zsh_index(), bash_index()] {
        let head = std::fs::read_to_string(&path)
            .unwrap_or_default()
            .lines()
            .next()
            .unwrap_or("")
            .to_string();
        assert!(
            head.starts_with("# tai"),
            "{where_} left the index fixture intact: {path:?} now starts {head:?}"
        );
    }
}
