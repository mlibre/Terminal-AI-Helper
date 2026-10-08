# No-retraining Jev-ify — turn any open LLM into a local decision engine

Source: Avi Chawla "Build your own Jev (100% local)" (SGLang `/v1/score`, Qwen/DeepSeek) + Akshay "Jev Clearly Explained" (positioning/rollout). Use this when you want Jev-shaped decisions **without training** — single forward pass, read next-token logits, softmax over declared labels.

> Scope: this recreates the **inference path only** — not Jev weights, RLCD calibration, or eval stack. Scores are restricted-softmax shares among your labels, **not** P(correct). You still need labeled examples to fit thresholds/temperature.

## 1. When scoring applies (and when it doesn't)

- Compatible: finite label set known **before** inference, each label → distinct downstream action, caller needs only `label + distribution`, not new text. (Support routing, risk gating, rerank, guardrails.)
- Not compatible: output content unknown beforehand (summarize, draft, extract-unknown-value). That's generation territory.
- **Scoring ≠ structured output.** Structured output (`{"team":"billing"}`) still autoregressively generates `{`, field name, value, `}` token-by-token, then you parse the field. Scoring supplies the full outcome list up front; the server reads one score per outcome and returns the distribution — **zero generated tokens, nothing to parse**.

Choose by required output: **generate** when content unknown; **score** when set known and selection suffices (first next-token vector already holds the ranking).

## 2. How first-token scoring works

1. Tokenizer → prompt to IDs. Model processes sequence → one vocab-sized logit vector for the next position (tens of thousands of entries for Qwen).
2. Normal generation applies temperature/decoding, picks one token, repeats. **Scoring stops at this first vector.**
3. Prompt assigns one short label per answer and ends at the answer position:
   ```
   A = billing questions and payment problems
   B = product errors and technical failures
   C = login, password, and account access problems
   ...
   Label:
   ```
4. Read logits at A/B/C positions only, ignore the rest, softmax over the selected values (e.g. `8.2, 5.5, 4.8 → 0.91, 0.06, 0.03`). That is "how the model divides preference among A/B/C **after you ruled everything else out**" — not P(A) over the whole vocab.

Same-ticket example for thresholds-in-code: `0.91/0.06/0.03` (clear → auto-route) vs `0.46/0.44/0.10` (tie → review). Both pick billing; code treats them differently, e.g. require top > 0.80 **and** margin > 0.20.

## 3. Why single-token labels (A/B/C, not words)

- Visible words ≠ one token: "billing" may be 1 token on one tokenizer and several on another; "technical support" always spans positions. Multi-word comparison needs sequence scoring (score token, append, score next, combine + length effects).
- Single-token labels dodge this: every option is one vocab entry at the same output position; semantics stay in the prompt descriptions.
- Verify each label is exactly one token **on the serving model's tokenizer**:
  - `"A"` vs `" A"` can be different IDs (leading-space encoding). Chat template may insert whitespace/control tokens before the answer.
  - Render the full prompt with the model's chat template, determine the exact continuation at the answer slot, send it to `/tokenize`, reject anything ≠ 1 token. Re-check per model — a label valid on Qwen may split elsewhere.
- Keep label mapping inside the scoring client: app sends `billing`/`technical_support`, never token IDs, never sees A/B/C. Public API stays model-independent.
- Always add an escape hatch (`OTHER`/`ESCALATE`) when the list may not cover the input — restricted softmax otherwise forces all mass onto wrong choices (e.g. security incident into billing/tech/account).

## 4. SGLang recipe (Qwen2.5-0.5B-Instruct, port 30000)

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install "sglang[all]==0.5.10.post1" "requests==2.34.2"
python -m sglang.launch_server --model-path Qwen/Qwen2.5-0.5B-Instruct --host 127.0.0.1 --port 30000
# keep running; first launch downloads from HF, later reuses cache
```

`decide.py` (5 steps):
```python
import json, requests
BASE_URL = "http://127.0.0.1:30000"
MODEL = "Qwen/Qwen2.5-0.5B-Instruct"
choices = {"A": "billing and payments", "B": "technical support", "C": "account access"}
ticket = "I was charged twice for the same subscription."
prompt = f"Ticket:\n{ticket}\n\nQuestion:\nWhich category matches the ticket?\n\nAllowed labels:\n" + \
    "\n".join(f"{l} = {m}" for l, m in choices.items()) + "\n\nReturn only the label.\nLabel:\n"

# 1. resolve label token IDs (exactly one token each)
label_token_ids = []
for label in choices:
    r = requests.post(f"{BASE_URL}/tokenize", json={"model": MODEL, "prompt": label, "add_special_tokens": False}, timeout=30)
    r.raise_for_status()
    toks = r.json()["tokens"]
    if len(toks) != 1: raise ValueError(f"{label!r} is not a single token: {toks}")
    label_token_ids.append(toks[0])  # Qwen2.5-0.5B: A->32, B->33, C->34

# 2. score position right after prompt; empty items = immediate next position
r = requests.post(f"{BASE_URL}/v1/score", json={"model": MODEL, "query": prompt, "items": [""],
    "label_token_ids": label_token_ids, "apply_softmax": True}, timeout=120)
r.raise_for_status()
scores = r.json()["scores"][0]  # order follows label_token_ids; e.g. [0.678, 0.311, 0.011]

probabilities = {choices[l]: float(s) for l, s in zip(choices, scores, strict=True)}
print(json.dumps({"decision": max(probabilities, key=probabilities.get), "probabilities": probabilities}, indent=2))
# billing wins at 0.678 here → a 0.70 policy would send to review, not auto-route
```

Demo lanes for benchmarking (same engine, same GPU): Jev-lane `engine.decide()` → `/v1/score`, no generated tokens; LLM-lane `engine.generate_response(..., max_tokens=32)` → `/v1/chat/completions` + parse first 100 chars for a choice name. Fair-harness notes: release both workers behind one `threading.Barrier(2)`, each lane sequential, concurrent via continuous batching on shared GPU/mem/scheduler — don't fire all 200 at once. Caveat from the source video: footage sped up after 8s makes LLM lane look faster than it is.

## 5. Calibration warning (do not skip)

Restricted-softmax value = share of mass among **your** choices. It does **not** prove correctness rate. Fit thresholds (top-p, margin) and temperature on labeled examples including ambiguous/adversarial cases; plot confidence vs accuracy before automating. See `training-guide.md` §2 (Brier/ECE) for the full calibration story.
