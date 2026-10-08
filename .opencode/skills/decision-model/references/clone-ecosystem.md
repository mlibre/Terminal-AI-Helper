# Clone ecosystem — Sept 2026 wave (verified + reported)

Companion to `open-alters.md` (HF-verified clones). This file folds in the
Sept 15-24 2026 clone wave from the community report. Status labels:

- **verified**: confirmed via official TypeSafe docs or live HuggingFace listing
- **reported**: plausible, consistent with verified signals, but only seen in
  community write-ups / GitHub (not independently fetched here)

## Background (reported, consistent with verified docs)

Only the facts this file cannot get from a contract reference. Price, rate
limits, context budget, the latency claims and the wire shape all live in
`api-contract.md` and SKILL.md §1 — do not restate them here.

- Jev launched ~Sept 15 2026 by TypeSafe AI as the first System One model.
  Founder includes Diogo Almeida (verified as TypeSafe cofounder in
  `introduction/machine-learning-primer`, credited there as RLHF
  co-inventor). The community report adds: ex-OpenAI/ChatGPT, co-inventor of
  InstructGPT/RLHF, two years in stealth, $40M seed led by DCVC. Reported quote
  worth keeping: "a frontier-intelligence function call: unstructured state in,
  typed probabilistic decisions out".
- Names: "System One" refs Kahneman's System 1, and Jev refs economist William
  Stanley Jevons (consistent with the community adapter name `jevons-lfm25`).
  Treat etymology as reported; positioning is in SKILL.md.
- Company (reported): TypeSafe AI, SF, founded 2024; CEO Diogo Almeida, CTO
  Erik Gafni, COO Sasha Sheng; proprietary, closed weights, no on-prem.
- Architecture (reported, undisclosed): a new architecture with a parallel
  sampler — Transformer-based but deliberately not an LLM. Third-party analysis
  ("Jev's Architecture Unmasked", reported) describes it as a **pointer-style
  scorer**: it reads each option's own representation directly (the main
  alternative to a numbered-slot decoder), so one parallel forward pass yields
  the whole answer distribution. No sequential decoding is what buys the speed
  and makes free text impossible.
- Training RLCD verified as method name + intent (calibrated: a higher stated
  probability means a higher hit rate; it targets the overconfidence RLHF leaves
  behind). First-party explanation: `docs.typesafe.ai/introduction/machine-learning-primer`.
- "Cannot hallucinate" = schema-valid by construction only: it cannot invent an
  output type or a malformed shape, but it **can** return the wrong
  label/score/probability. Validity != correctness, and JevAdvBench shows the
  state can still be argued (`SKILL.md` §5).
- Distribution detail worth knowing when reading a guide: "32,000 tokens" in
  community guides is the `state` + longest-question budget, not the whole
  request budget (`api-contract.md`).

## Clones — what to keep

### Already documented elsewhere — do not restate here

Per-model specs, measured numbers and run commands live in one place each:
`open-alters.md` for Laya (§0), GLiNER2.5-Decide (§0b), Julia 1 (§0c),
OpenThai (§1), NeoHorse-Jev-4B (§1b), JevAny (§6), xor (§6b), decider
(§7b) and the DiffusionGemma/Kev/von variants (§8). This file keeps only the
Sept-2026 wave that has no other home: the diffusion stack, the SemIf/APUS
lineage, and the research-shaped experiments. One signal correction worth
keeping: Kev's weights were once reported as HF-absent — `jaredpalmer/kev-0.8b`,
`kev-4b` and `kev-9b` are now public, so treat "no HF hit" as stale.

### DiffusionGemmaJev — PR #57250 MERGED, technically plausible

- Merged: vLLM contributor Matt Mastracci, `DiffusionGemma 26B` (Gemma-4
  26B-A4B MoE, discrete block-diffusion, 256-token canvas denoised in parallel)
  - structured generation mode (PR #57250, merged; 5 prereq/bugfix PRs folded
  in: #57414 stash-logprobs, #57416 prefill logit rows, #57417 converging-step
  logprob_token_ids, #57462 buffer dtype, #57589 multimodal). Framing: "We have
  Jev at home". Early selected-item evals reported rough accuracy parity (slight
  PII win); the broad Decision Index suite does not, so do not generalize those
  results. Local single DGX Spark. Follow-ups in flight per thread: perf
  (#58216 constrained reads, #58226 sampler kernel), Laya `/v1/systemone`
  PR #58429, MUSA port.
- Mechanism per PR: fix token positions on canvas (sequence denoised in one
  parallel step), lay out structured Q format with allowed single-token answer
  labels, fill all answer slots simultaneously, read logprobs at each answer
  position. Up to ~85 Qs per canvas at 3 toks/Q (reported). Request knobs:
  `diffusion_seed_canvas` + `diffusion_max_steps` + `diffusion_read_only` +
  `diffusion_canvas_length` + `logprob_token_ids` (≤128, honored on converging
  step). Single container: vLLM :8010, decision server :8011.
- Reported DGX Spark throughput: 1-way 8.7 req/s @ 0.12s, 32-way 54 req/s @
  0.58s (~162 decisions/s at 3 Qs/req); auto policy re-reads when H1 > 0.1.
  Backend caveat (GB10/Blackwell report): tensor-`causal` needs FLASH_ATTN or
  TRITON_ATTN (`--attention-backend TRITON_ATTN` workaround; fixed a7c23ac —
  verify on your build).
- Variants (reported, verify repo URLs before use): **OpenJev**
  (`razorback16/openjev`) wire protocol via one-step structured read with vLLM
  extensions `diffusion_seed_canvas` / `diffusion_read_only`; **LocalJev**
  (`githubnext/localjev`) portable TS bridge Jev req -> classification prompt
  -> JSON-prob normalize (wire-compatible, NOT mathematically equiv to logit
  read); **djev** (`mmastrac/djev`) Cloud Run + RTX PRO 6000 Blackwell.
- Serve (single container, reported): `vllm serve
  nvidia/diffusiongemma-26B-A4B-it-NVFP4 --diffusion-config '{"canvas_length":
  32}' --max-logprobs 32 --enable-prefix-caching --async-scheduling
  --attention-backend TRITON_ATTN --max-num-seqs 32` plus
  `python examples/features/structured_diffusion/structured_server.py
  --upstream ... --tokenizer ... --canvas 32`.
- Verified underneath: `google/diffusiongemma-26B-A4B-it` exists on HF.
  The Jev-wrap technique (fix vocab, read per-label scores, softmax in code)
  is exactly the jevify/jevons pattern, so keep as implementation recipe even
  if PR number / author attribution is not re-verified here.

### OpenJev / SemIf + APUS-OpenJev + JEV-CPU — partially verified

- OpenJev wrapper (reported): `razorback16/openjev` packages DiffusionGemma
  26B-A4B (Apache-2.0 weights) in two backends — vLLM on NVIDIA GPU or
  in-process MLX on Apple silicon — speaks same wire API, existing TypeSafe
  SDKs work unchanged. Also serves `/v1/chat/completions` OpenAI-compat text
  gen at no extra GPU mem. Hosted free endpoint reported:
  `api.codiv.ai/v1/systemone` (100M input toks on signup, no card — verify).
  MLX quant reported: `mlx-community/diffusiongemma-26B-A4B-it-OptiQ-4bit`
  (needs `mlx-optiq >= 0.3.2`, vendored decoder — stock mlx-lm/mlx-vlm cannot
  load DiffusionGemma).
- Verified signals: `Meanblock/JEV-CPU` exists on HF (Zero-Shot
  Classification); `com-kotobalabs/open-jev-deberta-v3-large` proves the
  `open-jev` tag is live; `ZefanCai/Open-Jev-2B/9B/27B` exist. HF search
  `semif+openjev` = 0, so SemIf/APUS likely GitHub + later HF push.
  Keep the logit-readout pattern — it is the cheapest valid Jev-ify.
- SemIf-OpenJev (reported): `github.com/TheoLeeCJ/SemIf-OpenJev` — reproduces
  interface pattern on open models (typed option probs directly, no answer
  sentence / JSON repair / decode loop). Qwen3.8-27B EXL3 bridge + PyTorch/MPS
  scoring for Apple Silicon. Explicitly NOT Jev weights/training.
- APUS family (reported): 4B/9B/35B-A3B, 35B 88.75% / 9B 85.00% vs Jev API
  82.50%. Earlier community write-up described TheoLeeCJ frozen Qwen3.5-4B
  logit-readout + JEV-CPU port + web UI under SemIf/OpenJev names — treat as
  same lineage, verify repo before citing.

### Smaller / niche (reported)

- mini-jev / NanoJV / Decider-as-education — small experiments.
  apidog "Top Jev Open Source Alternatives" README-by-README comparison
  (reported).
- Jev-Mem (research paper, reported) — builds on decision-model shape, not API
  clone: fast Jev-style control plane + structured multi-relational memory
  plane for agentic memory ops.

### Jevlike — reported, plausible

- Reported: ~40KB byte-embedding + option-attention, 192B ctx / 32B opt
  defaults, CPU training, MIT, 21 binary dec/s on 3090 vs 5.3 autoregressive
  JSON. Weak on language meaning by its own README.
- Not on HF under tested queries. Keep as minimal-reference / edge experiment,
  not a quality competitor.

### Bespoke Nimble — verified exists via OpenThai card

- Verified: `iapp/OpenThai-SystemOne` model card benchmarks against
  "Bespoke Nimble's `docs/PUBLIC_BENCHMARKS.md`" with Nimble-9B numbers —
  Nimble is real and its 13-subset public bench is the comparison point.
- Reported: Qwen3.5-9B + LoRA + contrastive curation, 66%→90% after curation,
  ~100ms H100, 90.12% vs base 66.36% vs 27B 84.88%, 2 days, no Jev
  distillation. Consistent with OpenThai's table (Nimble-9B 74.8 macro).
  Keep; re-verify exact percents against Bespoke repo before citing.

## No-retraining Jev-ify (SGLang scoring — inference only, no RLCD)

- Recipe: any open LLM (Qwen/DeepSeek tested) → SGLang `/v1/score` first-token
  logit readout over single-token A/B/C labels → restricted softmax → thresholds
  in code. Zero generated tokens. Full steps + `decide.py` + `/tokenize` 1-token
  check + OTHER/ESCALATE hatch + barrier-harness notes: `no-retrain-scoring.md`.
- Key rules: scoring ≠ structured output (structured still generates JSON
  token-by-token); single-token labels dodge multi-token/length effects;
  `"A"` vs `" A"` can differ (render via chat template, verify per model);
  scores = mass-share among your labels, **not** P(correct) — fit thresholds/
  temperature on labeled data (Brier/ECE).

## Calibration and benchmark hygiene

- **DecisionBench** (Jev 1.13 vs Kev-9B vs Laya vs a GPT-6 Astra reference): a
  community harness, not a leaderboard — Kev-9B 20/50 vs Jev 17/50 in one run.
- **A narrow win can be real and still not transfer.** Laya's own domain table
  (ahead of Jev on 2-way/AGNews/DAIR-Emotion) is Laya-reported, and Laya is
  rarely confident, so a router escalates. Same shape elsewhere: a card that
  wins its own suite and loses the broad one is not a contradiction.
- **Knowledge-heavy tasks are where clones lose.** Knowledge MCQ is the known
  weak spot of encoder/single-pass scorers; every open row here trails Jev there.
- **Calibration is a training objective, not a readout.** RLCD-trained Jev is
  calibrated; entropy-based confidence (Laya, Winnow, most logit wrappers) is
  uncalibrated by construction. Ask which one a number is before you gate on it.
- **A benchmark's changelog is part of its evidence.** Decision Index removed
  reflex 4B for confirmed train-on-test leakage; JevBench's sealed pool rotates
  every release, so sealed numbers move for a fixed model.

Method, weights, row tables and every number behind the two broad gates:
`decision-index.md`, `jevbench.md`, `multimodal-decision-models.md`.
