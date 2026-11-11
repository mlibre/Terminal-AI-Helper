//! tai CLI — embedded index builder + direct one-shot inference.
//!
//! Port of `tai/cli.py`. Normal shell autocomplete uses generated zsh/bash
//! indexes and never invokes this binary during typing. These commands are
//! for learning, refreshing, updating, and diagnostics.

use std::path::PathBuf;
use std::process::Command;
use std::time::{SystemTime, UNIX_EPOCH};

pub const REPO_URL: &str = "https://github.com/mlibre/terminal-ai-helper";

/// The two knobs of the record path's self-refresh, named where the web page
/// can quote them: a rebuild at most once per this many seconds (bursts of
/// records coalesce into one background build), and one unconditional build
/// every this many recorded commands — TAI_REBUILD_EVERY moves the hundred.
pub const REBUILD_MIN_INTERVAL: f64 = 5.0;

pub fn auto_rebuild_every() -> i64 {
    env_count("TAI_REBUILD_EVERY", 100)
}

fn env_count(name: &str, default: i64) -> i64 {
    match std::env::var(name) {
        Ok(v) => v.trim().parse::<i64>().map(|n| n.max(1)).unwrap_or(default),
        Err(_) => default,
    }
}

fn print(x: &str) {
    use std::io::Write;
    let mut out = std::io::stdout();
    let _ = out.write_all(x.as_bytes());
    let _ = out.flush();
}

/// Python's `repr()` for a string, which the CLI's messages quote with: a
/// single-quoted form, unless the text itself holds a single quote and no
/// double quote — the one shape the same message must not misquote.
pub fn py_repr(s: &str) -> String {
    if s.contains('\'') && !s.contains('"') {
        format!("\"{}\"", s)
    } else {
        format!("'{}'", s)
    }
}

/// Print without a trailing newline, flushed: the dashboard and long-running
/// commands speak line-by-line, and a child process writing after them must
/// not appear ahead of what was already said.
pub fn cmd_print(x: &str) {
    print(x);
    use std::io::Write;
    let _ = std::io::stdout().write_all(b"\n");
    let _ = std::io::stdout().flush();
}

fn println(x: &str) {
    let mut s = String::with_capacity(x.len() + 1);
    s.push_str(x);
    s.push('\n');
    print(&s);
}

// -- suggest ----------------------------------------------------------------

pub fn cmd_suggest(
    prefix: &str,
    cwd: &str,
    git: &str,
    branch: &str,
    last: &str,
    limit: usize,
    json: bool,
    jev: bool,
) -> i32 {
    let last_cmds: Vec<String> = if last.is_empty() {
        Vec::new()
    } else {
        last.split(';').map(|s| s.trim().to_string()).filter(|s| !s.is_empty()).collect()
    };
    let mut res = crate::predictor::suggest(prefix, cwd, git, branch, &last_cmds, limit, None);
    if jev {
        match run_jev(prefix, &res, cwd, git, branch, &last_cmds) {
            Ok(v) => {
                // The gate is deterministic and in code: what the model chose
                // is offered only when every gate opens, and the completion
                // rides the same choice.
                let apply = v.get("__apply").and_then(|a| a.as_bool()).unwrap_or(false);
                let rejected = v.get("rejected").and_then(|r| r.as_str()).map(|s| s.to_string());
                let choice = v.get("choice").and_then(|c| c.as_str()).unwrap_or("").to_string();
                if apply && !choice.is_empty() {
                    res["choice"] = serde_json::json!(choice);
                    res["completion"] = serde_json::json!(
                        if choice.starts_with(prefix) {
                            choice[prefix.len()..].to_string()
                        } else {
                            String::new()
                        }
                    );
                } else if let Some(reason) = rejected {
                    res["jev_rejected"] = serde_json::json!(reason);
                }
                let mut clean = v.clone();
                if let serde_json::Value::Object(map) = &mut clean {
                    map.remove("__apply");
                    map.remove("rejected");
                }
                res["jev"] = clean;
            }
            Err(e) => {
                // Said out loud, because a rerank that never ran must not look
                // like one that ran and agreed.
                res["jev_error"] = serde_json::json!(e);
                eprintln!("tai: semantic rerank unavailable ({}); using the local ranking", e);
            }
        }
    }
    if json {
        println(&serde_json::to_string(&res).unwrap_or_default());
    } else if limit > 1 {
        if let Some(choices) = res.get("choices").and_then(|c| c.as_array()) {
            for c in choices {
                println(c.get("cmd").and_then(|c| c.as_str()).unwrap_or(""));
            }
        }
    } else {
        println(res.get("choice").and_then(|c| c.as_str()).unwrap_or(""));
    }
    0
}

fn run_jev(
    prefix: &str,
    res: &serde_json::Value,
    cwd: &str,
    repo: &str,
    branch: &str,
    last: &[String],
) -> Result<serde_json::Value, String> {
    let mut candidates: Vec<String> = res
        .get("choices")
        .and_then(|c| c.as_array())
        .map(|a| {
            a.iter()
                .filter_map(|c| c.get("cmd").and_then(|c| c.as_str()).map(|s| s.to_string()))
                .collect()
        })
        .unwrap_or_default();
    if !prefix.is_empty() && !candidates.iter().any(|c| c == prefix) {
        candidates.push(prefix.to_string());
    }
    let j = crate::jev::decide(
        prefix,
        &candidates,
        cwd,
        repo,
        branch,
        last.last().map(|s| s.as_str()).unwrap_or(""),
    )?;
    // The gate is deterministic and in code. The model's own `destructive`
    // grade grades its own homework and is not trusted; the echo rule still
    // applies, so a choice equal to the prefix is not a completion.
    let echo = j.choice == prefix;
    let unsafe_score = if j.choice.is_empty() {
        0.0
    } else {
        crate::decisions::unsafe_score(&j.choice)
    };
    let mut out = serde_json::json!({
        "choice": j.choice,
        "probabilities": serde_json::Value::Object(
            j.probabilities.iter().map(|(k, v)| (k.clone(), serde_json::json!(v))).collect()),
        "confidence": j.confidence,
        "show_now": j.show_now,
        "destructive": j.destructive,
        "model": j.model,
    });
    if !j.choice.is_empty() && !echo && j.show_now >= 0.5 && j.destructive < 0.5 && unsafe_score < 0.5
    {
        out["__apply"] = serde_json::json!(true);
    } else if !j.choice.is_empty() && (echo || unsafe_score >= 0.5) {
        out["rejected"] = serde_json::json!(format!("not offering {:?}", j.choice));
    }
    Ok(out)
}

// -- refresh ----------------------------------------------------------------

pub fn cmd_refresh(quiet: bool) -> i32 {
    let imported = crate::store::import_shell_history();
    match crate::maintenance::rebuild_index() {
        None => {
            println("tai: another index rebuild holds the lock; try again in a moment");
            1
        }
        Some(Err(e)) => {
            // A person is watching this one, so the reason is worth the words.
            // The previous index is still in place, which is what "failed"
            // means here.
            println(&format!("tai: index rebuild failed: {}", e));
            1
        }
        Some(Ok(count)) => {
            if !quiet {
                println(&format!(
                    "✓ {} commands indexed{}",
                    count,
                    if imported > 0 { format!(", {} new", imported) } else { String::new() }
                ));
            }
            0
        }
    }
}

// -- flush / record ---------------------------------------------------------

pub fn cmd_flush() -> i32 {
    let (ingested, skipped, total, _newest, learned) = crate::spool::flush();
    let _ = total;
    if ingested > 0 {
        let mut parts = vec![format!("ingested {}", ingested)];
        if skipped > 0 {
            parts.push(format!("skipped {} (secrets, control bytes, or not one line)", skipped));
        }
        if learned > 0 {
            parts.push(format!("learned {} new tools", learned));
        }
        println(&format!("✓ {}", parts.join(", ")));
    } else if skipped > 0 {
        println(&format!("✓ nothing ingested; skipped {} unusable records", skipped));
    } else {
        println("✓ nothing pending");
    }
    0
}

pub fn auto_maintain(total: i64, newest_ts: i64) {
    if total % auto_rebuild_every() != 0 {
        rebuild_when_stale(newest_ts);
    } else {
        crate::maintenance::rebuild_quietly();
    }
}

/// A record may not wait a hundred records to be worth suggesting: every
/// record asks one cheap question (one stat, one comparison), and when the
/// index on disk is older than the newest row by more than the debounce
/// window, the background rebuild runs.
fn rebuild_when_stale(newest_ts: i64) {
    let idx = crate::paths::index_location();
    if !std::path::Path::new(&idx).exists() {
        crate::maintenance::rebuild_quietly();
        return;
    }
    let mtime = match std::fs::metadata(&idx).and_then(|m| m.modified()) {
        Ok(t) => t,
        Err(_) => return,
    };
    let age = mtime.elapsed().map(|e| e.as_secs_f64()).unwrap_or(0.0);
    if age < REBUILD_MIN_INTERVAL {
        return;
    }
    let mut newest = newest_ts;
    if newest == 0 {
        // The one caller that has no timestamp is the missing-index case
        // above, which rebuilds before ever reading it; ask the store here.
        match crate::store::connect() {
            Ok(con) => {
                newest = con
                    .query_row("SELECT COALESCE(MAX(ts), 0) FROM commands", [], |r| r.get(0))
                    .unwrap_or(0);
            }
            Err(_) => return,
        }
    }
    use std::time::UNIX_EPOCH;
    let mtime_s = mtime
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs() as i64)
        .unwrap_or(0);
    if newest > mtime_s {
        crate::maintenance::rebuild_quietly();
    }
}

fn learn_on_use(command: &str) {
    /// Learn a tool's help the first time the user actually runs it. This
    /// whole call already happens inside a process nothing waits on, so
    /// nothing here touches the prompt, and it fires at most once per tool
    /// because the result is cached in cli_tools. TAI_NO_LEARN=1 turns it
    /// off.
    if std::env::var("TAI_NO_LEARN").unwrap_or_default() == "1" {
        return;
    }
    let name = command.split_whitespace().next().unwrap_or("").to_string();
    if name.is_empty() || name.starts_with('-') || name.contains('/') || name.len() > 64 {
        return;
    }
    if crate::util::which(&name).is_none() {
        return;
    }
    if crate::knowledge::is_known(&name) {
        return;
    }
    let learned = crate::knowledge::learn(Some(&[name]));
    if learned > 0 {
        crate::maintenance::rebuild_quietly();
    }
}

pub fn cmd_record(command: &str, cwd: &str, git: &str, branch: &str, exit: i64) -> i32 {
    // Spooled records land first, so a record typed by hand is the newest
    // row, not a row competing with a pending batch for the same moment.
    let _ = crate::spool::drain();
    let (ok, total, newest) =
        crate::store::append_and_count(command, cwd, git, branch, exit);
    if !ok {
        if command.trim().is_empty() {
            println("tai: nothing to record — no command was given");
        } else {
            let shown: String = {
                let t = command.trim();
                if t.chars().count() > 40 {
                    let cut: String = t.chars().take(39).collect();
                    format!("{}…", cut)
                } else {
                    t.to_string()
                }
            };
            println(&format!(
                "tai: not recorded — {} looks like a secret or a session-harness wrapper",
                crate::cli::py_repr(&shown)
            ));
        }
        return 1;
    }
    auto_maintain(total, newest);
    learn_on_use(command);
    println("✓ recorded");
    0
}

// -- discover ---------------------------------------------------------------

pub fn cmd_discover(tools: &[String]) -> i32 {
    let n = crate::knowledge::learn(if tools.is_empty() { None } else { Some(tools) });
    println(&format!("discovered {} installed tools", n));
    0
}

// -- purge / forget ---------------------------------------------------------

pub fn cmd_purge(stale: bool, rebuild: bool) -> i32 {
    let n = crate::store::purge_unrecordable();
    let m = if stale { crate::store::purge_stale() } else { 0 };
    if (n > 0 || m > 0) && rebuild {
        crate::maintenance::rebuild_quietly();
    }
    let mut parts: Vec<String> = Vec::new();
    if n > 0 {
        parts.push(format!("purged {} unusable history rows", n));
    }
    if m > 0 {
        parts.push(format!("purged {} commands whose paths no longer exist", m));
    }
    if !parts.is_empty() {
        println(&format!(
            "{}{}",
            parts.join("; "),
            if rebuild { " and rebuilt the index" } else { "" }
        ));
    } else {
        println("nothing to purge");
        if !stale {
            println!("run 'tai purge --stale' to also drop commands whose paths are gone");
        }
    }
    if n > 0 && !rebuild {
        println("run 'tai refresh' to rebuild the shell indexes");
    }
    0
}

pub fn cmd_forget(commands: &[String], rebuild: bool) -> i32 {
    let names: Vec<String> =
        commands.iter().map(|n| n.trim().to_string()).filter(|n| !n.is_empty()).collect();
    if names.is_empty() {
        println("usage: tai forget <command> [more commands...]");
        println("       the names are exact — 'tai forget opencoe git sta' forgets those lines, not their prefixes");
        return 2;
    }
    let n = match crate::store::forget_commands(&names) {
        Ok(n) => n,
        Err(e) => {
            println(&format!("tai forget: could not read the history store: {}", e));
            return 1;
        }
    };
    let mut shown = names
        .iter()
        .take(6)
        .map(|x| format!("`{}`", x))
        .collect::<Vec<_>>()
        .join(", ");
    if names.len() > 6 {
        shown.push('…');
    }
    if n == 0 {
        println(&format!("nothing forgotten — no stored row matches {}", shown));
        return 0;
    }
    if rebuild {
        crate::maintenance::rebuild_quietly();
    }
    let row_word = if n == 1 { "row" } else { "rows" };
    let cmd_word = if names.len() == 1 { "command" } else { "commands" };
    println(&format!(
        "forgot {} {} for {} {}: {}{}",
        n,
        row_word,
        names.len(),
        cmd_word,
        shown,
        if rebuild { "; index rebuilt" } else { "" }
    ));
    if !rebuild {
        println("run 'tai refresh' to rebuild the shell indexes");
    }
    0
}

// -- version / update -------------------------------------------------------

pub fn read_version() -> String {
    env!("TAI_VERSION").to_string()
}

/// The checkout this `tai` belongs to, when it can be named: a checkout keeps
/// install.sh and VERSION beside it, and that is the copy an update has to
/// replace. No state file, no configuration.
fn repo_dir() -> Option<std::path::PathBuf> {
    if let Ok(env) = std::env::var("TAI_REPO_DIR") {
        if !env.is_empty() {
            return Some(std::path::PathBuf::from(env));
        }
    }
    if let Ok(exe) = std::env::current_exe() {
        if let Some(dir) = exe.parent() {
            if dir.join("install.sh").exists() && dir.join("VERSION").exists() {
                return Some(dir.to_path_buf());
            }
        }
    }
    let home = crate::paths::home_dir();
    let checkout = std::path::PathBuf::from(
        std::env::var("TAI_DIR").unwrap_or_else(|_| format!("{}/.tai", home)),
    );
    if checkout.join("install.sh").exists() {
        return Some(checkout);
    }
    None
}

/// 'npm', 'git', or 'none' — decided by where the running code lives.
fn install_method(repo: Option<&std::path::Path>) -> &'static str {
    if let Ok(exe) = std::env::current_exe() {
        let s = exe.to_string_lossy();
        if s.contains("node_modules/terminal-ai-helper") {
            return "npm";
        }
    }
    // The installer records the door for a binary copied out of the tree it
    // came from; a marker missing falls through to the checkout's own shape.
    let data = crate::paths::data_location();
    if let Ok(text) = std::fs::read_to_string(format!("{}/install-method", data)) {
        match text.trim() {
            "npm" => return "npm",
            "git" => return "git",
            "deb" => return "deb",
            _ => {}
        }
    }
    match repo {
        Some(r) if r.join(".git").exists() => "git",
        _ => "none",
    }
}

/// True when npm's postinstall hook already ran the installer from the tree.
fn npm_configured() -> bool {
    let tail = "node_modules/terminal-ai-helper";
    let wrapper = std::path::PathBuf::from(format!(
        "{}/tai",
        std::env::var("BIN_DIR").unwrap_or_else(|_| format!("{}/.local/bin", crate::paths::home_dir()))
    ));
    let text = match std::fs::read_to_string(&wrapper) {
        Ok(t) => t,
        Err(_) => return false,
    };
    if !text.contains(&format!("{}/tai/cli.py", tail)) && !text.contains(tail) {
        return false;
    }
    let home = crate::paths::home_dir();
    for (rc, plugin) in [(".zshrc", "tai.zsh"), (".bashrc", "tai.bash")] {
        let lines = match std::fs::read_to_string(format!("{}/{}", home, rc)) {
            Ok(t) => t,
            Err(_) => return false,
        };
        if !lines
            .lines()
            .any(|l| l.contains("source") && l.contains(&format!("{}/plugins/{}", tail, plugin)))
        {
            return false;
        }
    }
    true
}

fn update_npm(repo: &std::path::Path) -> i32 {
    let npm = match crate::util::which("npm") {
        Some(n) => n,
        None => {
            println("tai: npm is not installed, so there is nothing to update from");
            println("     reinstall with: npm i -g terminal-ai-helper@latest --allow-scripts=terminal-ai-helper");
            return 1;
        }
    };
    let before = read_version();
    let status = Command::new(&npm)
        .args(["install", "-g", "terminal-ai-helper@latest", "--allow-scripts=terminal-ai-helper"])
        .status();
    if status.map(|s| !s.success()).unwrap_or(true) {
        println("tai: npm install failed — the previous version is still installed");
        return 1;
    }
    let after = read_version();
    // Flushed before anything else prints: npm's own output above is the
    // progress, and this line is the verdict.
    let verdict = if after == before {
        "✓ already up to date".to_string()
    } else {
        format!("✓ updated to {}", after)
    };
    println(&verdict);
    if npm_configured() {
        return 0;
    }
    let installer = repo.join("install.sh");
    if !installer.exists() {
        println(&format!("tai: {} is missing, so the new code was not installed", installer.display()));
        return 1;
    }
    // A subprocess, not a re-entrant call: the install just replaced the
    // files this process was loaded from.
    Command::new("bash")
        .arg(&installer)
        .status()
        .map(|s| s.code().unwrap_or(1))
        .unwrap_or(1)
}

pub fn cmd_update() -> i32 {
    let repo = repo_dir();
    if install_method(repo.as_deref()) == "npm" {
        return match repo {
            Some(r) => update_npm(&r),
            None => {
                println("tai: npm is not installed, so there is nothing to update from");
                1
            }
        };
    }
    let git = match crate::util::which("git") {
        Some(g) => g,
        None => {
            println("tai: git is not installed, so there is nothing to update from");
            println(&format!("     reinstall from {}", REPO_URL));
            return 1;
        }
    };
    let repo = match repo {
        Some(r) if r.join(".git").exists() => r,
        Some(r) => {
            println(&format!("tai: {} is not a git checkout, so there is nothing to pull", r.display()));
            println(&format!("     reinstall from {} to get the latest version", REPO_URL));
            return 1;
        }
        None => {
            println("tai: this install is not a checkout, so there is nothing to pull");
            println(&format!("     reinstall from {} to get the latest version", REPO_URL));
            return 1;
        }
    };

    let head = || -> String {
        Command::new(&git)
            .args(["-C", &repo.to_string_lossy(), "rev-parse", "--short", "HEAD"])
            .output()
            .map(|o| String::from_utf8_lossy(&o.stdout).trim().to_string())
            .unwrap_or_default()
    };
    let run_git = |args: &[&str]| -> (bool, String) {
        let out = Command::new(&git)
            .arg("-C")
            .arg(&repo)
            .args(args)
            .output();
        match out {
            Ok(o) => {
                let ok = o.status.success();
                let text = String::from_utf8_lossy(&o.stderr).to_string();
                (ok, text)
            }
            Err(e) => (false, e.to_string()),
        }
    };

    // The checkout the one-liner cloned is this install's own tree, not a
    // development branch: the update is a fetch one commit deep and a reset
    // to what origin named. A checkout anywhere else is somebody's tree, and
    // it keeps the careful pull: fast-forward only, a divergence fails loudly.
    let home = crate::paths::home_dir();
    let managed_dir = std::path::PathBuf::from(
        std::env::var("TAI_DIR").unwrap_or_else(|_| format!("{}/.tai", home)),
    );
    let managed = repo.canonicalize().ok() == managed_dir.canonicalize().ok()
        && managed_dir.exists();

    let before = head();
    if managed {
        let (ok, _) = run_git(&["rev-parse", "--abbrev-ref", "HEAD"]);
        if !ok {
            println("tai: git rev-parse failed");
            return 1;
        }
        let branch = Command::new(&git)
            .arg("-C")
            .arg(&repo)
            .args(["rev-parse", "--abbrev-ref", "HEAD"])
            .output()
            .map(|o| String::from_utf8_lossy(&o.stdout).trim().to_string())
            .unwrap_or_default();
        let mut failed_step: Option<String> = None;
        for argv in [
            vec!["fetch", "--depth", "1", "--quiet", "origin", branch.as_str()],
            vec!["reset", "--hard", "--quiet", "FETCH_HEAD"],
        ] {
            let (ok, err) = run_git(&argv);
            if !ok {
                failed_step = Some(argv[0].to_string());
                println(&format!("tai: git {} failed:", argv[0]));
                println(err.trim());
                return 1;
            }
        }
        // Hygiene, not a step.
        let _ = run_git(&["gc", "--prune=now", "--quiet"]);
    } else {
        let (ok, err) = run_git(&["pull", "--ff-only"]);
        if !ok {
            // A local edit in the checkout is the usual cause, and git's own
            // message says exactly what to do about it.
            println("tai: git pull failed:");
            println(err.trim());
            return 1;
        }
    }
    let after = head();
    let verdict = if after == before {
        "✓ already up to date".to_string()
    } else {
        format!("✓ updated to {}", after)
    };
    println(&verdict);

    let installer = repo.join("install.sh");
    if !installer.exists() {
        println(&format!("tai: {} is missing, so the new code was not installed", installer.display()));
        return 1;
    }
    Command::new("bash")
        .arg(&installer)
        .status()
        .map(|s| s.code().unwrap_or(1))
        .unwrap_or(1)
}

pub fn cmd_version() -> i32 {
    /// Which release this is, and where the shell's plugin line points. The
    /// second half exists because the two can disagree: an install that
    /// switched doors left the rc line naming the older tree, and the shell
    /// went on running code `tai version` no longer reported.
    let version = read_version();
    println(&format!("tai {}", version));
    let repo = repo_dir();
    let home = crate::paths::home_dir();
    for (shell, rc_name, plugin) in
        [("zsh", ".zshrc", "tai.zsh"), ("bash", ".bashrc", "tai.bash")]
    {
        let mut wired = String::new();
        if let Ok(text) = std::fs::read_to_string(format!("{}/{}", home, rc_name)) {
            for line in text.lines() {
                if line.trim_start().starts_with('#') || !line.contains("source") || !line.contains(plugin) {
                    continue;
                }
                let tokens: Vec<&str> =
                    line.split_whitespace().filter(|t| t.contains(plugin)).collect();
                if let Some(t) = tokens.last() {
                    wired = t.trim_matches('"').trim_matches('\'').to_string();
                    break;
                }
            }
        }
        if wired.is_empty() {
            println(&format!("{} plugin not wired", shell));
            continue;
        }
        let mut note = String::new();
        if let Some(ref repo) = repo {
            let expanded = crate::paths::expanduser(&wired);
            let ours = std::fs::canonicalize(&expanded).ok();
            let theirs = std::fs::canonicalize(repo.join("plugins").join(plugin)).ok();
            if ours.is_some() && ours != theirs {
                note = " — another install; 'tai update' rewires it here".to_string();
            }
        }
        println(&format!("{} plugin {}{}", shell, wired, note));
    }
    // What the running shells say they sourced. The rc file names a tree, but a
    // shell started before an update keeps the old code in memory for its whole
    // life — the freeze the current release had fixed kept reproducible in a
    // session that had never re-sourced, with `tai version` reporting the new
    // release and nothing able to say otherwise. The plugin leaves one line
    // here at source time ("zsh 3.1.1"); any version but this binary's own is
    // the difference between a bug report about the current code and one about
    // code this release already replaced.
    let marker = format!("{}/plugin-version", crate::paths::data_location());
    if let Ok(text) = std::fs::read_to_string(&marker) {
        let text = text.trim();
        if let Some((shell, plugin_version)) = text.split_once(' ') {
            if plugin_version != version {
                if ver_older(plugin_version, &version) {
                    println(&format!(
                        "the running {} shell still holds plugin {} — open a new shell (exec {}) to load {}",
                        shell, plugin_version, shell, version
                    ));
                } else {
                    println(&format!(
                        "the running {} shell holds plugin {} while this binary is {} — the tai on PATH is stale; update it",
                        shell, plugin_version, version
                    ));
                }
            }
        }
    }
    0
}

/// True when the running shell's plugin version sorts before the binary's —
/// the "your session predates the fix" shape. A non-release plugin version
/// (empty, "unknown") counts as older: the advice is the same either way.
fn ver_older(plugin: &str, binary: &str) -> bool {
    let (p, b) = (plugin.trim(), binary.trim());
    if p.is_empty() || b.is_empty() {
        return true;
    }
    let nums = |s: &str| -> Vec<u64> {
        s.split('.')
            .map(|p| p.parse::<u64>().unwrap_or(0))
            .collect()
    };
    nums(p) < nums(b)
}

// -- uninstall --------------------------------------------------------------

pub fn cmd_uninstall() -> i32 {
    let home = crate::paths::home_dir();
    // TAI_DATA_DIR overrides only *this* command's idea of where the data is;
    // it is a seam for the install tests, not a knob.
    let data = std::path::PathBuf::from(
        std::env::var("TAI_DATA_DIR").unwrap_or_else(|_| crate::paths::data_location()),
    );
    let wrapper = std::path::PathBuf::from(format!(
        "{}/tai",
        std::env::var("BIN_DIR").unwrap_or_else(|_| format!("{}/.local/bin", home))
    ));
    let mut removed: Vec<String> = Vec::new();
    let mut npm_door = false;
    if wrapper.exists() || std::fs::symlink_metadata(&wrapper).map(|m| m.file_type().is_symlink()).unwrap_or(false) {
        // The wrapper names the door it came from. Read before unlink.
        if let Ok(text) = std::fs::read_to_string(&wrapper) {
            npm_door = text.contains("node_modules/terminal-ai-helper");
        }
        let _ = std::fs::remove_file(&wrapper);
        removed.push(wrapper.to_string_lossy().to_string());
    }
    if data.exists() {
        let _ = std::fs::remove_dir_all(&data);
        removed.push(data.to_string_lossy().to_string());
    }
    for rc_name in [".zshrc", ".bashrc"] {
        let rc = std::path::PathBuf::from(format!("{}/{}", home, rc_name));
        if !rc.exists() {
            continue;
        }
        let text = match std::fs::read(&rc) {
            Ok(bytes) => String::from_utf8_lossy(&bytes).to_string(),
            Err(_) => continue,
        };
        // Two managed blocks, each identified by its marker and its own
        // payload lines rather than by position, so a user's own edits
        // between them cannot leave half a block behind.
        let mut kept: Vec<String> = Vec::new();
        let src: Vec<&str> = text.split_inclusive('\n').collect();
        let mut i = 0;
        while i < src.len() {
            let line = src[i];
            if line.trim() == "# tai autocomplete (installed by ./install.sh)" {
                i += 1;
                while i < src.len() && src[i].contains("plugins/") && src[i].contains("source") {
                    i += 1;
                }
                continue;
            }
            if line.starts_with("# tai: zsh-autosuggestions paused") {
                i += 1;
                while i < src.len()
                    && (src[i].contains("_zsh_autosuggest_start") || src[i].starts_with("# "))
                {
                    i += 1;
                }
                continue;
            }
            if line.contains("plugins/tai.") {
                i += 1;
                continue;
            }
            kept.push(line.to_string());
            i += 1;
        }
        let new = kept.concat();
        if new != text {
            let tmp = rc.with_file_name(format!("{}.tai-uninstall", rc_name));
            if std::fs::write(&tmp, new.as_bytes()).is_ok()
                && std::fs::rename(&tmp, &rc).is_ok()
            {
                removed.push(rc.to_string_lossy().to_string());
            }
        }
    }
    // The piped door clones the code itself to TAI_DIR (default ~/.tai), and
    // leaving it behind would leave the program installed in every way but
    // the wrapper. One floor stands: never a directory the home lives in or
    // under. Last on purpose: this command usually runs from inside the
    // checkout, so removing it has to be the last thing the run does.
    let checkout = std::path::PathBuf::from(
        std::env::var("TAI_DIR").unwrap_or_else(|_| format!("{}/.tai", home)),
    );
    let mut held = String::new();
    if checkout.exists() {
        let home_path = std::path::PathBuf::from(&home);
        let home_inside = checkout
            .canonicalize()
            .ok()
            .map(|c| home_path.canonicalize().ok().map(|h| h.starts_with(&c)).unwrap_or(false))
            .unwrap_or(false);
        if home_inside {
            held = format!("left {} — it holds the home directory", checkout.display());
        } else {
            let ours = checkout.join("install.sh").exists();
            if checkout.is_dir() {
                let _ = std::fs::remove_dir_all(&checkout);
            } else {
                let _ = std::fs::remove_file(&checkout);
            }
            removed.push(format!(
                "{}{}",
                checkout.display(),
                if ours { "" } else { " (not a tai checkout — removed anyway)" }
            ));
        }
    }
    // The npm door's code is npm's to remove.
    let mut npm_note = String::new();
    if npm_door || install_method(repo_dir().as_deref()) == "npm" {
        if let Some(npm) = crate::util::which("npm") {
            let out = Command::new(npm)
                .args(["rm", "-g", "terminal-ai-helper"])
                .output();
            match out {
                Ok(o) if o.status.success() => {
                    removed.push("terminal-ai-helper (npm global package)".to_string())
                }
                Ok(o) => {
                    npm_note = String::from_utf8_lossy(&o.stderr).trim().to_string()
                }
                Err(e) => npm_note = e.to_string(),
            }
        } else {
            npm_note = "npm is not on PATH — the package is removed with: npm rm -g terminal-ai-helper".to_string();
        }
    }
    println(if removed.is_empty() { "tai was not installed" } else { "tai removed" });
    for path in &removed {
        println(&format!("  removed {}", path));
    }
    if !npm_note.is_empty() {
        println(&format!("  {}", npm_note));
    }
    if !held.is_empty() {
        println(&format!("  {}", held));
    }
    0
}

// -- web / bench / tune / eval ----------------------------------------------

pub fn cmd_web(port: u16, no_browser: bool) -> i32 {
    crate::web::serve(port, !no_browser)
}

pub fn cmd_bench(limit: usize) -> i32 {
    crate::bench::main(limit);
    0
}

pub fn cmd_tune(limit: usize, trials: usize) -> i32 {
    crate::tune::main(limit, trials);
    0
}

pub fn cmd_doctor() -> i32 {
    crate::bench::doctor();
    0
}

pub fn cmd_eval() -> i32 {
    crate::evaluate::main()
}

pub fn cmd_eval_jev() -> i32 {
    crate::evaluate_jev::main()
}

pub fn cmd_jev(prefix: &str, candidates: &[String], cwd: &str, git: &str, branch: &str, previous: &str, json: bool) -> i32 {
    match crate::jev::decide(prefix, candidates, cwd, git, branch, previous) {
        Ok(result) => {
            let value = serde_json::json!({
                "choice": result.choice,
                "probabilities": serde_json::Value::Object(
                    result.probabilities.iter().map(|(k, v)| (k.clone(), serde_json::json!(v))).collect()),
                "confidence": result.confidence,
                "show_now": result.show_now,
                "destructive": result.destructive,
                "model": result.model,
                "usage": result.usage,
            });
            if json {
                println(&serde_json::to_string_pretty(&value).unwrap_or_default());
            } else {
                println(result.choice.as_str());
            }
            0
        }
        Err(e) => {
            eprintln!("tai: {}", e);
            1
        }
    }
}

// -- the debug trail ---------------------------------------------------------

/// Wrap one command's body in begin/end trail lines. The trail is silent
/// unless TAI_DEBUG=1, so when it is off this is two calls that return
/// immediately; when it is on, every tai process the user or a plugin runs
/// leaves its argv, its exit code and its duration on the same file the
/// shell's half of the trail is writing — which is what turns "tai was slow"
/// into a number and "tai crashed" into a last line.
fn with_dbg(tag: &str, detail: String, f: impl FnOnce() -> i32) -> i32 {
    if crate::dbg::enabled() {
        crate::dbg::log(tag, &format!("begin {}", detail));
    }
    let t0 = std::time::Instant::now();
    let code = f();
    if crate::dbg::enabled() {
        crate::dbg::log(
            tag,
            &format!("end code={} in {:.1}ms", code, t0.elapsed().as_secs_f64() * 1000.0),
        );
    }
    code
}

/// A UTC timestamp, civil from days — no date library in the tree, and `tai
/// debug` reads better with a date on it than with a bare epoch. Valid to
/// 9999; good enough for a log header.
fn utc_string(secs: u64) -> String {
    let days = (secs / 86_400) as i64;
    let rem = secs % 86_400;
    // Howard Hinnant's civil_from_days.
    let z = days + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z.rem_euclid(146_097);
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let y = yoe + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = doy - (153 * mp + 2) / 5 + 1;
    let m = if mp < 10 { mp + 3 } else { mp - 9 };
    let y = if m <= 2 { y + 1 } else { y };
    format!(
        "{:04}-{:02}-{:02} {:02}:{:02}:{:02} UTC",
        y,
        m,
        d,
        rem / 3600,
        (rem % 3600) / 60,
        rem % 60
    )
}

/// `tai debug` — print everything the trail gathered.
///
/// The point of the command is one paste: run it, send the output. It prints
/// the report — versions, the shell's plugin version, the paths and their
/// sizes, the knobs that were set — and then the whole trail, oldest line
/// first, so the crash is the last thing in the paste.
///
///   --clear    empty the trail (it starts again with the next event)
///   --tail N   only the last N trail lines, when the whole file is too much
///              for one paste
pub fn cmd_debug(argv: &[String]) -> i32 {
    let mut clear = false;
    let mut tail: Option<usize> = None;
    let mut i = 0;
    while i < argv.len() {
        match argv[i].as_str() {
            "--clear" => clear = true,
            "--tail" | "-n" => {
                i += 1;
                if i < argv.len() {
                    tail = argv[i].trim().parse::<usize>().ok();
                }
            }
            "--help" | "-h" => {
                println("usage: tai debug [--tail N] [--clear]");
                println("       print everything the debug trail gathered, oldest first");
                println("       --tail N  only the last N lines");
                println("       --clear   empty the trail afterwards (or before: either order works)");
                println("");
                println("turn the trail on with TAI_DEBUG=1, or by running `tai-debug` inside a shell");
                println("that has the plugin loaded — then reproduce the problem and run `tai debug`.");
                return 0;
            }
            other => {
                println(&format!("tai debug: unknown argument {} — try --help", py_repr(other)));
                return 2;
            }
        }
        i += 1;
    }
    if clear {
        let path = crate::dbg::file_path();
        if std::fs::write(&path, b"").is_ok() {
            println(&format!("tai: trail cleared — {}", path.display()));
        }
    }

    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0);
    println(&format!("tai debug report — {}", utc_string(now)));
    println("");

    // -- what is running ----------------------------------------------------
    let exe = std::env::current_exe()
        .map(|p| p.display().to_string())
        .unwrap_or_else(|_| "?".to_string());
    println(&format!(
        "binary      : {} ({}, {})",
        exe,
        std::env::consts::OS,
        std::env::consts::ARCH
    ));
    println(&format!("version     : {}", env!("CARGO_PKG_VERSION")));
    // The release the running shells sourced. The plugin writes this at every
    // shell start; an old value beside a new binary is the classic “updated
    // but the session predates it” answer, and it is one read.
    let plugin_version_file = crate::paths::data_dir().join("plugin-version");
    println(&format!(
        "plugin      : {} ({})",
        std::fs::read_to_string(&plugin_version_file)
            .map(|s| s.trim().to_string())
            .unwrap_or_else(|_| "nothing recorded yet".to_string()),
        plugin_version_file.display(),
    ));
    println(&format!(
        "shell       : {} on {}",
        std::env::var("SHELL").unwrap_or_else(|_| "?".to_string()),
        std::env::var("TERM").unwrap_or_else(|_| "?".to_string()),
    ));
    println("");

    // -- the knobs ----------------------------------------------------------
    println("environment (what is set):");
    let mut knobs = vec![
        "TAI_DEBUG",
        "TAI_DEBUG_FILE",
        "TAI_BIN",
        "TAI_DB",
        "TAI_INDEX",
        "TAI_SPOOL",
        "TAI_SPOOL_MAX",
        "TAI_SPOOL_SECONDS",
        "TAI_HISTORY_FILES",
        "TAI_NO_MENU",
        "TAI_NO_LEARN",
        "TAI_NO_AUTO_RECORD",
        "TAI_JEV_TIMEOUT",
        "TAI_RELEASE_URL",
        "XDG_DATA_HOME",
        "XDG_CACHE_HOME",
    ];
    knobs.sort_unstable();
    let mut any_knob = false;
    for k in &knobs {
        if let Ok(v) = std::env::var(k) {
            if !v.trim().is_empty() {
                any_knob = true;
                println(&format!("  {}={}", k, py_repr(&v)));
            }
        }
    }
    if !any_knob {
        println("  (none — every default in force, TAI_DEBUG off)");
    }
    println("");

    // -- the files ----------------------------------------------------------
    println("files (name, bytes, modified):");
    let cache = crate::dbg::file_path()
        .parent()
        .map(|p| p.to_path_buf())
        .unwrap_or_else(|| PathBuf::from("/nonexistent"));
    let mut listing: Vec<PathBuf> = vec![
        crate::paths::db_path(),
        PathBuf::from(crate::spool::spool_path()),
        PathBuf::from(crate::paths::index_location()),
        PathBuf::from(crate::paths::bash_index_location()),
        cache.join("units-system.txt"),
        cache.join("units-user.txt"),
        plugin_version_file,
    ];
    listing.sort();
    listing.dedup();
    for f in &listing {
        match std::fs::metadata(f) {
            Ok(m) => {
                let modified = m
                    .modified()
                    .ok()
                    .and_then(|t| t.duration_since(UNIX_EPOCH).ok())
                    .map(|d| utc_string(d.as_secs()))
                    .unwrap_or_else(|| "?".to_string());
                println(&format!(
                    "  {:>12}  {}  {}",
                    m.len(),
                    modified,
                    f.display()
                ));
            }
            Err(_) => println(&format!("  {:>12}  {}", "-", f.display())),
        }
    }
    println("");

    // -- the trail ----------------------------------------------------------
    let path = crate::dbg::file_path();
    let body = std::fs::read(&path).unwrap_or_default();
    let text = String::from_utf8_lossy(&body);
    let lines: Vec<&str> = text.lines().collect();
    println(&format!(
        "trail: {} ({} lines) — {}",
        path.display(),
        lines.len(),
        if crate::dbg::enabled() {
            "TAI_DEBUG is on in THIS process"
        } else {
            "TAI_DEBUG is off in THIS process (the trail below was written by others)"
        }
    ));
    if lines.is_empty() {
        println("  (empty)");
        println("");
        println("Nothing on the trail yet. To gather a report from a shell that has the plugin:");
        println("  1. inside that shell, run: tai-debug            (or start it with TAI_DEBUG=1)");
        println("  2. reproduce the problem — the trail writes in realtime, a crash loses nothing;");
        println("  3. run: tai debug                              (this command) and send the output.");
        println("");
        println("The shell's own events are one line each — 'upd' per keystroke redraw with the buffer,");
        println("'query' per lookup, 'menu'/'paint' per draw, 'alive' once a second, and 'shell gone' the");
        println("moment the shell died. The binary's events are 'cli', 'begin'/'end' per command, and");
        println("'panic' if one died. Whatever killed zsh, its last keystroke is in here.");
        return 0;
    }
    let start = match tail {
        Some(n) if n < lines.len() => lines.len() - n,
        _ => 0,
    };
    if start > 0 {
        println(&format!("  (--tail {} — the {} earlier lines are in the file)", tail.unwrap(), start));
    }
    for l in &lines[start..] {
        println(l);
    }
    0
}

// -- rc editing (the installer's text surgery) -------------------------------

fn read_rc(path: &str) -> String {
    std::fs::read(path)
        .map(|b| String::from_utf8_lossy(&b).to_string())
        .unwrap_or_default()
}

fn write_rc(path: &str, text: &str) -> bool {
    // Replaced atomically, the same rule the index builder follows. This file
    // is the user's shell: an interrupt between truncate and write would
    // otherwise leave a half-written .zshrc and a shell that cannot start.
    let tmp = format!("{}.tai-new", path);
    if std::fs::write(&tmp, text.as_bytes()).is_ok() && std::fs::rename(&tmp, path).is_ok() {
        return true;
    }
    let _ = std::fs::remove_file(&tmp);
    false
}

/// Enable the plugin source line in one rc file, retargeting the line an
/// older install's door left behind, refreshing the managed block in place,
/// and never adding a second copy.
fn rc_enable(rc: &str, source_line: &str, plugin_name: &str, ours: &str) -> i32 {
    let marker = "# tai autocomplete (installed by ./install.sh)";
    let text = read_rc(rc);
    let mut cleaned: Vec<String> = Vec::new();
    let mut retargeted: Vec<String> = Vec::new();
    let src: Vec<&str> = text.split_inclusive('\n').collect();
    let mut i = 0;
    while i < src.len() {
        let line = src[i];
        if line.trim() == marker {
            // The managed block: refresh its line in place rather than
            // removing and re-appending, so the block keeps its place and a
            // moved tree rewrites the one line instead of gaining a second.
            cleaned.push(line.to_string());
            i += 1;
            if i < src.len() && src[i].contains(plugin_name) && src[i].contains("source") {
                cleaned.push(format!("{}\n", source_line));
                i += 1;
            }
            continue;
        }
        let stripped = line.trim_start();
        let mut line = line.to_string();
        if !stripped.starts_with('#')
            && line.contains("source")
            && line.contains("plugins/")
            && line.contains(plugin_name)
            && !line.contains(ours)
        {
            // A tai source line left by an older door: the same plugin name
            // from another tree. Matching by name alone said "already
            // enabled" and kept the shell sourcing code updates never touch.
            // The line is this install's to own, so it is retargeted.
            let old = line
                .split_whitespace()
                .find(|t| t.contains(plugin_name))
                .map(|t| t.trim_matches('"').trim_matches('\'').to_string())
                .unwrap_or_else(|| line.trim().to_string());
            retargeted.push(old);
            line = format!("{}\n", source_line);
        }
        cleaned.push(line);
        i += 1;
    }
    // A retarget can meet a stale line twice; the line is sourced once, first
    // occurrence wins.
    let wanted = format!("{}\n", source_line);
    let mut deduped: Vec<String> = Vec::new();
    let mut seen = false;
    for line in cleaned {
        if line == wanted {
            if seen {
                continue;
            }
            seen = true;
        }
        deduped.push(line);
    }
    // A manually configured integration is already enabled; do not add a
    // second copy when the installer is rerun.
    let has_source = deduped.iter().any(|l| {
        l.contains(plugin_name) && l.contains("source") && !l.trim_start().starts_with('#')
    });
    if !has_source {
        if let Some(last) = deduped.last_mut() {
            if !last.ends_with('\n') && !last.ends_with('\r') {
                last.push('\n');
            }
        }
        deduped.push(format!("{}\n", marker));
        deduped.push(wanted);
    }
    for old in &retargeted {
        println(&format!(
            "tai: retargeted the {} source line ({} — was another install's tree)",
            plugin_name, old
        ));
    }
    if write_rc(rc, &deduped.concat()) {
        0
    } else {
        1
    }
}

/// Pause zsh-autosuggestions for the user who wants tai's hints to be the
/// visible ones, or give the block back. `add` is decided by the installer.
fn rc_pause_autosuggest(rc: &str, add: bool) -> i32 {
    let text = read_rc(rc);
    let src: Vec<&str> = text.split_inclusive('\n').collect();
    let mut kept: Vec<String> = Vec::new();
    let mut i = 0;
    while i < src.len() {
        let line = src[i];
        if line.starts_with("# tai: zsh-autosuggestions paused") {
            i += 1;
            while i < src.len()
                && (src[i].contains("_zsh_autosuggest_start") || src[i].starts_with("# "))
            {
                i += 1;
            }
            continue;
        }
        kept.push(line.to_string());
        i += 1;
    }
    if add {
        if let Some(last) = kept.last_mut() {
            if !last.ends_with('\n') && !last.ends_with('\r') {
                last.push('\n');
            }
        }
        kept.push("# tai: zsh-autosuggestions paused (installed by ./install.sh)\n".to_string());
        kept.push(
            "# tai draws its hint into the same POSTDISPLAY, and this plugin fetches\n".to_string(),
        );
        kept.push(
            "# asynchronously, so tai's own hints never reach the screen. Both work.\n".to_string(),
        );
        kept.push("# Removed by `tai uninstall`, and by TAI_KEEP_AUTOSUGGEST=1.\n".to_string());
        kept.push("add-zsh-hook -d precmd _zsh_autosuggest_start 2>/dev/null\n".to_string());
    }
    if write_rc(rc, &kept.concat()) {
        0
    } else {
        1
    }
}

// -- parsing ----------------------------------------------------------------

/// Hand-parse `tai suggest …`'s seven flags, or return None to say no.
/// suggest is the one command a curious person runs first and the one the
/// dashboard's try box re-runs. Any token this parser cannot name returns
/// None and the full parser reports it properly.
struct SuggestArgs {
    prefix: String,
    cwd: String,
    git: String,
    branch: String,
    last: String,
    limit: usize,
    json: bool,
    jev: bool,
}

fn fast_suggest(argv: &[String]) -> Option<SuggestArgs> {
    let mut a = SuggestArgs {
        prefix: String::new(),
        cwd: String::new(),
        git: String::new(),
        branch: String::new(),
        last: String::new(),
        limit: 1,
        json: false,
        jev: false,
    };
    let mut positional: Vec<String> = Vec::new();
    let mut it = argv.iter();
    while let Some(tok) = it.next() {
        if tok == "--" {
            return None;
        }
        if let Some(rest) = tok.strip_prefix("--") {
            let (name, eq, value) = match rest.split_once('=') {
                Some((n, v)) => (n, true, v.to_string()),
                None => (rest, false, String::new()),
            };
            match name {
                "json" if !eq => a.json = true,
                "jev" if !eq => a.jev = true,
                "cwd" | "git" | "branch" | "last" => {
                    let v = if eq { value } else { it.next()?.clone() };
                    match name {
                        "cwd" => a.cwd = v,
                        "git" => a.git = v,
                        "branch" => a.branch = v,
                        _ => a.last = v,
                    }
                }
                "limit" => {
                    let v = if eq { value } else { it.next()?.clone() };
                    a.limit = v.parse().ok()?;
                }
                _ => return None, // --help, a typo, an abbreviation
            }
            continue;
        }
        if tok.starts_with('-') {
            return None; // short flags belong to the full parser
        }
        positional.push(tok.clone());
    }
    if positional.len() > 1 {
        return None;
    }
    a.prefix = positional.first().cloned().unwrap_or_default();
    Some(a)
}

/// Hand-parse `tai record …`'s five flags, so record skips the full parser.
struct RecordArgs {
    command: String,
    cwd: String,
    git: String,
    branch: String,
    exit: i64,
}

fn fast_record(argv: &[String]) -> RecordArgs {
    let mut a = RecordArgs {
        command: String::new(),
        cwd: String::new(),
        git: String::new(),
        branch: String::new(),
        exit: 0,
    };
    let mut words: Vec<String> = Vec::new();
    let mut it = argv.iter();
    while let Some(tok) = it.next() {
        match tok.as_str() {
            "--cwd" => a.cwd = it.next().unwrap_or(&String::new()).clone(),
            "--git" => a.git = it.next().unwrap_or(&String::new()).clone(),
            "--branch" => a.branch = it.next().unwrap_or(&String::new()).clone(),
            "--exit" => {
                a.exit = it
                    .next()
                    .and_then(|v| v.parse().ok())
                    .unwrap_or(0)
            }
            _ => words.push(tok.clone()),
        }
    }
    a.command = words.join(" ");
    a
}

const HELP: &str = "\
usage: tai <command> [arguments]

learn the commands you run and suggest them as you type

commands:
  suggest   print what tai would suggest for a prefix
  record    store one command in the history
  flush     ingest the records the plugins spooled
  refresh   import new history rows and rebuild the indexes
  discover  read a tool's --help and cache its vocabulary
  purge     drop unusable stored history rows
  forget    drop every stored row for the named commands
  update    pull the latest version and reinstall (alias: upgrade)
  version   print the release this install is running
  debug     print the whole debug trail — reproduce with TAI_DEBUG=1 or
            `tai-debug` first, then run this and send the output
  bench     report load time, latency and memory
  tune      search the ranking weights against the history
  doctor    say what is indexed and what is held back
  web       serve a read-only localhost dashboard of what tai learned
  eval      score the ranker against held-out history
  eval-jev  the same, against a hosted model
  jev       ask the hosted model directly
  uninstall remove the plugins and the stored history
";

/// Run one tai invocation. Returns the process exit code.
pub fn run(argv: &[String]) -> i32 {
    // The binary's first line on the trail. The argv is what the caller said;
    // the env that follows is what shaped the run — the same knobs `tai debug`
    // reports, written here so the trail of a single run is self-contained
    // even when the user cannot run `tai debug` afterwards (a shell that died,
    // a process another tool spawned).
    crate::dbg::log(
        "cli",
        &format!(
            "argv={:?} version={} home={} shell={} term={} TAI_DB={} TAI_INDEX={} TAI_SPOOL={}",
            argv,
            env!("CARGO_PKG_VERSION"),
            crate::paths::home_dir(),
            std::env::var("SHELL").unwrap_or_default(),
            std::env::var("TERM").unwrap_or_default(),
            std::env::var("TAI_DB").unwrap_or_default(),
            std::env::var("TAI_INDEX").unwrap_or_default(),
            std::env::var("TAI_SPOOL").unwrap_or_default(),
        ),
    );
    if argv.is_empty() {
        println(HELP);
        return 2;
    }
    let head = argv[0].as_str();
    // `record` is the fallback write path the plugins call by proxy, and
    // `version` is the one a person types to see what they are running — both
    // answer before the full parser runs. A word that does not recognise
    // (--help, a typo) drops through to the full handling, which names the
    // problem.
    if head == "record" && !argv.contains(&"--help".to_string()) && !argv.contains(&"-h".to_string())
    {
        let a = fast_record(&argv[1..]);
        return with_dbg("record", format!("cmd={:?}", a.command), || {
            cmd_record(&a.command, &a.cwd, &a.git, &a.branch, a.exit)
        });
    }
    if argv.len() == 1 && head == "version" {
        return cmd_version();
    }
    // Any other command is a tai process the user asked for, and a non-empty
    // spool is records waiting for one: drain it here, so `tai suggest`,
    // `tai refresh` or the dashboard never answer from a store that is a few
    // records behind what the shells just did. `record` drained above, `flush`
    // is the drain, and `version` stays lean; `uninstall` is about to delete
    // the store either way.
    if !matches!(head, "record" | "flush" | "version" | "uninstall") {
        let t0 = std::time::Instant::now();
        let drained = crate::spool::drain();
        crate::dbg::log(
            "flush",
            &format!(
                "drained ahead of `{}` in {:.1}ms",
                head,
                t0.elapsed().as_secs_f64() * 1000.0
            ),
        );
        let _ = drained;
    }
    if head == "suggest" {
        if let Some(a) = fast_suggest(&argv[1..]) {
            return with_dbg(
                "suggest",
                format!("prefix={:?} last={:?} json={} jev={}", a.prefix, a.last, a.json, a.jev),
                || cmd_suggest(&a.prefix, &a.cwd, &a.git, &a.branch, &a.last, a.limit, a.json, a.jev),
            );
        }
        // fall through to the structured parser for --help and errors
    }
    match head {
        "suggest" => parse_full_suggest(&argv[1..]),
        "record" => {
            let a = fast_record(&argv[1..]);
            with_dbg("record", format!("cmd={:?}", a.command), || {
                cmd_record(&a.command, &a.cwd, &a.git, &a.branch, a.exit)
            })
        }
        "flush" => with_dbg("flush", String::new(), cmd_flush),
        "refresh" => {
            let quiet = argv.iter().skip(1).any(|a| a == "--quiet");
            with_dbg("refresh", format!("quiet={}", quiet), || cmd_refresh(quiet))
        }
        "update" | "upgrade" => with_dbg("update", String::new(), cmd_update),
        "version" => cmd_version(),
        "debug" => cmd_debug(&argv[1..]),
        "discover" => with_dbg(
            "discover",
            format!("args={:?}", &argv[1..]),
            || cmd_discover(&argv[1..].iter().cloned().collect::<Vec<_>>()),
        ),
        "purge" => {
            let mut stale = false;
            let mut rebuild = true;
            for a in &argv[1..] {
                match a.as_str() {
                    "--stale" => stale = true,
                    "--no-rebuild" => rebuild = false,
                    "--rebuild" => rebuild = true,
                    _ => {}
                }
            }
            with_dbg("purge", format!("stale={}", stale), || cmd_purge(stale, rebuild))
        }
        "forget" => {
            let mut commands = Vec::new();
            let mut rebuild = true;
            for a in &argv[1..] {
                match a.as_str() {
                    "--no-rebuild" => rebuild = false,
                    "--rebuild" => rebuild = true,
                    _ => commands.push(a.clone()),
                }
            }
            with_dbg("forget", format!("commands={:?}", commands), || {
                cmd_forget(&commands, rebuild)
            })
        }
        "uninstall" => with_dbg("uninstall", String::new(), cmd_uninstall),
        "bench" => {
            let mut limit = 5000;
            for pair in argv.windows(2) {
                if pair[0] == "--limit" {
                    if let Ok(n) = pair[1].parse() {
                        limit = n;
                    }
                }
            }
            with_dbg("bench", format!("limit={}", limit), || cmd_bench(limit))
        }
        "tune" => {
            let mut limit = 10000;
            let mut trials = 60;
            for pair in argv.windows(2) {
                if pair[0] == "--limit" {
                    if let Ok(n) = pair[1].parse() {
                        limit = n;
                    }
                }
                if pair[0] == "--trials" {
                    if let Ok(n) = pair[1].parse() {
                        trials = n;
                    }
                }
            }
            with_dbg("tune", format!("limit={} trials={}", limit, trials), || {
                cmd_tune(limit, trials)
            })
        }
        "doctor" => with_dbg("doctor", String::new(), cmd_doctor),
        "web" | "dashboard" => {
            let mut port: u16 = 8247;
            let mut no_browser = false;
            let args = &argv[1..];
            for pair in args.windows(2) {
                if pair[0] == "--port" {
                    if let Ok(n) = pair[1].parse() {
                        port = n;
                    }
                }
            }
            if args.iter().any(|a| a == "--no-browser") {
                no_browser = true;
            }
            with_dbg("web", format!("port={}", port), || cmd_web(port, no_browser))
        }
        "eval" => with_dbg("eval", String::new(), cmd_eval),
        "eval-jev" => with_dbg("eval-jev", String::new(), cmd_eval_jev),
        "jev" => {
            let mut prefix = String::new();
            let mut candidates: Vec<String> = Vec::new();
            let mut cwd = String::new();
            let mut git = String::new();
            let mut branch = String::new();
            let mut previous = String::new();
            let mut json = false;
            let args = &argv[1..];
            let mut i = 0;
            while i < args.len() {
                match args[i].as_str() {
                    "--candidate" => {
                        i += 1;
                        if i < args.len() {
                            candidates.push(args[i].clone());
                        }
                    }
                    "--cwd" => {
                        i += 1;
                        if i < args.len() {
                            cwd = args[i].clone();
                        }
                    }
                    "--git" => {
                        i += 1;
                        if i < args.len() {
                            git = args[i].clone();
                        }
                    }
                    "--branch" => {
                        i += 1;
                        if i < args.len() {
                            branch = args[i].clone();
                        }
                    }
                    "--previous" => {
                        i += 1;
                        if i < args.len() {
                            previous = args[i].clone();
                        }
                    }
                    "--json" => json = true,
                    other => {
                        if prefix.is_empty() {
                            prefix = other.to_string();
                        }
                    }
                }
                i += 1;
            }
            if prefix.is_empty() {
                println("usage: tai jev <prefix> [--candidate cmd ...]");
                return 2;
            }
            cmd_jev(
                &prefix,
                &candidates,
                &cwd,
                &git,
                &branch,
                &previous,
                json,
            )
        }
        "--help" | "-h" | "help" => {
            println(HELP);
            0
        }
        // Hidden verbs the installer calls instead of shipping its own text
        // editor: the rc-editing rules are one implementation, tested where
        // the rest of the CLI conversation is tested.
        "rc-enable" => {
            let a: Vec<String> = argv[1..].to_vec();
            if a.len() < 4 {
                eprintln!("usage: tai rc-enable <rc> <source-line> <plugin-name> <ours>");
                return 2;
            }
            rc_enable(&a[0], &a[1], &a[2], &a[3])
        }
        "rc-pause-autosuggest" => {
            let a: Vec<String> = argv[1..].to_vec();
            if a.len() < 2 {
                eprintln!("usage: tai rc-pause-autosuggest <rc> <0|1>");
                return 2;
            }
            rc_pause_autosuggest(&a[0], a[1] == "1")
        }
        other => {
            println(&format!("tai: unknown command '{}'\n", other));
            println(HELP);
            2
        }
    }
}

fn parse_full_suggest(args: &[String]) -> i32 {
    let mut prefix = String::new();
    let mut cwd = String::new();
    let mut git = String::new();
    let mut branch = String::new();
    let mut last = String::new();
    let mut limit = 1usize;
    let mut json = false;
    let mut jev = false;
    let mut i = 0;
    while i < args.len() {
        match args[i].as_str() {
            "--cwd" => {
                i += 1;
                if i < args.len() {
                    cwd = args[i].clone();
                }
            }
            "--git" => {
                i += 1;
                if i < args.len() {
                    git = args[i].clone();
                }
            }
            "--branch" => {
                i += 1;
                if i < args.len() {
                    branch = args[i].clone();
                }
            }
            "--last" => {
                i += 1;
                if i < args.len() {
                    last = args[i].clone();
                }
            }
            "--limit" => {
                i += 1;
                if i < args.len() {
                    limit = args[i].parse().unwrap_or(1);
                }
            }
            "--json" => json = true,
            "--jev" => jev = true,
            other => {
                if prefix.is_empty() {
                    prefix = other.to_string();
                }
            }
        }
        i += 1;
    }
    cmd_suggest(&prefix, &cwd, &git, &branch, &last, limit, json, jev)
}
