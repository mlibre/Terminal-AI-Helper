//! Path liveness for command candidates, and every location decision.
//!
//! Port of `tai/paths.py`. A command is only a useful suggestion if the paths
//! it names still exist. The check is deliberately conservative: a token is
//! judged only when it is unambiguously a filesystem path; unknown means "no
//! opinion" and never removes a candidate; MISSING is the only verdict that
//! does. It runs at index build time and on the one-shot suggest path — never
//! on the keystroke path.

use std::collections::{HashMap, HashSet};
use std::env;
use std::fs;
use std::path::{Path, PathBuf};
use std::sync::{Mutex, OnceLock};

pub const EXISTS: &str = "exists";
pub const MISSING: &str = "missing";
pub const UNKNOWN: &str = "unknown";

/// Commands whose first argument names a directory, by what the command means.
pub const DIRECTORY_ARGUMENT: &[&str] = &["cd", "pushd"];

/// What a `cd` argument has to be for it to be a *destination*.
pub const NOT_A_DESTINATION: &[&str] = &[".", "..", "-", "./", "../"];

/// Commands whose *last* word is a file, whatever sits in front of it.
pub const FILE_LAST: &[&str] = &["chmod", "chown", "chgrp", "cp", "mv", "ln", "install", "."];

/// Commands whose last word is a file when everything in front of it is a flag.
pub const FILE_FLAGGED: &[&str] = &[
    "bat", "cat", "code", "diff", "du", "file", "head", "less", "md5sum", "more", "nano", "nvim",
    "readlink", "rm", "sha256sum", "sort", "stat", "tail", "tar", "unzip", "vim", "wc",
    "xdg-open", "zip",
];

/// Characters that make a token impossible to resolve statically.
const UNANALYSABLE: &[char] = &[
    '$', '`', '*', '?', '[', ']', '{', '}', '<', '>', '|', ';', '&', '(', ')', '!', '#', '\\',
    '\n', '\t', '"', '\'',
];

struct DirMemo {
    yes: HashSet<String>,
    no: HashSet<String>,
}

impl DirMemo {
    fn new() -> Self {
        DirMemo { yes: HashSet::new(), no: HashSet::new() }
    }
    fn is_dir(&mut self, path: &str) -> bool {
        if self.yes.contains(path) {
            return true;
        }
        if self.no.contains(path) {
            return false;
        }
        let hit = Path::new(path).is_dir();
        if hit {
            self.yes.insert(path.to_string());
        } else {
            self.no.insert(path.to_string());
        }
        hit
    }
}

// Per-process memo, the same promise the Python module keeps: the filesystem
// does not change under one run. The web server answers on threads, so the
// memo lives behind a mutex; the CLI is single-threaded and pays one lock per
// question, which is a nanosecond next to the stat it guards.
static DIR_MEMO: OnceLock<Mutex<DirMemo>> = OnceLock::new();

fn dirs() -> &'static Mutex<DirMemo> {
    DIR_MEMO.get_or_init(|| Mutex::new(DirMemo::new()))
}

fn is_dir(path: &str) -> bool {
    dirs().lock().unwrap().is_dir(path)
}

/// A one-off directory test for callers outside this module.
pub fn is_dir_cached(path: &str) -> bool {
    Path::new(path).is_dir()
}

// -- token caches -----------------------------------------------------------

static PATH_LIKE: OnceLock<Mutex<HashMap<String, bool>>> = OnceLock::new();

// Verdict caches: (token, cwd) -> exists. Absolute and `~` tokens are cached
// under the empty cwd; they are true or false in every directory.
static VERDICT: OnceLock<Mutex<HashMap<(String, String), bool>>> = OnceLock::new();

fn in_set(list: &[&str], item: &str) -> bool {
    list.iter().any(|&x| x == item)
}

pub fn is_directory_argument(word: &str) -> bool {
    in_set(DIRECTORY_ARGUMENT, word)
}

// -- accounts ---------------------------------------------------------------

/// Every account name on this machine, read through the same enumeration
/// `pwd.getpwall()` performs. None means "cannot tell", which makes `~name`
/// UNKNOWN and therefore removable by nothing.
fn home_users() -> Option<HashSet<String>> {
    static USERS: OnceLock<Option<HashSet<String>>> = OnceLock::new();
    USERS
        .get_or_init(|| unsafe {
            let mut out = HashSet::new();
            libc::setpwent();
            loop {
                let pw = libc::getpwent();
                if pw.is_null() {
                    break;
                }
                let name = (*pw).pw_name;
                if !name.is_null() {
                    let cstr = std::ffi::CStr::from_ptr(name);
                    if let Ok(s) = cstr.to_str() {
                        out.insert(s.to_string());
                    }
                }
            }
            libc::endpwent();
            Some(out)
        })
        .clone()
}

pub fn is_home_ref(token: &str) -> bool {
    if !token.starts_with('~') {
        return true;
    }
    if token == "~" || token.starts_with("~/") {
        return true;
    }
    let user = &token[1..];
    !user.is_empty() && !user.contains('/') && home_users().map_or(false, |u| u.contains(user))
}

/// `os.path.expanduser` for the shapes this program is asked about: `~` for
/// the current account, `~name` for an account that exists, unchanged
/// otherwise — the same contract, including git's revision syntax surviving.
pub fn expanduser(token: &str) -> String {
    if token == "~" {
        return home_dir();
    }
    if let Some(rest) = token.strip_prefix("~/") {
        return format!("{}/{}", home_dir(), rest);
    }
    if token.starts_with('~') {
        let user = &token[1..];
        let user = user.split('/').next().unwrap_or("");
        if let Some(users) = home_users() {
            if !user.is_empty() && users.contains(user) {
                if let Some(dir) = passwd_home(user) {
                    return format!("{}{}", dir, &token[1 + user.len()..]);
                }
            }
        }
    }
    token.to_string()
}

fn passwd_home(user: &str) -> Option<String> {
    let data = fs::read_to_string("/etc/passwd").ok()?;
    for line in data.lines() {
        let fields: Vec<&str> = line.split(':').collect();
        if fields.len() >= 6 && fields[0] == user {
            return Some(fields[5].to_string());
        }
    }
    None
}

pub fn home_dir() -> String {
    env::var("HOME").unwrap_or_else(|_| "/".to_string())
}

// -- locations --------------------------------------------------------------

/// Where tai keeps its files: `$XDG_DATA_HOME`, else `~/.local/share`, plus
/// `/tai`. Honoured everywhere, so the plugins and the builder never split the
/// install across two directories.
pub fn data_dir() -> PathBuf {
    PathBuf::from(data_location())
}

pub fn data_location() -> String {
    let base = env::var("XDG_DATA_HOME")
        .ok()
        .filter(|s| !s.is_empty())
        .unwrap_or_else(|| format!("{}/.local/share", home_dir()));
    format!("{}/tai", base.trim_end_matches('/'))
}

pub fn db_location() -> String {
    if let Ok(env) = env::var("TAI_DB") {
        if !env.is_empty() {
            return env;
        }
    }
    format!("{}/history.db", data_location())
}

pub fn db_path() -> PathBuf {
    PathBuf::from(db_location())
}

pub fn index_location() -> String {
    if let Ok(env) = env::var("TAI_INDEX") {
        if !env.is_empty() {
            return env;
        }
    }
    format!("{}/zsh-index.zsh", data_location())
}

/// The bash index is one file name away from the zsh one, derived rather than
/// decided twice.
pub fn bash_index_location() -> String {
    let zsh = index_location();
    match zsh.rsplit_once('/') {
        Some((dir, _)) => format!("{}/bash-index.bash", dir),
        None => "bash-index.bash".to_string(),
    }
}

/// False when TAI_SKIP_PATH_CHECK=1 turns the check off.
pub fn enabled() -> bool {
    env::var("TAI_SKIP_PATH_CHECK").unwrap_or_default() != "1"
}

// -- token shapes -----------------------------------------------------------

pub fn unquote(token: &str) -> &str {
    let b = token.as_bytes();
    if b.len() >= 2 && b[0] == b[b.len() - 1] && (b[0] == b'"' || b[0] == b'\'') {
        &token[1..token.len() - 1]
    } else {
        token
    }
}

/// True when `token` can only be read as a filesystem path.
pub fn is_path_like(token: &str) -> bool {
    if token.is_empty() || token.starts_with('-') || token.contains('=') {
        return false;
    }
    if token.chars().any(|c| UNANALYSABLE.contains(&c)) {
        return false;
    }
    if token.contains("://") {
        return false;
    }
    if let Some((host, rest)) = token.split_once(':') {
        if !rest.is_empty() && !host.contains('/') {
            return false; // scp/rsync remote spec, `cmd:arg`
        }
    }
    let digits = token.trim_start_matches(['+', '-']);
    if !digits.is_empty() && digits.chars().all(|c| c.is_ascii_digit()) {
        return false; // ports, pids, negative numbers
    }
    if token == "." || token == ".." {
        return true;
    }
    if token.starts_with('~') {
        return is_home_ref(token);
    }
    if token.starts_with('.') {
        // A leading dot is a hidden file's shape, and a hidden file is
        // unambiguously a relative path. A tail of nothing but dots and digits
        // is a number (`.5`) or a shape nothing on disk answers to (`...`) —
        // and an empty tail (a token of nothing but dots) is the same
        // no-opinion, because an empty set is a subset of anything.
        let tail = token.trim_start_matches('.');
        return !tail.chars().all(|c| c.is_ascii_digit() || c == '.');
    }
    if !token.contains('/') {
        return false;
    }
    // A first component made only of digits and version punctuation is a date,
    // a version or a ratio, not a directory.
    let head = token
        .trim_start_matches(['~', '/'])
        .splitn(2, '/')
        .next()
        .unwrap_or("");
    if !head.is_empty()
        && head.chars().all(|c| c.is_ascii_digit() || c == '.' || c == '+' || c == '-' || c == ':')
        && head.chars().any(|c| c.is_ascii_digit())
    {
        return false;
    }
    true
}

fn is_path_like_cached(token: &str) -> bool {
    let cache = PATH_LIKE.get_or_init(|| Mutex::new(HashMap::new()));
    if let Ok(map) = cache.lock() {
        if let Some(hit) = map.get(token) {
            return *hit;
        }
    }
    let hit = is_path_like(token);
    if let Ok(mut map) = cache.lock() {
        if map.len() > 16384 {
            map.clear();
        }
        map.insert(token.to_string(), hit);
    }
    hit
}

/// True when the last word of `cmd` names a file, by what the command means.
pub fn takes_file(cmd: &str) -> bool {
    let tokens: Vec<&str> = cmd.split_whitespace().collect();
    if tokens.len() < 2 {
        return false;
    }
    let last = unquote(tokens[tokens.len() - 1]);
    if last.starts_with('-') || last.chars().any(|c| UNANALYSABLE.contains(&c)) {
        return false;
    }
    let head = tokens[0];
    if in_set(FILE_LAST, head) {
        return true;
    }
    if !in_set(FILE_FLAGGED, head) {
        return false;
    }
    // Everything in front has to be a flag, or a flag's own argument.
    let mut previous = head;
    for token in &tokens[1..tokens.len() - 1] {
        if !token.starts_with('-') && !previous.starts_with('-') {
            return false;
        }
        previous = token;
    }
    true
}

// -- verdicts ---------------------------------------------------------------

fn resolve(token: &str, cwds: &[String]) -> &'static str {
    if token == "." || token == ".." {
        return EXISTS; // true in every directory, so no cwd is needed
    }
    if token.starts_with('~') {
        if !is_home_ref(token) {
            return UNKNOWN;
        }
        return if Path::new(&expanduser(token)).exists() { EXISTS } else { MISSING };
    }
    if Path::new(token).is_absolute() {
        let hit = cached_exists(token, "");
        return if hit { EXISTS } else { MISSING };
    }
    // A relative path is only knowable from a directory it was actually run in.
    let known: Vec<&String> = cwds.iter().filter(|c| !c.is_empty() && is_dir(c)).collect();
    if known.is_empty() {
        return UNKNOWN;
    }
    for cwd in known {
        if cached_exists(token, cwd) {
            return EXISTS;
        }
    }
    MISSING
}

fn cached_exists(token: &str, cwd: &str) -> bool {
    let cache = VERDICT.get_or_init(|| Mutex::new(HashMap::new()));
    let key = (token.to_string(), cwd.to_string());
    if let Ok(map) = cache.lock() {
        if let Some(hit) = map.get(&key) {
            return *hit;
        }
    }
    let hit = if cwd.is_empty() {
        Path::new(token).exists()
    } else {
        Path::new(cwd).join(token).exists()
    };
    if let Ok(mut map) = cache.lock() {
        if map.len() > 16384 {
            map.clear();
        }
        map.insert(key, hit);
    }
    hit
}

pub fn token_verdict(token: &str, cwds: &[String], as_path: bool) -> &'static str {
    let token = unquote(token);
    if !as_path && !is_path_like_cached(token) {
        return UNKNOWN;
    }
    resolve(token, cwds)
}

/// MISSING/EXISTS for a `cd`/`pushd` destination, evidence-split.
fn cd_destination_verdict(token: &str, cwds: &[String]) -> &'static str {
    let token = unquote(token);
    let mut norm = token.trim_end_matches('/').to_string();
    if let Some(stripped) = norm.strip_prefix("./") {
        norm = stripped.to_string();
    }
    let mut origins: Vec<String> = Vec::new();
    let mut dest_seen = false;
    for d in cwds {
        if d.is_empty() {
            continue;
        }
        let dd = d.trim_end_matches('/');
        if !norm.is_empty() && (dd == norm || dd.ends_with(&format!("/{}", norm))) {
            if is_dir(d) {
                return EXISTS; // the recorded landing spot still stands
            }
            dest_seen = true; // it was the target, and it is gone
        } else {
            origins.push(d.clone());
        }
    }
    let rest = resolve(token, &origins);
    if rest == EXISTS {
        return EXISTS;
    }
    if dest_seen || rest == MISSING {
        return MISSING;
    }
    UNKNOWN
}

/// MISSING when any path in `cmd` is gone, else EXISTS or UNKNOWN.
pub fn command_verdict(cmd: &str, cwds: &[String]) -> &'static str {
    let mut verdict = UNKNOWN;
    let tokens: Vec<&str> = cmd.split_whitespace().collect();
    for (position, token) in tokens.iter().enumerate() {
        let as_path =
            position == 1 && is_directory_argument(tokens[0]) && !token.starts_with('-');
        let one = if as_path {
            cd_destination_verdict(token, cwds)
        } else {
            token_verdict(token, cwds, false)
        };
        if one == MISSING {
            return MISSING;
        }
        if one == EXISTS {
            verdict = EXISTS;
        }
    }
    verdict
}

/// Commands whose paths no longer exist. `entries` yields (cmd, cwds) pairs.
/// A caller that *deletes* on the answer passes no fallback cwd.
pub fn stale_commands(entries: &[(String, Vec<String>)], cwd: &str) -> HashSet<String> {
    let mut out = HashSet::new();
    for (cmd, recorded) in entries {
        let mut cwds = recorded.clone();
        if !cwd.is_empty() && !cwds.iter().any(|c| c == cwd) {
            cwds.push(cwd.to_string());
        }
        if command_verdict(cmd, &cwds) == MISSING {
            out.insert(cmd.clone());
        }
    }
    out
}

/// The stale set for a built engine, with the path check applied once.
pub fn stale_in(cmd_cwds: &[(String, Vec<String>)], cwd: &str) -> HashSet<String> {
    if !enabled() {
        return HashSet::new();
    }
    stale_commands(cmd_cwds, cwd)
}

/// The learned words that are unambiguously *relative* filesystem paths.
pub fn relative_word_set(cmds: &[String]) -> HashSet<String> {
    let mut out = HashSet::new();
    for cmd in cmds {
        for token in cmd.split_whitespace() {
            let token = unquote(token);
            if token.starts_with('~') || token.starts_with('/') {
                continue;
            }
            if is_path_like(token) {
                out.insert(token.to_string());
            }
        }
    }
    out
}

/// True when every relative path in `cmd` resolves in `cwd`.
pub fn alive_in(cmd: &str, cwd: &str) -> bool {
    if !enabled() {
        return true;
    }
    let cwd = if cwd.is_empty() {
        env::current_dir()
            .map(|p| p.to_string_lossy().to_string())
            .unwrap_or_default()
    } else {
        cwd.to_string()
    };
    if cmd.is_empty() || !is_dir(&cwd) {
        return true;
    }
    for token in cmd.split_whitespace() {
        let token = unquote(token);
        if !is_path_like(token) {
            continue;
        }
        if token.starts_with('~') || Path::new(token).is_absolute() {
            continue;
        }
        if !Path::new(&cwd).join(token).exists() {
            return false;
        }
    }
    true
}

// -- destinations -----------------------------------------------------------

/// True when `cmd` is a `cd` whose argument names a place to be.
pub fn is_destination(cmd: &str) -> bool {
    let tokens: Vec<&str> = cmd.split_whitespace().collect();
    if tokens.len() < 2 || !is_directory_argument(tokens[0]) {
        return true;
    }
    !in_set(NOT_A_DESTINATION, unquote(tokens[1]))
}

/// Re-rank `(score, cmd)` pairs so `cd` destinations outrank relative moves.
/// The moves keep their own scores and order, and sit as a block below the
/// lowest-scoring real destination, one step apart.
///
/// The score type is whatever the two rankers carry: integer milli-scores in
/// the index, floats in the engine — same rule, no drift.
pub trait Score: Copy {
    fn cmp_s(&self, other: &Self) -> std::cmp::Ordering;
    fn step_down(&self, n: i64) -> Self;
}

impl Score for i64 {
    fn cmp_s(&self, other: &Self) -> std::cmp::Ordering {
        self.cmp(other)
    }
    fn step_down(&self, n: i64) -> Self {
        self - n
    }
}

impl Score for f64 {
    fn cmp_s(&self, other: &Self) -> std::cmp::Ordering {
        // The scores this walks are bounded arithmetic, never NaN.
        self.partial_cmp(other).unwrap_or(std::cmp::Ordering::Equal)
    }
    fn step_down(&self, n: i64) -> Self {
        self - n as f64
    }
}

pub fn destinations_first<S: Score>(scored: &mut Vec<(S, String)>) {
    let mut floor: Option<S> = None;
    let mut moves: Vec<(S, String)> = Vec::new();
    let mut out: Vec<(S, String)> = Vec::new();
    for (score, cmd) in scored.drain(..) {
        if !is_destination(&cmd) {
            moves.push((score, cmd));
            continue;
        }
        if is_directory_argument(cmd.split(' ').next().unwrap_or("")) {
            floor = Some(match floor {
                None => score,
                Some(f) => if f.cmp_s(&score) == std::cmp::Ordering::Greater { score } else { f },
            });
        }
        out.push((score, cmd));
    }
    if moves.is_empty() || floor.is_none() {
        out.extend(moves);
        *scored = out;
        return;
    }
    moves.sort_by(|a, b| b.0.cmp_s(&a.0).then(b.1.cmp(&a.1)));
    let floor = floor.unwrap();
    for (i, (_, cmd)) in moves.into_iter().enumerate() {
        out.push((floor.step_down(1 + i as i64), cmd));
    }
    *scored = out;
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn token_shapes() {
        // Flags, key=value, URLs, remote specs, numbers: no opinion.
        assert!(!is_path_like("-v"));
        assert!(!is_path_like("--long-flag"));
        assert!(!is_path_like("key=value"));
        assert!(!is_path_like("src/*.py"));
        assert!(!is_path_like("https://example.com/x"));
        assert!(!is_path_like("host:path"));
        assert!(!is_path_like("8080"));
        assert!(!is_path_like("-20"));
        // Relative moves and the shapes that always answer true.
        assert!(is_path_like("."));
        assert!(is_path_like(".."));
        assert!(is_path_like("~"));
        assert!(is_path_like("~/.ollama"));
        // A hidden file is unambiguously a relative path.
        assert!(is_path_like(".zhistory"));
        assert!(is_path_like("src/util.py"));
        assert!(is_path_like("Data/INI"));
        // A number, a git revision, a date: not paths. A version under a
        // word is a path to be judged: only a digit-led head is refused.
        assert!(!is_path_like(".5"));
        assert!(!is_path_like("..."));
        assert!(!is_path_like("1/2/2024"));
        assert!(is_path_like("pkg/1.2.0"));
        // A bare name is not a path: the command vocabulary owns it.
        assert!(!is_path_like("nano"));
        // git's revision syntax is only a home when the account exists; on
        // any machine without a `main` account the token gets no opinion.
        assert!(!is_path_like("~main"));
    }

    #[test]
    fn token_verdicts() {
        let no_cwds: Vec<String> = Vec::new();
        assert_eq!(token_verdict(".", &no_cwds, false), EXISTS);
        assert_eq!(token_verdict("..", &no_cwds, false), EXISTS);
        assert_eq!(token_verdict("-v", &no_cwds, false), UNKNOWN);
        assert_eq!(token_verdict("/definitely/not/here/xyz", &no_cwds, false), MISSING);
        // A relative path with no recorded directory is no opinion.
        assert_eq!(token_verdict("src/util.py", &no_cwds, false), UNKNOWN);
    }

    #[test]
    fn command_verdicts() {
        let no_cwds: Vec<String> = Vec::new();
        assert_eq!(command_verdict("cd /definitely/not/here/xyz", &no_cwds), MISSING);
        assert_eq!(command_verdict("cd ..", &no_cwds), EXISTS);
        // `cd -` is "go back", not a directory named `-`: a leading dash is
        // a flag whatever the command means, so no opinion — and unknown
        // removes nothing.
        assert_eq!(command_verdict("cd -", &no_cwds), UNKNOWN);
        // A flag is a flag whatever the command means.
        assert_eq!(command_verdict("ls -la /definitely/not/here/xyz", &no_cwds), MISSING);
        // An unknown first word is no opinion about the words it carries: a
        // real path in the line is the verdict, an all-flags line is unknown.
        assert_eq!(command_verdict("nonexistenttool --flag .", &no_cwds), EXISTS);
        assert_eq!(command_verdict("nonexistenttool --flag", &no_cwds), UNKNOWN);
    }

    #[test]
    fn cd_destinations() {
        assert!(!is_destination("cd .."));
        assert!(!is_destination("cd -"));
        assert!(!is_destination("cd ./"));
        assert!(!is_destination("cd ../"));
        assert!(is_destination("cd ~/projects/myapp"));
        assert!(is_destination("cd Data/INI"));
        // Not a cd line at all: no opinion, and never demoted.
        assert!(is_destination("ls .."));
    }

    #[test]
    fn destinations_rank_below_real_places() {
        let mut scored: Vec<(i64, String)> = vec![
            (3000, "cd ..".to_string()),
            (2800, "cd ~/projects/myapp".to_string()),
            (2600, "ls -la".to_string()),
        ];
        destinations_first(&mut scored);
        // The move keeps its order relative to other moves and sits below
        // the lowest real destination, one step apart.
        assert_eq!(scored[0].1, "cd ~/projects/myapp");
        assert_eq!(scored[1].1, "ls -la");
        assert_eq!(scored[2].0, 2799);
        assert_eq!(scored[2].1, "cd ..");
    }

    #[test]
    fn last_word_is_a_file_by_what_the_command_means() {
        assert!(takes_file("chmod +x script"));
        assert!(takes_file("cat -n file"));
        assert!(takes_file("tail -n 20 file"));
        assert!(takes_file("rm file"));
        assert!(takes_file("mv source destination"));
        // A subcommand is not a file, and an interpreter is never marked.
        assert!(!takes_file("python3 manage.py runserver"));
        assert!(!takes_file("ls -la"));
        assert!(!takes_file("git status"));
        // One word, or a flag as the last word: nothing to name.
        assert!(!takes_file("nano"));
        assert!(!takes_file("cat -n"));
        // A flag's own argument keeps the mark.
        assert!(takes_file("head -n 20 file"));
        assert!(!takes_file("cat file extra"));
    }

    #[test]
    fn relative_words_are_the_filesystem_questions() {
        let cmds = vec![
            "ls Data/INI".to_string(),
            "cat .zhistory".to_string(),
            "ls ~/Documents".to_string(),
            "cd /abs/path".to_string(),
            "git commit -m msg".to_string(),
        ];
        let live = relative_word_set(&cmds);
        assert!(live.contains("Data/INI"));
        assert!(live.contains(".zhistory"));
        assert!(!live.contains("~/Documents"));
        assert!(!live.contains("/abs/path"));
        assert!(!live.contains("commit"));
    }

    #[test]
    fn alive_in_judges_where_the_user_stands() {
        let tmp = std::env::temp_dir().join(format!("tai_paths_test_{}", std::process::id()));
        let _ = std::fs::create_dir_all(tmp.join("Data/INI"));
        let cwd = tmp.to_string_lossy().to_string();
        assert!(alive_in("ls Data/INI", &cwd));
        assert!(!alive_in("ls Data/GONE", &cwd));
        // Absolute and ~ paths are cwd-independent.
        assert!(alive_in("cat /etc/hostname", &cwd));
        let _ = std::fs::remove_dir_all(&tmp);
    }
}
