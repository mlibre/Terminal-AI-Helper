//! The files a path argument could be.
//!
//! Port of `tai/fresh.py`. The history answers *which file did you use last
//! time*, and that is the wrong question the moment you download something.
//! For a line that ends in a file, the filesystem is the vocabulary and the
//! history is only a tiebreak.

use std::fs;
use std::path::Path;
use std::time::UNIX_EPOCH;

use crate::paths;

/// How many files one root may contribute, and how many roots are read.
pub const PER_ROOT: usize = 8;
pub const MAX_ROOTS: usize = 8;

/// Directories a download lands in.
pub const DOWNLOAD_DIRS: &[&str] = &["$XDG_DOWNLOAD_DIR", "~/Downloads", "~/Download", "~/Desktop"];

/// `TAI_FILE_ROOTS`: more places to look, colon separated.
pub fn extra_roots() -> Vec<String> {
    let mut out = Vec::new();
    let env = std::env::var("TAI_FILE_ROOTS").unwrap_or_default();
    for item in env.split(':') {
        let item = item.trim();
        if !item.is_empty() {
            out.push(paths::expanduser(item));
        }
    }
    out
}

/// `(directory, how to write it)` pairs, most relevant first. Directories are
/// absolute, including the current one.
pub fn roots(cwd: &str) -> Vec<(String, String)> {
    let here = fs::canonicalize(cwd)
        .map(|p| p.to_string_lossy().to_string())
        .unwrap_or_else(|_| {
            if cwd.is_empty() {
                std::env::current_dir()
                    .map(|p| p.to_string_lossy().to_string())
                    .unwrap_or_default()
            } else {
                cwd.to_string()
            }
        });
    let home = paths::home_dir();
    let mut out: Vec<(String, String)> = vec![(here.clone(), String::new())];
    let mut seen = std::collections::HashSet::new();
    seen.insert(here.clone());
    let mut candidates: Vec<String> = extra_roots();
    for d in DOWNLOAD_DIRS {
        let expanded = if let Some(var) = d.strip_prefix('$') {
            std::env::var(var).unwrap_or_default()
        } else {
            paths::expanduser(d)
        };
        if !expanded.is_empty() {
            candidates.push(expanded);
        }
    }
    for raw in candidates {
        let path = if Path::new(&raw).is_absolute() {
            raw.clone()
        } else {
            Path::new(&here).join(&raw).to_string_lossy().to_string()
        };
        let path = normalize(&path);
        if path == here || !paths::is_dir_cached(&path) {
            continue;
        }
        if !seen.insert(path.clone()) {
            continue;
        }
        let written = if let Some(tail) = path.strip_prefix(&format!("{}/", home)) {
            format!("~{}/", tail)
        } else {
            format!("{}/", path)
        };
        out.push((path, written));
        if out.len() >= MAX_ROOTS {
            break;
        }
    }
    out
}

fn normalize(p: &str) -> String {
    // os.path.normpath for the shapes we build: no `..` or `.` beyond the
    // simple joins done here, so collapsing duplicate separators is enough.
    let mut out = String::new();
    let mut prev_slash = false;
    for c in p.chars() {
        if c == '/' {
            if prev_slash {
                continue;
            }
            prev_slash = true;
        } else {
            prev_slash = false;
        }
        out.push(c);
    }
    while out.ends_with('/') && out.len() > 1 {
        out.pop();
    }
    out
}

fn is_dir_cached(path: &str) -> bool {
    Path::new(path).is_dir()
}

/// The files that could be `word`, newest first. `word` is a glob; empty
/// matches every file in a root. Directories are left out.
pub fn files(word: &str, cwd: &str) -> Vec<String> {
    let mut found: Vec<(f64, String)> = Vec::new();
    let pattern_word = paths::expanduser(word);
    let base = if !pattern_word.is_empty() && Path::new(&pattern_word).is_dir() {
        format!("{}/*", pattern_word.trim_end_matches('/'))
    } else {
        format!("{}*", pattern_word)
    };
    if pattern_word.starts_with('/') || pattern_word.contains('/') {
        found.extend(scan(&base, pattern_word.len(), word));
    } else {
        for (directory, written) in roots(cwd) {
            let pattern = format!(
                "{}/{}*",
                directory,
                pattern_word.trim_end_matches('/')
            );
            found.extend(scan(&pattern, directory.len() + 1, &written));
        }
    }
    // Ties by name: a same-second download batch answered in glob order is a
    // different list from one call to the next on some filesystems.
    found.sort_by(|a, b| b.0.partial_cmp(&a.0).unwrap_or(std::cmp::Ordering::Equal).then(a.1.cmp(&b.1)));
    found.into_iter().map(|(_, n)| n).collect()
}

/// `(mtime, name)` for the files a pattern matches. `cut` is how much of each
/// matched path to drop and `written` is what goes in front of what is left.
/// The cap is on *recency*, not on the order the glob happened to return.
fn scan(pattern: &str, cut: usize, written: &str) -> Vec<(f64, String)> {
    let (dir, prefix) = match pattern.rsplit_once('*') {
        Some((d, p)) => (d.to_string(), p.to_string()),
        None => return Vec::new(),
    };
    // A pattern the glob machinery cannot read is no answer at all.
    if pattern.contains('?') || pattern.contains('[') {
        return Vec::new();
    }
    let prefix = paths::expanduser(&prefix);
    let mut out: Vec<(f64, String)> = Vec::new();
    let entries = match fs::read_dir(&dir) {
        Ok(e) => e,
        Err(_) => return out,
    };
    for entry in entries.flatten() {
        let name = entry.file_name().to_string_lossy().to_string();
        if !name.starts_with(&prefix) {
            continue;
        }
        let full = format!("{}/{}", dir.trim_end_matches('/'), name);
        if Path::new(&full).is_dir() {
            continue; // the shell's own completion offers these
        }
        let mtime = match entry.metadata().and_then(|m| m.modified()) {
            Ok(t) => t.duration_since(UNIX_EPOCH).map(|d| d.as_secs_f64()).unwrap_or(0.0),
            Err(_) => continue,
        };
        let tail = if name.len() >= cut - dir.len().max(0) && full.len() >= cut {
            full[cut..].to_string()
        } else {
            name.clone()
        };
        out.push((mtime, format!("{}{}", written, tail)));
    }
    out.sort_by(|a, b| {
        b.0.partial_cmp(&a.0).unwrap_or(std::cmp::Ordering::Equal).then(a.1.cmp(&b.1))
    });
    out.truncate(PER_ROOT);
    out
}
