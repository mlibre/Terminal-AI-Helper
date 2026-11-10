//! tai tune — grid-search ranker weights on your history.
//!
//! Port of `tai/tune.py`, with one deliberate change: the Python
//! implementation wrote its best weights back into `tai/engine.py`, which is
//! a move only a from-source interpreter can make. The weights here are
//! written to a `weights.json` beside the database and applied at every
//! startup — the same override, survivable by an update, readable by hand.

use std::collections::HashMap;
use std::fs;
use std::path::PathBuf;

use crate::engine::{Engine, W_BRANCH, W_CWD, W_FREQ, W_HOUR, W_INSTALLED, W_RECENCY, W_REPO, W_SEQ, W_SUCCESS, W_TOKEN};
use crate::store;

const KEYS: &[&str] = &[
    "W_FREQ", "W_RECENCY", "W_CWD", "W_REPO", "W_BRANCH", "W_SUCCESS", "W_INSTALLED",
    "W_SEQ", "W_HOUR", "W_TOKEN",
];

pub fn weights_path() -> PathBuf {
    crate::paths::data_dir().join("weights.json")
}

/// The weights an engine should run with: the file `tai tune` wrote, else the
/// compiled defaults. Read once per process.
pub fn load_weights() -> HashMap<String, f64> {
    let mut out: HashMap<String, f64> = HashMap::new();
    let defaults: [(&str, f64); 10] = [
        ("W_FREQ", W_FREQ),
        ("W_RECENCY", W_RECENCY),
        ("W_CWD", W_CWD),
        ("W_REPO", W_REPO),
        ("W_BRANCH", W_BRANCH),
        ("W_SUCCESS", W_SUCCESS),
        ("W_INSTALLED", W_INSTALLED),
        ("W_SEQ", W_SEQ),
        ("W_HOUR", W_HOUR),
        ("W_TOKEN", W_TOKEN),
    ];
    for (k, v) in defaults {
        out.insert(k.to_string(), v);
    }
    if let Ok(text) = fs::read_to_string(weights_path()) {
        if let Ok(serde_json::Value::Object(map)) = serde_json::from_str::<serde_json::Value>(&text)
        {
            for (k, v) in map {
                if let Some(f) = v.as_f64() {
                    out.insert(k, f);
                }
            }
        }
    }
    out
}

fn eval_engine(eng: &mut Engine, ev_rows: &[crate::store::Row], on_path: &dyn Fn(&str) -> bool) -> (f64, HashMap<String, f64>) {
    let mut r1 = 0.0f64;
    let mut r3 = 0.0f64;
    let mut s1 = 0.0f64;
    let mut s3 = 0.0f64;
    let mut n = 0.0f64;
    for (i, r) in ev_rows.iter().enumerate() {
        let cmd = r.cmd.trim();
        if cmd.is_empty() || cmd.len() < 3 {
            continue;
        }
        let bytes: Vec<char> = cmd.chars().collect();
        let mut cut = (bytes.len() / 2).max(1);
        let scan_end = (cut + 4).min(bytes.len());
        if let Some(sp) = cmd[..scan_end].rfind(' ') {
            if sp > 1 {
                cut = sp + 1;
            }
        }
        let prev = if i > 0 { ev_rows[i - 1].cmd.trim().to_string() } else { String::new() };
        let prefix: String = bytes[..cut].iter().collect();
        let res = eng.suggest(
            &prefix,
            &r.cwd,
            &r.repo,
            &r.branch,
            &[prev.clone()],
            3,
            0.9,
            None,
            false,
            &mut |_| false,
            Some(on_path),
        );
        let cands: Vec<String> = res.choices.iter().map(|c| c.cmd.clone()).collect();
        n += 1.0;
        if cands.first().map(|c| c == cmd).unwrap_or(false) {
            r1 += 1.0;
        }
        if cands.iter().take(3).any(|c| c == cmd) {
            r3 += 1.0;
        }
        let res2 = eng.suggest(
            "",
            "",
            "",
            "",
            &[prev.clone()],
            3,
            0.9,
            None,
            false,
            &mut |_| false,
            Some(on_path),
        );
        let c2: Vec<String> = res2.choices.iter().map(|c| c.cmd.clone()).collect();
        if c2.first().map(|c| c == cmd).unwrap_or(false) {
            s1 += 1.0;
        }
        if c2.iter().take(3).any(|c| c == cmd) {
            s3 += 1.0;
        }
    }
    if n == 0.0 {
        return (0.0, HashMap::new());
    }
    let m = HashMap::from([
        ("prefix_r1".to_string(), r1 / n),
        ("prefix_r3".to_string(), r3 / n),
        ("seq_r1".to_string(), s1 / n),
        ("seq_r3".to_string(), s3 / n),
        ("n".to_string(), n),
    ]);
    (0.7 * (r1 / n) + 0.3 * (s1 / n), m)
}

pub fn main(limit: usize, trials: usize) {
    let rows = match store::load_rows(limit as i64) {
        Ok(r) => r,
        Err(e) => {
            let note = store::schema_note();
            println!("cannot read the history database: {}", if note.is_empty() { e } else { note });
            return;
        }
    };
    if rows.len() < 200 {
        println!("not enough history ({} rows, need 200+)", rows.len());
        return;
    }
    let cut = (rows.len() as f64 * 0.8) as usize;
    let train = &rows[..cut];
    let mut ev: Vec<crate::store::Row> = rows[cut..].to_vec();
    let step = (ev.len() / 400).max(1);
    ev = ev.into_iter().step_by(step).take(400).collect();
    println!("train={} eval={}", train.len(), ev.len());

    // The installed question, answered once for the whole run: every first
    // word the history carries, resolved against PATH here rather than inside
    // the engine.
    let words: std::collections::HashSet<String> = rows
        .iter()
        .filter(|r| !r.cmd.trim().is_empty())
        .map(|r| r.cmd.trim().split(' ').next().unwrap_or("").to_string())
        .collect();
    let on_path_set: std::collections::HashSet<String> =
        words.into_iter().filter(|w| crate::util::path_lookup(w)).collect();
    let on_path = |w: &str| on_path_set.contains(w);

    let build = |weights: &HashMap<String, f64>| -> Engine {
        // The weights ride the engine's public knobs for the duration of one
        // build; the module constants themselves are read-only in a binary.
        let mut eng = WeightedEngine::build(train, weights);
        eng
    };
    let base = load_weights();
    let mut best = base.clone();
    let (mut score, m) = eval_engine(&mut build(&best), &ev, &on_path);
    let m_show: Vec<String> = m
        .iter()
        .filter(|(k, _)| k.as_str() != "n")
        .map(|(k, v)| format!("'{}': {}", k, (v * 1000.0).round() / 1000.0))
        .collect();
    println!("base score={:.3} {{{}}}", score, m_show.join(", "));
    // coordinate search: perturb one weight at a time
    let mut rng = Rand7::new(7);
    for t in 0..trials {
        let k = KEYS[t % KEYS.len()];
        let mut cand = best.clone();
        let factor = rng.choice([0.5, 0.7, 1.3, 1.6]);
        let cur = cand.get(k).copied().unwrap_or(0.0);
        cand.insert(k.to_string(), ((cur * factor).max(0.0) * 1000.0).round() / 1000.0);
        let (score_c, _) = eval_engine(&mut build(&cand), &ev, &on_path);
        if score_c > score {
            score = score_c;
            best = cand;
            println!(
                "  [{}] {}={} -> {:.3}",
                t,
                k,
                best.get(k).copied().unwrap_or(0.0),
                score
            );
        }
    }
    let (_, m) = eval_engine(&mut build(&best), &ev, &on_path);
    let m_show: Vec<String> = m
        .iter()
        .filter(|(k, _)| k.as_str() != "n")
        .map(|(k, v)| format!("'{}': {}", k, (v * 1000.0).round() / 1000.0))
        .collect();
    println!("best score={:.3} {{{}}}", score, m_show.join(", "));
    let weights_show: Vec<String> =
        KEYS.iter().map(|k| format!("'{}': {}", k, best.get(*k).copied().unwrap_or(0.0))).collect();
    println!("weights: {{{}}}", weights_show.join(", "));
    let off: Vec<&str> = KEYS
        .iter()
        .filter(|k| best.get(**k).copied().unwrap_or(0.0) == 0.0 && base.get(**k).copied().unwrap_or(0.0) != 0.0)
        .copied()
        .collect();
    if !off.is_empty() {
        // A weight at zero is not a smaller number, it is a signal switched
        // off — so say it, while it can still be undone.
        println!("tai: these signals are now off: {}", off.join(", "));
    }
    let changed: Vec<&str> = KEYS
        .iter()
        .filter(|k| best.get(**k).copied().unwrap_or(0.0) != base.get(**k).copied().unwrap_or(0.0))
        .copied()
        .collect();
    if changed.is_empty() {
        println!("tai: no weight changed, {} left alone", weights_path().display());
        return;
    }
    let payload: serde_json::Value = serde_json::Value::Object(
        best.iter()
            .map(|(k, v)| (k.clone(), serde_json::json!(v)))
            .collect::<serde_json::Map<String, serde_json::Value>>(),
    );
    if let Some(dir) = weights_path().parent() {
        let _ = fs::create_dir_all(dir);
    }
    let tmp = weights_path().with_extension("json.tmp");
    if fs::write(&tmp, serde_json::to_string_pretty(&payload).unwrap_or_default()).is_ok()
        && fs::rename(&tmp, weights_path()).is_ok()
    {
        println!(
            "wrote {} ({} of {} weights changed: {})",
            weights_path().display(),
            changed.len(),
            best.len(),
            changed.join(", ")
        );
    } else {
        println!("tai: could not write {}", weights_path().display());
        std::process::exit(1);
    }
}

/// An engine whose weights come from a map rather than the compiled
/// constants: the one seam tuning needs.
struct WeightedEngine;

impl WeightedEngine {
    fn build(train: &[crate::store::Row], weights: &HashMap<String, f64>) -> Engine {
        // The engine's scoring loop reads the module constants; a tuned run
        // builds with the tuned numbers through the same arithmetic.
        let mut eng = Engine::new();
        eng.build_from_rows(train);
        // The weights only matter while ranking; the engine built from the
        // same rows with the same constants is the honest baseline, and the
        // applied weights ride in the engine's `tuned` slot, which suggest
        // reads when it is set.
        eng.tuned = Some(weights.iter().map(|(k, v)| (k.clone(), *v)).collect());
        eng
    }
}

/// A tiny LCG standing in for `random.Random(7)`, so a tuned run is
/// reproducible the way the Python one was.
struct Rand7(u64);

impl Rand7 {
    fn new(seed: u64) -> Self {
        Rand7(seed)
    }
    fn next_u(&mut self) -> u64 {
        // LCG used by CPython for small integers is not reproducible here;
        // this one is, and the trials it drives are exploratory either way.
        self.0 = self.0.wrapping_mul(6364136223846793005).wrapping_add(1442695040888963407);
        self.0
    }
    fn choice<const N: usize>(&mut self, arr: [f64; N]) -> f64 {
        arr[(self.next_u() % N as u64) as usize]
    }
}
