//! Evaluate autocomplete candidates against labeled terminal examples.
//!
//! Port of `tai/evaluate.py`. Keep this small and deterministic: it is an
//! application-level benchmark, not an LLM benchmark.

const CASES: &[(&str, &str, &[&str])] = &[
    // (prefix, expect, reject) — expect empty means reject-only case.
    ("git st", "git status", &[]),
    ("git sw", "git switch", &[]),
    ("docker co", "docker compose up -d", &[]),
    ("python3 -m py", "python3 -m pytest", &[]),
    // A prefix that is already a whole command must not "complete" to itself,
    // and must not be answered with a command whose paths are gone.
    ("ls -l", "ls -la", &[]),
    ("cd ~/", "", &["cd ~/projects/myapp"]),
];

pub fn main() -> i32 {
    let mut passed = 0;
    for (prefix, expect, reject) in CASES {
        let history = crate::predictor::suggest(prefix, "", "", "", &[], 8, None);
        let mut candidates: Vec<String> = history
            .get("choices")
            .and_then(|c| c.as_array())
            .map(|a| {
                a.iter()
                    .filter_map(|c| c.get("cmd").and_then(|c| c.as_str()).map(|s| s.to_string()))
                    .collect()
            })
            .unwrap_or_default();
        candidates.extend(crate::knowledge::candidates_for_prefix(prefix, &[], 8));
        if !reject.is_empty() {
            let bad: Vec<&str> = reject.iter().filter(|r| candidates.contains(&r.to_string())).copied().collect();
            let ok = bad.is_empty();
            passed += ok as i32;
            println!(
                "{} {} -> must not offer {:?} got={:?}",
                if ok { "PASS" } else { "FAIL" },
                prefix,
                reject,
                candidates.iter().take(5).collect::<Vec<_>>()
            );
            continue;
        }
        let found = candidates.iter().any(|c| c == expect);
        passed += found as i32;
        println!(
            "{} {} -> {} got={:?}",
            if found { "PASS" } else { "FAIL" },
            prefix,
            expect,
            candidates.iter().take(5).collect::<Vec<_>>()
        );
    }
    println!("eval: {}/{} exact-candidate coverage", passed, CASES.len());
    if passed == CASES.len() as i32 {
        0
    } else {
        1
    }
}
