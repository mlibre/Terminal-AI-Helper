//! Build the embedded zsh and bash indexes.
//!
//! Port of `tai/index.py`. This is deliberately not a service: `tai refresh`
//! writes two small shell-sourceable snapshots; the plugins load them into
//! associative arrays and perform suggestions entirely inside the shell.
//!
//! Each snapshot stores:
//!   _TAI_SCORE[command]       base rank
//!   _TAI_FIRST[first-word]    newline-separated candidates
//!   _TAI_WORD[word-prefix]    newline-separated candidates
//!   _TAI_SEQ[previous]        newline-separated candidates
//!   _TAI_FILE[line-without-its-last-word]  1, when that word is a file
//!   _TAI_LIVE[word]           1, when the word is a relative path the shell
//!                             must find where it stands before offering it
//!
//! Commands are escaped as POSIX single-quoted strings, which zsh accepts.

use std::collections::{HashMap, HashSet};
use std::io::Write;
use std::path::{Path, PathBuf};

use crate::engine::{Engine, LEN_PENALTY_DIV, MAX_LEN_PEN, W_FREQ, W_RECENCY, W_SUCCESS, W_INSTALLED};
use crate::knowledge::{candidates_for_prefix, load_all as load_tool_knowledge};
use crate::paths;
use crate::seed::{seed_rank, SEED_COMMANDS};
use crate::store::{load_rows, schema_note};
use crate::typo::shadow_map;
use crate::util;

// Where a convention sits. A seed the user has never run is a convention and
// not an observation, so its score is *only* its place in the corpus — a band
// below anything the history actually saw.
pub const SEED_RANK_STEP: i64 = 3;

// Vocabulary generated from a tool's `--help` is a guess until the history
// says otherwise: its base score is multiplied by COLD_FACTOR.
pub const COLD_FACTOR: f64 = 0.6;
pub const TOOL_CANDIDATE_CAP: usize = 32;
pub const TOOL_FLAG_CAP: usize = 12;
pub const TOOL_SUB_CAP: usize = 4;
pub const TOOL_SUBFLAG_CAP: usize = 10;
pub const TOOL_SUBSUB_CAP: usize = 8;
pub const SEQ_CANDIDATE_CAP: usize = 15;
pub const WORD_CANDIDATE_CAP: usize = 20;
pub const WORD_KEY_MAX_DEPTH: usize = 8;

pub fn index_path() -> PathBuf {
    match std::env::var("TAI_INDEX") {
        Ok(v) if !v.is_empty() => PathBuf::from(v),
        _ => PathBuf::from(paths::index_location()),
    }
}

pub fn bash_index_path() -> PathBuf {
    index_path().with_file_name("bash-index.bash")
}

/// POSIX single-quote escaping, which both shells read the same way.
pub fn zq(value: &str) -> String {
    if !value.contains('\'') {
        return format!("'{}'", value);
    }
    format!("'{}'", value.replace('\'', "'\\''"))
}

/// One `_TAI_WORD` entry: the key's candidates, capped, newline-joined.
fn word_value(values: &[String]) -> String {
    values.iter().take(WORD_CANDIDATE_CAP).cloned().collect::<Vec<_>>().join("\n")
}

fn which_word(word: &str) -> bool {
    util::path_lookup(word)
}

/// Write both shell indexes atomically and return the command count.
///
/// A store that cannot be read aborts the build rather than producing one. An
/// empty history and an unreadable one used to look identical here, so a
/// single corrupt or locked database replaced a real index with the seed
/// corpus and the caller reported success — the one failure that destroys the
/// last good state and says nothing while doing it.
pub fn build(max_commands: usize) -> Result<usize, String> {
    let mut eng = Engine::new();
    let rows = load_rows(50000)?;
    if rows.is_empty() && index_path().exists() {
        let note = schema_note();
        if !note.is_empty() {
            return Err(format!("cannot read the history database: {}", note));
        }
    }
    eng.build_from_rows(&rows);
    let from_history: HashSet<String> = eng.cmds.keys().cloned().collect();

    // Universal cold-start vocabulary. These are suggestions, never actions.
    let now = util::now_epoch();
    for cmd in SEED_COMMANDS {
        if !eng.cmds.contains_key(*cmd) {
            eng.add(cmd, "", "", "", 0, Some(now), false);
            if let Some(st) = eng.cmds.get_mut(*cmd) {
                st.last_ts = now;
            }
        }
    }

    let mut generated: HashSet<String> = HashSet::new();
    let typed: HashSet<String> = eng
        .cmds
        .keys()
        .filter_map(|c| c.split_whitespace().next().map(|s| s.to_string()))
        .collect();

    // Add conservative knowledge candidates for installed CLI tools, and only
    // for the tools whose first word appears in the history.
    let tools = load_tool_knowledge();
    for tool in &tools {
        if !typed.contains(&tool.name) {
            continue;
        }
        let mut cands =
            candidates_for_prefix(&tool.name, std::slice::from_ref(tool), TOOL_CANDIDATE_CAP);
        // A second pass with a flag-shaped prefix, because the generator only
        // offers flags when the prefix it is given already extends past the
        // command name.
        cands.extend(candidates_for_prefix(
            &format!("{} -", tool.name),
            std::slice::from_ref(tool),
            TOOL_FLAG_CAP,
        ));
        // The flags of the subcommands this history actually runs.
        for sub in tool.subs.keys().take(TOOL_SUB_CAP).cloned().collect::<Vec<_>>() {
            cands.extend(candidates_for_prefix(
                &format!("{} {} -", tool.name, sub),
                std::slice::from_ref(tool),
                TOOL_SUBFLAG_CAP,
            ));
        }
        // …and the subcommands those subcommands have.
        for sub in tool.subverbs.keys().take(TOOL_SUB_CAP).cloned().collect::<Vec<_>>() {
            cands.extend(candidates_for_prefix(
                &format!("{} {} ", tool.name, sub),
                std::slice::from_ref(tool),
                TOOL_SUBSUB_CAP,
            ));
        }
        for cmd in cands {
            generated.insert(cmd.clone());
            if !eng.cmds.contains_key(&cmd) {
                eng.add(&cmd, "", "", "", 0, Some(now), false);
                if let Some(st) = eng.cmds.get_mut(&cmd) {
                    st.last_ts = now;
                }
            }
        }
    }

    // A command whose paths are gone is not a suggestion. It stays in SQLite,
    // so it comes back on its own if the directory returns; it just never wins.
    let cmd_cwds: Vec<(String, Vec<String>)> = eng
        .cmds
        .iter()
        .map(|(c, st)| (c.clone(), st.cwd.keys().cloned().collect()))
        .collect();
    let stale = paths::stale_in(&cmd_cwds, "");
    if !stale.is_empty() {
        for cmd in &stale {
            eng.cmds.remove(cmd);
        }
        eng.order.retain(|c| !stale.contains(c));
        eng.sorted_cmds.retain(|c| !stale.contains(c));
        eng.first_word_cache = None;
        eng.cmds_dirty = true;
        // The sequence table as well, in both directions.
        eng.seq.retain(|prev, counter| {
            if stale.contains(prev) {
                return false;
            }
            counter.retain(|c, _| !stale.contains(c));
            true
        });
    }
    if !rows.is_empty() && from_history.iter().all(|c| stale.contains(c)) {
        // Every command the user has actually run is judged missing. Publishing
        // an index here would replace everything they learned with the seed
        // corpus — `tai purge --stale` is the way out.
        return Err("every stored command names a path that is gone — \
                    run 'tai purge --stale' to drop them, then refresh"
            .to_string());
    }

    // A line the shell never ran is not a command: a line whose every recorded
    // run was exit 127 is hidden from suggestions, its rows kept in the store.
    let phantoms: HashSet<String> = eng
        .cmds
        .iter()
        .filter(|(_, st)| st.nf != 0 && st.nf == st.freq)
        .map(|(c, _)| c.clone())
        .collect();
    if !phantoms.is_empty() {
        for cmd in &phantoms {
            eng.cmds.remove(cmd);
        }
        eng.order.retain(|c| !phantoms.contains(c));
        eng.sorted_cmds.retain(|c| !phantoms.contains(c));
        eng.first_word_cache = None;
        eng.cmds_dirty = true;
        eng.seq.retain(|prev, counter| {
            if phantoms.contains(prev) {
                return false;
            }
            counter.retain(|c, _| !phantoms.contains(c));
            true
        });
    }

    // Which first words are installed — asked once per rebuild, offline, over
    // the whole vocabulary.
    let installed: HashSet<String> = eng
        .cmds
        .keys()
        .filter_map(|c| c.split(' ').next().map(|s| s.to_string()))
        .filter(|w| which_word(w))
        .collect();

    let now = util::now_epoch();
    let seed_set: HashSet<&str> = SEED_COMMANDS.iter().copied().collect();
    let n_seeds = SEED_COMMANDS.len() as i64;
    let mut scored: Vec<(i64, String)> = Vec::with_capacity(eng.cmds.len());
    for (cmd, st) in eng.cmds.iter() {
        // A seed the user has never run is a convention, not an observation.
        let pure = seed_set.contains(cmd.as_str()) && !from_history.contains(cmd);
        if pure {
            let seed_rank = seed_rank(cmd).unwrap_or(0) as i64;
            scored.push(((n_seeds - seed_rank) * SEED_RANK_STEP, cmd.clone()));
            continue;
        }
        let age_days = ((now - if st.last_ts != 0 { st.last_ts } else { now }) as f64 / 86400.0)
            .max(0.0);
        let recency = (-age_days / 14.0).exp();
        let freq = ((st.freq as f64).ln_1p() / 20f64.ln_1p()).min(1.0);
        let success = (((st.success as f64 - st.fail as f64 * 0.5) / st.freq.max(1) as f64)
            .clamp(-0.5, 1.0))
            * 0.5
            + 0.5;
        let mut score = W_FREQ * freq + W_RECENCY * recency + W_SUCCESS * success;
        let first_word = cmd.split(' ').next().unwrap_or("");
        if !pure && installed.contains(first_word) {
            score += W_INSTALLED;
        }
        if generated.contains(cmd) && !seed_set.contains(cmd.as_str()) {
            score *= COLD_FACTOR;
        }
        // The engine's length penalty, baked into the scores the plugins read.
        score -= ((cmd.len().saturating_sub(1)) as f64 / LEN_PENALTY_DIV).min(MAX_LEN_PEN);
        // Integer milli-score keeps comparisons native and process-free.
        scored.push(((score * 1000.0).round() as i64, cmd.clone()));
    }
    // One-off typos rank just below the line they shadow. Two passes, so a
    // typo of a typo lands below its own target whichever order the map
    // iterates in.
    let from_history_vec: Vec<String> = from_history.iter().cloned().collect();
    let shadows = shadow_map(&eng, Some(&from_history_vec));
    let mut score_of: HashMap<String, i64> =
        scored.iter().map(|(s, c)| (c.clone(), *s)).collect();
    for _ in 0..2 {
        for (typo, partner) in shadows.iter() {
            if let Some(cap) = score_of.get(partner).copied() {
                let t = score_of.get_mut(typo).unwrap();
                if *t > cap - 1 {
                    *t = cap - 1;
                }
            }
        }
    }
    let mut scored: Vec<(i64, String)> =
        scored.into_iter().map(|(_, c)| (score_of[&c], c)).collect();
    paths::destinations_first(&mut scored);
    scored.sort_by(|a, b| (b.0, b.1.as_str()).cmp(&(a.0, a.1.as_str())));
    scored.truncate(max_commands);
    let cmds: Vec<String> = scored.iter().map(|(_, c)| c.clone()).collect();
    let scores: HashMap<String, i64> =
        scored.iter().map(|(s, c)| (c.clone(), *s)).collect();

    let mut first: HashMap<String, Vec<String>> = HashMap::new();
    let mut words: HashMap<String, Vec<String>> = HashMap::new();
    // `cmds` is already best-first, so every list built here is too.
    for cmd in &cmds {
        let parts: Vec<&str> = cmd.split_whitespace().collect();
        if !parts.is_empty() {
            first.entry(parts[0].to_string()).or_default().push(cmd.clone());
        }
        for i in 2..=parts.len().min(WORD_KEY_MAX_DEPTH) {
            words
                .entry(parts[..i].join(" "))
                .or_default()
                .push(cmd.clone());
        }
    }

    let mut seq: HashMap<String, Vec<String>> = HashMap::new();
    for (prev, counter) in eng.seq.iter() {
        let mut rows: Vec<(String, i64, i64)> = counter
            .iter()
            .map(|(cmd, n)| (cmd.clone(), *n, scores.get(cmd).copied().unwrap_or(0)))
            .collect();
        // Ordered by how often the pair happened, then by how good the line
        // is, best first — "what did I run after this" is answered by what
        // followed *most*.
        rows.sort_by(|a, b| b.1.cmp(&a.1).then(b.2.cmp(&a.2)).then(a.0.cmp(&b.0)));
        let values: Vec<String> =
            rows.into_iter().take(SEQ_CANDIDATE_CAP).map(|(c, _, _)| c).collect();
        // A key whose every follower was stale has no answer left, and writing
        // it as an empty string is not an answer.
        if !values.is_empty() {
            seq.insert(prev.clone(), values);
        }
    }

    // Which lines end in a file, keyed by the line's own words without the
    // last one.
    let mut file_keys: HashSet<String> = HashSet::new();
    for cmd in &cmds {
        if paths::takes_file(cmd) {
            match cmd.rsplit_once(' ') {
                Some((head, _)) if !head.is_empty() => {
                    file_keys.insert(head.to_string());
                }
                _ => {}
            }
        }
    }

    // The words the shells must ask the filesystem about before offering them.
    let live = paths::relative_word_set(&cmds);

    let zsh_file = index_path();
    if let Some(dir) = zsh_file.parent() {
        let _ = std::fs::create_dir_all(dir);
    }
    let bash_file = bash_index_path();
    let tmp = zsh_file.with_extension("tmp");
    {
        let mut f = std::io::BufWriter::new(std::fs::File::create(&tmp).map_err(|e| e.to_string())?);
        writeln!(f, "# tai embedded zsh index; generated, do not edit").map_err(e2s)?;
        writeln!(
            f,
            "typeset -gA _TAI_SCORE _TAI_FIRST _TAI_WORD _TAI_SEQ _TAI_FILE _TAI_LIVE"
        )
        .map_err(e2s)?;
        // `_TAI_X+=(key value)` — a pair appended to an associative array, one
        // builtin call per entry and no function call at all.
        for (score, cmd) in &scored {
            writeln!(f, "_TAI_SCORE+=({} {})", zq(cmd), score).map_err(e2s)?;
        }
        for key in sorted_keys(&first) {
            let values = &first[&key];
            writeln!(
                f,
                "_TAI_FIRST+=({} {})",
                zq(&key),
                zq(&values.join("\n"))
            )
            .map_err(e2s)?;
        }
        for key in sorted_keys(&words) {
            let values = &words[&key];
            writeln!(f, "_TAI_WORD+=({} {})", zq(&key), zq(&word_value(values))).map_err(e2s)?;
        }
        for key in sorted_keys(&seq) {
            let values = &seq[&key];
            writeln!(f, "_TAI_SEQ+=({} {})", zq(&key), zq(&values.join("\n"))).map_err(e2s)?;
        }
        for key in sorted_keys_set(&file_keys) {
            writeln!(f, "_TAI_FILE+=({} 1)", zq(&key)).map_err(e2s)?;
        }
        for word in sorted_keys_set(&live) {
            writeln!(f, "_TAI_LIVE+=({} 1)", zq(&word)).map_err(e2s)?;
        }
        f.flush().map_err(e2s)?;
    }
    std::fs::rename(&tmp, &zsh_file).map_err(e2s)?;

    let bash_tmp = bash_file.with_extension("tmp");
    {
        let mut f = std::io::BufWriter::new(std::fs::File::create(&bash_tmp).map_err(e2s)?);
        writeln!(f, "# tai embedded bash index; generated, do not edit").map_err(e2s)?;
        writeln!(
            f,
            "declare -gA _TAI_SCORE _TAI_FIRST _TAI_WORD _TAI_SEQ _TAI_FILE _TAI_LIVE"
        )
        .map_err(e2s)?;
        for (score, cmd) in &scored {
            writeln!(f, "_TAI_SCORE[{}]={}", zq(cmd), score).map_err(e2s)?;
        }
        for key in sorted_keys(&first) {
            let values = &first[&key];
            writeln!(f, "_TAI_FIRST[{}]={}", zq(&key), zq(&values.join("\n"))).map_err(e2s)?;
        }
        for key in sorted_keys(&words) {
            let values = &words[&key];
            writeln!(f, "_TAI_WORD[{}]={}", zq(&key), zq(&word_value(values))).map_err(e2s)?;
        }
        for key in sorted_keys(&seq) {
            let values = &seq[&key];
            writeln!(f, "_TAI_SEQ[{}]={}", zq(&key), zq(&values.join("\n"))).map_err(e2s)?;
        }
        for key in sorted_keys_set(&file_keys) {
            writeln!(f, "_TAI_FILE[{}]=1", zq(&key)).map_err(e2s)?;
        }
        for word in sorted_keys_set(&live) {
            writeln!(f, "_TAI_LIVE[{}]=1", zq(&word)).map_err(e2s)?;
        }
        f.flush().map_err(e2s)?;
    }
    std::fs::rename(&bash_tmp, &bash_file).map_err(e2s)?;
    compile_zsh_index(&zsh_file);
    Ok(cmds.len())
}

fn e2s(e: std::io::Error) -> String {
    e.to_string()
}

fn sorted_keys(map: &HashMap<String, Vec<String>>) -> Vec<String> {
    let mut keys: Vec<String> = map.keys().cloned().collect();
    keys.sort();
    keys
}

fn sorted_keys_set(set: &HashSet<String>) -> Vec<String> {
    let mut keys: Vec<String> = set.iter().cloned().collect();
    keys.sort();
    keys
}

/// Compile the zsh index next to itself — the source-time cost halves.
///
/// An absent zcompile, a failed compile, or no zsh at all is not an error: the
/// text index alone is always the whole story the tests read.
fn compile_zsh_index(zsh_file: &Path) {
    let zsh = match util::which("zsh") {
        Some(z) => z,
        None => return,
    };
    if !zsh_file.exists() {
        return;
    }
    let zwc = zsh_file.with_extension("zsh.zwc");
    let script = format!("zcompile {}", zsh_file.to_string_lossy());
    let (out, err) = util::run_with_timeout(&[&zsh, "-fc", &script], 120.0, None, &[]);
    if !err.is_empty() && out.is_empty() {
        let _ = std::fs::remove_file(&zwc);
        return;
    }
    if !zwc.exists() {
        let _ = std::fs::remove_file(&zwc);
    }
}
