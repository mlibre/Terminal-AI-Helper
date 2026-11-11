# Decision Index — broad comparison of Jev-style systems

Source of truth for the snapshot below: the community-maintained static Space
[`multimodalart/jev-decision-index`](https://huggingface.co/spaces/multimodalart/jev-decision-index),
`data/index.json` + `data/methodology.json`, edition **Decision Index 0.2.1**,
`generated_utc` **2026-09-28T00:39:36Z**. Reproduction kit:
[`apolinario/decision-index`](https://github.com/apolinario/decision-index).
Not a TypeSafe leaderboard, not affiliated with TypeSafe AI.

**Verified 2026-10-02** by re-fetching `data/index.json` and recomputing every
headline from the published category scores (see §1). Previous snapshot in this
skill was 0.2 (2026-09-24) — **scores are not comparable across editions**; read
§5 before quoting a number.

## 1. What Decision Index 0.2.1 measures

- **120,340 requests**, **119,898 scoreable**, **43 benchmarks** (42 of them
  `jev_benchmarks`), panel of 38, five areas, one RTX PRO 6000, `jev_version`
  `jev-1.13.0`. Six interactive environments (MiniWoB++, Boxoban, RTFM,
  ScienceWorld, Hanabi, Codenames) stay out for every entrant, Jev included.
- Each benchmark keeps its native metric (accuracy, macro-F1, case-exact,
  nDCG@10, routing quality, Brier). Per-benchmark
  `skill = clip((score − chance)/(1 − chance), 0, 1)`.
- **The headline `balanced_skill` is *not* an equal-weight mean of the five
  areas.** 0.2.1 reweighted them:

  | Area | Weight |
  |---|---:|
  | Knowledge & Reasoning | 25.8% |
  | Language Understanding | 25.8% |
  | Retrieval & Classification | 20.0% |
  | Tools & Automation | 18.3% |
  | Arts & Human Judgment | 10.1% (fixed) |

  The other four share the remainder in proportion to the **square root of their
  benchmark count**. Thirteen gold-star benchmarks (MMLU-Pro, BBH, GPQA Diamond,
  HLE, ANLI, WinoGrande, HellaSwag, BANKING77, CLINC150, BRIGHT, BFCL, API-Bank,
  ForecastBench) weigh **1.2** inside their area.

  ```text
  balanced_skill = 100 · Σ_c w_c · skill_c
  breadth_skill  = 100 · ( Π_c (0.1 + 0.9·skill_c)^{w_c} − 0.1 ) / 0.9
  ```

  Recomputing Jev from its published category skills (0.514 / 0.6202 / 0.5542 /
  0.7509 / 0.3766) gives 57.89 vs the published 57.91 — and the same weights
  reproduce Rune (57.43 vs 57.44), AutoJev (56.39 vs 56.40), xor (41.48 = 41.48)
  and Laya (6.04 = 6.04). An equal-weight mean gives 56.32 for Jev and matches
  nothing. **`index.json` still carries a stale `formulas` string saying
  `0.2*category[c]`; the changelog weights are the correct ones.** `balanced_raw`
  is the un-normalised score, kept for reference.
- `frozen_scores` in the JSON are the older **frozen core25** panel — a stricter
  25-benchmark subset. Use `scores` (the 0.2.1 panel) as the headline; quote
  `frozen_scores` only as a second, harder number and say which one you used.
- Rules: an entrant must complete the panel; no truncation, no option filtering,
  no per-benchmark prompt tuning, no gold labels in the adapter, no
  correctness-based retry, unanswered/refused/errored count wrong. Native
  abstentions remain abstentions.
- **No confidence intervals on the headline.** Point estimates only, and models
  differ in prompting, readout, quantisation and runtime. A screening benchmark.

## 2. Snapshot leaders (balanced_skill, edition 0.2.1)

Calibration columns come from the separate 32-benchmark / 72,594-row sample
(`confidence` = probability placed on the chosen option); accuracy and
calibration are not redundant — a system can be well calibrated and weak.

| # | System | Type | skill | acc | ECE ↓ | Brier ↓ | Median ms* |
|---|---|---|---:|---:|---:|---:|---:|
| — | **Jev 1.13.0** | hosted official | **57.91** | 0.739 | 0.074 | 0.356 | 524.1 HTTP |
| 1 | Surogate Rune 26B-A4B v3 | full fine-tune, MoE | 57.44 | 0.748 | 0.120 | 0.370 | 120.5 |
| 2 | Decider chat · Gemma-4-31B | inference technique | 57.33 | 0.724 | 0.047 | 0.393 | 108.5 |
| 3 | AutoJev-27B | full fine-tune | 56.40 | 0.730 | **0.018** | 0.361 | 101.4 |
| 4 | simple-jev · Qwen3.8-27B | inference technique | 55.74 | 0.720 | 0.113 | 0.400 | 373.0 |
| 5 | frontier-infra Jebadiah 27B | LoRA | 54.67 | 0.720 | **0.014** | 0.365 | 110.4 |
| 6 | Eikos-27B-FP8 | LoRA | 53.13 | 0.705 | 0.057 | 0.401 | 129.7 |
| 7 | reflex Qwen3.8-27B-FP8 (wide choice) | inference technique | 52.16 | 0.704 | 0.024 | 0.397 | 108.3 |
| 8 | Decider chat · Qwen3.6-27B | inference technique | 51.35 | 0.697 | 0.021 | 0.400 | 83.6 |
| 9 | Winnow-12B Q8 | LoRA, vision | 50.02 | 0.670 | 0.168 | 0.486 | 72.5 |
| 10 | JoshuaSP diffgemma | diffusion structured read | 49.47 | 0.680 | 0.215 | 0.524 | 260.4 |
| 11 | Jevfire Qwen3.8-27B | no retraining | 49.37 | 0.689 | 0.052 | 0.414 | 89.6 |
| 12 | Decider 35B-A3B (NVFP4) | full fine-tune | 47.11 | 0.696 | 0.023 | 0.388 | 101.4 |
| 13 | JPT-9B | LoRA | 46.89 | 0.671 | 0.077 | 0.435 | 148.6 |
| 14 | Decision 1.0 Lux 9B | head / adapter | 43.49 | 0.664 | 0.076 | 0.440 | 51.0 |
| 15 | xor | full fine-tune, multimodal | 41.48 | 0.709 | **0.015** | 0.388 | 139.9 |
| 16 | Hopper (G) 1.2 | LoRA | 40.77 | 0.651 | 0.093 | 0.467 | 23.3 |
| 17 | Decider 4B | full fine-tune | 40.70 | 0.649 | 0.084 | 0.454 | 12.6 |
| 18 | Jev-Omni | LoRA + head | 40.53 | 0.614 | 0.161 | 0.534 | 54.9 |
| 19 | djev diffgemma | diffusion read | 40.28 | 0.632 | 0.212 | 0.566 | 84.4 |
| 20 | Bespoke Nimble 9B v2 | LoRA | 39.57 | 0.642 | 0.024 | 0.467 | 77.3 |
| 21 | JevK5 Qwen3.5-4B | LoRA | 38.81 | 0.647 | 0.027 | 0.459 | 22.0 |
| 22 | Kev 9B | LoRA + pointer head | 38.48 | 0.652 | 0.138 | 0.487 | 51.4 |
| 23 | InternLM Intern-Decision 4B | LoRA + head | 37.81 | 0.635 | 0.028 | 0.474 | 44.2 |
| 24 | razorback16 diffgemma (OpenJev) | diffusion read | 37.25 | 0.621 | 0.232 | 0.600 | 37.7 |
| 25 | NeoHorse-Jev-4B | pointer head | 36.75 | 0.607 | 0.104 | 0.513 | 49.0 |
| 26 | pngwn open-jev 4B | LoRA scorer | 29.91 | 0.586 | 0.059 | 0.545 | 112.3 |
| 27 | Tev1-4B | $17 custom classifier | 29.24 | 0.634 | 0.104 | 0.476 | 35.8 |
| 28 | SemIf Qwen3.5-4B | frozen logit readout | 25.94 | 0.640 | 0.099 | 0.482 | 113.1 |
| 29 | GLiNER2.5-Decide | DeBERTa classifier | 11.21 | 0.434 | 0.088 | 0.705 | 23.3 |
| 30 | CLM-v0.1-8B | two-encoder contrastive | 7.40 | 0.358 | 0.323 | 0.904 | 46.8 |
| 31 | Laya | non-autoregressive encoder | 6.04 | 0.377 | 0.140 | 0.721 | 5.8 |
| 32 | Supersonic Labs Julia 1 | mmBERT-small + head | 5.54 | 0.374 | **0.420** | 0.987 | 5.8 |

\* **Not a controlled speed comparison.** Protocol `latency-v1` (frozen
2026-09-26): every entrant runs the same 750 stratified rows, one request at a
time, one process, one RTX PRO 6000, ten untimed warm-ups, measured after scoring
on the fastest path its code supports. Earlier board figures came from the
scoring runs with 6–12 processes sharing a card. **Jev's 524.1 ms is a hosted
HTTPS round trip and is not comparable to the in-process rows.** Never rank
latency from this column without matching the measurement path.

## 3. What changed since 0.2, and why it matters

- **The open field closed on Jev.** 0.2: Jev 51.67, closest open AutoJev 50.94.
  0.2.1: Jev 57.91, Rune v3 57.44 (−0.5), Decider-chat/Gemma-4-31B 57.33, AutoJev
  56.40. Rune v3 replaced v1 only after the maintainers reproduced the author's
  run exactly on 3,000 rows. "Nothing open is close" is no longer true on
  breadth; the remaining differentiators are **latency, cost, licensing,
  availability and your own domain**.
- **The reweighting moved the number more than any model did.** Jev's 51.67 →
  57.91 is largely a *weighting* change, not a capability change (§1). Anyone
  quoting "Jev improved by 6 points" is reading a new formula.
- **Latency numbers were re-measured** on a dedicated 750-row protocol, so the
  0.2 column cannot be diffed against 0.2.1.
- **An entrant was removed for leakage.** reflex 4B's published LoRA trained on
  800 MMLU-Pro test items: 65.8% on those vs 52.1% held-out (95% CI 9.8–17.5).
  Board policy now removes such a row. Read the changelog before quoting any
  model card.
- SGD and RouterBench left the index (unfixed builder; a prompt that gives away
  the best route). ACOS is now per-review F1. RAGTruth's chance level is "always
  answer *hallucinated*". ForecastBench's contamination is **not certified**,
  because Jev's cutoff date is unpublished.
- Jev's own answers drifted between board runs: the 0.2.1 latency sample differs
  from the 19 Sept board rows on 20 choices (scores unchanged). A hosted model
  under an alias can move without a version bump — another reason to pin
  `jev-1.13.0` and log `response.model`.

## 4. Area findings that change model choice

Jev's area skills in 0.2.1: **Knowledge 0.514, Language 0.620, Retrieval 0.554,
Tools 0.751, Arts 0.377**.

- The strongest open rows are 27B-class and lead on *raw* accuracy (Rune v3
  0.748 > Jev 0.739) while carrying worse calibration (ECE 0.120 vs 0.074). If
  your workflow auto-acts on the answer, that trade is the whole decision.
- **Low ECE is not strength.** Jebadiah (ECE 0.014) and xor (0.015) are the best
  calibrated rows on the board and sit at 54.67 / 41.48 skill. Julia 1 is the
  cleanest warning: ECE 0.420 and Brier 0.987 — confidently wrong, which no
  confidence gate can catch (its mmlu is at chance).
- **No-retraining wrappers now beat most trained ones**: Jevfire 49.37 and
  simple-jev 55.74 with no training at all. If your use case is a bounded,
  well-specified label set, try the 25-line readout before you train anything.
  Their probabilities are still *relative preference*, not P(correct).
- The narrow-card reversals persist: Laya (6.04), CLM (7.40), GLiNER2.5-Decide
  (11.21) and Julia 1 (5.54) are excellent on their own targeted suites and near
  the floor here. Keep them for the domain their evidence covers; do not infer
  general Jev parity.
- DiffusionGemma structured reads remain real and still do not reach Jev
  broadly: 49.47 (JoshuaSP), 40.28 (djev), 37.25 (razorback), 32.24 (vLLM PR
  57250) vs Jev 57.91.
- NeoHorse-Jev-4B finally has a broad row (36.75) — it is no longer
  author-reported-only, and it does not reach Jev on breadth.

## 5. How to use this benchmark

1. **Say which edition every number comes from.** 0.1, 0.2 and 0.2.1 are three
   different formulas; a 0.2 figure in a 0.2.1 table is a bug.
2. **Screen breadth here, Jev-contract fitness with JevBench**
   (`jevbench.md`: typed outputs, calibration, speed, cost per 1,000 decisions,
   sealed generalisation), then your own domain eval with labelled cases —
   including long inputs, wide option sets, missing evidence, option-order swaps,
   adversarial injections and abstentions.
3. **Evaluate distributions, not just top-1**: top-1 accuracy, coverage, ECE,
   Brier/NLL and a top-p + top-2-margin policy. Report the operating threshold
   and the coverage it costs.
4. **Separate adapter from runtime**: quantisation, option order, temperature and
   readout change both accuracy and confidence. The board's own churn (Rune v3,
   Hopper 1.2, reflex 27B replacing earlier runs) is that effect, documented.
5. **Pin and replay**: model revision, prompt, option order, temperature,
   serving commit, context capacity, hardware, complete raw outputs.
6. **Re-fetch before recommending.** This file is a dated snapshot; the Space is
   refreshed every few days and the formula has already changed twice.

## 6. Useful discovery keywords

Broad web/HF/GitHub searches that work better than only `Jev clone`:

- `"typed decision model"`, `"System One model"`, `"Jev reproduction"`
- `"TypeSafe compatible"`, `"/v1/systemone"`, `"Decision Index 0.2.1"`
- `"candidate readout"`, `"pointer head"`, `"shared-prefix scorer"`
- `"parallel constrained decoding"`, `"single-token label scoring"`
- `"RLCD calibrated decisions"`, `"Brier ECE decision model"`
- `"multimodal decision model"`, `"JevBench"`, `"Decision Index"`
- model families: `AutoJev`, `Rune Surogate`, `Jevfire`, `Winnow`,
  `Decision-1.0`, `xor`, `Decider`, `OpenThai-SystemOne`, `Laya`, `Kev`,
  `OpenJev`, `DiffusionGemma`, `CLM`, `InternLM Intern-Decision`, `JPT`,
  `simple-jev`, `Eikos`, `Jebadiah`.

Useful Hugging Face filters: `decision-model`, `system-one`, `typed-decisions`,
`calibration`, `option-scoring`, `non-autoregressive`, `zero-token-generation`,
`endpoints_compatible`, `mlx`, `gguf`, `core-ml`, `RLCD`, `pointer-head`.
Useful GitHub topics: `decision-models`, `jev`, `typesafe`, `model-serving`,
`open-weights`, `llm-serving`, `calibration`, `inference-server`.
