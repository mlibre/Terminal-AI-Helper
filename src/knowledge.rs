//! CLI knowledge discovery for tools not present in shell history.
//!
//! Port of `tai/knowledge.py`. Deliberately conservative: it only runs
//! read-only discovery commands (`--help`, `help`, `man`) and never executes a
//! suggested command.

use std::collections::HashMap;
use std::fs;
use std::path::Path;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::Mutex;

use serde::{Deserialize, Serialize};

use crate::helptext;
use crate::store::load_rows;
use crate::util;

/// What one installed tool says about itself, read-only. The JSON shape is
/// the one `tai/store.py` cached since v2, so a v2 database loads as-is.
#[derive(Clone, Debug, Serialize, Deserialize, Default)]
pub struct ToolKnowledge {
    #[serde(default)]
    pub name: String,
    #[serde(default)]
    pub path: String,
    #[serde(default)]
    pub version: String,
    #[serde(default)]
    pub help_text: String,
    #[serde(default)]
    pub verbs: Vec<String>,
    #[serde(default)]
    pub flags: Vec<String>,
    /// Subcommand → the flags that subcommand accepts. Keyed rather than
    /// flattened because the position is the whole point.
    #[serde(default)]
    pub subs: HashMap<String, Vec<String>>,
    /// Subcommand → the subcommands *it* has. One level only.
    #[serde(default)]
    pub subverbs: HashMap<String, Vec<String>>,
    #[serde(default)]
    pub updated: i64,
}

pub fn discover(name: &str, timeout: Option<f64>, probe: &[String]) -> ToolKnowledge {
    let name = name.trim().to_string();
    let limit = timeout.unwrap_or_else(helptext::tool_timeout);
    let path = util::which(&name).unwrap_or_default();
    let mut k = ToolKnowledge { name: name.clone(), path, updated: util::now_epoch(), ..Default::default() };
    if k.path.is_empty() {
        return k;
    }
    // Probe in a throwaway directory. Not every tool treats `--help` as a pure
    // read: `temporal-service --help` wrote a 565KB SQLite database into the
    // working directory before printing a line.
    let scratch = std::env::temp_dir().join(format!("tai-probe-{}-{}", std::process::id(), name));
    let _ = fs::create_dir_all(&scratch);
    let scratch_s = scratch.to_string_lossy().to_string();
    let mut out = helptext::run(&[&name, "--help"], limit, Some(&scratch_s)).0;
    if out.trim().is_empty() {
        out = helptext::run(&[&name, "help"], limit, Some(&scratch_s)).0;
    }
    if out.trim().is_empty() {
        out = helptext::run(&["man", &name], limit, Some(&scratch_s)).0;
    }
    let (declared, subs, subverbs) = helptext::declared(&name, probe, Some(&scratch_s), None);
    let _ = fs::remove_dir_all(&scratch);
    if helptext::is_no_help(&out) {
        out = String::new();
    }
    k.help_text = out.chars().take(helptext::MAX_HELP_BYTES).collect();
    k.version = version(&name);
    let (verbs, flags) = helptext::extract(&k.help_text, &name);
    k.verbs = verbs;
    k.flags = flags;
    // The tool's own answer about itself, when it gives one. Declared
    // subcommands go *before* scraped ones, because where the two disagree
    // about what a subcommand is called, the tool is right and the parser is
    // guessing.
    if !declared.is_empty() {
        let declared_set: std::collections::HashSet<&String> = declared.iter().collect();
        let rest: Vec<String> = k.verbs.iter().filter(|v| !declared_set.contains(v)).cloned().collect();
        let mut merged = declared;
        merged.extend(rest);
        merged.truncate(96);
        k.verbs = merged;
        k.subs = subs.into_iter().collect();
        k.subverbs = subverbs.into_iter().collect();
    }
    k
}

fn version(name: &str) -> String {
    let (out, _) = helptext::run(&[name, "--version"], 0.5, Some("/tmp"));
    let out = out.trim().to_string();
    if out.is_empty() {
        return String::new();
    }
    let first = out.lines().next().unwrap_or("").to_string();
    first.chars().take(300).collect()
}

pub fn default_tools() -> Vec<&'static str> {
    vec![
        "git", "docker", "docker-compose", "kubectl", "helm", "terraform", "mise", "uv", "pipx",
        "cargo", "npm", "pnpm", "bun", "deno", "go", "python", "python3", "node", "ruby", "make",
        "just", "task", "gh", "aws", "gcloud", "az", "ssh", "curl", "jq", "yq", "ffmpeg",
        "rustc", "poetry", "bundle", "composer", "gradle", "mvn", "nix", "npx",
    ]
}

/// Directories the distribution owns. Everything else on PATH is something the
/// user put there, which is exactly the set that has no history behind it yet.
pub const SYSTEM_DIRS: &[&str] = &["/bin", "/sbin", "/usr/bin", "/usr/sbin"];

/// Every executable on PATH outside a distribution directory.
pub fn path_tools() -> Vec<String> {
    let mut found = std::collections::HashSet::new();
    if let Ok(path) = std::env::var("PATH") {
        for d in path.split(':') {
            let d = d.trim();
            if d.is_empty() || SYSTEM_DIRS.contains(&d) || !Path::new(d).is_dir() {
                continue;
            }
            let entries = match fs::read_dir(d) {
                Ok(e) => e,
                Err(_) => continue,
            };
            for entry in entries.flatten() {
                let p = entry.path();
                if p.is_file() && util::is_executable_path(&p) {
                    found.insert(entry.file_name().to_string_lossy().to_string());
                }
            }
        }
    }
    let mut out: Vec<String> = found.into_iter().collect();
    out.sort();
    out
}

fn parallel_discover(
    names: &[String],
    workers: usize,
    timeout: f64,
    probe: &HashMap<String, Vec<String>>,
) -> Vec<ToolKnowledge> {
    let workers = workers.clamp(1, names.len().max(1));
    let next = AtomicUsize::new(0);
    let results: Mutex<Vec<(usize, ToolKnowledge)>> = Mutex::new(Vec::new());
    std::thread::scope(|s| {
        for _ in 0..workers {
            let next = &next;
            let results = &results;
            s.spawn(move || loop {
                let i = next.fetch_add(1, Ordering::SeqCst);
                if i >= names.len() {
                    break;
                }
                let n = &names[i];
                let k = discover(n, Some(timeout), probe.get(n).map(|v| v.as_slice()).unwrap_or(&[]));
                results.lock().unwrap().push((i, k));
            });
        }
    });
    let mut out: Vec<(usize, ToolKnowledge)> = results.into_inner().unwrap();
    out.sort_by_key(|(i, _)| *i);
    out.into_iter().map(|(_, k)| k).collect()
}

/// `{tool: [subcommand, …]}` for the subcommands the history actually runs,
/// most-used first, so the cap spends itself on what the user reaches for.
pub fn used_subcommands(limit: i64) -> HashMap<String, Vec<String>> {
    let mut tally: HashMap<String, Counter> = HashMap::new();
    let rows = match load_rows(limit) {
        Ok(r) => r,
        Err(_) => return HashMap::new(),
    };
    for row in rows {
        let parts: Vec<&str> = row.cmd.split_whitespace().collect();
        if parts.len() < 2 || parts[1].starts_with('-') {
            continue;
        }
        *tally.entry(parts[0].to_string()).or_default().entry(parts[1].to_string()) += 1;
    }
    tally
        .into_iter()
        .map(|(tool, counts)| {
            let subs = counts.into_sorted_vec(true);
            (tool, subs)
        })
        .collect()
}

/// Learn the named tools, or the installed ones when `names` is None. Runs in
/// parallel because the cost is dominated by tools that ignore `--help` and
/// have to be killed at the timeout. A second pass gives the tools that stayed
/// silent a longer budget.
pub fn discover_many(
    names: Option<&[String]>,
    probe: &HashMap<String, Vec<String>>,
) -> Vec<ToolKnowledge> {
    let all: Vec<String> = match names {
        Some(n) => n.to_vec(),
        None => {
            let mut set: std::collections::BTreeSet<String> = std::collections::BTreeSet::new();
            for t in default_tools() {
                set.insert(t.to_string());
            }
            for t in path_tools() {
                set.insert(t);
            }
            set.into_iter().collect()
        }
    };
    let todo: Vec<String> = all.into_iter().filter(|n| !n.is_empty() && util::which(n).is_some()).collect();
    if todo.is_empty() {
        return Vec::new();
    }
    let found = parallel_discover(&todo, helptext::discover_workers(), helptext::tool_timeout(), probe);
    let silent: Vec<String> = todo
        .iter()
        .zip(found.iter())
        .filter(|(_, k)| k.help_text.is_empty())
        .map(|(n, _)| n.clone())
        .collect();
    let mut result = found;
    if !silent.is_empty() {
        // Fewer workers: these are the slow ones, and they are the last thing
        // standing between a tool and its vocabulary.
        let retry = parallel_discover(
            &silent,
            (helptext::discover_workers() / 2).max(1),
            helptext::tool_timeout_slow(),
            probe,
        );
        let by_name: HashMap<String, ToolKnowledge> =
            silent.iter().cloned().zip(retry.into_iter()).collect();
        for (i, n) in todo.iter().enumerate() {
            if let Some(k) = by_name.get(n) {
                result[i] = k.clone();
            }
        }
    }
    result
}

pub fn save_many(tools: &[ToolKnowledge]) {
    let con = match crate::store::connect() {
        Ok(c) => c,
        Err(_) => return,
    };
    use rusqlite::params;
    for k in tools {
        let data = serde_json::to_string(k).unwrap_or_default();
        let _ = con.execute(
            "INSERT INTO cli_tools(name,data,updated) VALUES(?1,?2,?3) \
             ON CONFLICT(name) DO UPDATE SET data=excluded.data, updated=excluded.updated",
            params![k.name, data, k.updated],
        );
    }
    let _ = con.execute_batch("COMMIT");
}

pub fn load_all() -> Vec<ToolKnowledge> {
    let con = match crate::store::connect() {
        Ok(c) => c,
        Err(_) => return Vec::new(),
    };
    let mut stmt = match con.prepare("SELECT data FROM cli_tools ORDER BY name") {
        Ok(s) => s,
        Err(_) => return Vec::new(),
    };
    let rows = match stmt.query_map([], |row| {
        let data: String = row.get(0)?;
        Ok(serde_json::from_str::<ToolKnowledge>(&data).unwrap_or_default())
    }) {
        Ok(r) => r,
        Err(_) => return Vec::new(),
    };
    rows.filter_map(|r| r.ok()).collect()
}

pub fn is_known(name: &str) -> bool {
    // True when this tool's help has already been learned.
    match crate::store::connect() {
        Ok(con) => con
            .query_row(
                "SELECT 1 FROM cli_tools WHERE name=?1",
                rusqlite::params![name],
                |_| Ok(1i64),
            )
            .is_ok(),
        Err(_) => false,
    }
}

/// Generate safe candidate command strings from cached help knowledge.
pub fn candidates_for_prefix(
    prefix: &str,
    tools: &[ToolKnowledge],
    cap: usize,
) -> Vec<String> {
    let prefix = prefix.trim();
    if prefix.is_empty() {
        return Vec::new();
    }
    let mut out: Vec<String> = Vec::new();
    let mut seen = std::collections::HashSet::new();
    let tools_owned: Vec<ToolKnowledge>;
    let tools: &[ToolKnowledge] = if tools.is_empty() {
        tools_owned = load_all();
        &tools_owned
    } else {
        tools
    };
    for tool in tools {
        let base = &tool.name;
        if !(base.starts_with(prefix) || prefix.starts_with(base.as_str())) {
            continue;
        }
        let mut candidates: Vec<String> = vec![base.clone()];
        if prefix == base || prefix.starts_with(&format!("{} ", base)) || prefix == format!("{} ", base) {
            for v in &tool.verbs {
                candidates.push(format!("{} {}", base, v));
            }
            for v in tool.verbs.iter().take(12) {
                candidates.push(format!("{} {} --help", base, v));
            }
        }
        if prefix == base || prefix == format!("{} ", base) {
            candidates.push(format!("{} --help", base));
        }
        // Flags are valid candidates too: this is what enables `ls -` and
        // `docker --` even when the exact flag has never appeared in history.
        if prefix.starts_with(&format!("{} -", base)) || prefix.starts_with(&format!("{} --", base)) {
            for flag in &tool.flags {
                candidates.push(format!("{} {}", base, flag));
            }
        }
        // …and the flags of the *subcommand being typed*. The candidate is
        // written in full rather than as the prefix plus the flag, so the
        // same startswith filter narrows `docker run --p` to the flags that
        // begin `--p`.
        let head = prefix.trim_end();
        if head.starts_with(&format!("{} ", base))
            && !head[base.len() + 1..].starts_with('-')
        {
            let sub = head[base.len() + 1..].splitn(2, ' ').next().unwrap_or("").to_string();
            if let Some(subs) = tool.subverbs.get(&sub) {
                for s in subs {
                    candidates.push(format!("{} {} {}", base, sub, s));
                }
            }
            if let Some(subs) = tool.subs.get(&sub) {
                for flag in subs {
                    candidates.push(format!("{} {} {}", base, sub, flag));
                }
            }
        }
        for c in candidates {
            if c.starts_with(prefix) && !seen.contains(&c) {
                seen.insert(c.clone());
                out.push(c);
                if out.len() >= cap {
                    return out;
                }
            }
        }
    }
    // Universal cold-start vocabulary, useful even when no tool is installed
    // in PATH and no history entry exists yet.
    for cmd in crate::seed::SEED_COMMANDS {
        if cmd.starts_with(prefix) && !seen.contains(*cmd) {
            seen.insert(cmd.to_string());
            out.push(cmd.to_string());
            if out.len() >= cap {
                return out;
            }
        }
    }
    out
}

/// Discover, cache, and return the number of tools learned. The history is
/// read first so that the subcommand flags worth harvesting are the ones
/// actually run.
pub fn learn(tools: Option<&[String]>) -> i64 {
    let probe = used_subcommands(50000);
    let discovered = discover_many(tools, &probe);
    let n = discovered.len() as i64;
    save_many(&discovered);
    n
}

/// A tiny most-common helper mirroring collections.Counter, for the one
/// String-keyed tally the discovery side keeps.
pub struct Counter {
    map: HashMap<String, i64>,
}

impl Counter {
    pub fn new() -> Self {
        Counter { map: HashMap::new() }
    }
    pub fn entry(&mut self, key: String) -> &mut i64 {
        self.map.entry(key).or_insert(0)
    }
    pub fn into_sorted_vec(self, most_first: bool) -> Vec<String> {
        let mut pairs: Vec<(String, i64)> = self.map.into_iter().collect();
        pairs.sort_by(|a, b| {
            if most_first {
                b.1.cmp(&a.1).then(a.0.cmp(&b.0))
            } else {
                a.1.cmp(&b.1)
            }
        });
        pairs.into_iter().map(|(k, _)| k).collect()
    }
}

impl Default for Counter {
    fn default() -> Self {
        Self::new()
    }
}


