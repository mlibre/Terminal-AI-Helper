//! Typo tolerance for the first word of a command: `dokcer` is `docker`.
//!
//! Port of `tai/typo.py`. Two rules that are not about spelling: half-typed is
//! not a licence to invent, and a near miss is ranked below every exact match.

use std::collections::{HashMap, HashSet};

use crate::engine::Engine;

/// Levenshtein distance, capped at `max_dist + 1`. The cap is the whole trick:
/// a caller only ever asks "is this within n". Shared head and tail are peeled
/// first, and the DP walks only the diagonal band `|i - j| <= max_dist`.
pub fn lev1(a: &str, b: &str, max_dist: usize) -> usize {
    if a == b {
        return 0;
    }
    let mut a: Vec<char> = a.chars().collect();
    let mut b: Vec<char> = b.chars().collect();
    let (mut la, mut lb) = (a.len(), b.len());
    if la > lb {
        std::mem::swap(&mut a, &mut b);
        std::mem::swap(&mut la, &mut lb);
    }
    if lb - la > max_dist {
        return max_dist + 1;
    }
    // Peel the shared head. `p < la` keeps the peel from consuming everything.
    let mut p = 0;
    while p < la && a[p] == b[p] {
        p += 1;
    }
    let mut s = 0;
    while s < la - p && a[la - 1 - s] == b[lb - 1 - s] {
        s += 1;
    }
    if p > 0 {
        a.drain(..p);
        b.drain(..p);
        la -= p;
        lb -= p;
    }
    if s > 0 {
        a.truncate(la - s);
        b.truncate(lb - s);
        la -= s;
        lb -= s;
    }
    if la == 0 {
        return lb;
    }
    if lb == 0 {
        return la;
    }
    let cap = max_dist + 1;
    let mut prev: Vec<usize> = (0..=lb).collect();
    for i in 1..=la {
        let ca = a[i - 1];
        let mut lo = i.saturating_sub(max_dist);
        if lo < 1 {
            lo = 1;
        }
        let mut hi = i + max_dist;
        if hi > lb {
            hi = lb;
        }
        let mut cur = vec![cap; lb + 1];
        cur[0] = i;
        let mut row_min = if lo == 1 { cur[lo - 1] } else { cap };
        if lo == 1 {
            let cost = if ca == b[0] { 0 } else { 1 };
            let mut v = prev[0] + cost;
            let w = prev[1] + 1;
            if w < v {
                v = w;
            }
            if cur[0] + 1 < v {
                v = cur[0] + 1;
            }
            cur[1] = v;
            if v < row_min {
                row_min = v;
            }
        }
        let start = if lo > 1 { lo } else { 2 };
        for j in start..=hi {
            let cost = if ca == b[j - 1] { 0 } else { 1 };
            let mut v = prev[j - 1] + cost;
            let w = prev[j] + 1;
            if w < v {
                v = w;
            }
            let w = cur[j - 1] + 1;
            if w < v {
                v = w;
            }
            cur[j] = v;
            if v < row_min {
                row_min = v;
            }
        }
        if row_min > max_dist {
            return cap;
        }
        prev = cur;
    }
    let d = prev[lb];
    if d <= max_dist {
        d
    } else {
        cap
    }
}

/// Every distinct command name's first word, in lexical order. Built on demand
/// and discarded when the engine learns a new command.
fn first_words(eng: &mut Engine) -> Vec<String> {
    if let Some(cached) = &eng.first_word_cache {
        return cached.clone();
    }
    eng.ensure_sorted();
    let mut seen: Vec<String> = Vec::new();
    let mut seen_set: HashSet<String> = HashSet::new();
    for c in &eng.sorted_cmds {
        let w = c.splitn(2, ' ').next().unwrap_or("").to_string();
        if !w.is_empty() && !seen_set.contains(&w) {
            seen_set.insert(w.clone());
            seen.push(w);
            if seen.len() > 2000 {
                break;
            }
        }
    }
    eng.first_word_cache = Some(seen.clone());
    seen
}

/// Whole commands whose first word is within an edit or two of `prefix`'s.
/// Only for a first word of three characters or more. The result is filtered
/// against the rest of the line, and the word comparison folds case.
pub fn near_miss_lines(eng: &mut Engine, prefix: &str, limit: usize) -> Vec<String> {
    let words: Vec<&str> = prefix.split_whitespace().collect();
    let first = words.first().copied().unwrap_or(prefix);
    if first.chars().count() < 3 {
        return Vec::new();
    }
    let first_l = first.to_lowercase();
    let mut cands: Vec<String> = Vec::new();
    let max_d = if first.chars().count() <= 4 { 1 } else { 2 };
    for w in first_words(eng) {
        let w_l = w.to_lowercase();
        if w_l.starts_with(&first_l) || first_l.starts_with(&w_l) {
            continue;
        }
        if lev1(&first_l, &w_l, max_d) <= max_d {
            cands.push(w);
            if cands.len() >= 10 {
                break;
            }
        }
    }
    let rest_l = prefix[first.len()..].to_lowercase();
    let mut out: Vec<String> = Vec::new();
    for w in cands {
        for c in eng.prefix_range(&w) {
            let has_more = c.splitn(2, ' ').count() > 1;
            if has_more || prefix.trim() == first {
                // only keep commands consistent with rest of prefix
                let rem = &c[w.len()..];
                if rem.to_lowercase().starts_with(&rest_l) {
                    out.push(c);
                    if out.len() >= limit {
                        return out;
                    }
                }
            }
        }
    }
    out
}

/// A one-off that nearly duplicates a stronger line is a typo of it, and it is
/// demoted just below that line instead of ranking beside it. A line the user
/// has run three times is a habit, and habits are not demoted.
pub const TYPO_FREQ_MAX: i64 = 2;

/// Rare lines that are two edits from a stronger line, mapped to it.
///
/// The target is the *stronger* spelling: more successes, then more runs, then
/// more recent — and on a full tie, the all-lowercase spelling stands, byte
/// order otherwise. The population both sides come from is `lines` (None =
/// every command the engine holds).
pub fn shadow_map(eng: &Engine, lines: Option<&[String]>) -> HashMap<String, String> {
    let names: Vec<String> = match lines {
        None => eng.cmds.keys().cloned().collect(),
        Some(ls) => ls.iter().filter(|c| eng.cmds.contains_key(*c)).cloned().collect(),
    };
    // The comparison runs on lowered copies; every key returned is the store's
    // own spelling.
    let low: HashMap<String, String> =
        names.iter().map(|c| (c.clone(), c.to_lowercase())).collect();
    // Per-line letter sets and counts: the two cheap gates before the edit
    // distance. Two lines within two edits can differ by at most two distinct
    // letters per side and by at most two in character surplus per side.
    let sets: HashMap<String, HashSet<char>> =
        names.iter().map(|c| (c.clone(), low[c].chars().collect())).collect();
    let mut counts: HashMap<String, HashMap<char, i64>> = HashMap::new();
    for c in &names {
        let mut bag: HashMap<char, i64> = HashMap::new();
        for ch in low[c].chars() {
            *bag.entry(ch).or_insert(0) += 1;
        }
        counts.insert(c.clone(), bag);
    }
    // The evidence each line carries: (successes, runs, recency).
    let ev_of: HashMap<String, (i64, i64, i64)> = names
        .iter()
        .map(|c| {
            let st = &eng.cmds[c];
            (c.clone(), (st.success, st.freq, st.last_ts))
        })
        .collect();
    // Groups by lowered first word, then by length.
    let mut groups: HashMap<String, HashMap<usize, Vec<String>>> = HashMap::new();
    for c in &names {
        let first = low[c].splitn(2, ' ').next().unwrap_or("").to_string();
        groups
            .entry(first)
            .or_default()
            .entry(c.len())
            .or_default()
            .push(c.clone());
    }
    // The full-tie spelling to stand: all-lowercase first, byte order otherwise.
    let tkey: HashMap<String, (i64, &str)> = names
        .iter()
        .map(|c| {
            if low[c] == *c {
                (c.clone(), (0, c.as_str()))
            } else {
                (c.clone(), (1, c.as_str()))
            }
        })
        .collect();
    // Every bucket is walked strongest-first, ties by the likelier spelling.
    for bucket_lists in groups.values_mut() {
        for members in bucket_lists.values_mut() {
            members.sort_by(|a, b| {
                let ea = ev_of[a];
                let eb = ev_of[b];
                eb.0.cmp(&ea.0)
                    .then(eb.1.cmp(&ea.1))
                    .then(eb.2.cmp(&ea.2))
                    .then(tkey[a].cmp(&tkey[b]))
            });
        }
    }
    let mut out: HashMap<String, String> = HashMap::new();
    for cmd in &names {
        let st = &eng.cmds[cmd];
        if st.freq > TYPO_FREQ_MAX {
            continue;
        }
        let first = low[cmd].splitn(2, ' ').next().unwrap_or("").to_string();
        let buckets = match groups.get(&first) {
            Some(b) => b,
            None => continue,
        };
        let s_set = &sets[cmd];
        let s_bag = &counts[cmd];
        let ev = ev_of[cmd];
        let len_cmd = cmd.len();
        let mut best: Option<String> = None;
        let mut best_oev: Option<(i64, i64, i64)> = None;
        let lo = len_cmd.saturating_sub(2);
        for ln in lo..=(len_cmd + 2) {
            let members = match buckets.get(&ln) {
                Some(m) => m,
                None => continue,
            };
            for other in members {
                if other == cmd {
                    continue;
                }
                let oev = ev_of[other];
                // The target must be the stronger spelling — or, on a full
                // tie, the likelier spelling. The bucket is ordered
                // strongest-first, so the first candidate that fails here
                // fails for every candidate after it too.
                if oev < ev || (oev == ev && tkey[other] > tkey[cmd]) {
                    break;
                }
                let o_set = &sets[other];
                if (o_set.difference(s_set).count() > 2)
                    || (s_set.difference(o_set).count() > 2)
                {
                    continue;
                }
                let o_bag = &counts[other];
                let mut surplus: i64 = 0;
                let mut overflow = false;
                for (ch, have) in s_bag.iter() {
                    let extra = have - o_bag.get(ch).copied().unwrap_or(0);
                    if extra > 0 {
                        surplus += extra;
                        if surplus > 2 {
                            overflow = true;
                            break;
                        }
                    }
                }
                if overflow
                    || surplus > 2
                    || surplus - (len_cmd as i64 - other.len() as i64) > 2
                {
                    continue;
                }
                if lev1(&low[cmd], &low[other], 2) > 2 {
                    continue;
                }
                // Strongest-first order says this survivor is the best target
                // its own bucket holds; the five length buckets are still
                // compared against each other.
                let take = match (&best, &best_oev) {
                    (None, _) => true,
                    (Some(b), Some(boev)) => {
                        oev > *boev || (oev == *boev && tkey[other] < tkey[b])
                    }
                    _ => false,
                };
                if take {
                    best = Some(other.clone());
                    best_oev = Some(oev);
                }
                break;
            }
        }
        if let Some(b) = best {
            out.insert(cmd.clone(), b);
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::engine::{CmdStats, Engine};

    /// Textbook Levenshtein: the full matrix, no cap, no band — the
    /// reference the banded walk must be equal to wherever the truth is
    /// under the cap.
    fn reference_distance(a: &str, b: &str) -> usize {
        let a: Vec<char> = a.chars().collect();
        let b: Vec<char> = b.chars().collect();
        let (la, lb) = (a.len(), b.len());
        let mut prev: Vec<usize> = (0..=lb).collect();
        for i in 1..=la {
            let mut cur = vec![i; lb + 1];
            let ca = a[i - 1];
            for j in 1..=lb {
                let cost = if ca == b[j - 1] { 0 } else { 1 };
                cur[j] = (prev[j] + 1).min(cur[j - 1] + 1).min(prev[j - 1] + cost);
            }
            prev = cur;
        }
        prev[lb]
    }

    struct Rng(u64);
    impl Rng {
        fn next(&mut self) -> u64 {
            self.0 = self
                .0
                .wrapping_mul(6364136223846793005)
                .wrapping_add(1442695040888963407);
            self.0 >> 33
        }
        fn below(&mut self, n: usize) -> usize {
            (self.next() % n as u64) as usize
        }
    }

    #[test]
    fn banded_walk_agrees_with_the_matrix() {
        let mut rng = Rng(20261010);
        let alphabet = ['a', 'b', 'c', 'd', 'e'];
        let mut words: Vec<String> = Vec::new();
        for _ in 0..2500 {
            let len = 1 + rng.below(14);
            let w: String = (0..len)
                .map(|_| alphabet[rng.below(alphabet.len())])
                .collect();
            words.push(w);
        }
        let mut pairs: Vec<(String, String)> = Vec::new();
        for _ in 0..4000 {
            let a = words[rng.below(words.len())].clone();
            let b = words[rng.below(words.len())].clone();
            pairs.push((a, b));
        }
        // Deliberate near-duplicates, built by one or two edits.
        for _ in 0..4000 {
            let w = words[rng.below(words.len())].clone();
            let mut chars: Vec<char> = w.chars().collect();
            let roll = rng.next() % 100;
            if roll < 30 && !chars.is_empty() {
                let at = rng.below(chars.len());
                chars[at] = alphabet[rng.below(alphabet.len())];
            } else if roll < 60 {
                let at = rng.below(chars.len() + 1);
                chars.insert(at, alphabet[rng.below(alphabet.len())]);
            } else if !chars.is_empty() {
                let at = rng.below(chars.len());
                chars.remove(at);
            }
            let b = words[rng.below(words.len())].clone();
            pairs.push((chars.into_iter().collect(), b));
        }
        // Words the real history holds: long, mixed case, punctuation, unicode.
        let real = [
            "git commit -m 'fix the thing'",
            "docker compose up -d --build",
            "sudo systemctl restart nginx",
            "ls -la ~/Downloads",
            "python3 -m venv .venv",
            "héllo wörld",
            "日本語のコマンド",
            "grep -rn 'TODO\\|FIXME' src/",
            "git push --force-with-lease origin main",
        ];
        for a in &real {
            for b in &real {
                pairs.push((a.to_string(), b.to_string()));
            }
            pairs.push((a.to_string(), a.chars().rev().collect::<String>()));
        }
        for (a, b) in &pairs {
            let want = reference_distance(a, b);
            for md in [1usize, 2, 3] {
                let got = lev1(a, b, md);
                if want <= md {
                    assert_eq!(got, want, "exact {a:?} vs {b:?} cap {md}");
                } else {
                    assert!(got > md, "over-cap {a:?} vs {b:?} cap {md} (true {want})");
                }
                assert!(got <= md + 1, "cap {a:?} vs {b:?} cap {md}");
            }
        }
    }

    #[test]
    fn the_shapes_every_caller_relies_on() {
        assert_eq!(lev1("docker", "docker", 2), 0);
        assert_eq!(lev1("", "git", 2), 3);
        assert_eq!(lev1("git", "", 2), 3);
        assert_eq!(lev1("a", "abcdef", 2), 3);
        assert_eq!(lev1("ab", "abcd", 2), 2);
        assert_eq!(lev1("héllo", "hello", 2), 1);
        assert_eq!(lev1("ab", "ba", 2), 2);
        assert_eq!(lev1("aaaa", "bbbb", 1), 2);
    }

    fn eng_with(rows: &[(&str, i64, i64)]) -> Engine {
        let mut eng = Engine::new();
        for (i, (cmd, freq, success)) in rows.iter().enumerate() {
            for _ in 0..*freq {
                eng.add(cmd, "", "", "", if *success > 0 { 0 } else { 1 }, Some(1700000000 + i as i64), false);
            }
        }
        eng
    }

    #[test]
    fn a_one_off_ranks_below_its_habit() {
        let mut eng = eng_with(&[
            ("tai uninstall", 3, 1),
            ("tai unsintall", 1, 1),
            ("tai unisntall", 1, 1),
        ]);
        let names: Vec<String> = eng.cmds.keys().cloned().collect();
        let shadows = shadow_map(&eng, Some(&names));
        assert_eq!(shadows.get("tai unsintall").map(|s| s.as_str()), Some("tai uninstall"));
        assert_eq!(shadows.get("tai unisntall").map(|s| s.as_str()), Some("tai uninstall"));
        // And the suggest path demotes the shadow below the habit:
        let res = eng.suggest("tai un", "", "", "", &[], 1, 0.9, None, false, &mut |_| false, None);
        assert_eq!(res.choice, "tai uninstall", "the habit, not the one-off");
    }

    #[test]
    fn a_habit_is_never_demoted() {
        let eng = eng_with(&[("docker build", 1, 1), ("docker buil", 1, 1)]);
        let names: Vec<String> = eng.cmds.keys().cloned().collect();
        let shadows = shadow_map(&eng, Some(&names));
        // Same evidence, full tie: the all-lowercase spelling stands, and the
        // capitalised twin is the accident. Here both are lowercase and the
        // map picks the byte-order winner — but a habit of 3 runs is above
        // TYPO_FREQ_MAX and shadows nothing.
        let _ = shadows;
        let eng = eng_with(&[("docker build", 3, 1), ("docker builx", 1, 1)]);
        let names: Vec<String> = eng.cmds.keys().cloned().collect();
        let shadows = shadow_map(&eng, Some(&names));
        assert_eq!(shadows.get("docker builx").map(|s| s.as_str()), Some("docker build"));
        // The habit is never a key.
        assert!(!shadows.contains_key("docker build"));
    }

    #[test]
    fn case_folds_like_every_lookup() {
        let eng = eng_with(&[("ls -la", 3, 1), ("LS -la", 1, 1)]);
        let names: Vec<String> = eng.cmds.keys().cloned().collect();
        let shadows = shadow_map(&eng, Some(&names));
        assert_eq!(shadows.get("LS -la").map(|s| s.as_str()), Some("ls -la"));
    }

    #[test]
    fn near_miss_answers_the_word_being_typed() {
        let mut eng = eng_with(&[("docker ps", 2, 1), ("docker images", 1, 1)]);
        let out = near_miss_lines(&mut eng, "dokcer ps", 50);
        assert_eq!(out, vec!["docker ps".to_string()]);
        // Half-typed is not a licence to invent: nothing begins with godx.
        let out = near_miss_lines(&mut eng, "godx", 50);
        assert!(out.is_empty());
        // A word shorter than three is not a typo question.
        let out = near_miss_lines(&mut eng, "do", 50);
        assert!(out.is_empty());
    }
}
