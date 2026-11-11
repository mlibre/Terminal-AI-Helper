//! Optional Jev System One adapter for bounded terminal decisions.
//!
//! Port of `tai/jev.py`. Jev is used as a semantic judge/reranker, never as a
//! command generator. The normal embedded autocomplete remains local and
//! sub-millisecond. Call this module only for ambiguous/missing-candidate
//! cases or explicitly with `tai suggest --jev`.
//!
//! Environment:
//!     TYPESAFE_API_KEY       required for hosted Jev
//!     TYPESAFE_BASE_URL      default https://api.typesafe.ai
//!     TYPESAFE_MODEL         default jev-latest
//!     TAI_JEV_TIMEOUT        default 1.5 seconds

use serde_json::json;

fn base_url() -> String {
    std::env::var("TYPESAFE_BASE_URL")
        .unwrap_or_else(|_| "https://api.typesafe.ai".to_string())
        .trim_end_matches('/')
        .to_string()
}

fn model() -> String {
    std::env::var("TYPESAFE_MODEL").unwrap_or_else(|_| "jev-latest".to_string())
}

fn timeout() -> f64 {
    std::env::var("TAI_JEV_TIMEOUT")
        .ok()
        .and_then(|v| v.parse().ok())
        .unwrap_or(1.5)
}

#[derive(Clone, Debug, Default)]
pub struct JevResult {
    pub choice: String,
    pub probabilities: Vec<(String, f64)>,
    pub confidence: f64,
    pub show_now: f64,
    pub destructive: f64,
    pub model: String,
    pub usage: serde_json::Value,
    pub raw: serde_json::Value,
}

/// Keep the rubric in one place so it is reviewable and versionable.
fn question_payload(candidates: &[String]) -> serde_json::Value {
    let mut criteria = serde_json::Map::new();
    for c in candidates {
        criteria.insert(
            c.clone(),
            json!(format!("Terminal completion candidate: {}", c)),
        );
    }
    criteria.insert("NONE".to_string(), json!("No listed candidate fits the context."));
    json!({
        "best_candidate": {
            "type": "choice",
            "instructions": (
                "Which terminal command candidate best matches what the user is \
                 likely trying to do? Use only the candidates. If none is \
                 appropriate, choose NONE."
            ),
            "criteria": criteria,
        },
        "show_now": {
            "type": "noul",
            "instructions": (
                "Is the selected candidate useful and sufficiently certain to \
                 show as an immediate ghost-text completion?"
            ),
            "criteria": {
                "true": "The candidate is relevant, plausible, and likely to help.",
                "false": "The candidate is weak, ambiguous, unsupported, or too uncertain.",
            },
        },
        "destructive": {
            "type": "noul",
            "instructions": (
                "Does the selected candidate risk destructive effects such as \
                 deleting data, resetting state, terminating processes, or \
                 destroying infrastructure?"
            ),
            "criteria": {
                "true": "The candidate may delete, destroy, reset, terminate, or overwrite important state.",
                "false": "The candidate is read-only or has no material destructive effect.",
            },
        },
    })
}

fn answer_value(answer: &serde_json::Value, key: &str, default: serde_json::Value) -> serde_json::Value {
    if !answer.is_object() {
        return default;
    }
    answer.get(key).cloned().unwrap_or(default)
}

/// Run one batched Jev request containing all bounded decisions.
pub fn decide(
    prefix: &str,
    candidates: &[String],
    cwd: &str,
    repo: &str,
    branch: &str,
    previous: &str,
) -> Result<JevResult, String> {
    let mut seen = std::collections::HashSet::new();
    let candidates: Vec<String> = candidates
        .iter()
        .filter(|c| !c.is_empty() && seen.insert((*c).clone()))
        .take(64)
        .cloned()
        .collect();
    if candidates.is_empty() {
        return Ok(JevResult::default());
    }
    let state = json!({
        "typed_prefix": prefix,
        "current_directory": cwd,
        "repository": repo,
        "branch": branch,
        "previous_command": previous,
        "candidate_commands": candidates,
    });
    let payload = json!({
        "state": state,
        "model": model(),
        "questions": question_payload(&candidates),
    });
    let key = std::env::var("TYPESAFE_API_KEY").unwrap_or_default();
    if key.is_empty() {
        return Err("TYPESAFE_API_KEY is not set".to_string());
    }
    let url = format!("{}/v1/systemone", base_url());
    let body = serde_json::to_string(&payload).map_err(|e| e.to_string())?;
    let agent = ureq::AgentBuilder::new()
        .timeout(std::time::Duration::from_secs_f64(timeout().max(0.05)))
        .build();
    let raw: serde_json::Value = agent
        .post(&url)
        .set("Authorization", &format!("Bearer {}", key))
        .set("Content-Type", "application/json")
        .send_string(&body)
        .map_err(|e| match e {
            ureq::Error::Status(code, resp) => {
                let detail = resp.into_string().unwrap_or_default();
                let detail: String = detail.chars().take(300).collect();
                format!("Jev HTTP {}: {}", code, detail)
            }
            other => format!("Jev unavailable: {}", other),
        })?
        .into_json()
        .map_err(|e| format!("Jev unavailable: {}", e))?;

    let answers = raw.get("answers").cloned().unwrap_or(json!({}));
    let best = answers.get("best_candidate").cloned().unwrap_or(json!({}));
    let mut choice =
        answer_value(&best, "choice", json!("NONE")).as_str().unwrap_or("NONE").to_string();
    let probs_raw = answer_value(&best, "probabilities", json!({}));
    let mut probs: Vec<(String, f64)> = if probs_raw.is_object() {
        probs_raw
            .as_object()
            .unwrap()
            .iter()
            .filter_map(|(k, v)| v.as_f64().map(|p| (k.clone(), p)))
            .collect()
    } else {
        Vec::new()
    };
    let mut conf = answer_value(&best, "confidence", json!(0.0)).as_f64().unwrap_or(0.0);
    let mut show = answer_value(&answers.get("show_now").cloned().unwrap_or(json!({})), "noul", json!(0.0))
        .as_f64()
        .unwrap_or(0.0);
    let destructive = answer_value(&answers.get("destructive").cloned().unwrap_or(json!({})), "noul", json!(0.0))
        .as_f64()
        .unwrap_or(0.0);
    if choice == "NONE" {
        choice = String::new();
        conf = 0.0;
        show = 0.0;
    } else if !candidates.contains(&choice) {
        // The model may only choose from the candidates it was shown. Anything
        // else is invented text, and it is discarded here rather than at each
        // caller.
        choice = String::new();
        probs = Vec::new();
        conf = 0.0;
        show = 0.0;
    }
    Ok(JevResult {
        choice,
        probabilities: probs,
        confidence: conf,
        show_now: show,
        destructive,
        model: raw
            .get("model")
            .and_then(|m| m.as_str())
            .unwrap_or(&model())
            .to_string(),
        usage: raw.get("usage").cloned().unwrap_or(json!({})),
        raw,
    })
}
