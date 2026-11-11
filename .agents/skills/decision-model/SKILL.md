---
name: Decision Model
description: Build fast, type-safe AI decisions with decision models (a.k.a. System One models) - Choice, Score, Noul questions, confidence routing, no-retraining logit scoring via SGLang/vLLM, broad Decision Index 0.2.1 + JevBench v1.5.5 model selection, running the same questions through an LLM as a baseline, training your own decision-model scorer (RLCD, Tev1 $17, contrastive data, LoRA/slot-head recipes), and local decision-model-compatible alters (Cygnet, Winnow, Jev-Omni, Rune, decider, xor, JevK5, AutoJev, OpenThai, Julia-1, Laya, GLiNER2.5-Decide, Kev, NeoHorse-Jev-4B, von, Nimble, SemIf/OpenJev, reflex, djev, open-jev, jevify, jevons, JevAny, JevForge, CLM, Tev1, DiffusionGemma-as-decision-model).
---

# Decision Model Skill

Use this skill when the task needs a **fast, structured decision from unstructured text** that code can branch on — routing, classification, scoring, yes/no checks, guardrails, reranking, verification — not chat, code-gen, or free-text generation.

A **decision model** (TypeSafe calls the class *System One*) takes `state + typed questions` and returns typed answers + probabilities + confidence. No text generation. No JSON parsing. Code calculates, the decision model judges, reasoning models reason and generate — code owns control flow/side effects, the model supplies narrow semantic judgments. Jev is TypeSafe's flagship decision model (TypeSafe AI, SF, founded 2024; CEO Diogo Almeida, CTO Erik Gafni, COO Sasha Sheng — reported) and the first System One model, launched Sept 15 2026 limited early access with ~$40M seed led by DCVC (reported). Mental model: System One = fast gut-check judgments for software (cf. Kahneman System 1 snap judgment vs System 2 step-by-step reasoning traces); a decision model is the machine-native-intelligence bet that ~99% of AI calls in software will be narrow, typed, calibrated decisions rather than chat. It is trained with RLCD (reinforcement learning for calibrated decisions) so probabilities track outcomes, instead of optimizing for human preference the way RLHF/RLVR do. Founder framing: "a frontier-intelligence function call: unstructured state in, typed probabilistic decisions out".

> Positioning: Jev fits bounded-output + semantic + fast-judgment tasks (classify, detect, score, route, verify, rank — what an expert decides in seconds). Open-output or deliberative work (summarize, draft, research, strategy) stays with LLMs/humans. If Q-B depends on Q-A's answer, bake A into a second request's state.

> TypeSafe's "cannot hallucinate" = **schema-valid by construction** (fixed output space, no malformed shape possible). It can still return the *wrong* label/score/probability. Validity ≠ correctness.

> Jev is NOT a coding-agent LLM. There is no `model: jev-latest` setting for opencode/Claude Code/Cursor. Use your normal LLM to write code that *calls* Jev for decisions.

**This file was verified on 2026-10-03** against docs.typesafe.ai, TypeSafe's launch post, JevAdvBench (arXiv 2609.31142), OpenClaw's `decisionModel` docs, OpenRouter, the Vercel AI Gateway model list, JevBench v1.5.5 (+ Image v0.1.5, Audio v0.1) and Decision Index 0.2.1. **The live docs win wherever they disagree** — versions, prices and rate limits move, and both benchmark snapshots below are dated files that this project has already had to replace once. Fetch what you need rather than trusting this file for those: `https://docs.typesafe.ai/llms.txt` is the page index, every page is Markdown if you append `.md` (`https://docs.typesafe.ai/models.md`), and `llms-full.txt` is the whole site in one file. TypeSafe also ships its own skill (`typesafe-ai/skills`, MIT) which is better on *architecture* and thinner on the wire contract and the open ecosystem that this file carries; install both if you can: `claude plugin marketplace add typesafe-ai/skills && claude plugin install typesafe@typesafe-ai`, or `npx skills add typesafe-ai/skills`.

## 1. Core contract — memorize this

Endpoint:

```http
POST https://api.typesafe.ai/v1/systemone
Authorization: Bearer $TYPESAFE_API_KEY
Content-Type: application/json
```

Request:

```json
{
  "state": "string | object | array — content to judge",
  "model": "jev-latest",
  "questions": {
    "<your_id>": { "type": "choice|score|noul", "instructions": "...", "criteria": {} }
  }
}
```

- `state`: the material. String for one text, object for named fields/records, array for sequences. Text-only on official Jev — no image, audio or video. English best; other languages including CJK accepted with lower accuracy.
- `model`: **required** by the raw HTTP API (only the SDK defaults it). `jev-latest` (currently `jev-1.13.0`) or `jev-preview`, which today points at the same build — there is no preview build. Pin `jev-1.13.0` if you tuned thresholds. Response echoes versioned ID — log it, because an alias moves on release and a hosted answer can change without one: Decision Index saw Jev's own output drift on 20 choices between two runs days apart.
- `questions`: map you name. IDs are **for your code only, never sent to the model**. Write the full question in `instructions` even if the ID looks obvious.
- Limits (jev-1.13.0): 64k tokens/request total, 32k for `state` + longest question (not 32k flat). **$42/Btok input ($0.042/Mtok), output tokens free.** **Rate limits: 100k tokens/second and 40 requests/second** — the docs say plainly that these are adjusting dynamically and can change without notice, so do not build a queue assumption on them.
- Latency: the docs claim *most queries complete in about 100 ms*. The 70–500 ms range and the headline **193.6× faster / 444.6× cheaper** come from TypeSafe's launch material, not the docs, and they are worth quoting with their own caveats attached: they are **workflow** evals at `evals.typesafe.ai` (four workflows, TypeSafe's own capabilities team) against the **average of GPT-6 Astra and Fable 5.1** driven through TypeSafe's System One LLM wrapper over OpenRouter. TypeSafe itself writes that the reference "biases answers towards OpenAI and Anthropic's models", that OpenRouter routing may favour complex queries, and that they expect these "on the higher end of real world gains". The same post prices it: their Doom demo ran ~10 queries/second at about **$7/hour**.
- Independent measurement, for the shape rather than the marketing: JevBench v1.5.5 measures Jev's own p50 at **616 ms** over HTTPS and its broad-suite accuracy slightly *below* the best open 27B rows, while its calibration is the best in the field. **Judge it on calibration and cost per decision, not on the multipliers.**
- **Above 255 candidates is two requests, not one.** TypeSafe's own high-cardinality demo (Wikiracing: thousands of links per step) scores candidates independently, then makes an explicit choice over the survivors — and notes the occasional slowdown that causes. A shortlist also caps your recall: measure recall@k of the filter separately from the decider.
- Cost is per **question**, not per call: one call with 13 questions over a 54k-character document pays for the document once; 13 single-question calls pay for it 13 times.
- `GET https://api.typesafe.ai/v1/models` lists aliases your key can use (versioned ids work in `model` even when unlisted). Errors: `401` bad key, `422` validation, `429` rate limited, **`529` overloaded** — retry 429/529 with backoff.
- Gateways and frameworks (verified 2026-10-02, full table in `references/api-contract.md`): OpenRouter's id is **`~typesafe/jev-latest`** (leading `~`) and its context is **32k, not 64k**; Vercel AI Gateway is `https://ai-gateway.vercel.sh/typesafe`; Pydantic AI Gateway is `https://gateway-us.pydantic.dev/proxy/typesafe`. First-party SDKs: AI SDK `@ai-sdk/typesafe-ai` (calls Noul `boolean`, key is `TYPESAFE_AI_API_KEY`), LangChain `langchain-typesafe`, Pydantic AI's `TypeSafeModel`, n8n nodes, and `system-one-adapter` — **which runs your exact question set through OpenAI/Anthropic/Gemini instead, the only honest way to answer "is Jev worth it here?"** Point the official SDK at a local clone with `TYPESAFE_BASE_URL`.

Response:

```json
{
  "model": "jev-1.13.0",
  "answers": { "<same_id>": { "type": "...", "...": "..." } },
  "usage": { "input_tokens": 392, "output_tokens": 65 }
}
```

Questions run **in parallel, in isolation**. Adding questions barely changes latency, costs only question tokens. Always batch. Never do one-question-per-call.

## 2. The three primitives

Read `references/api-contract.md` for full request/response schemas.

### Choice — `Which of these?`

```json
"department": {
  "type": "choice",
  "instructions": "Which team should handle this?",
  "criteria": {
    "billing": "Payments, invoicing, refunds",
    "technical": "Bugs, outages, integrations",
    "sales": "Pricing, upgrades, new accounts"
  }
}
```

- `criteria`: map option->description, `null` allowed when name is self-evident. Max **255 options** per Choice.
- Returns `{ type, choice, probabilities:{opt:prob}, confidence: 0..1 }`. Sum(probabilities)=1.
- Add `other` / `none_of_the_above` when list may not cover input. **A Choice always has a winner** — its probabilities sum to 1, so when nothing in the list fits, the top option still ranks first and that is an artefact of normalisation, not an answer. Either give the list an explicit `none` option or ask a separate presence Noul in the same call. TypeSafe's line-by-line search cookbook is built on exactly this pair.
- **The model cannot choose a value you did not offer.** If recall of the candidate set is the real problem, no amount of instruction fixes it. Find candidates in code (regex tuned to over-find, BM25, embeddings), let the Choice pick, and copy the pick verbatim — then the answer is one of your own spans and cannot be invented.
- Wide sets: accuracy drops past ~50 options without tuning (reported; Banking77-77way is the stress case — Jev 0.870 vs Laya 0.425 reported, both from author-run suites rather than an independent board). Mitigate: `predict_shortlist` (embed-filter top-k, one forward pass on shortlist), hierarchy/beam-search, or fine-tune.
- **A shortlist is a ceiling, not a speedup.** Any prefilter/router/beam stage bounds final accuracy by *its own* recall@k, and grouped reranking yields probabilities over the surviving candidates only, never a global distribution. Three independent models now show wide-set collapse (Jev 0.870, Laya 0.425, Julia-1 0.64 *with* a top-16 shortlist). Measure prefilter recall@k separately from decider accuracy, or you will read a prefilter number as a model number. Julia-1's own released metrics price both halves: recall@16 = 0.99 (ceiling) against 0.83 achieved — and a second run of the same suite reports 0.64 at the same ~0.99 recall, so a wide-set number needs its recall@k printed next to it before it means anything.
- Native option caps differ per model and are hard: Jev 255, OpenThai 256, Laya call-time, Julia-1 **20**. Check the cap before porting a question set.
- For deep hierarchies: chain Choices level-by-level, beam-search top-K paths. See Hierarchical Classification cookbook.
- Confusable options: use structured criteria `{ what, not_for, examples }` with same field names across options. See Choice docs.

### Score — `Where on this spectrum?`

```json
"frustration": {
  "type": "score",
  "instructions": "How frustrated the customer appears",
  "criteria": ["Calm, just stating facts", "Frustrated but civil", "Very angry, strong language"]
}
```

- `criteria`: ordered array, 2–10 levels, low->high. Level number = array index from 0.
- Returns `{ type, score, legend:{"0":"..."}, probabilities:{"0":p}, confidence }`.
- `score` = sum(level * prob) — may land *between* levels. Normalize with `score / (len(criteria)-1)` before weighting.
- Describe **situations, not degrees**. Bad: `"Moderately severe"`. Good: `"Broken feature, workaround exists"`. Model never sees level numbers/neighbours — `"worse than previous"` means nothing.
- One dimension per Score. Split multi-dimensional judgments, combine with weights in code (Composite Scoring).
- **The same Score question asked per item gives you a ranking.** "How relevant is this passage to the query?", asked once per candidate with the same levels, produces comparable numbers across candidates — which is what makes graded reranking work where a per-pair Choice cannot.
- Never interpolate Score to recover an exact number. Use it to threshold/rank/round. Score levels are also **weakly calibrated numerically**: they say where on your described scale the state sits, not how many units. "Moderately severe" as a level gives you nothing to convert.

### Noul — `Is this true?` (yes/no, pronounced "nool")

```json
"is_urgent": {
  "type": "noul",
  "instructions": "The message conveys urgency or time-sensitivity",
  "criteria": { "true": "Explicitly time-sensitive", "false": "No urgency expressed" }
}
```

- Returns `{ type, noul: 0..1 }` = p(yes). Near 1 = yes, near 0 = no, ~0.5 = uncertain. **No separate `confidence`** — the value *is* the distribution.
- `criteria` optional. Add when boundary is subtle.
- Phrase so high = yes. One condition per Noul. Statement or question both work — test both.
- Threshold in code: `>0.9` to act when false-yes is expensive, `<0.1`-style low when missing true-yes is expensive, `0.5` when symmetric. Route middles to human.
- **One Noul per label when several can apply.** Multi-label questions are several Nouls plus a combination rule in code, never one Noul with an "or" in it.
- Noul is NOT a scale. `0.5` does not mean "medium skill". For degree, use Score. Recorded `jev-1.13.0` values on the same question, for calibration intuition: "How do I reset my password?" 0.07, "I need this sorted today" 0.26 (urgent but never asks for a person), "Are you a bot?" 0.40, "Is there any way to speak to someone about my invoice?" 0.84, "Can I please just talk to a real person?" 0.99.

Dot-path references: point at nested state with backticked paths — ``"Does `ticket.messages[0].text` request a refund given `order.charges`?"``. Structured `instructions`/`criteria` can be objects/arrays to keep code-supplied data separate from question text.

## 3. Confidence — a derived statistic, not a second opinion

Choice and Score return `confidence: 0..1`. It is **computed from that answer's own `probabilities`**, not produced alongside them, so it carries no information you do not already have — TypeSafe's own docs say to use `probabilities` instead if you have a particular statistic in mind. On every worked response published in the docs, `confidence` equals the **top-1 probability rescaled away from uniform**:

```text
confidence = (n · p_max − 1) / (n − 1)        n = number of options/levels
```

This matches **all eight** worked JSON responses in the API and Choice pages — `{0.88, 0.12, 0}` over three options → 0.81 published, `{0.74, 0.26, 0, 0, 0}` over five → 0.67, `{0, 0.95, 0.05}` over three levels → 0.92, one-hot → 1.0 — to within the rounding of the displayed probabilities, and three of the five cases in the interactive Score explorer. The docs' own confidence demo uses this same expression and calls it an approximation, and two of the explorer's five cases do not match it. So: **a close description of the shape, not a promised contract** — but the direction is certain, because the docs say outright that confidence comes from the distribution, and this is the only expression that reproduces the published numbers. **It is a function of the peak, not of the shape.**

Three consequences, in order of how often they bite:

- **The same threshold means a different thing at a different option count**, because it measures concentration *relative to uniform* rather than absolute certainty. Inverting it, `confidence >= 0.5` requires p_max ≈ 0.75 with 2 options, 0.667 with 3, 0.625 with 4, 0.55 with 10 and 0.525 with 20 — while a fixed 0.9 threshold asks for 0.95, 0.933, 0.925 and 0.900 respectively. So a threshold tuned on a 3-way routing question becomes steadily **more permissive** as you widen the option set, and a 0.9 gate on a 50-way question is almost no gate at all. Threshold per question shape, or gate on `p_max` directly and compare it to your own chance level.
- **It ignores the tail.** With four options, `{0.4, 0.4, 0.1, 0.1}` (a near-tie) and `{0.4, 0.3, 0.2, 0.1}` (a clear runner-up) both score 0.20. If that difference matters to you, compute margin or entropy yourself — this repo's own ranker does exactly that, `conf = min(1, 0.35 + (p_best − p_second) * 2.2)` over top-2 margin.
- **Several acceptable answers also look flat.** "Which of these three replies is fine?" spreads probability across all three without anything being wrong. For a **preference**, take the argmax and stop; confidence is for **risk**, not for taste.

Default gate:

```python
if confidence < 0.5:
    route_to_human()          # genuinely unsure, don't guess
elif choice == "check_balance":
    show_balance()            # low stakes, act
elif choice == "approve_transfer":
    if confidence > 0.9: confirm_then_execute()
    else: ask_user_to_confirm()  # high stakes needs higher bar
```

- Thresholds scale with risk. Tune on *your* data, plot confidence vs accuracy, report coverage separately. Start conservative.
- Calibration is group-wise, not a per-answer guarantee. `0.8` should be right ~80 % of the time across many calls. Jev's calibration is the one axis where it leads the field (JevBench v1.5.5: 88.0, best of 109 ranked systems), and it is the reason to prefer it when code auto-acts on the number.
- Confidence is not permission to act. It summarises this distribution, not your workflow's correctness.
- **Use uncertainty for the branches you take.** A speculative question you ended up ignoring does not need a gate.

## 4. How to build — code owns control flow

1. **Keep deterministic work in code.** Dates, math, counting, filtering — never ask Jev to count/arithmetically compare.
2. **Send only relevant state.** Filter/retrieve first. Large irrelevant state = context-rot + worse accuracy. Use a Noul relevance filter for RAG.
3. **Decompose to atomic questions.** "Is this spam?" → 6 Nouls (requests_credentials, unexpected_reward, time_pressure, identity_mismatch, link_mismatch, disguised_link). Combine with weights in code so the weights stay reviewable.
4. **Structure state as a JSON object** with named fields, and reference fields by backticked path. An option's description can be an object too: `{what, not_for, examples}`, same field names across options, is what sharpens a boundary between two similar options.
5. **Fan out speculatively.** Ask everything you *might* need in one call and ignore what does not apply. Batching adds no noise as well as no latency, so there is no accuracy reason to split a request.
6. **Compose in code or classical ML.** Weighted sum, if/elif routing, or CatBoost/logreg on the probabilities as features. A Score answer is worth **two** columns (the level it points at, and how spread out it is); a Noul is one.
7. **Route on uncertainty.** High → auto-act, medium → confirm/flag, low → human or a stronger reasoning model.
8. **Escalate through a cascade, not a bigger model by default.** Cheap extractor → verify each field with a Noul that returns P(something is wrong) → expensive reasoning model only when a verifier fires.
9. **Let uncertainty pick how specific your answer is.** When unsure, report a coarser label derived in code from the taxonomy rather than spending a second call.

Rules, the measurements behind them and the full triage example: `references/patterns.md`.

Python:

```bash
pip install typesafe-sdk  # or: uv add typesafe-sdk
export TYPESAFE_API_KEY=...
```

```python
from typesafe_sdk import Choice, Noul, Score, TypeSafeClient
with TypeSafeClient() as client:  # default model jev-latest
    resp = client.system_one(
        state={"ticket_message": "...", "refund_policy": "..."},
        questions={
            "refund_requested": Noul(instructions="Does `ticket_message` request a refund?"),
            "request_type": Choice(instructions="What is main request?", criteria={"refund": "...", "rebooking": "...", "information": "..."}),
            "frustration": Score(instructions="How frustrated?", criteria=["Calm", "Frustrated", "Very angry"]),
        },
    )
print(resp.answers["refund_requested"].noul)
```

cURL:

```bash
curl -X POST https://api.typesafe.ai/v1/systemone \
 -H "Authorization: Bearer $TYPESAFE_API_KEY" -H "Content-Type: application/json" \
 -d '{"state":"Hi, ...","model":"jev-latest","questions":{"urgency":{"type":"noul","instructions":"Does this express urgency?"}}}'
```

JS: `npm i @typesafe-ai/sdk`, `new TypeSafeClient()`, same `system_one()` shape with `choice()/noul()/score()` helpers.

Playground first: https://console.typesafe.ai/playground — paste state, add questions, then copy working questions into code.

**And measure the baseline before you commit.** `pip install "system-one-adapter @ git+https://github.com/typesafe-ai/system-one-adapter-python"` gives you a drop-in `system_one` backed by OpenAI, Anthropic, Gemini or any OpenAI-compatible endpoint, returning the same `SystemOneResponse`. One question set, two engines, one comparison — which is the only honest answer to "is Jev worth it for this decision?" and the fastest way to catch a question set that a plain LLM already handles.

## 5. Jaggedness — jev-1.13 failure modes

1. **Literal reading** — answers what you wrote, not meant. Fix: exact conditions + boundary cases in criteria.
2. **Math/counting** — does not count, no calculator. Fix: regex/parser in code; per-item Nouls then `sum()` in code.
3. **Dates/times** — reads as text, not ordered. Fix: Choice-extract parts (with `not_stated` option), assemble + compare in code.
4. **Indirection/double-negatives/multi-hop** — accuracy drops. Fix: direct wording, name state parts.
5. **Large noisy state** — distractors hurt. Fix: filter first.
6. **Adversarial/injected instructions** — state is data, not hostile-filtered, and it is now measured. **JevAdvBench** (arXiv 2609.31142, Hu et al., 25 Sep 2026; first adversarial benchmark for RLCD models — 812 typed questions over 66 scenarios, 9,744 black-box **single-edit** variants on jev-1.13.0, each edit confirmed by billed input tokens) found: rewording stays within **1.2 pp** of the re-run baseline, and **fields outside the schema never reach the model** — but **one unverified opinion appended to the state flips 12.1 % of decisions** (statistically tied with the strongest injected command, 10.1 %) and **pushes 38 % of confident answers below the 0.8 threshold** that routes them to human review. Fix: precise criteria + edge-case tests, treat state as untrusted argued input, and do not read "schema-valid by construction" as "not manipulable". Copy their method when you eval this yourself: score each attacked decision against **the model's own clean answer**, and baseline it against **an identical re-run** — labels from the model itself are circular and identical requests can differ, so a fixed gold set understates the effect.
7. **Contradictory instructions vs criteria** — confuses. Fix: align them, don't invert true/false.
8. **No structural invariants** — `P(yes)+P(no)` != 1 across separate questions; Noul threshold != Choice threshold. Fix: ask one way, enforce identities in code.
9. **No generation** — don't chain Choices to emit text. Fix: regex/LLM proposes candidates, Jev picks via Choice.
10. **Numeric representations** — it reads semantic representations better than numeric ones. Two hex colours are judged less reliably than "red" and "orange"; high-level languages better than assembly. Fix: convert in code and pass the computed number or a **named bucket**, keeping the model for the part that is a judgment ("does this colour read as a warning?").
11. **A Score is not a magnitude** — score levels are weakly calibrated numerically. Use the expectation to test a threshold; never to recover the number between two levels.

Item 8 has published numbers, which is why it is worth believing. On the ticket *"I'm not happy with the fit. What are my options here?"*, the Noul "Is the customer asking for a refund?" returns **0.22** while the same question as a yes/no Choice returns `probabilities {yes: 0.01, no: 0.99}` with `confidence 0.97` — and on *"I was charged twice for the same order"*, `refund` = 0.72 and its negation `not_refund` = 0.47, summing to **1.19**. Never carry a threshold from one form to another, and never expect arithmetic identities between separate questions. A Choice is *relative* (which option); each Noul is *absolute* (whether this holds) and can be low for all options at once.

A second request is justified only when the second request's state or options **do not exist yet** — you need the first answer to fetch evidence, build the state, or choose the next options. Otherwise ask everything together (`references/patterns.md`).

## 6. Open alters — local decision-model-compatible models

Official Jev weights are closed. These are independent re-implementations of the *shape* (`POST /v1/systemone`, Choice/Score/Noul + probabilities + confidence) plus some local classifiers with the same decision semantics and a different API. **A model card's own numbers are not a selection**: read `references/decision-index.md` (Decision Index 0.2.1, 43 benchmarks, 119,898 scoreable requests, 70 systems) and `references/jevbench.md` (JevBench v1.5.5, 1,624 Jev-shaped decisions per system) first — both are dated snapshots, and **neither is comparable to the edition it replaced**.

Per-model detail lives in `references/open-alters.md` (HF-verified specs, one section per model) and `references/clone-ecosystem.md` (the diffusion stack, the SemIf/APUS lineage, and the research-shaped experiments). Train one: `references/training-guide.md`. Score an existing LLM without retraining: `references/no-retrain-scoring.md`.

| Alter | HF ID / home | Pick when | Hard limit or caveat |
|---|---|---|---|
| Winnow-12B Q8 | `EldanRing/Winnow-12B` | Highest Intelligence on the text board; chat + decisions + vision from one ~12.7 GB Q8 load | Entropy confidence **uncalibrated** |
| Cygnet | `blockbrain-ai/cygnet-recipe` | No training at all: frozen Gemma-4-12B-it + a shim | T=3.4 fitted on the author's own items; **422s on >26 options** |
| Surogate Rune v3 | `surogate/rune-26b-a4b-GGUF` | Closest open breadth on Decision Index; text+image; 262k ctx | ECE .120 against Jev's .074 |
| decider family | `Mapika/decider-2b` v10 + | Best-documented size-differentiated family, outcome-only RL | 2B weak on knowledge; no operator endpoint |
| Laya | `convaiinnovations/laya-typed-decisions` | Local encoder for narrow sets; fine-tune planned | Loses wide option sets (77-way .425 vs Jev .870); entropy confidence |
| OpenThai-SystemOne | `iapp/OpenThai-SystemOne` | Thai/English, `order_invariant`, 40 ms H100 | Thai-first tuning |
| Julia 1 | `SupersonicLabs/Julia-1` (+`-ONNX`) | Sub-200M multilingual on CPU, or ONNX WebGPU in a browser | **2–20 options**, `max_probability` not `confidence`, gate on `logits()`; weights are at chance on knowledge |
| GLiNER2.5-Decide | `fastino/GLiNER2.5-Decide` | Local English operational classification, call-time labels | Not a `/v1/systemone` clone; not a reasoning model |
| Kev | `jaredpalmer/kev-0.8b/4b/9b` | Small trainable decision model, Mac-friendly | Needs fine-tuning before it is useful |
| Jevfire / simple-jev | `kikoncuo/jevfire` | vLLM/GPU and no training at all | Probabilities are relative preference, not P(correct) |
| xor | `juspay/xor` | Released 2-GPU serving bundle; `images` array (≤8) | 2×GPU, ~120 GB disk; localhost has no auth |
| JevAny-27B | `tianxinwei/JevAny-27B-{SFT,RLCR}` | Native image/video evidence | Early, verify before relying on it |
| NeoHorse-Jev-4B | `TokenRhythm/NeoHorse-Jev-4B` | 4B prefill-only, one optional image | Each question re-branches the state — not one shared forward pass |
| jevify / jevons | `kushalpatil/jevify-…`, `gopalanj/jevons-…` | Jev wire format over Gemma/LFM (transformers, MLX) | Wrapper quality, not a trained model |
| pngwn scorer, open-jev DeBERTa, JEV-CPU/SemIf, JevForge, CLM-8B, Nimble, von, Jevlike, tiny crowd | see references | Training recipes, no-retrain logit readout, edge/CPU students | Card numbers are author-reported except where a board row is quoted below |

Two gates before any of these is selected, and they are the only two that compare systems:

**Broad suite (Decision Index 0.2.1, generated 2026-09-28, verified 2026-10-02):** 43 benchmarks / 119,898 scoreable requests / 70 systems, chance-corrected skill (0 = random, 100 = perfect). The five areas are **no longer equal weight** — Knowledge 25.8 %, Language 25.8 %, Retrieval 20.0 %, Tools 18.3 %, Arts 10.1 %, gold-star benchmarks 1.2 — so this is not comparable to the 0.2 edition's 51.67. Jev **57.91**; closest open **Rune v3 57.44**, Decider-chat/Gemma-4-31B 57.33, AutoJev 56.40, `simple-jev` (no training) 55.74; then xor 41.48, GLiNER2.5-Decide 11.21, CLM 7.40, Laya 6.04, Julia 1 5.54. **The open field is within half a point of Jev on breadth**, so what remains to prefer Jev is calibration, latency, cost, licensing and availability — not capability. Never rank latency from that column: Jev's 524 ms is hosted HTTPS and the others are in-process on one RTX PRO 6000.

**Jev-shaped (JevBench v1.5.5, verified 2026-10-03):** headline is **Capability Score = mean(Intelligence, Calibration)**; **Jev 1.13.0 leads at 80.0** (I 72.0 / C 88.0, $0.032/1k, 0.62 s) > Winnow 79.3 > Cygnet 79.0 > Rune v3 79.0 > Jev-Omni 76.5 > djev 76.4 > JevK5 v0.3 72.3 > decider-4b v2 70.7. "Jev-class" is a defined band: ≤ 2× Jev's cost and median latency. **Scores do not carry across versions** — Jev measured 63.3 on v1.4.2 and 72.1 on v1.5.4 with no model change. Ranks 1–10 are one statistical cluster, and the small fast 4B rows (JevK5, decider, Hopper) buy their speed with 15–25 points of Intelligence. Read the axes, not the composite. Image and audio have their own boards because Jev is text-only: `references/multimodal-decision-models.md`.

**Most local alters share one serving trick:** prefill, read the next-token/slot distribution over answer labels, softmax, assemble JSON in code — never ask the model to write JSON. Clones lose to Jev on knowledge-heavy tasks, and calibration (RLCD vs entropy) is the open gap. JevBench costs are **$ per 1,000 decisions, not per 1,000 tokens** — never compare an estimated row to a measured bill.

**What the ecosystem looks like now, and how to filter it.** About 70 systems are on Decision Index and 109 on JevBench, and the newest wave is mostly *thin*: dozens of LoRAs, GGUF/ONNX re-exports, and `/v1/systemone` shims in Rust, Swift, C++ and Node, plus a long tail of empty repos that copy a popular model card's tag block with zero downloads. Four things are worth knowing rather than listing:

- **Distillation is now a route of its own.** `autotrust/JEV-27B` (Apache-2.0, Qwen3.8-27B LoRA + dual head, vLLM) is explicitly a student of Jev 1.13 and reports 90.3 % agreement with it (95.8 % on decisive cases) — an unvalidated self-report, and it has no board row, but "train against Jev's outputs" is now cheaper than imitating its architecture and is the obvious first thing to try if you need Jev's behaviour under your own licence terms.
- **Adding a fourth question type is a fork, not a feature.** `Jwuthri/SelfJev` ships `Multi` alongside Noul/Choice/Score. Useful if you need it; it is no longer the Jev contract, so code written against the three will not transfer.
- **A host now treats a decision model as its own model role, not a chat model.** OpenClaw (`docs.openclaw.ai/concepts/decision-models`, role added after release 2026.9.5) puts `decisionModel` beside `primaryModel` and `utilityModel`, gives it a separate Decision picker, and exposes a core `decision_evaluate` tool; per-agent config can set one role or disable decisions entirely, and **there is no automatic fallback to a conversational model**. Two design points worth stealing: the rubric travels **with every request** (no separate rubric-registration step, so a rubric change is a request change and `rubricVersion` goes into result provenance), and the host states plainly that **sharing the interface does not make providers' probabilities or reasoning ability interchangeable** — plus that an alias such as `kev-latest` does not identify a loaded checkpoint. Its providers run from a 0.4B local ONNX classifier to hosted Jev behind one interface.
- **Multimodal decision models are benchmarked separately, and Jev is not among them.** Image JevBench v0.1.5 is led by a *closed* API (Wity-1, 85.7, $0.0074/1k) ahead of open JPT-9B (83.6) and Imajev-4B (82.1); AudioJevBench v0.1 by Qwen3-Omni-30B-A3B-Instruct (83.2). Leaders, method and traps: `references/multimodal-decision-models.md`.

**How to filter a new candidate cheaply:** does it have a row on Decision Index or JevBench, or a reproducible eval artifact; can you run it through the *official* SDK via `TYPESAFE_BASE_URL`; does its confidence mean anything (many are uncalibrated entropy); and does the option cap fit your question set. A model card with an impressive narrow benchmark and no external row is a hypothesis, not a selection.

## 7. DiffusionGemma-as-Jev local paths vs Kev

`DiffusionGemma` (`google/diffusiongemma-26B-A4B-it`, Gemma-4 26B-A4B MoE, discrete block-diffusion, 256-token canvas denoised in parallel) is a generative model, not a decision model. "As Jev" means wrapping it — and the wrap is the transferable part, because jevify/jevons do exactly this for Gemma/LFM and the local open clones do it for everything else:

1. Fix answer vocabulary per question (option keys / yes-no / level digits).
2. Single prefill of `state + instructions + criteria`, read per-label logits (or denoise-step marginal), temperature-scale, softmax within question.
3. Assemble `{choice, probabilities, confidence, noul, score, legend}` in code — never let the model write JSON.
4. Fit temperature on held-out (ECE/Brier), add an `abstain` slot and `order_invariant` averaging (cyclic permutations, batched) if you need OpenThai-style robustness.

This is an **inference method, not a trained model**, and the broad gates do not show parity: Decision Index 0.2.1 scores Jev 57.91 against the original vLLM-PR path 32.24, djev 40.28, the JoshuaSP wrapper 49.47 and razorback one-read 37.25, even though early author-run comparisons on selected items looked roughly tied. On JevBench v1.5.5 djev is #6 (Capability 76.4, second-highest Intelligence 72.3, worst Cost of the leaders). Serving details — merged vLLM PR #57250, the `diffusion_*` request knobs, the DGX Spark numbers, the Blackwell `TRITON_ATTN` caveat and the exact serve command — live in `references/clone-ecosystem.md` (`DiffusionGemmaJev`); variants and hosts in `references/open-alters.md` §8.

## 8. Review checklist (for humans)

Questions + thresholds live in **one file**. Review those, not plumbing. Expect to co-edit questions with the agent — agents write mediocre questions on first pass, and TypeSafe's own skill says so. Validate with cheap live `TYPESAFE_API_KEY` experiments before refactoring; after edits, re-run the threshold sweep. Before trusting a model card, add one broad external gate (Decision Index 0.2.1 + JevBench v1.5.5 where practical) and include unsupported/refused coverage, option-order swaps, abstentions, and **single-edit state injections** (§5) in the local eval — an injection that survives here also survives the gate you build on top of the model. JevBench costs are per 1,000 **decisions**, not tokens — keep cost bases separate, and never compare an estimated row to a bill. Never automate from a leaderboard score alone.

Five operational things that are easy to get wrong and cheap to get right:

1. **Pin `jev-1.13.0`, not `jev-latest`, in anything with thresholds**, and log `response.model` with every result. Aliases move, and the same version's answers were observed to drift between two runs days apart.
2. **Threshold per option count.** `confidence` is top-1 probability rescaled away from uniform, so 0.5 means p_max ≈ 0.67 with three options but ≈ 0.55 with ten and ≈ 0.525 with twenty. Gate on `p_max`, or restate the threshold per question.
3. **Re-run the eval against `system-one-adapter` on an LLM** before declaring the model necessary. Same questions, same state, one comparison run.
4. **Check what your gateway does to you.** Context and rounding are not the same everywhere: OpenRouter gives 32k instead of 64k, and the AI SDK documents probabilities rounded to 2 dp that need not sum to 1 — so do not assert `sum == 1` on a gateway response.
5. **Snapshot dates.** Both benchmark references in this skill are dated snapshots of fast-moving boards; §6's gate paragraphs say which edition each number came from. Re-fetch before recommending, and never mix editions.

Docs: https://docs.typesafe.ai/ (append `.md` to any page for Markdown; `llms.txt` is the index, `llms-full.txt` the whole site) — API: `/api`, Models: `/models`, Primitives: `/primitives`, Confidence: `/confidence`, Patterns: `/patterns`, Cookbooks: `/cookbooks`, Skill: `/agent-skill`, Jaggedness: `/model-jaggedness/jev-1.13`, RLCD: `/introduction/machine-learning-primer`. Launch post and workflow evals: https://typesafe.ai/blog/introducing-system-one-models-and-jev and https://evals.typesafe.ai/. Community broad comparison (unofficial): https://huggingface.co/spaces/multimodalart/jev-decision-index — see `references/decision-index.md`; Jev-shaped board: https://benchmarkheaven.com/jev-models — see `references/jevbench.md`.
