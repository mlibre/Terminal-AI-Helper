//! tai engine — in-memory fast ranker.
//!
//! Port of `tai/engine.py`. Goals: sub-millisecond suggest on 10k distinct,
//! low memory. Signals: freq + recency + cwd + repo + branch + hour + exit +
//! sequence + token n-gram + typo-tolerant prefix. The engine never touches
//! the filesystem: the caller answers `on_path` and supplies `exclude`.

use std::collections::HashMap;
use std::sync::OnceLock;

use crate::paths;
use crate::typo::{near_miss_lines, shadow_map};
use crate::util;

// Tunable weights (see tai tune). Defaults from smoke + manual tuning.
pub const W_FREQ: f64 = 3.0;
pub const W_RECENCY: f64 = 2.0;
pub const W_CWD: f64 = 2.2;
pub const W_REPO: f64 = 1.2;
pub const W_BRANCH: f64 = 1.0;
pub const W_SUCCESS: f64 = 0.6;
pub const W_INSTALLED: f64 = 0.5;
pub const W_SEQ: f64 = 2.5;
pub const W_HOUR: f64 = 0.4;
pub const W_TOKEN: f64 = 0.8;
pub const LEN_PENALTY_DIV: f64 = 60.0;
pub const MAX_LEN_PEN: f64 = 0.6;
pub const EXACT_CTX_BONUS: f64 = 0.5;
pub const TYPO_PENALTY: f64 = 1.0;
pub const TEMP_DEFAULT: f64 = 0.9;

fn log1p20() -> f64 {
    20f64.ln_1p()
}

static LOG1P20: OnceLock<f64> = OnceLock::new();
fn log1p20_cached() -> f64 {
    *LOG1P20.get_or_init(log1p20)
}

const TAU: f64 = 14.0;
const MAX_CMD_LEN: usize = 1000;

/// The store's longest-learned-command bound: a row this long is rare, and the
/// engine refuses longer adds the same way the store refuses longer records.
pub fn max_cmd_len() -> usize {
    MAX_CMD_LEN
}

pub fn hour_bucket(ts: Option<i64>) -> i64 {
    let t = ts.unwrap_or(util::now_epoch());
    let h = local_hour(t);
    if h < 6 {
        0
    } else if h < 12 {
        1
    } else if h < 18 {
        2
    } else {
        3
    }
}

fn local_hour(ts: i64) -> i64 {
    unsafe {
        let mut tm: libc::tm = std::mem::zeroed();
        let t = ts as libc::time_t;
        if libc::localtime_r(&t, &mut tm).is_null() {
            return 0;
        }
        tm.tm_hour as i64
    }
}

// Hour buckets are asked once per stored row; keyed by the ten-minute window
// so the table is bounded by the history's span.
static HOURS: OnceLock<std::sync::Mutex<HashMap<i64, i64>>> = OnceLock::new();

fn hour_of(ts: i64) -> i64 {
    let window = ts / 600;
    let map = HOURS.get_or_init(|| std::sync::Mutex::new(HashMap::new()));
    let mut m = map.lock().unwrap();
    if let Some(b) = m.get(&window) {
        return *b;
    }
    let bucket = hour_bucket(Some(ts));
    m.insert(window, bucket);
    bucket
}

fn softmax(scores: &[f64], temp: f64) -> Vec<f64> {
    if scores.is_empty() {
        return Vec::new();
    }
    let m = scores.iter().cloned().fold(f64::NEG_INFINITY, f64::max);
    let temp = temp.max(1e-6);
    let exps: Vec<f64> = scores.iter().map(|s| ((s - m) / temp).exp()).collect();
    let tot: f64 = exps.iter().sum::<f64>().max(1e-12);
    exps.iter().map(|e| e / tot).collect()
}

#[derive(Clone, Debug, Default)]
pub struct CmdStats {
    pub freq: i64,
    pub last_ts: i64,
    pub cwd: HashMap<String, i64>,
    pub repo: HashMap<String, i64>,
    pub branch: HashMap<String, i64>,
    pub hour: [i64; 4],
    pub success: i64,
    pub fail: i64,
    /// Runs the shell refused outright: exit 127 is "command not found".
    pub nf: i64,
}

/// What the caller of `suggest` uses to drop candidates before ranking:
/// a closure answered per candidate, remembered or not by the caller.
pub type Excluder<'a> = &'a mut dyn FnMut(&str) -> bool;

/// The engine's answer to one suggest.
#[derive(Clone, Debug)]
pub struct Choice {
    pub cmd: String,
    pub prob: f64,
    pub score: f64,
}

#[derive(Clone, Debug)]
pub struct SuggestResult {
    pub choice: String,
    pub completion: String,
    pub probabilities: Vec<(String, f64)>,
    pub choices: Vec<Choice>,
    pub confidence: f64,
    pub latency_ms: f64,
    pub count: usize,
    pub wrapped: Option<String>,
    pub from_disk: Option<Vec<String>>,
}

#[derive(Clone, Debug)]
pub struct ExplainFactor {
    pub label: String,
    pub weight: f64,
    pub value: f64,
    pub contrib: f64,
    pub detail: String,
}

#[derive(Clone, Debug)]
pub struct ExplainResult {
    pub cmd: String,
    pub prefix: String,
    pub score: f64,
    pub freq: i64,
    pub last_ts: i64,
    pub age_days: f64,
    pub near_miss: bool,
    pub phantom: bool,
    pub factors: Vec<ExplainFactor>,
}

pub struct Engine {
    /// Insertion order is the row order; `keys()` preserves it like Python's
    /// dict, which the empty-prefix candidate list relies on.
    pub order: Vec<String>,
    pub cmds: HashMap<String, CmdStats>,
    pub seq: HashMap<String, HashMap<String, i64>>,
    pub token_bigram: HashMap<String, HashMap<String, i64>>,
    pub token_trigram: HashMap<(String, String), HashMap<String, i64>>,
    pub sorted_cmds: Vec<String>,
    /// The lowered mirror the case-insensitive prefix walk runs over:
    /// (lowered, original) pairs, sorted by the lowered form.
    pub sorted_lower: Vec<(String, String)>,
    prev_cmd: Option<String>,
    pub first_word_cache: Option<Vec<String>>,
    // Make the field writable from the index module, which filters the same
    // engine the suggest path reads.
    pub cmds_dirty: bool,
    /// The weights `tai tune` wrote, riding this engine for one build.
    pub tuned: Option<HashMap<String, f64>>,
}

impl Default for Engine {
    fn default() -> Self {
        Self::new()
    }
}

impl Engine {
    pub fn new() -> Self {
        Engine {
            order: Vec::new(),
            cmds: HashMap::new(),
            seq: HashMap::new(),
            token_bigram: HashMap::new(),
            token_trigram: HashMap::new(),
            sorted_cmds: Vec::new(),
            sorted_lower: Vec::new(),
            prev_cmd: None,
            first_word_cache: None,
            cmds_dirty: false,
            tuned: None,
        }
    }

    pub fn ensure_sorted(&mut self) {
        if self.cmds_dirty {
            self.sorted_cmds.sort();
            let mut lower: Vec<(String, String)> = self
                .sorted_cmds
                .iter()
                .map(|c| (c.to_lowercase(), c.clone()))
                .collect();
            lower.sort();
            self.sorted_lower = lower;
            self.cmds_dirty = false;
        }
    }

    /// Learn one command. `use_last` continues the sequence the rows built.
    #[allow(clippy::too_many_arguments)]
    pub fn add(
        &mut self,
        cmd: &str,
        cwd: &str,
        repo: &str,
        branch: &str,
        exit_code: i64,
        ts: Option<i64>,
        use_last: bool,
    ) {
        let cmd = cmd.trim().to_string();
        if cmd.is_empty() {
            return;
        }
        if cmd.len() > MAX_CMD_LEN {
            return;
        }
        let ts = ts.unwrap_or_else(util::now_epoch);
        if !self.cmds.contains_key(&cmd) {
            self.cmds.insert(cmd.clone(), CmdStats::default());
            self.order.push(cmd.clone());
            self.sorted_cmds.push(cmd.clone());
            self.cmds_dirty = true;
            self.first_word_cache = None;
        }
        let st = self.cmds.get_mut(&cmd).unwrap();
        st.freq += 1;
        if ts >= st.last_ts {
            st.last_ts = ts;
        }
        if !cwd.is_empty() {
            *st.cwd.entry(cwd.to_string()).or_insert(0) += 1;
        }
        if !repo.is_empty() {
            *st.repo.entry(repo.to_string()).or_insert(0) += 1;
        }
        if !branch.is_empty() {
            *st.branch.entry(branch.to_string()).or_insert(0) += 1;
        }
        st.hour[hour_of(ts) as usize] += 1;
        if exit_code == 0 {
            st.success += 1;
        } else {
            st.fail += 1;
            if exit_code == 127 {
                st.nf += 1;
            }
        }
        // token n-grams within command
        let toks: Vec<&str> = cmd.split_whitespace().collect();
        for i in 1..toks.len() {
            let prev_tok = toks[i - 1].to_string();
            let tok = toks[i].to_string();
            let row = self.token_bigram.entry(prev_tok.clone()).or_default();
            *row.entry(tok.clone()).or_insert(0) += 1;
            if i >= 2 {
                let key = (toks[i - 2].to_string(), prev_tok);
                let row3 = self.token_trigram.entry(key).or_default();
                *row3.entry(tok).or_insert(0) += 1;
            }
        }
        // sequence
        let prev = if use_last { self.prev_cmd.clone() } else { None };
        if let Some(p) = prev {
            let row = self.seq.entry(p).or_default();
            *row.entry(cmd.clone()).or_insert(0) += 1;
        }
        self.prev_cmd = Some(cmd);
    }

    /// rows: (cmd, cwd, repo, branch, exit_code, ts) in chronological order
    /// (oldest first) for correct seq. Rows the recorder itself would reject
    /// are dropped here as well.
    pub fn build_from_rows(&mut self, rows: &[crate::store::Row]) {
        for r in rows {
            let cmd = r.cmd.trim().to_string();
            if !crate::store::is_recordable(&cmd) {
                continue;
            }
            self.add(
                &cmd,
                &r.cwd,
                &r.repo,
                &r.branch,
                r.exit_code,
                Some(if r.ts != 0 { r.ts } else { util::now_epoch() }),
                true,
            );
        }
    }

    /// The commands whose *lowercased* text begins with `prefix`, capped.
    pub fn prefix_range(&mut self, prefix: &str) -> Vec<String> {
        if prefix.is_empty() {
            return Vec::new();
        }
        let lp = prefix.to_lowercase();
        if lp.is_empty() {
            return Vec::new();
        }
        self.ensure_sorted();
        let pairs = &self.sorted_lower;
        // bisect_left on (lp,) — the first pair whose lowered text is >= lp.
        let mut lo: usize = 0;
        let mut hi: usize = pairs.len();
        while lo < hi {
            let mid = (lo + hi) / 2;
            // Compare (lowered,) with (lowered, original): only the lowered
            // text matters for the lower bound.
            if pairs[mid].0.as_str() < lp.as_str() {
                lo = mid + 1;
            } else {
                hi = mid;
            }
        }
        let mut out = Vec::new();
        let mut i = lo;
        while i < pairs.len() && out.len() < 2000 {
            let (lc, c) = &pairs[i];
            if !lc.starts_with(&lp) {
                break;
            }
            out.push(c.clone());
            i += 1;
        }
        out
    }

    fn phantom(&self, c: &str) -> bool {
        match self.cmds.get(c) {
            Some(st) => st.nf != 0 && st.nf == st.freq,
            None => false,
        }
    }

    // -- suggest ----------------------------------------------------------
    #[allow(clippy::too_many_arguments)]
    pub fn suggest(
        &mut self,
        prefix: &str,
        cwd: &str,
        repo: &str,
        branch: &str,
        last_commands: &[String],
        limit: usize,
        temp: f64,
        now_ts: Option<i64>,
        exclude_active: bool,
        exclude: Excluder,
        on_path: Option<&dyn Fn(&str) -> bool>,
    ) -> SuggestResult {
        let limit = limit.max(1);
        let t0 = std::time::Instant::now();
        let prefix = prefix.to_string();
        let now = now_ts.unwrap_or_else(util::now_epoch);
        let hb = hour_bucket(Some(now));
        let prev = last_commands
            .last()
            .map(|s| s.trim().to_string())
            .unwrap_or_default();

        let prefix_stripped = prefix.trim().to_string();
        let mut cand_names: Vec<String> = Vec::new();
        let mut typo_set: std::collections::HashSet<String> = std::collections::HashSet::new();
        if !prefix_stripped.is_empty() {
            cand_names = self.prefix_range(&prefix);
            if prefix != prefix_stripped {
                let extra = self.prefix_range(&prefix_stripped);
                if !extra.is_empty() {
                    let seen: std::collections::HashSet<String> =
                        cand_names.iter().cloned().collect();
                    for c in extra {
                        if !seen.contains(&c) {
                            cand_names.push(c);
                        }
                    }
                }
            }
            if cand_names.len() < 3 {
                let typo = near_miss_lines(self, &prefix_stripped, 50);
                let seen: std::collections::HashSet<String> =
                    cand_names.iter().cloned().collect();
                for c in typo {
                    if !seen.contains(&c) {
                        typo_set.insert(c.clone());
                        cand_names.push(c);
                    }
                }
            }
        } else {
            // empty prefix: rank all distinct (two-stage filter inside scoring)
            cand_names = self.order.clone();
        }

        if exclude_active {
            let dropped: Vec<String> = cand_names
                .iter()
                .filter(|c| exclude(c))
                .cloned()
                .collect();
            if !dropped.is_empty() {
                cand_names.retain(|c| !dropped.contains(c));
            }
        }
        // A line the shell never ran is not a command: every run exit 127.
        if !cand_names.is_empty() {
            let phantom: Vec<String> =
                cand_names.iter().filter(|c| self.phantom(c)).cloned().collect();
            if !phantom.is_empty() {
                cand_names.retain(|c| !phantom.contains(c));
            }
        }
        if cand_names.is_empty() {
            if let Some((head, rest)) = split_wrapper(&prefix) {
                let mut inner = self.suggest(
                    &rest,
                    cwd,
                    repo,
                    branch,
                    last_commands,
                    limit,
                    temp,
                    now_ts,
                    exclude_active,
                    &mut *exclude,
                    on_path,
                );
                rewrap(&mut inner, &head, &prefix);
                return inner;
            }
            return fallback(&prefix, limit);
        }

        // two-stage: if huge, pre-filter by cheap freq+recency to 600.
        let mut pre: Option<HashMap<String, (f64, f64)>> = None;
        if cand_names.len() > 600 {
            let mut pre_map = HashMap::new();
            let mut scored_pre: Vec<(f64, String)> = Vec::with_capacity(cand_names.len());
            for name in &cand_names {
                let st = &self.cmds[name];
                let age_days =
                    ((now - if st.last_ts != 0 { st.last_ts } else { now }) as f64 / 86400.0)
                        .max(0.0);
                let rec = (-age_days / TAU).exp();
                let fq = (st.freq as f64).ln_1p();
                pre_map.insert(name.clone(), (fq, rec));
                scored_pre.push((fq + rec, name.clone()));
            }
            scored_pre.sort_by(|a, b| b.0.total_cmp(&a.0).then(b.1.cmp(&a.1)));
            cand_names = scored_pre.into_iter().take(600).map(|(_, n)| n).collect();
            pre = Some(pre_map);
        }

        let follow = if !prev.is_empty() { self.seq.get(&prev) } else { None };
        let max_follow = follow.map(|f| f.values().cloned().max().unwrap_or(0)).unwrap_or(0);

        // The weights a tuned run rides, or the shipped constants.
        let tw = self.tuned.as_ref();
        let w = |name: &str, default: f64| -> f64 {
            tw.and_then(|t| t.get(name).copied()).unwrap_or(default)
        };
        let (w_freq, w_recency, w_cwd, w_repo, w_branch, w_success, w_hour, w_seq, w_token) = (
            w("W_FREQ", W_FREQ),
            w("W_RECENCY", W_RECENCY),
            w("W_CWD", W_CWD),
            w("W_REPO", W_REPO),
            w("W_BRANCH", W_BRANCH),
            w("W_SUCCESS", W_SUCCESS),
            w("W_HOUR", W_HOUR),
            w("W_SEQ", W_SEQ),
            w("W_TOKEN", W_TOKEN),
        );

        // token context for next-token bonus.
        let prefix_l = prefix.to_lowercase();
        let ptoks: Vec<&str> = prefix.split_whitespace().collect();
        let tri_ctx: Option<(String, String)> = if ptoks.len() >= 2 {
            Some((ptoks[ptoks.len() - 2].to_string(), ptoks[ptoks.len() - 1].to_string()))
        } else {
            None
        };
        let bi_ctx: Option<&str> = ptoks.last().copied();
        let tri_row = tri_ctx.as_ref().and_then(|k| self.token_trigram.get(k));
        let tri_total = tri_row.map(|r| r.values().sum::<i64>()).unwrap_or(0);
        let bi_row = bi_ctx.and_then(|k| self.token_bigram.get(k));
        let bi_total = bi_row.map(|r| r.values().sum::<i64>()).unwrap_or(0);

        let mut scored: Vec<(f64, String)> = Vec::with_capacity(cand_names.len());
        for name in &cand_names {
            let st = &self.cmds[name];
            let (recency, freq_s) = if let Some(p) = &pre {
                let (fq, rec) = p[name];
                (rec, (fq / log1p20_cached()).min(1.0))
            } else {
                let age_days =
                    ((now - if st.last_ts != 0 { st.last_ts } else { now }) as f64 / 86400.0)
                        .max(0.0);
                let recency = (-age_days / TAU).exp();
                let freq_s = ((st.freq as f64).ln_1p() / log1p20_cached()).min(1.0);
                (recency, freq_s)
            };
            let maxf = st.freq.max(1) as f64;
            let mut cwd_s = if cwd.is_empty() {
                0.0
            } else {
                (st.cwd.get(cwd).copied().unwrap_or(0) as f64 / maxf).min(1.0)
            };
            // basename fallback: half credit
            if !cwd.is_empty() && cwd_s == 0.0 && !st.cwd.is_empty() {
                let base = cwd.trim_end_matches('/').rsplit('/').next().unwrap_or("");
                for (k, v) in st.cwd.iter() {
                    if k.trim_end_matches('/').rsplit('/').next() == Some(base) {
                        cwd_s = (0.5 * *v as f64 / maxf).min(0.5);
                        break;
                    }
                }
            }
            let repo_s = if repo.is_empty() {
                0.0
            } else {
                (st.repo.get(repo).copied().unwrap_or(0) as f64 / maxf).min(1.0)
            };
            let branch_s = if branch.is_empty() {
                0.0
            } else {
                (st.branch.get(branch).copied().unwrap_or(0) as f64 / maxf).min(1.0)
            };
            let success_s = (((st.success as f64 - st.fail as f64 * 0.5) / maxf)
                .clamp(-0.5, 1.0))
                * 0.5
                + 0.5;
            let hour_s = if st.freq != 0 { st.hour[hb as usize] as f64 / maxf } else { 0.0 };
            let seq_s = match (follow, max_follow) {
                (Some(f), m) if m > 0 => f.get(name).copied().unwrap_or(0) as f64 / m as f64,
                _ => 0.0,
            };

            // token n-gram bonus: P(next token | context).
            let mut tok_s = 0.0f64;
            if !prefix.is_empty() && name.to_lowercase().starts_with(&prefix_l) {
                let rest = name[prefix.len().min(name.len())..].trim_start();
                if !rest.is_empty() {
                    let nxt = rest.split_whitespace().next().unwrap_or("");
                    if let Some(tr) = tri_row {
                        tok_s = tok_s.max(tr.get(nxt).copied().unwrap_or(0) as f64
                            / tri_total.max(1) as f64);
                    }
                    if let Some(br) = bi_row {
                        tok_s = tok_s.max(0.7
                            * br.get(nxt).copied().unwrap_or(0) as f64
                            / bi_total.max(1) as f64);
                    }
                }
            }

            let extra_len = (name.len().saturating_sub(prefix.len())) as f64;
            let len_pen = (extra_len / LEN_PENALTY_DIV).min(MAX_LEN_PEN);

            let mut score = w_freq * freq_s
                + w_recency * recency
                + w_cwd * cwd_s
                + w_repo * repo_s
                + w_branch * branch_s
                + w_success * success_s
                + w_hour * hour_s
                + w_seq * seq_s
                + w_token * tok_s
                - len_pen;
            if !cwd.is_empty()
                && !repo.is_empty()
                && st.cwd.get(cwd).copied().unwrap_or(0) > 0
                && st.repo.get(repo).copied().unwrap_or(0) > 0
            {
                score += EXACT_CTX_BONUS;
            }
            if let Some(op) = on_path {
                let fw = name.splitn(2, ' ').next().unwrap_or("");
                if op(fw) {
                    score += W_INSTALLED;
                }
            }
            if typo_set.contains(name) {
                score -= TYPO_PENALTY;
            }
            scored.push((score, name.clone()));
        }

        // A one-off that nearly duplicates a stronger line ranks just below it.
        // Computed over the candidates, because the cap only means something
        // when both spellings are on offer. Wide answers skip it.
        if cand_names.len() <= 64 {
            let shadows = shadow_map(self, Some(&cand_names));
            if !shadows.is_empty() {
                let mut by_name: HashMap<String, f64> =
                    scored.iter().map(|(s, n)| (n.clone(), *s)).collect();
                let mut demoted = false;
                for _ in 0..2 {
                    for (typo, canon) in shadows.iter() {
                        if let (Some(cs), Some(cs_ts)) =
                            (by_name.get(canon).copied(), by_name.get(typo).copied())
                        {
                            if cs_ts > cs - 0.01 {
                                by_name.insert(typo.clone(), cs - 0.01);
                                demoted = true;
                            }
                        }
                    }
                }
                if demoted {
                    scored = by_name.into_iter().map(|(n, s)| (s, n)).collect();
                }
            }
        }
        // `cd ..` and `cd -` are true in every directory there is.
        paths::destinations_first(&mut scored);
        scored.sort_by(|a, b| b.0.total_cmp(&a.0).then(b.1.cmp(&a.1)));
        // A suggestion has to extend the line.
        let kept: Vec<(f64, String)> = scored
            .into_iter()
            .filter(|(s, n)| {
                (n.to_lowercase().starts_with(&prefix_l) && n != &prefix)
                    || typo_set.contains(n)
            })
            .take((limit * 3).min(200))
            .collect();
        let top_scores: Vec<f64> = kept.iter().map(|(s, _)| *s).collect();
        let top_names: Vec<String> = kept.iter().map(|(_, n)| n.clone()).collect();
        let probs = softmax(&top_scores, temp);

        let mut conf = 0.0f64;
        if probs.len() == 1 {
            conf = (0.55 + probs[0] * 0.3).min(0.95);
        } else if !probs.is_empty() {
            conf = ((probs[0] - probs[1]) * 2.2 + 0.35).clamp(0.0, 1.0);
        }

        let elapsed_ms = t0.elapsed().as_secs_f64() * 1000.0;
        let mut choices: Vec<Choice> = Vec::new();
        for (n, p) in top_names.iter().zip(probs.iter()) {
            choices.push(Choice {
                cmd: n.clone(),
                prob: (p * 10000.0).round() / 10000.0,
                score: 0.0,
            });
        }
        // The score rides beside the prob, rounded like Python's round(s, 3).
        for (i, s) in top_scores.iter().enumerate() {
            if i < choices.len() {
                choices[i].score = (s * 1000.0).round() / 1000.0;
            }
        }
        choices.truncate(limit);
        let best = top_names.first().cloned().unwrap_or_default();
        let completion = if best.to_lowercase().starts_with(&prefix.to_lowercase()) {
            best[prefix.len().min(best.len())..].to_string()
        } else {
            String::new()
        };
        let probabilities: Vec<(String, f64)> = choices
            .iter()
            .map(|c| (c.cmd.clone(), c.prob))
            .collect();
        SuggestResult {
            choice: best,
            completion,
            probabilities,
            choices,
            confidence: (conf * 1000.0).round() / 1000.0,
            latency_ms: (elapsed_ms * 100.0).round() / 100.0,
            count: cand_names.len(),
            wrapped: None,
            from_disk: None,
        }
    }

    // -- explain ----------------------------------------------------------
    #[allow(clippy::too_many_arguments)]
    pub fn explain(
        &self,
        prefix: &str,
        cmd: &str,
        cwd: &str,
        repo: &str,
        branch: &str,
        last_commands: &[String],
        now_ts: Option<i64>,
        on_path: Option<&dyn Fn(&str) -> bool>,
    ) -> Option<ExplainResult> {
        let st = self.cmds.get(cmd)?;
        let now = now_ts.unwrap_or_else(util::now_epoch);
        let hb = hour_bucket(Some(now));
        let prev = last_commands
            .last()
            .map(|s| s.trim().to_string())
            .unwrap_or_default();
        let prefix = prefix.to_string();

        let mut factors: Vec<ExplainFactor> = Vec::new();
        let mut add = |label: &str, weight: f64, value: f64, detail: String| {
            factors.push(ExplainFactor {
                label: label.to_string(),
                weight,
                value: (value * 10000.0).round() / 10000.0,
                contrib: (weight * value * 10000.0).round() / 10000.0,
                detail,
            });
        };

        let age_days =
            ((now - if st.last_ts != 0 { st.last_ts } else { now }) as f64 / 86400.0).max(0.0);
        let recency = (-age_days / TAU).exp();
        let freq_s = ((st.freq as f64).ln_1p() / log1p20_cached()).min(1.0);
        add(
            "frequency",
            W_FREQ,
            freq_s,
            format!("run {}× (log1p scaled at 20)", st.freq),
        );
        add(
            "recency",
            W_RECENCY,
            recency,
            format!("last run {:.1}d ago (half-life 14d)", age_days),
        );

        let maxf = st.freq.max(1) as f64;
        let mut cwd_s = if cwd.is_empty() {
            0.0
        } else {
            (st.cwd.get(cwd).copied().unwrap_or(0) as f64 / maxf).min(1.0)
        };
        let cwd_detail = if cwd.is_empty() {
            format!("{} runs recorded, no directory asked", st.freq)
        } else if cwd_s == 0.0 && !st.cwd.is_empty() {
            let base = cwd.trim_end_matches('/').rsplit('/').next().unwrap_or("").to_string();
            let mut detail = format!(
                "{} of {} runs in this directory",
                st.cwd.get(cwd).copied().unwrap_or(0),
                st.freq
            );
            for (k, v) in st.cwd.iter() {
                if k.trim_end_matches('/').rsplit('/').next() == Some(&base) {
                    cwd_s = (0.5 * *v as f64 / maxf).min(0.5);
                    detail = format!(
                        "half credit: {} of {} runs in a directory also named {}/",
                        v, st.freq, base
                    );
                    break;
                }
            }
            detail
        } else {
            format!(
                "{} of {} runs in this directory",
                st.cwd.get(cwd).copied().unwrap_or(0),
                st.freq
            )
        };
        add("this directory", W_CWD, cwd_s, cwd_detail);

        let repo_s = if repo.is_empty() {
            0.0
        } else {
            (st.repo.get(repo).copied().unwrap_or(0) as f64 / maxf).min(1.0)
        };
        add(
            "repository",
            W_REPO,
            repo_s,
            if repo.is_empty() {
                format!("{} runs recorded, no repository asked", st.freq)
            } else {
                format!(
                    "{} of {} runs in this repository",
                    st.repo.get(repo).copied().unwrap_or(0),
                    st.freq
                )
            },
        );

        let branch_s = if branch.is_empty() {
            0.0
        } else {
            (st.branch.get(branch).copied().unwrap_or(0) as f64 / maxf).min(1.0)
        };
        add(
            "git branch",
            W_BRANCH,
            branch_s,
            if branch.is_empty() {
                format!("{} runs recorded, no branch asked", st.freq)
            } else {
                format!(
                    "{} of {} runs on this branch",
                    st.branch.get(branch).copied().unwrap_or(0),
                    st.freq
                )
            },
        );

        let success_s = (((st.success as f64 - st.fail as f64 * 0.5) / maxf).clamp(-0.5, 1.0))
            * 0.5
            + 0.5;
        add(
            "success",
            W_SUCCESS,
            success_s,
            format!("{} ok / {} failed", st.success, st.fail),
        );

        if let Some(op) = on_path {
            let fw = cmd.splitn(2, ' ').next().unwrap_or("");
            let hit = op(fw);
            add(
                "installed command",
                W_INSTALLED,
                if hit { 1.0 } else { 0.0 },
                format!("`{}` is{} on PATH", fw, if hit { "" } else { " not" }),
            );
        }

        let hour_s = if st.freq != 0 { st.hour[hb as usize] as f64 / maxf } else { 0.0 };
        add(
            "hour of day",
            W_HOUR,
            hour_s,
            format!("{} of {} runs in this hour of the day", st.hour[hb as usize], st.freq),
        );

        let follow = if !prev.is_empty() { self.seq.get(&prev) } else { None };
        let max_follow = follow.map(|f| f.values().cloned().max().unwrap_or(0)).unwrap_or(0);
        let seq_s = match (follow, max_follow) {
            (Some(f), m) if m > 0 => f.get(cmd).copied().unwrap_or(0) as f64 / m as f64,
            _ => 0.0,
        };
        let seq_n = follow.map(|f| f.get(cmd).copied().unwrap_or(0)).unwrap_or(0);
        if !prev.is_empty() {
            add(
                "follows the last command",
                W_SEQ,
                seq_s,
                format!(
                    "followed `{}` {}×{}",
                    prev,
                    seq_n,
                    if seq_s == 0.0 { "; never this command" } else { "" }
                ),
            );
        } else {
            add(
                "follows the last command",
                W_SEQ,
                0.0,
                "no last command in the question".to_string(),
            );
        }

        // token n-gram bonus: P(next token | context).
        let mut tok_s = 0.0f64;
        let mut tok_detail = "no next word behind the prefix".to_string();
        let prefix_l = prefix.to_lowercase();
        let ptoks: Vec<&str> = prefix.split_whitespace().collect();
        let tri_ctx: Option<(String, String)> = if ptoks.len() >= 2 {
            Some((ptoks[ptoks.len() - 2].to_string(), ptoks[ptoks.len() - 1].to_string()))
        } else {
            None
        };
        let bi_ctx: Option<&str> = ptoks.last().copied();
        if !prefix.is_empty() && cmd.to_lowercase().starts_with(&prefix_l) {
            let rest = cmd[prefix.len().min(cmd.len())..].trim_start();
            if !rest.is_empty() {
                let nxt = rest.split_whitespace().next().unwrap_or("");
                tok_detail = format!("`{}` has never followed the typed words", nxt);
                if let Some(tc) = tri_ctx.as_ref().and_then(|k| self.token_trigram.get(k)) {
                    let total: i64 = tc.values().sum();
                    tok_s = tok_s.max(tc.get(nxt).copied().unwrap_or(0) as f64 / total.max(1) as f64);
                    tok_detail = format!(
                        "`{}` followed `{}` {} of {}×",
                        nxt,
                        format!("{} {}", tri_ctx.as_ref().unwrap().0, tri_ctx.as_ref().unwrap().1),
                        tc.get(nxt).copied().unwrap_or(0),
                        total
                    );
                }
                if let Some(bc) = bi_ctx.and_then(|k| self.token_bigram.get(k)) {
                    let total: i64 = bc.values().sum();
                    let cand = 0.7
                        * bc.get(nxt).copied().unwrap_or(0) as f64
                        / total.max(1) as f64;
                    if cand > tok_s {
                        tok_s = cand;
                        tok_detail = format!(
                            "`{}` followed `{}` {} of {}×",
                            nxt,
                            bi_ctx.unwrap_or(""),
                            bc.get(nxt).copied().unwrap_or(0),
                            total
                        );
                    }
                }
            }
        }
        add("next-word evidence", W_TOKEN, tok_s, tok_detail);

        let extra_len = cmd.len().saturating_sub(prefix.len()) as f64;
        let len_pen = (extra_len / LEN_PENALTY_DIV).min(MAX_LEN_PEN);
        factors.push(ExplainFactor {
            label: "length".to_string(),
            weight: -1.0,
            value: (len_pen * 10000.0).round() / 10000.0,
            contrib: -((len_pen * 10000.0).round() / 10000.0),
            detail: format!(
                "{} characters past the prefix{}",
                extra_len as i64,
                if len_pen >= MAX_LEN_PEN { ", at the penalty cap" } else { "" }
            ),
        });

        let mut score: f64 = factors.iter().map(|f| f.contrib).sum();
        if !cwd.is_empty()
            && !repo.is_empty()
            && st.cwd.get(cwd).copied().unwrap_or(0) > 0
            && st.repo.get(repo).copied().unwrap_or(0) > 0
        {
            score += EXACT_CTX_BONUS;
            factors.push(ExplainFactor {
                label: "directory and repository together".to_string(),
                weight: 1.0,
                value: EXACT_CTX_BONUS,
                contrib: EXACT_CTX_BONUS,
                detail: "run here, in this repository".to_string(),
            });
        }
        let near_miss = !cmd.to_lowercase().starts_with(&prefix_l);
        if near_miss {
            score -= TYPO_PENALTY;
            factors.push(ExplainFactor {
                label: "near-miss".to_string(),
                weight: -1.0,
                value: TYPO_PENALTY,
                contrib: -TYPO_PENALTY,
                detail: format!("does not start with `{}`; offered as a typo of it", prefix),
            });
        }
        let phantom = st.nf != 0 && st.nf == st.freq;
        if phantom {
            factors.push(ExplainFactor {
                label: "command not found".to_string(),
                weight: 0.0,
                value: 0.0,
                contrib: 0.0,
                detail: format!(
                    "all {} runs were exit 127 — the shell never found it; hidden from suggestions",
                    st.nf
                ),
            });
        }

        Some(ExplainResult {
            cmd: cmd.to_string(),
            prefix,
            score: (score * 1000.0).round() / 1000.0,
            freq: st.freq,
            last_ts: st.last_ts,
            age_days: (age_days * 100.0).round() / 100.0,
            near_miss,
            phantom,
            factors,
        })
    }
}

fn rewrap(result: &mut SuggestResult, head: &str, prefix: &str) {
    if result.choice.is_empty() {
        return;
    }
    let full = format!("{} {}", head, result.choice);
    result.completion = if full.to_lowercase().starts_with(&prefix.to_lowercase()) {
        full[prefix.len().min(full.len())..].to_string()
    } else {
        String::new()
    };
    result.probabilities =
        result.probabilities.iter().map(|(c, p)| (format!("{} {}", head, c), *p)).collect();
    for c in result.choices.iter_mut() {
        c.cmd = format!("{} {}", head, c.cmd);
    }
    result.wrapped = Some(head.to_string());
    result.choice = full;
}

fn fallback(prefix: &str, limit: usize) -> SuggestResult {
    let defaults: Vec<&str> = crate::seed::SEED_COMMANDS.to_vec();
    let prefix_l = prefix.to_lowercase();
    let mut cands: Vec<String> = if !prefix.is_empty() {
        defaults
            .iter()
            .filter(|d| d.to_lowercase().starts_with(&prefix_l) && **d != prefix)
            .map(|s| s.to_string())
            .collect()
    } else {
        defaults.iter().take(3).map(|s| s.to_string()).collect()
    };
    if cands.is_empty() && !prefix.is_empty() && !prefix.chars().any(|c| c.is_whitespace()) {
        if util::path_lookup(prefix) {
            cands = vec![format!("{} --help", prefix)];
        }
    }
    cands.truncate(limit);
    let scores: Vec<f64> = (0..cands.len()).map(|i| 1.0 - i as f64 * 0.2).collect();
    let probs = softmax(&scores, 1.0);
    let choice = cands.first().cloned().unwrap_or_default();
    let completion = if choice.to_lowercase().starts_with(&prefix_l) {
        choice[prefix.len().min(choice.len())..].to_string()
    } else {
        String::new()
    };
    let probabilities: Vec<(String, f64)> = cands
        .iter()
        .cloned()
        .zip(probs.iter())
        .map(|(c, p)| (c, (p * 10000.0).round() / 10000.0))
        .collect();
    let choices: Vec<Choice> = cands
        .iter()
        .cloned()
        .zip(probs.iter())
        .map(|(c, p)| Choice { cmd: c, prob: (p * 10000.0).round() / 10000.0, score: 0.0 })
        .collect();
    SuggestResult {
        choice,
        completion,
        probabilities,
        choices,
        confidence: if cands.is_empty() { 0.0 } else { 0.25 },
        latency_ms: 0.05,
        count: 0,
        wrapped: None,
        from_disk: None,
    }
}

/// Words that run the command after them rather than being the command.
pub const WRAPPERS: &[&str] = &["sudo", "doas", "nohup", "time", "nice", "ionice", "stdbuf", "command"];

/// ("sudo", "git st") when `prefix` runs another command behind a wrapper.
/// A wrapper carrying its own options is left alone.
pub fn split_wrapper(prefix: &str) -> Option<(String, String)> {
    let (head, rest) = match prefix.split_once(' ') {
        Some((h, r)) => (h, r),
        None => return None,
    };
    if !WRAPPERS.contains(&head) || rest.is_empty() || rest.contains('=') {
        return None;
    }
    Some((head.to_string(), rest.to_string()))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn seeded() -> Engine {
        let rows = [
            ("git status", 3, 0, 1700000100),
            ("git add -A", 2, 0, 1700000200),
            ("git commit -m 'fix the thing'", 2, 0, 1700000300),
            ("git push", 1, 0, 1700000400),
            ("ls -la", 2, 0, 1700000500),
            ("ls -l", 1, 0, 1700000600),
            ("cd ~/projects/myapp", 2, 0, 1700000700),
            ("cd ..", 1, 0, 1700000800),
            ("tai uninstall", 3, 0, 1700000900),
            ("tai unsintall", 1, 0, 1700001000),
            ("opencoe --serve", 2, 127, 1700001100),
        ];
        let mut eng = Engine::new();
        for (cmd, freq, code, ts) in rows {
            for i in 0..freq {
                eng.add(cmd, "", "", "", code, Some(ts + i as i64), true);
            }
        }
        eng
    }

    #[test]
    fn a_suggestion_extends_the_line() {
        let mut eng = seeded();
        let res = eng.suggest("ls -l", "", "", "", &[], 1, 0.9, None, false, &mut |_| false, None);
        // `ls -l` suggests `ls -la`, never itself.
        assert_eq!(res.choice, "ls -la");
        assert_eq!(res.completion, "a");
        // A whole command suggests nothing.
        let res = eng.suggest("git status", "", "", "", &[], 1, 0.9, None, false, &mut |_| false, None);
        assert_eq!(res.choice, "");
    }

    #[test]
    fn a_phantom_is_not_a_candidate() {
        let mut eng = seeded();
        // Every recorded run was exit 127: the shell never found it.
        let res = eng.suggest("openco", "", "", "", &[], 5, 0.9, None, false, &mut |_| false, None);
        assert!(
            !res.choices.iter().any(|c| c.cmd == "opencoe --serve"),
            "a line the shell refused is not a suggestion"
        );
    }

    #[test]
    fn the_typo_ranks_below_the_habit() {
        let mut eng = seeded();
        let res = eng.suggest("tai un", "", "", "", &[], 1, 0.9, None, false, &mut |_| false, None);
        assert_eq!(res.choice, "tai uninstall");
    }

    #[test]
    fn the_sequence_answers_what_followed() {
        let mut eng = seeded();
        // A whole line cannot extend itself, so it answers nothing — the
        // fresh-prompt prophecy is the empty prefix with the last command.
        let whole = eng.suggest("git add -A", "", "", "", &[], 1, 0.9, None, false, &mut |_| false, None);
        assert_eq!(whole.choice, "");
        // What followed `git add -A` three times in a row is the commit; the
        // unrelated habits are older and rarer, so the pair is the answer.
        let mut eng = seeded();
        for _ in 0..3 {
            eng.add("git commit -m 'fix the thing'", "", "", "", 0, Some(1700001500), true);
        }
        eng.add("git commit -m 'fix the thing'", "", "", "", 0, Some(1700001500), false);
        // (the adds above chained from `git add -A`; one explicit pass keeps
        // the pair unambiguous against the other git habits)
        let res = eng.suggest(
            "",
            "",
            "",
            "",
            &["git add -A".to_string()],
            1,
            0.9,
            None,
            false,
            &mut |_| false,
            None,
        );
        assert_eq!(res.choice, "git commit -m 'fix the thing'");
    }

    #[test]
    fn a_wrapper_is_transparent() {
        let mut eng = seeded();
        // Nothing for `sudo git st` in this history, so rank the line as if
        // the wrapper were not there and put it back.
        let res = eng.suggest("sudo git s", "", "", "", &[], 1, 0.9, None, false, &mut |_| false, None);
        assert_eq!(res.choice, "sudo git status");
        assert_eq!(res.wrapped.as_deref(), Some("sudo"));
    }

    #[test]
    fn the_case_insensitive_prefix_walk() {
        let mut eng = seeded();
        let res = eng.suggest("GIT S", "", "", "", &[], 1, 0.9, None, false, &mut |_| false, None);
        assert_eq!(res.choice, "git status");
    }

    #[test]
    fn the_fallback_is_honest() {
        let mut eng = seeded();
        // A word nothing begins with and nothing on PATH answers: nothing,
        // never an echo of the prefix. (The `tool --help` answer for an
        // installed word is pinned in the CLI conversation tests, where a
        // PATH can be controlled.)
        let res = eng.suggest("zzzznothing", "", "", "", &[], 1, 0.9, None, false, &mut |_| false, None);
        assert_eq!(res.choice, "");
        // The fallback answers from the seed corpus when ranking cannot:
        // seeds that extend the prefix, and never the prefix itself.
        let res = eng.suggest("git sw", "", "", "", &[], 1, 0.9, None, false, &mut |_| false, None);
        assert_eq!(res.choice, "git switch branch");
    }

    #[test]
    fn two_stage_prefilter_keeps_the_winner() {
        let mut eng = Engine::new();
        for i in 0..700 {
            eng.add(
                &format!("filler{:04} x", i),
                "",
                "",
                "",
                0,
                Some(1700000000 + i),
                false,
            );
        }
        eng.add("git status", "", "", "", 0, Some(1700099999), false);
        let res = eng.suggest("g", "", "", "", &[], 1, 0.9, None, false, &mut |_| false, None);
        assert_eq!(res.choice, "git status");
    }

    #[test]
    fn explain_totals_match_suggest() {
        let mut eng = seeded();
        let res = eng.suggest("git ", "", "", "", &[], 8, 0.9, None, false, &mut |_| false, None);
        let best = res.choices.first().unwrap().cmd.clone();
        let exp = eng.explain("git ", &best, "", "", "", &[], None, None).unwrap();
        let by_suggest = res
            .choices
            .iter()
            .find(|c| c.cmd == best)
            .map(|c| c.score)
            .unwrap();
        // A factor row the suggest loop does not have (the near-miss) only
        // appears when the line is a near miss; the best choice is not one.
        assert!(!exp.near_miss);
        assert!(
            (exp.score - by_suggest).abs() < 0.01,
            "explain {} vs suggest {}",
            exp.score,
            by_suggest
        );
    }
}
