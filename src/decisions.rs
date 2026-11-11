//! Jev-style bounded decision layer for terminal candidates.
//!
//! Port of `tai/decisions.py`. This module does not call an LLM. It exposes
//! the same contract used for a local or hosted System One model: candidate
//! Choice + safety Noul + show-now Noul. Deterministic code owns
//! safety-critical checks; a model may rerank candidates later.

/// 1.0 when `cmd` is destructive, else 0.0. Deterministic, in code, on
/// purpose.
///
/// A model is allowed to rerank candidates. It is not allowed to decide what
/// is dangerous, because that decision is only as trustworthy as the answer,
/// and the answer is text off a network. **A false positive is cheap here and
/// a false negative is not** — this is a substring match over the whole line,
/// so the rules below are deliberately blunt.
pub fn unsafe_score(cmd: &str) -> f64 {
    use regex::Regex;
    use std::sync::OnceLock;
    static PATTERNS: OnceLock<Vec<Regex>> = OnceLock::new();
    let patterns = PATTERNS.get_or_init(|| {
        [
            // Any short-flag group holding r/R: `-r`, `-rf`, `-fr`, `-rfz` —
            // and the long forms, which read as *safer* and are the same
            // command.
            r"\brm\s+(-{1,2}[^\s]+\s+)*-{1,2}[^\s]*[rR][^\s]*\b",
            r"\brm\s+(-{1,2}[^\s]+\s+)*--\s*(recursive|force|no-preserve-root)\b",
            r"\bgit\s+reset\s+--hard\b",
            r"\bgit\s+clean\b.*-[^\s]*f",
            r"\bmkfs\b",
            r"\bdd\s+if=",
            r":\(\)\s*\{.*\};?",
            r"\bshutdown\b",
            r"\breboot\b",
            r"\bkill(all|\s+(-[a-zA-Z0-9]+|\d+))\b",
            r"\bterraform\s+destroy\b",
            r"\bkubectl\s+delete\b",
            r"\bhelm\s+uninstall\b",
            r"\bdocker\s+(system|volume|image|container|network)\s+prune\b",
            r"\bdocker\s+(rm|rmi)\s+(-[^\s]+\s+)*-[a-zA-Z]*f",
        ]
        .iter()
        .map(|p| Regex::new(p).unwrap())
        .collect()
    });
    let s = cmd.to_lowercase();
    if patterns.iter().any(|p| p.is_match(&s)) {
        1.0
    } else {
        0.0
    }
}

/// The bounded reranker's own decision.
#[derive(Clone, Debug)]
pub struct Decision {
    pub kind: String,
    pub choice: String,
    pub probabilities: Vec<(String, f64)>,
    pub confidence: f64,
    pub show_now: f64,
    pub destructive: f64,
}

/// Bounded reranker. `candidates` must already be generated locally.
pub fn decide(
    candidates: &[String],
    scores: Option<&HashMap61>,
    typed_prefix: &str,
    typed_seconds: f64,
) -> Decision {
    let mut seen = std::collections::HashSet::new();
    let candidates: Vec<String> = candidates
        .iter()
        .filter(|c| seen.insert((*c).clone()))
        .take(32)
        .cloned()
        .collect();
    if candidates.is_empty() {
        return Decision {
            kind: "choice".to_string(),
            choice: String::new(),
            probabilities: Vec::new(),
            confidence: 0.0,
            show_now: 0.0,
            destructive: 0.0,
        };
    }
    let empty: HashMap61 = HashMap61::new();
    let scores = scores.unwrap_or(&empty);
    let vals: Vec<f64> = candidates.iter().map(|c| scores.get(c).copied().unwrap_or(0.0)).collect();
    let m = vals.iter().cloned().fold(0.0f64, f64::max);
    // min(20, ...) keeps one runaway score from overflowing; the shift by the
    // maximum is what makes the result a distribution rather than a magnitude.
    let exps: Vec<f64> = vals.iter().map(|v| 20f64.min(v - m).exp()).collect();
    let total: f64 = exps.iter().sum::<f64>().max(1e-12);
    let mut probs: Vec<(String, f64)> =
        candidates.iter().cloned().zip(exps.iter().map(|e| e / total)).collect();
    let best = probs
        .iter()
        .max_by(|a, b| a.1.partial_cmp(&b.1).unwrap_or(std::cmp::Ordering::Equal))
        .map(|(c, _)| c.clone())
        .unwrap_or_default();
    let best_prob = probs.iter().find(|(c, _)| *c == best).map(|(_, p)| *p).unwrap_or(0.0);
    let mut others: Vec<f64> = probs.iter().map(|(_, p)| *p).collect();
    others.sort_by(|a, b| b.partial_cmp(a).unwrap_or(std::cmp::Ordering::Equal));
    let second = if others.len() > 1 { others[1] } else { 0.0 };
    let conf = (0.35 + (best_prob - second) * 2.2).min(1.0);
    let destructive = unsafe_score(&best);
    let mut show = if !typed_prefix.is_empty() && typed_seconds >= 0.08 && conf >= 0.5 {
        1.0
    } else {
        0.0
    };
    if destructive != 0.0 {
        show *= 0.25;
    }
    // Python keeps the probabilities dict in insertion (candidate) order.
    probs = candidates
        .iter()
        .cloned()
        .map(|c| {
            let p = probs.iter().find(|(k, _)| *k == c).map(|(_, p)| *p).unwrap_or(0.0);
            (c, p)
        })
        .collect();
    Decision {
        kind: "choice".to_string(),
        choice: best,
        probabilities: probs,
        confidence: (conf * 1000.0).round() / 1000.0,
        show_now: show,
        destructive,
    }
}

pub type HashMap61 = std::collections::HashMap<String, f64>;

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_blunt_patterns_cover_the_spellings() {
        for cmd in [
            "rm -rf /",
            "rm -fr /",
            "rm --recursive --force /",
            "rm -r /tmp/x",
            "git reset --hard HEAD~1",
            "git clean -fd",
            "mkfs.ext4 /dev/sda",
            "dd if=/dev/zero of=/dev/sda",
            "shutdown -h now",
            "reboot",
            // A substring over the whole line: the quote does not save it,
            // and the one cost of a false positive is that the line is not
            // *offered*.
            "echo 'shutdown'",
            "kill -9 1234",
            "terraform destroy",
            "kubectl delete pod x",
            "helm uninstall x",
            "docker system prune -af",
            "docker volume prune",
            "docker rm -f x",
        ] {
            assert_eq!(unsafe_score(cmd), 1.0, "{} is destructive", cmd);
        }
        for cmd in [
            "ls -la",
            "rm /tmp/one-file", // a removal that names no force
            "git reset HEAD~1", // mixed, not --hard
            "git clean -n",
            "docker ps",
            // `pkill` is a word of its own: the boundary before `kill` is
            // the `p`, and the pattern is kill — the same answer Python
            // gives, blunt and auditable.
            "pkill -f server",
            "git status",
        ] {
            assert_eq!(unsafe_score(cmd), 0.0, "{} is not destructive", cmd);
        }
    }

    #[test]
    fn the_reranker_never_invents_and_gates_what_it_chose() {
        let d = decide(
            &["git status".to_string(), "git reset --hard".to_string()],
            None,
            "git re",
            0.2,
        );
        assert!(d.probabilities.iter().any(|(c, _)| c == "git status"));
        assert!(!d.choice.is_empty());
        // Whatever it chose, the destructive grade rides beside the choice:
        // the model's own opinion is not trusted, this one is.
        assert_eq!(d.destructive, unsafe_score(&d.choice));
        // A destructive choice is shown far less readily.
        let hot = decide(&["rm -rf /".to_string()], None, "rm ", 1.0);
        if hot.choice == "rm -rf /" {
            assert!(hot.show_now < 1.0, "a destructive pick is damped");
        }
        // Duplicates collapse; an empty list answers nothing.
        assert!(decide(&[], None, "", 0.0).choice.is_empty());
        let d = decide(
            &["git status".to_string(), "git status".to_string()],
            None,
            "",
            0.0,
        );
        assert_eq!(d.probabilities.len(), 1);
    }
}
