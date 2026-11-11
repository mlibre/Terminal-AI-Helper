//! Turning CLI help output into candidate command vocabulary.
//!
//! Port of `tai/helptext.py`. `_run` executes a tool's own read-only
//! introspection (`--help`, `__complete`); everything else is parsing of what
//! those probes print. Discovery policy lives in `knowledge.rs`.

use std::env;
use std::sync::OnceLock;

use regex::Regex;

use crate::util;

pub fn tool_timeout() -> f64 {
    env::var("TAI_HELP_TIMEOUT")
        .ok()
        .and_then(|v| v.parse().ok())
        .unwrap_or(1.5)
}

/// A second, longer budget for tools that stayed silent.
pub fn tool_timeout_slow() -> f64 {
    env::var("TAI_HELP_TIMEOUT_SLOW")
        .ok()
        .and_then(|v| v.parse().ok())
        .unwrap_or(6.0)
}

pub const MAX_HELP_BYTES: usize = 256 * 1024;

pub fn discover_workers() -> usize {
    env::var("TAI_DISCOVER_WORKERS")
        .ok()
        .and_then(|v| v.parse().ok())
        .unwrap_or(8)
}

pub fn complete_timeout() -> f64 {
    env::var("TAI_COMPLETE_TIMEOUT")
        .ok()
        .and_then(|v| v.parse().ok())
        .unwrap_or(2.0)
}

/// How many of one tool's subcommands get their flags harvested.
pub const SUB_FLAG_CAP: usize = 8;

fn no_help_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| {
        Regex::new(r"(?i)^\s*no (manual entry|entry) for\b|\bnot found in the man database\b")
            .unwrap()
    })
}

/// `man` answers a miss with a sentence rather than a failure, and caching
/// that sentence as a tool's vocabulary produces candidates made of the words
/// "No manual entry for".
pub fn is_no_help(out: &str) -> bool {
    no_help_re().is_match(out)
}

fn invocation_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"^[a-z][\w.:-]*\s+[a-z][\w.:-]*\s").unwrap())
}

fn complete_line_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"^([^\t:][^\t]*)\t").unwrap())
}

fn complete_directive_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"(?m)^:\d+$").unwrap())
}

const META_FLAGS: &[&str] = &["--help", "-h", "--version", "-v", "--usage", "-V"];

/// Run a tool's own read-only introspection with merged output, stdin closed,
/// a scratch cwd and PAGER=cat. Returns (stdout-capped, "" | "timeout" | err).
pub fn run(args: &[&str], timeout: f64, cwd: Option<&str>) -> (String, String) {
    let (out, err) = util::run_with_timeout(
        args,
        timeout,
        cwd,
        &[("PAGER", "cat"), ("TERM", "dumb")],
    );
    if out.len() > MAX_HELP_BYTES {
        (out[..MAX_HELP_BYTES].to_string(), err)
    } else {
        (out, err)
    }
}

/// Extract likely verbs and flags from help text conservatively.
pub fn extract(text: &str, name: &str) -> (Vec<String>, Vec<String>) {
    let mut verbs: Vec<String> = Vec::new();
    let mut flags: Vec<String> = Vec::new();
    let mut seen_v: std::collections::HashSet<String> = std::collections::HashSet::new();
    let mut seen_f: std::collections::HashSet<String> = std::collections::HashSet::new();
    let mut short: Vec<String> = Vec::new();

    let catalog_re = Regex::new(r"(?i)\b(commands?|actions?)\b").unwrap();
    let catalog_entry_re = Regex::new(r"^([a-z][a-z0-9_:-]{1,31})\s{2,}\S").unwrap();
    let long_flag_re = Regex::new(r"(?:^|[^0-9A-Za-z_\-])(--[a-zA-Z0-9][a-zA-Z0-9-]{0,40})").unwrap();

    let mut catalog = false;
    for raw in text.lines().take(800) {
        let line = raw.trim();
        if line.is_empty() || line.len() > 500 {
            continue;
        }
        if catalog_re.is_match(line) {
            catalog = true;
        }
        if let Some(m) = catalog_entry_re.captures(line) {
            let v = m[1].to_string();
            if !seen_v.contains(&v) && v != name {
                verbs.push(v.clone());
                seen_v.insert(v);
                catalog = true;
            }
        }
        // A line that reads as a command invocation belongs to a subcommand,
        // and its flags are not the tool's own.
        let invocation =
            !(line.starts_with('-') || line.starts_with('(') || line.starts_with('[')
                || line.starts_with('>') || line.starts_with('|'));
        if invocation && invocation_re().is_match(line) {
            continue;
        }
        for c in long_flag_re.captures_iter(line) {
            let flag = c[1].to_string();
            if !seen_f.contains(&flag) {
                flags.push(flag.clone());
                seen_f.insert(flag);
            }
        }
        for (start, end) in scan_short_flags(line) {
            let flag = &line[start..end];
            if !seen_f.contains(flag) {
                short.push(flag.to_string());
                seen_f.insert(flag.to_string());
            }
        }
    }
    // Long options first, and the meta options last — taken from *both*
    // lists, because reading them off the already filtered `flags` dropped
    // every long meta option on the floor.
    let meta_flags: Vec<String> = flags
        .iter()
        .chain(short.iter())
        .filter(|f| META_FLAGS.contains(&f.as_str()))
        .cloned()
        .collect();
    let mut kept: Vec<String> = flags
        .iter()
        .filter(|f| !META_FLAGS.contains(&f.as_str()))
        .cloned()
        .collect();
    kept.extend(short.iter().filter(|f| !META_FLAGS.contains(&f.as_str())).cloned());
    kept.extend(meta_flags);
    flags = kept;

    // If no catalog was found, conservatively parse explicit usage lines.
    if !catalog {
        let usage_re = Regex::new(r"(?i)\b(usage|synopsis)\b").unwrap();
        let usage_prefix_re = Regex::new(r"(?i)^(usage|synopsis)\s*:\s*").unwrap();
        let angle_re = Regex::new(r"<[^>]*>").unwrap();
        for line in text.lines().take(100) {
            if !usage_re.is_match(line) {
                continue;
            }
            let mut tail = usage_prefix_re.replace(line, "").to_string();
            tail = angle_re.replace_all(&tail, " ").to_string();
            let tail = tail.split('[').next().unwrap_or("").to_string();
            for (start, end) in scan_usage_tokens(&tail, 31) {
                let tok = &tail[start..end];
                if ["the", "and", "for", "with", "from", "this", "that", "usage",
                    "options", "flags", "help", "version", "path", "command", "args", "name",
                    "value"]
                .contains(&tok)
                {
                    continue;
                }
                if !seen_v.contains(tok) && tok != name {
                    verbs.push(tok.to_string());
                    seen_v.insert(tok.to_string());
                }
            }
        }
    }

    // Only add verbs/flags that are plausible command words. A malformed help
    // parser should reduce recall, never inject arbitrary prose as shell
    // candidates.
    let stop: &[&str] = &[
        "version", "help", "path", "command", "args", "name", "value", "page",
        "file", "directory", "all", "one", "two", "use", "see", "also",
    ];
    let verbs: Vec<String> = verbs
        .into_iter()
        .filter(|v| v.chars().all(|c| c.is_alphabetic()) && !stop.contains(&v.as_str()))
        .take(64)
        .collect();
    (verbs, flags.into_iter().take(96).collect())
}

/// Candidate names from one `__complete` reply. None means *this tool has no
/// such command* — the `:<bits>` line is the discriminator.
pub fn complete_candidates(out: &str) -> Option<Vec<String>> {
    if !complete_directive_re().is_match(out) {
        return None;
    }
    let mut names: Vec<String> = Vec::new();
    let mut seen = std::collections::HashSet::new();
    for line in out.lines() {
        if let Some(m) = complete_line_re().captures(line) {
            let name = m[1].trim().to_string();
            if !name.is_empty() && !seen.contains(&name) {
                seen.insert(name.clone());
                names.push(name);
            }
        }
    }
    if names.is_empty() {
        None
    } else {
        Some(names)
    }
}

/// `(subcommands, {sub: its flags}, {sub: its own subcommands})`. Empty when
/// the tool does not speak the protocol, which is the common case and costs
/// one fork to discover.
pub fn declared(
    name: &str,
    probe: &[String],
    cwd: Option<&str>,
    timeout: Option<f64>,
) -> (Vec<String>, Vec<(String, Vec<String>)>, Vec<(String, Vec<String>)>) {
    let limit = timeout.unwrap_or_else(complete_timeout);
    let (out, _) = run(&[name, "__complete", ""], limit, cwd);
    let subs_all = match complete_candidates(&out) {
        Some(s) => s,
        None => return (Vec::new(), Vec::new(), Vec::new()),
    };
    // A flag is not a subcommand.
    let subs_all: Vec<String> =
        subs_all.into_iter().filter(|s| !s.starts_with('-')).collect();
    let mut subs: Vec<(String, Vec<String>)> = Vec::new();
    let mut subverbs: Vec<(String, Vec<String>)> = Vec::new();
    for sub in probe.iter().take(SUB_FLAG_CAP) {
        if !subs_all.contains(sub) {
            continue;
        }
        // Its own subcommands first: `gh pr ` is a question about `checkout`
        // and `close`, and the options come after those.
        let (reply, _) = run(&[name, "__complete", sub, ""], limit, cwd);
        if let Some(nested) = complete_candidates(&reply) {
            let filtered: Vec<String> =
                nested.into_iter().filter(|s| !s.starts_with('-')).collect();
            if !filtered.is_empty() {
                subverbs.push((sub.clone(), filtered));
            }
        }
        let (reply, _) = run(&[name, "__complete", sub, "--"], limit, cwd);
        if let Some(got) = complete_candidates(&reply) {
            let flags: Vec<String> = got
                .into_iter()
                .filter(|f| f.starts_with('-') && !META_FLAGS.contains(&f.as_str()))
                .collect();
            if !flags.is_empty() {
                subs.push((sub.clone(), flags));
            }
        }
    }
    (subs_all, subs, subverbs)
}

/// `(?<![\w-])(-[A-Za-z][A-Za-z0-9]{0,3})(?![\w-])`, answered without
/// lookaround: the letter and its run are read as the Python pattern reads
/// them, greedy to the run's end and back off only while a shorter take
/// changes what the boundary test sees — which for an alnum run it never
/// does, so the match stands or falls on the character after the whole run.
fn scan_short_flags(line: &str) -> Vec<(usize, usize)> {
    let chars: Vec<char> = line.chars().collect();
    let mut byte_at = vec![0usize; chars.len() + 1];
    for (i, c) in chars.iter().enumerate() {
        byte_at[i + 1] = byte_at[i] + c.len_utf8();
    }
    let wordish = |c: char| c.is_alphanumeric() || c == '_' || c == '-';
    let mut out = Vec::new();
    for i in 0..chars.len() {
        if chars[i] != '-' {
            continue;
        }
        if i > 0 && wordish(chars[i - 1]) {
            continue;
        }
        if i + 1 >= chars.len() || !chars[i + 1].is_ascii_alphabetic() {
            continue;
        }
        let mut r = 0;
        while i + 2 + r < chars.len() && chars[i + 2 + r].is_ascii_alphanumeric() {
            r += 1;
        }
        if r <= 3 && (i + 2 + r >= chars.len() || !wordish(chars[i + 2 + r])) {
            out.push((byte_at[i], byte_at[i + 2 + r]));
        }
    }
    out
}

/// `(?<![\w-])([a-z][a-z0-9_:-]{1,31})(?![\w-])`, the usage-line token, with
/// the same hand-applied boundary. The run class here holds `:`, which the
/// boundary test does not — so a backtracked take can end on a `:` the
/// greedy take walked past, and the descending loop below reproduces that.
fn scan_usage_tokens(tail: &str, cap: usize) -> Vec<(usize, usize)> {
    let chars: Vec<char> = tail.chars().collect();
    let mut byte_at = vec![0usize; chars.len() + 1];
    for (i, c) in chars.iter().enumerate() {
        byte_at[i + 1] = byte_at[i] + c.len_utf8();
    }
    let in_class = |c: char| c.is_ascii_lowercase() || c.is_ascii_digit() || c == '_' || c == ':' || c == '-';
    let wordish = |c: char| c.is_alphanumeric() || c == '_' || c == '-';
    let mut out = Vec::new();
    for i in 0..chars.len() {
        if !chars[i].is_ascii_lowercase() {
            continue;
        }
        if i > 0 && wordish(chars[i - 1]) {
            continue;
        }
        let mut r = 0;
        while i + 1 + r < chars.len() && in_class(chars[i + 1 + r]) {
            r += 1;
        }
        let k_max = r.min(cap);
        for k in (1..=k_max).rev() {
            let after = i + 1 + k;
            if after >= chars.len() || !wordish(chars[after]) {
                out.push((byte_at[i], byte_at[after]));
                break;
            }
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn short_flags_read_like_the_lookahead() {
        let flags = |line: &str| -> Vec<String> {
            scan_short_flags(line).into_iter().map(|(a, b)| line[a..b].to_string()).collect()
        };
        assert_eq!(flags("-p, --port"), vec!["-p".to_string()]);
        assert_eq!(flags("-px 5"), vec!["-px".to_string()]);
        // Four characters after the dash (letter + three) is the cap; five
        // is a run the shape cannot hold, and the lookahead rejects it all.
        assert_eq!(flags("-abcd"), vec!["-abcd".to_string()]);
        assert!(flags("-abcde").is_empty());
        // A dash on both sides is inside a word shape, not a flag.
        assert!(flags("x -p- y").is_empty());
        assert!(flags("--only-long").is_empty());
        // The boundary before rejects what a word character precedes.
        assert!(flags("abc-p").is_empty());
        assert_eq!(flags("(-p)"), vec!["-p".to_string()]);
    }

    #[test]
    fn usage_tokens_read_like_the_lookahead() {
        let toks = |tail: &str| -> Vec<String> {
            scan_usage_tokens(tail, 31).into_iter().map(|(a, b)| tail[a..b].to_string()).collect()
        };
        // (the `usage:` prefix is stripped by extract before this scanner
        //  runs, so the tail here starts past it)
        assert_eq!(toks("prog add remove"), vec!["prog", "add", "remove"]);
        // The `:` a greedy run walked past still ends a backtracked take —
        // and the same `:` passes the boundary before the word after it,
        // exactly as the Python lookahead reads the line.
        assert_eq!(toks("prog:sub <file>"), vec!["prog:sub", "sub", "file"]);
        assert_eq!(toks("prog:sub"), vec!["prog:sub", "sub"]);
        // Uppercase ends the run and the boundary rejects nothing before it.
        assert_eq!(toks("prog SUB"), vec!["prog"]);
        // An underscore runs with the token; a dash too.
        assert_eq!(toks("dry-run_all"), vec!["dry-run_all"]);
        // Digits lead nowhere: the first character is a letter or nothing.
        assert!(toks("1/2/2024").is_empty());
    }

    #[test]
    fn extract_yields_the_vocabulary_a_help_page_holds() {
        let (verbs, flags) = extract(
            "Usage: docker [OPTIONS] COMMAND\n\nCommands:\n  run      Create and run a container\n  ps       List containers\n\nOptions:\n  -p, --publish <port>\n  --detach\n  -h, --help\n",
            "docker",
        );
        assert!(verbs.contains(&"run".to_string()));
        assert!(verbs.contains(&"ps".to_string()));
        assert!(flags.contains(&"--publish".to_string()));
        assert!(flags.contains(&"--detach".to_string()));
        // The meta options ride at the end, taken from both lists in the
        // order they appeared: the long form first, the short form last.
        assert!(flags.contains(&"-h".to_string()));
        assert_eq!(flags.last().map(|s| s.as_str()), Some("-h"));
        // No prose leaks in as a verb.
        assert!(!verbs.contains(&"and".to_string()));
    }
}
