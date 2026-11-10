//! tai predictor — the one-shot suggest path, over engine + store.
//!
//! Port of `tai/predictor.py`. This is where the path-liveness policy is
//! applied to the one-shot path. The index's stale set is computed the same
//! way from the same engine — recorded cwd only — so the one-shot path never
//! *offers* what the index already dropped. It may hide one or two more
//! (judged against the directory it is run in), which is the safe direction
//! to disagree.

use std::collections::HashMap;
use std::sync::{Mutex, MutexGuard, OnceLock};

use crate::engine::{Engine, TEMP_DEFAULT};
use crate::fresh;
use crate::paths;
use crate::util;

/// Is this command's first word installed? Asked once per word per process
/// and kept here: PATH does not change under a running process in any way
/// worth re-statting per question. The engine itself never touches the
/// filesystem — it is a sub-millisecond ranker.
pub fn on_path(word: &str) -> bool {
    static WHICH: OnceLock<Mutex<HashMap<String, bool>>> = OnceLock::new();
    let cache = WHICH.get_or_init(|| Mutex::new(HashMap::new()));
    if let Ok(map) = cache.lock() {
        if let Some(hit) = map.get(word) {
            return *hit;
        }
    }
    let hit = util::path_lookup(word);
    if let Ok(mut map) = cache.lock() {
        map.insert(word.to_string(), hit);
    }
    hit
}

/// The engine's stale set, asked lazily and remembered per command.
///
/// `paths::stale_in` walks every distinct command's every token up front, and
/// the one-shot suggest paid that whole walk before ranking a handful of
/// candidates for one prefix. The verdict for one command is a pure function
/// of the command, the directories it was recorded in, and the directory the
/// answer is shown in, so it is asked per candidate instead.
pub struct LazyStale {
    cwds_of: HashMap<String, Vec<String>>,
    cwd: String,
    memo: HashMap<String, bool>,
}

impl LazyStale {
    pub fn new(eng: &Engine, cwd: &str) -> LazyStale {
        let mut cwds_of = HashMap::new();
        for (c, st) in eng.cmds.iter() {
            cwds_of.insert(c.clone(), st.cwd.keys().cloned().collect());
        }
        LazyStale { cwds_of, cwd: cwd.to_string(), memo: HashMap::new() }
    }

    pub fn ask(&mut self, cmd: &str) -> bool {
        if let Some(hit) = self.memo.get(cmd) {
            return *hit;
        }
        if !paths::enabled() {
            self.memo.insert(cmd.to_string(), false);
            return false;
        }
        let mut cwds = self.cwds_of.get(cmd).cloned().unwrap_or_default();
        if !self.cwd.is_empty() && !cwds.contains(&self.cwd) {
            cwds.push(self.cwd.clone());
        }
        let hit = paths::command_verdict(cmd, &cwds) == paths::MISSING;
        self.memo.insert(cmd.to_string(), hit);
        hit
    }

    /// Ask every command in the engine once, so a count is the full count.
    pub fn materialize(&mut self, eng: &Engine) {
        for cmd in eng.cmds.keys() {
            self.ask(cmd);
        }
    }

    pub fn stale_count(&self) -> usize {
        self.memo.values().filter(|v| **v).count()
    }

    pub fn cwd(&self) -> &str {
        &self.cwd
    }
}

/// The warmed engine, keyed on the store it was built from so a long-lived
/// dashboard drops it the moment the store moves.
pub struct WarmedEngine {
    pub eng: Engine,
    pub stale: LazyStale,
}

static WARMED: OnceLock<Mutex<Option<WarmedEngine>>> = OnceLock::new();
static DB_KEY: OnceLock<Mutex<Option<(u64, i64)>>> = OnceLock::new();

fn warmed() -> &'static Mutex<Option<WarmedEngine>> {
    WARMED.get_or_init(|| Mutex::new(None))
}

fn db_key() -> &'static Mutex<Option<(u64, i64)>> {
    DB_KEY.get_or_init(|| Mutex::new(None))
}

fn store_key() -> Option<(u64, i64)> {
    use std::os::unix::fs::MetadataExt;
    std::fs::metadata(paths::db_location())
        .ok()
        .map(|m| (m.size(), m.mtime_nsec() as i64))
}

/// Drop the warmed engine when the store has changed underneath it, so the
/// panel never explains yesterday's history.
pub fn fresh_engine() {
    let key = store_key();
    let mut cur = db_key().lock().unwrap();
    if *cur != key {
        *warmed().lock().unwrap() = None;
        *cur = key;
    }
}

fn build_engine() -> Engine {
    // A broken store must not become an empty ranking here either, but this
    // path answers a question and cannot exit: it says what it could not do
    // and falls through to what it can read.
    let mut eng = Engine::new();
    match crate::store::load_rows(20000) {
        Ok(rows) => eng.build_from_rows(&rows),
        Err(e) => {
            let note = crate::store::schema_note();
            eprintln!(
                "tai: cannot read the history database: {}",
                if note.is_empty() { e } else { note }
            );
        }
    }
    eng
}

/// The shared engine, built once and kept until the store changes. A fresh
/// process rebuilds the engine from SQLite unless a disk cache taken by an
/// earlier process is still current. The path-liveness set is deliberately
/// NOT cached across processes: it answers against the directory this process
/// runs in, which changes call to call.
pub fn shared_engine() -> MutexGuard<'static, Option<WarmedEngine>> {
    fresh_engine();
    let mut guard = warmed().lock().unwrap();
    if guard.is_none() {
        let db = paths::db_location();
        let eng = crate::engcache::get(&db, build_engine);
        let cwd = std::env::current_dir()
            .map(|p| p.to_string_lossy().to_string())
            .unwrap_or_default();
        let stale = LazyStale::new(&eng, &cwd);
        *guard = Some(WarmedEngine { eng, stale });
    }
    guard
}

/// Reset the warmed state (used by tests and by an explicit reload).
pub fn reset() {
    *warmed().lock().unwrap() = None;
    *db_key().lock().unwrap() = None;
}

/// The one-shot suggest, with path liveness and the file answer applied.
pub fn suggest(
    prefix: &str,
    cwd: &str,
    repo: &str,
    branch: &str,
    last_commands: &[String],
    limit: usize,
    temp: Option<f64>,
) -> serde_json::Value {
    let t0 = std::time::Instant::now();
    let temp = temp.unwrap_or(TEMP_DEFAULT);
    let mut guard = shared_engine();
    let w = guard.as_mut().unwrap();
    let WarmedEngine { eng, stale } = &mut *w;
    let res = eng.suggest(
        prefix,
        cwd,
        repo,
        branch,
        last_commands,
        limit,
        temp,
        None,
        true,
        &mut |cmd: &str| stale.ask(cmd),
        Some(&on_path),
    );
    let mut res = to_json(&res);

    // The stale set above never hides a relative path that was true where it
    // was recorded — which is most of them, and that is right for a snapshot
    // shown in every directory. This answer is shown in one directory, so its
    // lines are judged there too.
    let where_ = if cwd.is_empty() {
        std::env::current_dir()
            .map(|p| p.to_string_lossy().to_string())
            .unwrap_or_default()
    } else {
        cwd.to_string()
    };
    let all = res.get("choices").and_then(|c| c.as_array()).cloned().unwrap_or_default();
    let live: Vec<serde_json::Value> = all
        .iter()
        .filter(|c| {
            let cmd = c.get("cmd").and_then(|c| c.as_str()).unwrap_or("");
            paths::alive_in(cmd, &where_)
        })
        .cloned()
        .collect();
    if live.len() != all.len() {
        res["choices"] = serde_json::Value::Array(live.clone());
        let mut probs = serde_json::Map::new();
        for c in &live {
            probs.insert(
                c.get("cmd").and_then(|c| c.as_str()).unwrap_or("").to_string(),
                c.get("prob").cloned().unwrap_or(serde_json::Value::Null),
            );
        }
        res["probabilities"] = serde_json::Value::Object(probs);
        let best = live
            .first()
            .and_then(|c| c.get("cmd").and_then(|c| c.as_str()))
            .unwrap_or("")
            .to_string();
        res["choice"] = serde_json::Value::String(best.clone());
        res["completion"] = serde_json::Value::String(if !best.is_empty()
            && best.to_lowercase().starts_with(&prefix.to_lowercase())
        {
            best[prefix.len().min(best.len())..].to_string()
        } else {
            String::new()
        });
    }
    let eng = &w.eng;
    let mut res = file_answer(prefix, res, eng);
    if res.get("latency_ms").and_then(|v| v.as_f64()).unwrap_or(0.0) == 0.0 {
        res["latency_ms"] = serde_json::json!(
            (t0.elapsed().as_secs_f64() * 1000.0 * 100.0).round() / 100.0
        );
    }
    res
}

/// The dashboard's why: same engine, same path-liveness policy. A command
/// hidden as stale is reported as hidden rather than scored, because the
/// honest answer to "why don't I see it" is not its arithmetic.
pub fn explain(
    prefix: &str,
    cmd: &str,
    cwd: &str,
    repo: &str,
    branch: &str,
    last_commands: &[String],
) -> Option<serde_json::Value> {
    let mut guard = shared_engine();
    let w = guard.as_mut().unwrap();
    let WarmedEngine { eng, stale } = &mut *w;
    let out = eng.explain(prefix, cmd, cwd, repo, branch, last_commands, None, Some(&on_path))?;
    let mut v = explain_json(&out);
    v["stale"] = serde_json::json!(stale.ask(cmd));
    Some(v)
}

fn to_json(res: &crate::engine::SuggestResult) -> serde_json::Value {
    let probabilities: serde_json::Map<String, serde_json::Value> = res
        .probabilities
        .iter()
        .map(|(c, p)| (c.clone(), serde_json::json!((p * 10000.0).round() / 10000.0)))
        .collect();
    let choices: Vec<serde_json::Value> = res
        .choices
        .iter()
        .map(|c| {
            serde_json::json!({
                "cmd": c.cmd,
                "prob": (c.prob * 10000.0).round() / 10000.0,
                "score": (c.score * 1000.0).round() / 1000.0,
            })
        })
        .collect();
    serde_json::json!({
        "type": "choice",
        "choice": res.choice,
        "completion": res.completion,
        "probabilities": probabilities,
        "choices": choices,
        "confidence": res.confidence,
        "latency_ms": res.latency_ms,
        "count": res.count,
    })
}

fn explain_json(out: &crate::engine::ExplainResult) -> serde_json::Value {
    let factors: Vec<serde_json::Value> = out
        .factors
        .iter()
        .map(|f| {
            serde_json::json!({
                "label": f.label,
                "weight": f.weight,
                "value": f.value,
                "contrib": f.contrib,
                "detail": f.detail,
            })
        })
        .collect();
    serde_json::json!({
        "cmd": out.cmd,
        "prefix": out.prefix,
        "score": out.score,
        "freq": out.freq,
        "last_ts": out.last_ts,
        "age_days": out.age_days,
        "near_miss": out.near_miss,
        "phantom": out.phantom,
        "factors": factors,
    })
}

/// A path argument is answered by the filesystem, not by the history.
///
/// Three questions, and every "no" leaves the ranking exactly as it was: is
/// the word being typed a file (the history's evidence), is what tai learned
/// still a file here (it keeps the top place), and is there a file at all.
fn file_answer(prefix: &str, res: serde_json::Value, eng: &Engine) -> serde_json::Value {
    if prefix.is_empty() || !prefix.contains(' ') {
        return res;
    }
    let (before, word) = match prefix.rsplit_once(' ') {
        Some((b, w)) => (b.to_string(), w.to_string()),
        None => return res,
    };
    let mut key = before.split_whitespace().collect::<Vec<_>>().join(" ");
    // A wrapper runs the command behind it, so it is transparent to the file
    // too. The typed form is kept beside the stripped one, because the
    // engine's own answer for a wrapped line carries the wrapper back on it —
    // the learned answer is read against the line as the user typed it.
    let typed = key.clone();
    let head = key.splitn(2, ' ').next().unwrap_or("").to_string();
    if crate::engine::WRAPPERS.contains(&head.as_str()) {
        key = if key.contains(' ') {
            key.splitn(2, ' ').nth(1).unwrap_or("").to_string()
        } else {
            String::new()
        };
    }
    if key.is_empty() {
        return res;
    }
    let evidence = eng
        .cmds
        .keys()
        .any(|cmd| cmd.starts_with(&format!("{} ", key)) && paths::takes_file(cmd));
    if !evidence {
        return res;
    }
    let learned = res.get("choice").and_then(|c| c.as_str()).unwrap_or("").to_string();
    let typed_prefix = format!("{} ", typed);
    let last = if learned.starts_with(&typed_prefix) {
        learned[typed_prefix.len()..].to_string()
    } else {
        String::new()
    };
    if !last.is_empty()
        && last.starts_with(&word)
        && std::path::Path::new(&paths::expanduser(&last)).exists()
    {
        return res;
    }
    // A completion has to extend the word being typed. A glob match does not
    // always: `chmod +x freeb` matching `~/Downloads/freebuff…` does not start
    // with the word, so it cannot replace the letters typed.
    let found: Vec<String> = fresh::files(&word, "")
        .into_iter()
        .filter(|name| name.starts_with(&word))
        .collect();
    if found.is_empty() {
        return res;
    }
    // A file is a *word*, so the answer replaces the word being typed rather
    // than being appended after it — appending is right for `chmod +x ` and
    // wrong for `chmod +x ~/Downloads/f`.
    let join = if prefix.ends_with(' ') {
        prefix.to_string()
    } else {
        format!("{} ", before)
    };
    let choice = format!("{}{}", join, found[0]);
    let mut out = res.clone();
    out["choice"] = serde_json::Value::String(choice.clone());
    out["completion"] =
        serde_json::Value::String(choice[prefix.len().min(choice.len())..].to_string());
    let mut probs = serde_json::Map::new();
    probs.insert(choice.clone(), serde_json::json!(1.0));
    out["probabilities"] = serde_json::Value::Object(probs);
    out["choices"] = serde_json::json!([{"cmd": choice, "prob": 1.0, "score": 0.0}]);
    let conf = out.get("confidence").and_then(|c| c.as_f64()).unwrap_or(0.0);
    out["confidence"] = serde_json::json!(conf.max(0.5));
    out["from_disk"] = serde_json::json!(found.iter().take(8).cloned().collect::<Vec<_>>());
    out
}
