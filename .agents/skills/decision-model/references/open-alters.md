# Open alters — Jev-compatible System One clones and local decision models

Official Jev weights are closed. Most entries below are independent open re-implementations
of the *shape*: `state + {choice|score|noul} -> {choice/probabilities/confidence, score/legend, noul}`.
Most expose a `POST /v1/systemone` wire format; adapters may be wire-compatible rather than official. NeoHorse's adapter is System One-style and explicitly not official Jev calibration or billing. GLiNER2.5-Decide is a local, schema-driven
classifier with similar bounded decision semantics, but it is **not** a System One wire clone.
Numbers are NOT comparable to TypeSafe or to each other because data, metrics, coverage,
and calibration differ.
Detail on Sept 2026 wave (Laya, Kev, NeoHorse-Jev-4B, Nimble, SemIf, xor, DiffusionGemmaJev): `clone-ecosystem.md`.
No-training Jev-ify of any open LLM (SGLang `/v1/score` first-token readout): `no-retrain-scoring.md`.
Current broad-suite comparisons and selection caveats: `decision-index.md` (Decision Index 0.2.1, generated 2026-09-28, 43 benchmarks / 119,898 scoreable requests / 70 systems) + `jevbench.md` (JevBench v1.5.5, 904 open + 720 sealed decisions per system, I/C/S/K composite with published CIs). **Neither snapshot is comparable to the edition it replaced**, so quote the edition with the number. JevBench is more useful for Jev-contract fitness (typed outputs + calibration + speed + cost per decision); Decision Index is more useful for screening breadth than comparing unrelated narrow card metrics.

## 0. convaiinnovations/laya-typed-decisions — open-source Jev (encoder, local)
- HF: `convaiinnovations/laya-typed-decisions` (+ `laya`, `laya-multilingual`, GGUF/MLX/ONNX/CoreML forks), 0.4B, Apache-2.0, rel. ~Sept 18 2026
- Base (reported): ModernBERT-large 421M EN (512-ctx, typed-decisions 1024-ctx) + mmBERT-base 322M multilingual 100+ langs (1024, up to 8192 w/ RoPE). Head: 2 transformer layers + option-marker scorer + act/escalate head.
- Mechanism: each offered option scored at its own `[MASK]`, softmax over that Q's options. Per-request schema, no retraining, zero output tokens.
- Speed = local vs cloud trade-off. Reported: ~32.8ms p50 T4, 86.5 dec/s vs Jev 3.2/s, ~9ms vs ~317ms p50, 15.3ms vs 298.1ms M5 Pro, 60 dec/s M3 Max MLX ~1GB.
- Accuracy nuance: reported wins vs Jev on 2-way/AGNews/DAIR-Emotion, BUT base ckpts weak zero-shot (0.362 vs 0.318 random vs 0.461 majority). Convai line: "fast base to specialise, not zero-shot engine" — fine-tune for prod. Entropy-based confidence, uncalibrated.
- Run (reported): `@receptron/laya` (ONNX/Node), MLX port, Core ML, Railway self-host Jev-compat API.

## 0b. fastino/GLiNER2.5-Decide — local operational classifier (not a wire clone)
- HF: `fastino/GLiNER2.5-Decide`, English, Apache-2.0, DeBERTa-v3-large encoder, 340M parameters; load locally through `gliner2`.
- Interface: `AutoExtractor.from_pretrained(...)` and `classify_text(text, schema)`. Labels are supplied at call time. A call can combine independent single-label heads and multi-label heads; single-label heads return one label, while multi-label heads return labels above `cls_threshold`. No prompt template and no generated answer tokens.
- Useful tasks: customer/banking/travel/clinic intent, ticket/email/document routing, sentiment, aspects, moderation/policy, severity, urgency, spam, human handoff, agent-finished checks, passage questions, genre, and ordinal ratings. Use the model only as a specialist decision component; code owns thresholds and side effects.
- Multilingual: the card points to `fastino/GLiNER2.5-multi-Decide`; this entry describes the English checkpoint only.
- Benchmark (model-card reported): 60.2% exact-match average on `fastino/fast-decisions` (17 domains, 300 held-out examples each). The same card reports 59.6% for Decide-1B, 57.6% JevK5, 56.7% multi-Decide, 56.4% SemIf (Qwen3.5-4B), 49.0% GLiFormer large-v1, and 46.6% Laya Router. Treat this as a useful domain-specific signal, not a broad leaderboard or Jev API result.
- Verified details from the HF card and PyPI page: package `gliner2`, Python 3.10+, CPU-first local processing, `gliner2[local]` for local inference. The card labels it 340M while HF metadata also displays a rounded 0.5B model-size field; use the checkpoint's config for deployment sizing.
- Caveats: not a general-purpose reasoner; it does not explain, generate prose, or answer open-ended questions. The published card benchmark is a separate held-out suite; Decision Index 0.2.1 gives it a broad row at **11.21 skill, 23 ms, ECE .088**, and no calibration claim is established here either. Verify confidence output/threshold behavior against the installed `gliner2` version before using it for automated escalation.

## 0c. SupersonicLabs/Julia-1 — 144M multilingual CPU decision model (HF-verified)
- HF: `SupersonicLabs/Julia-1` (+ separate `SupersonicLabs/Julia-1-ONNX`), 144.3M, Apache-2.0, PyTorch, Python 3.11+. Weights FP32 **550.5 MiB**. `juliamodel` package installed from the snapshot (`pip install -e ./Julia-1`); the private training pipeline is not released.
- Base: `jhu-clsp/mmBERT-small` (multilingual ModernBERT encoder, ~140M) + a decision head and tokenizer retained from upstream. Card is explicit that this is **not** a fine-tuned Qwen. Smallest multilingual Jev-shaped entry in this file.
- Interface (Jev-shaped, not a wire server): `from julia import load_model`; `load_model("Julia-1", device="cpu", strict_encoding=True, max_length=8192, head_length=512)`, then `engine.predict(state=..., questions={"team": {"type": "choice", "instructions": ..., "criteria": {...}}})`. Named-question form matches Jev's shape and returns caller IDs unchanged; questions are scored independently in a batch. Legacy list form `engine.predict([{state, question, options, type}])` returns `index` + display-rounded probabilities; `engine.logits(rows)` for raw scores. Load once and keep resident — no per-call reload.
- **Contract deltas that break drop-in assumptions** (verify before porting Jev questions):
  - `choice` = **2–20 options** (Jev: up to 255). `score` = **2–20 rubric levels** (Jev: 2–10).
  - confidence is `max_probability` (the peak) on choice/score, **not** Jev's spread-derived `confidence`. Gate on entropy/top-2 gap yourself, as elsewhere in this skill.
  - `criteria` must be plain nonempty **strings**; the structured `{what, not_for, examples}` form in SKILL.md §2 is not available.
  - per-option 48-token cap; `head_length=512` default question+options budget; `strict_encoding=True` **rejects** overflow rather than truncating. Runtime default is 8,192 combined tokens; benchmarks used 1,024.
  - `noul` matches Jev (no separate confidence). No dot-path state references are documented — test before relying on them.
- **Author-reported accuracy (2026-09-24, H200 BF16, strict encoding).** The Jev column is the *supplied reference from the pinned Jev protocol* (`jev-benchmarks@0d610cc`), not a fresh Jev run. Typed decisions come from `LocalLLaMA/typed-decisions`; the three classification pilots are 100 examples each via `btzsc/btzsc`; abstentions count as incorrect.
  - Typed decisions **73.15%** (1,463/2,000) vs Jev 72.70% — Choice 428/600 (71.33%), Noul 484/600 (80.67%), Score 551/800 (68.88%).
  - AG News 4-label 94/100 vs 91. DAIR Emotion 6-label 86/100 vs 48 (+38 pp).
  - Banking77 72-label **through a ranking/top-16 shortlist**: 64/100 vs 87 (**−23 pp**).
  - MASSIVE 18-scenario: 110,573/154,648 = **71.50%** macro over 52 locales (pt-PT 86.25%, en-US 86.75%).
  - **Read this carefully:** these 100-example pilots plus a +0.45 pp typed margin are *not* "beats Jev". There is no Calibration/Speed/Cost axis in the card's own comparison, and no JevBench row. Decision Index 0.2.1 has since given it a broad row — **skill 5.54, ECE .420, Brier .987** — which measures the same verdict the mmlu/arc numbers give. It is however the first entry here whose Jev reference is protocol-pinned and matches the 0.727 figure already cited for Laya, so the typed-decision axis is close to comparable; the pilots are not.
- **Wide-option lesson (transferable).** The grouped `Router` narrows candidates in groups then reranks survivors; the card states narrowing **can lose the correct answer** and that grouped probabilities cover final candidates only, **not** a global distribution. Combined with the 2–20 native cap and the −23 pp Banking result, this makes *shortlist recall a hard ceiling*: measure recall@k of any prefilter separately from the decider, or a "fixed" wide-set number is really a prefilter number.
- CPU/edge numbers (vendor-reported; workloads differ, do not compare across rows):
  - Apple M4, 100 context words + 4 options, 4 CPU threads: **33.15 ms median / 44.23 ms p95** one-per-call (28.01 dec/s); batches of 16 → 312.11 ms/batch (51.20 dec/s). 370.6 MiB RSS.
  - Intel i5-1235U, per-100-example set: AG News 107.83 ms, DAIR Emotion 89.83 ms, **Banking77 3,713.54 ms median / 5,125.10 ms p95** — the 72-label shortlist path is ~35–40× the 4/6-way cost. The i5 "2,000 typed decisions / 294.81 ms" row's unit is **not stated** on the page; do not quote it as per-decision latency.
  - Samsung SM-X510 (Android 16, ONNX Runtime 1.27.0, Rust tokenizer): 40 decisions in 8 s = **5 dec/s**, 193–205 ms/decision, ~92 tokens/decision, peak RSS 393.1 MB with the 550.1 MB weight file memory-mapped. **XNNPACK miscompiles a `Reshape` on this graph and was excluded — keep XNNPACK off for this export.**
- Deployment: `JULIA_CPU_THREADS` (default 4); CUDA path when PyTorch sees a BF16-capable GPU. An 8,192-token CPU smoke test passed but 8k *task accuracy* is explicitly not established. `SupersonicLabs/Julia-1-ONNX` is the browser path — details in §0c.1.
- Training economics: ~**R$540 (US$104)** total cloud GPU for the whole program; they started from an existing small multilingual encoder rather than training a foundation. Announced API $0.025/M input, $0.00/M output, not open at time of writing. **Julia 2** (own foundation architecture, no mmBERT) is in development — watch item only.
- Not established by these evals: external knowledge, multi-step calculation, unseen domains, high-stakes use. Card framing: "clear choice grounded in supplied context". The released `provenance.json` turns the first two from caveats into measurements — see §0c.2.

### 0c.1 Julia-1-ONNX — browser WebGPU path (HF-verified 2026-09-26, read from `index.js` + released JSON)

Same Julia 1 weights and decision graph exported to ONNX. **WebGPU only** — the card is explicit that CPU/CUDA deployment belongs to the PyTorch repo, and `JuliaWebGPU.create()` throws `WebGPU is unavailable` when `navigator.gpu` is absent. Do not read the numbers below as a CPU mode of this library.

- **Download budget** — the four runtime files total **616,974,137 B (616.97 MB / 588.4 MiB)**; repo `usedStorage` is 617,939,929 B including docs and assets: `model.onnx.data` **576,782,336 B (576.8 MB / 550.1 MiB)** external weights + `model.onnx` **2,988,923 B (2.99 MB)** graph (must stay co-located; external data is wired by filename convention `<modelUrl>.data`) + `tokenizer.json` **34,363,188 B (32.8 MiB)** + `wasm/julia_webgpu_encode_bg.wasm` 2,839,690 B (2.71 MiB). The 34 MB tokenizer is easy to forget when sizing a first paint.
- **API** (`index.js`, read directly): `const { JuliaWebGPU } = await import('./index.js'); const j = await JuliaWebGPU.load('/models/Julia-1-ONNX/')` — downloads graph + weights + tokenizer, creates the WebGPU session, and **warms it** with one dummy decision. Keep the instance resident. `j.logits(rows)` -> raw scores; `j.predict(rows)` -> `{index, probabilities}`. `create({modelUrl, tokenizerUrl, encoder, maxLength, headLength, strictEncoding})` for overrides.
- **Row shape, not Jev's named-question map**: each row is `{state, question, options[], type}`. `type` is `choice|score|noul`, encoded to a per-row `qtype` tensor, so a batch may mix types. **There is no `questions: {id: {...}}` form and no caller IDs** — you fan out rows yourself, and `state` is re-encoded into every row, so an N-question batch costs ~N× the state tokens. `state` accepts string | array | plain object, but an object is re-serialized to Python-repr text; **no dot-path resolution**.
- **Enforced validation** (throws `TypeError`/`Error`, hard 2–20 `options`, every option a non-empty string, `question` a string, and **`type === 'noul'` requires exactly 2 options** — the false/true pair, in that order). Per-option **48-token** cap. `strictEncoding: true` (default) *rejects* rather than truncates: a `<mask>` marker anywhere in state/question/options, question/options over the lossless head budget, or state over the context budget. With strict off, overflow is silently truncated (options sliced to `max(4, …)` per option, state to whatever room is left) — a lossy path, not a fallback.
- **Defaults are tighter than the Python side** (a real porting trap): `maxLength` **1024** and `headLength` **256** here, vs 8192 / 512 in the Python `load_model`. Raise them deliberately or long states will be rejected under strict encoding.
- **`predict().probabilities` is a display value, not a distribution.** `displayProbabilities()` collapses to exact one-hot `[1,0,0,…]` when peak > 0.95 and all others < 0.045; otherwise it **zeroes everything below 0.01 and renormalizes**. So mid-confidence answers read as more certain than they are, and confident ones read as certainty. **Any calibration, entropy, top-2-gap, or confidence gate must read `logits()` and softmax in code** — this is the same trap as the Python legacy list form, and it is easy to miss because the field is literally called `probabilities`.
- Inputs are `input_ids`, `attention_mask`, `marker_pos`, `marker_mask`, `qtype` (int64/bool, batch padded to a multiple of 8). A generic ONNX runtime will not drive this graph correctly; the JS adapter is the supported path.
- The Rust WASM tokenizer is **optional** — pass `encoder`, or omit it and `create()` falls back to `@huggingface/transformers` `AutoTokenizer` plus the JS `serialize()`. The `rust/` N-API binding is for apps wanting resident native encoding; `export.py` regenerates the ONNX graph from the original checkpoint.
- **Verified WebGPU benchmark** (`benchmark-webgpu.json`: Brave on Linux, local Vite, 100 real Julia validation requests, batch 4, 5 measured runs after warmup): median **7,546.6 ms / 100 decisions = 75.47 ms per decision** (min 6,302.2, max 8,510.1), load+warm from warm browser cache **5,843 ms**, **100/100 predictions match** PyTorch, max abs logit difference **2.2464e-3**.
- **Two parity references, do not conflate them.** `parity-cpu.json`: 100/100 match, max abs logit error **7.82e-5** vs PyTorch (`strict_encoding: true`, source `data/fast-frontier-v2/validation.jsonl`) — i.e. the ONNX graph itself is near-exact on CPU. The WebGPU path is ~29× looser at 2.2e-3. The README's "original CPU run took 18.23 s on these 100 requests" is a **reference for the original model's output, not a CPU mode of this library**; no CUDA comparison was available and the card states it is not a controlled hardware speedup claim.
- **Parity is not accuracy.** The card states the published accuracy (73.15% typed etc.) was measured on the **original runtime and not rerun on WebGPU**; 100/100 on one 100-request set is output parity, not a new accuracy figure. Nearly-tied logits can flip under WGSL float rounding, so re-check your own decision set rather than assuming bit-equality.
- Not deployed by any Inference Provider. Collection `SupersonicLabs/julia-1` (4 items). Practical niche: a client-side/edge reranker or an offline eval oracle. **Not a keystroke-path model** — 75 ms/decision in a browser and a ~5.8 s cold load are both disqualifying for per-keystroke use.

### 0c.2 Julia released metric artifacts — read these before quoting any Julia number

The ONNX repo ships three metric artifacts — `metrics/accuracy-20260924.json`, `metrics/validation.json`, and `provenance.json`. They are internally replayed by the author, not Decision Index or JevBench rows, and they do not all describe the same thing. `accuracy-20260924.json` and `provenance.json` share `weights_sha256 df853bf7…` (the same released checkpoint, `step 500`, `variant: posttrained-candidate`); **`metrics/validation.json` is a different, later checkpoint** (`step 900`, `runs/julia-auto-1000/checkpoint-00000900`) and is *not* the released weights — do not mix its rows in.

- **The decisive negative result** (`provenance.json`, same released weights): `jev:mmlu` **0.2626** (n=198, NLL **5.02**) and `jev:arc_challenge` **0.285** (n=200, NLL **4.90**) — at or below 4-way chance (0.25). `jev:sst5` 0.405 (n=200). This is the concrete form of "clones lose to Jev on knowledge-heavy tasks": **NLL ≈ 5 means confidently wrong, not uncertain**, so a confidence gate will *not* catch it. Never route knowledge/external-fact questions to Julia-1; put them in state or use a reasoning model. This is the same jaggedness family as SKILL.md §5 — a decision model judges supplied state, it does not hold world knowledge — and it is why the `open-jev:*` control splits (workflow-controls 0.98–1.0, vizdoom 0.974, customer_service 0.993) and these knowledge splits diverge so hard.
- **Wide-set numbers are unsettled — the same suite name appears twice with different accuracy.** `metrics/accuracy-20260924.json` banking77: 100 asked, 99 answered (1 abstained), coverage 0.99, **accuracy 0.64** vs Jev reference 0.87. `provenance.json` banking77: n=100, **accuracy 0.83** with **`recall_at_16` 0.99**, unsupported 0. Both have ~0.99 shortlist recall yet differ by 19 pp, and the repo does not explain the protocol difference. This is the SKILL.md §2 "shortlist is a ceiling" lesson with the two halves priced separately: recall@16 0.99 is the **ceiling**, the 0.64/0.83 gap below it is the **reranker**. Re-measure your own wide set; do not cite either number as Julia's banking77 ability.
- **MASSIVE 71.50% macro hides a large locale spread** (`count 154,648` = 52 locales × 2,974, macro 0.71500, 802.7 examples/s, 192.7 s total on the author's run). Strong: en-US 0.868, pt-PT 0.862, es-ES 0.851, fr-FR 0.846, zh-CN 0.836, ru-RU 0.824, nl-NL 0.823, ja-JP 0.822, it-IT 0.818. Weak tail: am-ET **0.449**, km-KH 0.478, ka-GE 0.505, my-MM 0.509, mn-MN 0.544, cy-GB 0.551, ml-IN 0.556, kn-IN 0.556, sw-KE 0.556, ta-IN 0.619, jv-ID 0.606. This is an 18-slot task, so the tail is not "at chance" — it is ~40 pp below the strong locales, which is the point. `provenance.json` reproduces the same shape on the same released weights at a smaller per-locale n (200): en-US 0.855, am-ET 0.455, km-KH 0.48, my-MM 0.50, ka-GE 0.48. **The spread is a property of the model, not one run** — "multilingual" does not mean uniform, so check your own locale before assuming coverage.
- **The only ECE/Brier numbers in the release are vacuous.** `diagnostics` in `accuracy-20260924.json` is 3/3/2/2 examples with perfect predictions and ECE ~1e-5. That is not a calibration result. `provenance.json` reports `"quality_gate": false`. So the honest statement is: **no usable calibration data exists for Julia-1**, and the mmlu/arc NLLs are direct evidence of overconfidence on out-of-domain knowledge.
- Sample-size trap: `provenance.json` `open-jev:trex_runner-v1` is **n=2**. Several `open-jev:*` splits are small (tile_platformer n=22, workflow-controls n=71). Use them as directional signal on *control-shaped* decisions only, and re-measure.
- `metrics/validation.json` (step 900, **not** the released weights) for completeness: overall 0.6982 over 3,814 items — gsm8k 1.000, banking77 0.9496, emotion 0.9130, agnews 0.8792, winogrande 0.5571, mmlu 0.4864, hellaswag 0.3640. Note gsm8k 1.0 is **option ranking, not arithmetic** (the same "does not count / does not calculate" jaggedness as Jev, SKILL.md §5 item 2), and do not compare it to the released run's rows.

## 1. iapp/OpenThai-SystemOne — best open drop-in
- HF: `iapp/OpenThai-SystemOne`, 0.8B, Apache-2.0, Thai+English
- Base: Qwen/Qwen3.5-0.8B-Base text tower (24L hybrid Gated-DeltaNet/attention, 262k ctx), vision removed, ~5B Thai CPT tokens
- Head: LM head replaced by 256-way slot head. Control tokens `<|ts_opt_0|>…<|ts_opt_254|>`, hidden at `<|ts_answer|>` -> 256 logits, mask beyond n_opts, softmax. Slot 255 = abstain.
- Contract: `choice` up to 255 opts, `score` 2-10 levels, `noul` p(yes). Python `openthai-systemone` client mirrors TypeSafe SDK. HTTP server TypeSafe-compatible.
- `order_invariant: true` / `permutations: n` — averages cyclic option orders batched, auto for choice>10 opts. Fixes 5-12% order-flip (72% at 77-way). Cost ~2x latency.
- Latency: ~40ms H100 (166 tok ticket), 44ms 255-opt, 154ms M3 Max MPS. 64k tokens/request.
- Eval v0.3: public 13-subset macro 74.3 vs Nimble-9B 74.8 vs Jev 76.0. Thai MASSIVE-th 90.0, Prachathai topics 98.1, xLAM tool 99.4. Weak: summeval-relevance 21.7, banking77 45.4 single-order (63.3 order-invariant), wisesight 51.6, pubmedqa 64.
- Use:
```bash
pip install openthai-systemone
OPENTHAI_SYSTEMONE_MODEL=iapp/OpenThai-SystemOne uvicorn openthai_systemone.server:app --port 8000
```
```python
from openthai_systemone import SystemOneClient, Choice, Score, Noul
client = SystemOneClient("iapp/OpenThai-SystemOne")
resp = client.system_one(state={"ticket": "..."}, questions={"department": Choice(...)})
```

## 1b. TokenRhythm/NeoHorse-Jev-4B — 4B local multimodal decision model (HF-verified)
- HF: [`TokenRhythm/NeoHorse-Jev-4B`](https://huggingface.co/TokenRhythm/NeoHorse-Jev-4B), Apache-2.0, public model listing last modified 2026-09-24. It is approximately 4B, based on `TokenRhythm/NeoHorse-1-4B` (Qwen3.5-4B lineage). The released manifest describes a unified multimodal `Qwen3_5Model` backbone plus an independent FP32 pointer head; it is not a chat causal LM.
- Contract: prefill-only `Choice`, `Noul`, and `Score`, with candidate/level distributions and an expected Score. The native `neohorse_decision` runtime exposes `POST /v1/decision`; its System One-style adapter exposes `POST /v1/systemone`. The adapter is explicitly **not official Jev calibration or billing**. System One-style Choice/Score `confidence` is a local distribution statistic, not calibrated `P(correct)`; native Python `DecisionEngine` does not currently return confidence.
- Deployment: the complete release is required (backbone, tokenizer, `pointer_head.safetensors`, manifest, and matching runtime). It includes a native wheel/source runtime plus vLLM 0.28.0 and SGLang 0.5.17 adapters. The recorded native environment is Linux/Python 3.12/PyTorch 2.8/Transformers 5.17/Triton 3.7.1/flash-linear-attention 0.5.2 with a BF16 CUDA GPU; the bundle is about 9.08 GB for the backbone plus 5.25 MB for the head. Keep weights, tokenizer, and runtime from the same release.
- Input/runtime limits: text state 2,048 tokens, up to 16 questions, 8,192 tokens per question branch, and a 32,768-token expanded budget. Image requests accept one PNG/JPEG/WebP image plus text and one question (1,024 visual tokens); multiple images, video, and audio are not supported. The native implementation serializes one GPU request and repeats the state in each question branch: “prefill-only” does **not** mean a single shared forward pass or that questions are computationally isolated. Its System One-style token usage counts the shared state once even though the branches repeat it, so do not use that count as a speed proxy.
- Evaluation (model-card reported): 77.70 equal-weight six-group average (JevBench 75.73, Kev 81.92, OpenJev text 58.74, Nimble 87.23, VitaminC 77.13, MASSIVE 85.43), 83.26% mean on Nimble/VitaminC/MASSIVE, and 60.65% Image-NLI accuracy on 8,000 examples. These are author-reported card results. **Decision Index 0.2.1 now has an independent row for it — skill 36.75, 49 ms, ECE .104** — which is a long way from Jev's 57.91 and is the number to quote instead of the card's own 77.70.
- Limits: NLL/Brier/ECE calibration results and a common latency, GPU-memory, or cost comparison are not reported. Missing evidence, candidate descriptions/order, and domain shift can change decisions. Treat it as a candidate for local evaluation, not as a verified broad Jev replacement.

## 2. com-kotobalabs/open-jev-deberta-v3-large — CPU-friendly true scorer
- HF: `com-kotobalabs/open-jev-deberta-v3-large`, 0.4B DeBERTa-v3-large encoder, Apache-2.0
- Format: `[CLS] [STATE] state [Q] instructions [OPT] opt1 [OPT] opt2 … [SEP]`. Head scores `[mean(q); mean(opt); product]` per option, softmax within question group. Cross-entropy + Brier, temp fitted on val.
- In: state+any number typed questions, out calibrated dist per question, one forward pass. Zero structured-output error by construction.
- Data: 18k states / 42k Qs, public gold only (banking77, SST-5, BoolQ), no synthetics, 1 epoch, 229s H100.
- Results: in-domain acc 0.854 Brier 0.213 ECE 0.022; OOD (new instructions/opts) 0.69 Brier 0.399 ECE 0.035. banking77-77way 0.916, boolq 0.879, sst5-level 0.599.
- Limits: English only, 512 tok total (state cut 256), OOD ordered scales weak. CPU M1 Max 1.8s/4Qs, H100 28ms/10Qs.
- Use: `from typed_decisions.open_jev import OpenJev; m.decide(state, [{type, instructions, options}])`

## 3. pngwn/system-one-qwen3.5-4b-scorer — reference training recipe
- HF: `pngwn/system-one-qwen3.5-4b-scorer` (+ v2b, v2-2ep, smoke, ONNX). PEFT LoRA r16 + scalar score head on Qwen3.5-4B-Base, CC-BY-NC-4.0 (ticket data).
- Method: each (state,question,option) scored by seq-class head, softmax per question. No generation.
- Data: `pngwn/system-one-decisions` 12.9k train Qs 9 families. 2200 steps, batch 8 Qs, max_len 384, opt-cap 16, lr 1e-4 cosine.
- Results test n=576 uncapped: acc 0.707 ECE 0.044 Brier 0.373 @T=1.75 (raw ECE 0.135). Prompted baseline 0.679/0.093. Latency 112ms/4-opts, 560ms/77-opts.
- Limits: cardinality mismatch (tickets_queue 0.234), 384 trunc, weak on knowledge MCQ.
- Training code in repo `system_one.py` — best to study to build your own.
- Use:
```python
from peft import PeftModel
from transformers import AutoModelForSequenceClassification
base = AutoModelForSequenceClassification.from_pretrained("Qwen/Qwen3.5-4B-Base")
model = PeftModel.from_pretrained(base, "pngwn/system-one-qwen3.5-4b-scorer")
```

## 4. kushalpatil/jevify-gemma4-26b-a4b — Jevify pattern
- HF: `kushalpatil/jevify-gemma4-26b-a4b`, Gemma-4-26B-A4B-it LoRA r64 merged, Gemma license
- Server: https://github.com/kushalpatil07/jevify — `jevify serve` = Jev wire format. One prefill, read next-token dist over answer labels. Loss KL(target||label).
- Data: ~47k (state,question,target-dist) 16 sources, randomized subsets/order, multi-annotator dists, long states to 24k.
- Results OOD n=307: acc 0.834 NLL 0.45 Brier 0.241 ECE 0.061. In-dist 0.757->0.821 ECE 0.234->0.032 after train.
- Use:
```bash
pip install "jevify[transformers] @ git+https://github.com/kushalpatil07/jevify"
jevify serve --model kushalpatil/jevify-gemma4-26b-a4b
```
```python
from jevify import Jevify, Noul, Choice, Score
jev = Jevify.from_transformers("kushalpatil/jevify-gemma4-26b-a4b")
jev.system_one("Help! ...", {"urgent": Noul(...)})
```

## 5. gopalanj/jevons-lfm25-1.2b-systemone — Mac-local pattern
- HF: `gopalanj/jevons-lfm25-1.2b-systemone`, MLX LoRA on `mlx-community/LFM2.5-1.2B-Instruct-8bit`, LFM 1.0 license (need Liquid AI license)
- Server: https://github.com/gopalanj/jevons, MIT. Scores allowed outcomes from logits, assembles JSON in code. 100% schema-valid by construction.
- Honest: holdout modal 72.4% (=base), ECE worse 0.122->0.248. Ranking adapter, not calibrated RLCD. Seed-only teacher aliases.
- Use:
```sh
uv run hf download mlx-community/LFM2.5-1.2B-Instruct-8bit --local-dir models/LFM2.5-1.2B-Instruct-8bit
uv run hf download gopalanj/jevons-lfm25-1.2b-systemone --local-dir adapters/lfm25-1.2b-systemone
JEVONS_ADAPTER=adapters/lfm25-1.2b-systemone JEVONS_TEMPERATURE=1 JEVONS_CALIBRATION=off uv run jevons serve --port 8000
```

## 6. tianxinwei/JevAny-27B-SFT + RLCR — multimodal
- HF: `tianxinwei/JevAny-27B-SFT` (general, recom.) + `JevAny-27B-RLCR`, LoRA r16 + pointer head on Qwen3.8-27B, Apache-2.0
- Early multimodal alter with native image/video evidence via backbone vision path. Text/JSON + 1 isolated multimodal Q per request, 2048 packed tok envelope, bounded visual budget.
- Data: 107k prefs/agent/tool/hard-MC/classification/policy + image/video. Separate calib partition for temp.
- Results: dev 90.34% NLL 0.265, transfer-v9 82.41%, MMLU-Pro 73, AI2D 86, MMMU 68.
- Limits: needs Qwen base + JevAny runtime separately, HTTP media opt-in local-root only, recalibrate thresholds per deploy.

## 6b. juspay/xor — verified multimodal typed-decision model (HF-verified 2026)
- HF: `juspay/xor` (verified live), Apache-2.0. Base `Qwen/Qwen3.6-35B-A3B` MoE, 35B total / ~3B active per token, BF16, fully merged (no adapter).
- Interface: TypeSafe-compat `/v1/systemone` (`noul` binary prob, `choice` + full dist, `score` expected ordinal + full dist) **plus `images` array (up to 8 URLs/data-URLs)** — classify from images + text jointly. Second multimodal alter after JevAny, first verified-downloadable one.
- Serving layer (part of release, required for repro): deterministic single-token candidate readout + forward/reverse option-order eval + probability calibration + schema conversion.
- Self-run JEVBench public tiers (harness `fd51755`, `typesafe` adapter, 1 req at a time, 2× RTX PRO 6000 96GB, TP2, pinned SGLang image `lmsysorg/sglang@sha256:6bcaa4…`): Easy 48/48 acc 1.0 Brier 0.0018 ECE 0.0257 · Original 70/72 acc 0.9722 Brier 0.0897 · Hard-public 86/111 acc 0.7748 macro 0.8033 Brier 0.3460 · schema-valid 1.0 all tiers · p50 ~77ms/77ms/136ms. Self-run public tiers only, **not** official rank; full held-out eval requested (JEVBench #20).
- Run (Linux x86-64, HF CLI, Docker Compose v2, NVIDIA Container Toolkit, ~120GB disk):
```bash
export XOR_REVISION=xor-v1 INSTALL_ROOT="$PWD/xor-eval" MODEL_DIR="$INSTALL_ROOT/model"
hf download juspay/xor --revision "$XOR_REVISION" --local-dir "$MODEL_DIR"
(cd "$MODEL_DIR/serving" && sha256sum -c xor-serving.tar.gz.sha256)
mkdir -p "$INSTALL_ROOT/runtime" && tar -xzf "$MODEL_DIR/serving/xor-serving.tar.gz" -C "$INSTALL_ROOT/runtime" --strip-components=1
cd "$INSTALL_ROOT/runtime" && CUDA_VISIBLE_DEVICES=0,1 TP_SIZE=2 API_PORT=49001 ./run.sh
curl -sS -X POST http://127.0.0.1:49001/v1/systemone -H 'Content-Type: application/json' --data @examples/request.json
# images: --data @examples/image-request.json
```
- Ops: ~66GB weights; validated prefill 250k, mem-fraction 0.85, TP2 on 2× PRO 6000. Localhost needs no key; remote must add auth/TLS/rate-limits/size-limits at ingress. Compare latency only on same GPU×count×TP×image; else label hardware-specific.
- **Decision Index 0.2.1 external result:** skill **41.48**, accuracy .709, ECE **.015**, Brier .388, 140 ms (38.77 in the 0.2 edition, which is a different formula). Excellent calibration and image support do not imply broad strength: retrieval skill is only .175. Keep the card's narrow JEVBench self-run and this broad result as separate evidence.

## 6c. Decision Index 0.2.1 rows, as caveats rather than a table

The screen and its top rows live in `decision-index.md` and in SKILL.md §6; four
things from it belong to *this* file, because they change how a spec above
should be read:

- **A narrow win is not broad parity.** GLiNER2.5-Decide (11.21), CLM (7.40),
  Laya (6.04) and **Julia 1 (5.54, ECE .420, Brier .987)** all carry strong
  narrow or verifier reports and weak broad rows. Keep them for the domains
  their own evidence covers.
- **Broad accuracy and calibration can disagree.** Rune v3 acc .748 (above
  Jev's .739) with ECE .120 (against Jev's .074); xor acc .709 with ECE .015
  but only .175 retrieval skill. Quote the axis you are choosing on.
- **Untrained wrappers are competitive.** Jevfire 49.37 (90 ms, ECE .052) and
  `simple-jev` 55.74 — but their probabilities are relative preference, not
  P(correct).
- **Read a benchmark's changelog before quoting a card.** reflex 4B was removed
  for confirmed leakage (65.8 % on the 800 MMLU-Pro items its LoRA trained on vs
  52.1 % held-out).

## 7. Tiny crowd (edge/CPU)
- `DavidHatley/system-one-mini` 69M feature-ext, `kaivoss/system-one-270m` 0.3B classifier, `dwidlee/systemone-lite-0.5b`, `aimeigaoshou/agent-jev` 0.6B, `samatv256/mini-Jev` 263k, `Mannedood/local-system-one-student` 0.1B, `shreyanbr/system-one-*` 70M zeroshot/distilled/gold, `mpuig/system-one-qwen3-0.6b`, `olafura/gemma4-12b-system-one`, `ZefanCai/Open-Jev-2B/9B/27B`, `chaoliangUNSW/Jev-Style-Qwen3.5-2B-Decision-*` (GGUF/MLX). All Jev-shape, measure before use.
- Newer small entries worth a look before writing your own (2026-10-02, HF-verified existence, **no board rows**): `jevhome/jevhome-B` (~150M ModernBERT, CC-BY-NC-4.0, ONNX + PyTorch + a Rust binary, CPU, self-reported JevBench Easy 100 / Standard 78 / Hard 31, 69 ms p50, 0.9 GB peak); `hotchpotch/bekko-system-one-v0-{17m,68m,400m}` (cross-encoder rerankers, 17M–400M, ONNX in a browser; the card itself says they fall well behind Jev 1.13 and that v0 exists because its training data overlaps the eval); `jinghao1632/bit-jev-2b-distilled` (BitNet b1.58 2B + pointer head, CPU, 2,183 downloads, **no licence tag** — resolve before use); `stephenlb/system-one-model` (Gemma-4-12B with the 262k LM head replaced by a low-dim vector, self-reported 34 ms p50 on a 5090; Gemma licence); `quazim0t0/On-Fly-Jev` (spiking network, research curiosity).
- **Filter by external evidence, not by name.** Several dozen repos share the popular Laya tag block with zero downloads and no weights; a curated list of ~226 projects exists (`Amal-David/awesome-jev`) and is a better index than any single row here.

## 7b. Mapika/decider family — best-documented open System One family (HF-verified; JevBench v1.5.5 #11, Decision Index 0.2.1 40.70 at 4B)
- HF collection `Mapika/decider` (6 items, Apache-2.0): `decider-2b` v10 (default, Qwen3.5-2B-Base, 3.5GB bf16, 4ms CUDA-graphs) · `decider-4b` v1 (8.4GB dense, no RL, 0.834/0.788, 87/95 >2B) · `decider-4b v2` (**JevBench v1.5.5 #11 at Capability 70.7; v1.5.4 was #7 at 71.3, A→C 61.6 at the Intelligence-60 floor**: I 55.8 / C 85.6 / S 90.9 / K 64.5, ~$0.015/1k est, I_sealed 49.8; `decider-ai 1.2.2`, letter logits ÷ T=1.935, offline network-disabled container, no operator endpoint. It was #1 on v1.4.2 at 64.1 — the protocol changed under it) · `decider-35b-a3b` v1 (65GB, 3B active, 0.855/0.810, 93/95 >2B, JevBench-hard 0.676, Bespoke 0.774) · `decider-35b-a3b-nvfp4` (19.6GB Blackwell, 1–1.5 pts under bf16) · `decider-0.8b` (1.4GB, within 1–4 pts of 2B, 1.5× faster) · `decider-2b-vision` (4.1GB, Visual7W 0.89, Breakout 41 from pixels).
- One interface (`decider.infer.Decider`, `POST /v1/systemone`; the official
  typesafe-sdk works via `TYPESAFE_BASE_URL`) and one readout (letter logits ÷
  T=1.30, never generated, one pass, ≤10 options sampled per training example
  with the gold kept and order shuffled, so it conditions on the candidates
  rather than a fixed head). Limits: the 2B is no-reasoning, the family is
  English-only, v10 lacks v9's terse-bucket data, and the full 151-way case
  scores 0.88 against 0.98 sampled. No operator endpoint — it ships an offline
  network-disabled container. Repo `github.com/Mapika/decider`; the SFT → RL
  recipe and its measured outcomes are `training-guide.md` Path 5, not
  repeated here.

## 7c. JevBench v1.5.5 notes on the specs above

Board, metric and row table: `jevbench.md` §3–§5; summary in SKILL.md §6. Only
the spec-relevant parts:

- **Cygnet** (frozen Gemma-4-12B-it, no training, T fitted on the author's own
  items) 422s on over-context and >26-option inputs, which the board counts as
  wrong — so its Capability Score is conditional on staying narrow.
- **Winnow-12B Q8**: highest Intelligence on the board, entropy confidence
  uncalibrated. **Jev-Omni**: closest open row to Jev's profile on every axis.
- **JevK5 v0.3 / decider-4b v2 / Hopper**: the small fast 4B rows trade
  15–25 Intelligence points for cost and latency, and Hopper discloses heavy
  public-directed development (26 configs, 20+ calibration maps against the
  public half) — re-test held-out before trusting it.
- **djev** (#6, Apache-2.0) is an **inference method**, not trained weights.

## How to pick — the constraints, not the leaderboard

Per-model picks are the "Pick when" column of SKILL.md §6 and the sections
above. These are the rules that decide between them:

- **Start broad, then test narrow.** Decision Index 0.2.1 + JevBench v1.5.5
  screen; your own labelled distribution decides. Never skip to a card.
- **Wide option sets (>20): nothing local is safe.** A prefilter bounds recall
  by its own recall@k, so measure that separately from decider accuracy.
- **Never gate on uncalibrated confidence.** Ask whether a number is RLCD-
  trained or an entropy/max-prob readout before a threshold acts on it.
- **A local model's licence and its knowledge ceiling are both facts.** Julia 1
  is Apache-2.0 and CPU-native but its weights are at chance on mmlu/arc, so it
  answers control/interface questions and nothing that needs facts.
- **Want Jev's behaviour under your own licence rather than its weights ->
  distil it** (`autotrust/JEV-27B`; the caveats are in SKILL.md §6).
- **Learn to train -> pngwn scorer + kotoba-lang/typed-decisions repo.** Loss
  study: `open-jev` RLCDLoss (probability distributions, not one-hot) and the
  Together $17 Qwen3.5-4B recipe — `training-guide.md`.
- **No retraining at all, any open LLM -> `no-retrain-scoring.md`** (SGLang
  `/v1/score`, single-token labels, `/tokenize` check, OTHER/ESCALATE hatch).

## 8. DiffusionGemma-as-Jev variants + Kev + von

- **The diffusion stack is documented in `clone-ecosystem.md`** (PR #57250
  status, request knobs, DGX Spark numbers, Blackwell backend caveat, serve
  command, and the OpenJev / LocalJev / djev variants). The transferable wrap
  recipe is SKILL.md §7. Short version: it is an **inference method, not
  trained weights**, and Decision Index 0.2.1 puts every variant well below
  Jev — early author-run parity on selected items is not broad parity.
- Kev: `github.com/jaredpalmer/kev`, HF `jaredpalmer/kev-0.8b`/`kev-4b`/`kev-9b`,
  Apache-2.0 — Qwen3.5 bases + LoRA + pointer readout head, single-prefill
  parallel, `/v1/systemone`, Mac-trainable. Decision Index 0.2.1 skill: 14.60 /
  34.64 / 38.48 respectively.
- von: `github.com/wfzyx/von` — non-autoregressive System One, small,
  sub-15ms claimed (README only, unverified), HF weights reported.
