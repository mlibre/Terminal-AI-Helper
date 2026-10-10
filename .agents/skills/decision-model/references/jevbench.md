# JevBench — Jev-shaped benchmark (Benchmark Heaven, independent)

Independent benchmark for Jev-class decision models: state + bounded rubric in,
typed answer out. Run by Benchmark Heaven (one-person hobby project, Florian
Standhartinger), not affiliated with TypeSafe AI. Harness + items + scoring
(MIT): `github.com/fstandhartinger/jevbench`. Board: `https://benchmarkheaven.com/jev-models`.

**Snapshot below: protocol `jevbench::v1.5`, revision v1.5.5, verified 2026-10-03**
from `https://benchmarkheaven.com/api/jevbench/v1.5.5` and
`docs/METHOD-v1.5.md` (aggregate JSON sha256 `591e56fe98b5…`). **This skill previously cited v1.4.2; scores do not carry across the
protocol change — see §2.**

## 1. What v1.5 measures

- **1,624 decisions per system** (904 open + 720 sealed), 106 ranked of a
  112-system roster. Question types **Choice 50 % / Noul 25 % / Score 25 %**,
  scored natively. Tier weights **easy 10 / standard 20 / judge 30 / hard 40**
  — judge + hard now carry 70 %, because that is where Jev-class systems
  actually differ (field means in v1.4.2: easy 0.95, standard 0.82).
- **Four axes 0–100**, combined as a weighted harmonic mean (power mean p = −1).
  For Intelligence, Speed and Cost separately, an axis below 50 multiplies the
  composite by (axis/50)². `Int = 100·(0.5·I_open + 0.5·I_sealed − penalty)`.
- **Chance is per item** (Choice 1/options, Noul 0.5, Score uniform-level MAE),
  and each item is chance-corrected (CC) before the overfit penalty.
- **Overfit penalty**: excess open-minus-sealed gap over the *field median*,
  above 8 CC points (was a fixed 25 raw points).
- **Sealed set**: 720 decisions drawn fresh from a 2,805-item authored and
  critic-reviewed pool, with the same tier mix as the open half. Sealed items
  and answers stay private; only system-level aggregates are published.
- **Speed** = `100 − 20·log10(s / 0.1s)`, mean of p50 and p95, measured serially
  (one decision per request). Self-hosted rows carry the v1.4 ×2 + 0.15 s
  production adjustment — **an assumption, not a measurement**; the raw value is
  published beside it and hosted rows are unadjusted.
- **Cost** = `100 − 30·log10($ / 1,000 decisions / $0.001)`, tokens pooled over
  all 1,624 decisions. **Per 1,000 decisions, never per 1,000 tokens.** Rows
  without a bookable price get an estimate from the base model's reference price
  and are labelled `estimate` — do not compare an estimate to a bill.
- **Support**: a type a system does not implement is *excluded*, never scored
  zero; only rows covering all three types are ranked. Label-only systems are
  scored through fixed adapters (Noul → Yes/No label; Score → Choice over
  levels). **Calibration = 0 for label-only rows.**
- **Ties are now published**: paired bootstrap, and "`#1` only if significant".
  Per-system 95 % CIs are in the JSON (`composite_ci95`).
- **API flag** marks rows where the operator endpoint saw sealed item text
  (without answers) — true for Jev. Treat that as a disclosure, not a penalty.

## 2. v1.4.2 → v1.5: why Jev's number moved

| | v1.4.2 | v1.5 |
|---|---|---|
| Open decisions / system | 534 (231 published) | **904** (601 published) |
| Sealed decisions / system | 308, all hard-style | **720**, same tier mix as open |
| Sealed pool | 308 fixed | **2,805**, fresh sample per release |
| Sealed share of Intelligence | 20 % | **50 %** |
| Tier weights | 14/28/28/30 | **10/20/30/40** |
| Request types | Choice-centred | **Choice 50 / Noul 25 / Score 25** |
| Chance baseline | tier-average; sealed fixed 0.293 | **per item** |
| Overfit penalty | gap > 25 raw points | **excess gap over field median > 8 CC** |
| Ties | not shown | **paired bootstrap; CI published** |

**Jev 1.13.0 measured 63.3 on v1.4.2 (rank #2) and 72.1 on v1.5.4 (rank #3).
Nothing about the model changed.** The Intelligence axis was redefined (sealed
share 20 %→50 %, new per-item chance correction, new penalty) and the item pool
grew. Speed moved +0.55 and Cost +2.76 across the same boundary, which is what a
real model change would look like; Intelligence moved +19. **Only cost, latency
and raw per-tier accuracy are comparable across the boundary.** This skill's
previous "sealed accuracy ~33–37 %, chance 29.3 %" note is void: on v1.5.4 Jev's
sealed accuracy is 72.5 with an open-minus-sealed gap of **−0.9**.

The board moved for the same reason: v1.4.2's #1 (decider-4b v2, 64.1) is #7 at
71.3, and v1.5's #1 (Cygnet) was #4 at 61.8.

## 3. Leaders (v1.5.5, 109 ranked)

v1.5.5's headline preset is the **Capability Score** (mean of Intelligence and
Calibration); the old I/C/S/$ harmonic-mean composite with a sub-50 axis gate
still exists internally but is no longer the ranking. "Jev-class" is a defined
band: systems at most 2x Jev's cost and median latency. Latency p50 raw; cost is
`$ per 1,000 decisions`.

| # | System | Capability | I | C | $/1k | p50 ms |
|---|---|---:|---:|---:|---:|---:|
| 1 | **Jev 1.13.0 (TypeSafe, closed)** | **80.0** | 72.0 | **88.0** | 0.032 | 0.62 s |
| 2 | Winnow-12B Q8 (Eldan Ring) | 79.3 | **74.4** | 84.1 | 0.028 | 0.34 s |
| 3 | Cygnet (blockbrain, frozen Gemma-4-12B-it) | 79.0 | 71.1 | 87.0 | 0.028 | 0.23 s |
| 4 | Surogate Rune 26B-A4B v3 | 79.0 | 69.7 | 88.3 | 0.050 | 0.35 s |
| 5 | Jev-Omni (akhilaaa3, Gemma-4-12B merged) | 76.5 | 70.5 | 82.6 | 0.029 | 0.38 s |
| 6 | djev (Maisa, diffusion-gemma) | 76.4 | 72.3 | 80.4 | 0.053 | 0.25 s |
| 7 | JevK5 v0.3 (4B) | 72.3 | 56.3 | 88.3 | 0.017 | 0.18 s |
| 8 | Plumb-4B (crh225, JevK5 v0.2 + LoRA) | 71.6 | 55.8 | 87.4 | 0.017 | 0.18 s |
| 9 | Decision 4B v1.2 (FlyMyJev) | 71.1 | 53.7 | 88.6 | 0.017 | 0.18 s |
| 10 | Imajev-4B | 70.8 | 53.5 | 88.1 | 0.017 | 0.23 s |
| 11 | decider-4b v2 (Mapika) | 70.7 | 55.8 | 85.6 | 0.015 | 0.20 s |
| 12 | Clef-Flash (Cloudflare, Qwen3.5-9B) | 70.3 | 53.1 | 87.6 | 0.059 | 0.55 s |

## 4. How to read this board

- **Ranks 1–10 are one statistical cluster.** Capability scores at the top
  (Jev 80.0, Winnow 79.3, Cygnet 79.0, Rune 79.0) differ by about a point and
  the underlying CIs overlap. Treat the ordering as noise and pick on the axes
  you actually care about.
- **Capability is now I+C, but Speed and Cost still decide the pick.** Jev is
  #1 on Capability, not on speed or price: the 4B rows (JevK5, decider, Hopper,
  Decision 4B) cost ~4–10x less and run ~3–5x faster for ~15–25 points of
  Intelligence, where Speed/Cost historically did that sorting. The old
  composite-with-floor ranking punished weak axes harder (JevK5 71.9 → 63.2,
  decider-4b v2 71.3 → 61.6, Hopper 67.5 → 46.9); the new one does not, so a
  row's Intelligence deserves a direct look before you choose it. **If your use
  cannot tolerate a wrong answer, read I, not the composite.**
- **Two groups, not one ladder.** Intelligence ≥ 70: Winnow, Jev, Cygnet,
  Jev-Omni, Rune, djev. Intelligence 49–62: everything 4B. Speed and Cost are
  inversely ordered with group — that is the whole trade.
- **Jev's own profile**: best-in-class Calibration (88.0, ahead of Cygnet 87.0
  and Rune 88.3 ≈ tie), lowest-but-one Cost (54.7), Speed 83.8 (hosted 616 ms
  p50, unadjusted), Intelligence 72.0. It is the row to copy when your code
  auto-acts on a probability; it is not the fastest or cheapest row.
- **Schema validity held**: Jev returned `invalid_rate` 0 across 799 Choice,
  495 Noul and 330 Score decisions. "Cannot hallucinate a shape" survives
  independent measurement; "returns the right label" does not follow from it.

## 5. Row disclosures worth reading before you pick

- **Cygnet** (#3): frozen `google/gemma-4-12B-it`, no training. Options become
  letters in benchmark order, logits masked to letters, T = 3.4 fitted on the
  author's own items. Shim MIT, weights Apache-2.0 + Gemma Prohibited Use
  Policy. Over-context and >26-option inputs return HTTP 422 and are counted
  wrong. The no-training logit-readout pattern is in
  `no-retrain-scoring.md`; the fitted temperature on author items is the reason
  to re-run your own eval.
- **Winnow-12B Q8** (#2, highest Intelligence at 74.4, sealed 76.4): merged LoRA
  on Gemma-4-12B, ~12.7 GB Q8, llama.cpp `/v1/systemone` plus ordinary chat and
  vision from one load. Entropy-based confidence is **not** calibrated.
- **JevK5 v0.3 / Plumb-4B** (#7/#8): Qwen3.5-4B LoRA, repo not recorded in the
  row and openness `unknown`. Their A→C collapse is the gap penalty plus the
  Intelligence floor: I_open 61.6 → I_sealed 50.9.
- **decider-4b v2** (#11): Apache-2.0, letter-logit readout ÷ T = 1.935,
  `decider-ai` wheel, `POST /v1/systemone`. Best Cost axis in the top 10 (64.5)
  and a 26 ms raw p50, at half of Jev's Intelligence.
- **Jev-Omni** (#5): Gemma-4-12B merged, Apache-2.0 following Gemma 4; the closest
  open row to Jev's profile on every axis.
- **Hopper** (#14): still the best Calibration among 4B rows (87.9), and still
  discloses heavy public-directed development. Its C score of 46.9 is the board's
  cleanest example of the composite punishing one weak axis.
- **reflex 4B**: p50 1358 ms raw — an order of magnitude slower than the
  other 4B rows. Latency, not accuracy, is its problem here.
- **djev** (#6): diffusion structured read on Google's DiffusionGemma
  weights; no djev-specific weights — an inference method, not a trained model.
  Cost 0.053/1k is the highest of the leaders.
- **Surogate Rune v3** (#4): strongest Intelligence among the self-hosted rows
  (69.7) but the worst Cost (49.0).

## 6. Caveats that change selection

- **The sealed pool rotates.** v1.5 draws a fresh 720 from a 2,805-item pool each
  release, so sealed numbers move between releases for a fixed model. Only
  compare within one revision.
- **Do not compare across cost bases or latency paths.** Every open row here is
  an `estimate` derived from a reference price; Jev's is an estimate from list
  price too. Self-hosted latency carries a ×2 + 0.15 s assumption while Jev's is
  measured as-is over HTTPS. Pin GPU, TP size, image and serving commit before
  comparing speed, and read the `raw` column, not the adjusted one.
- **Option order matters for small models** (the v1.4.2 open-alternative-jev row
  scored 72 % vs 21 % on answer-judging items from a pure A/B flip). Include order
  swaps, and `order_invariant` averaging for >10 options, in your own eval.
- **Excluded ≠ bad.** Apple-Silicon-only, gated weights, 404 repos, unshipped
  eval dependencies, schema mismatches and auth-walled endpoints are
  availability facts; the board lists them under "who could not be measured".
- **`api_flag` on Jev** means the operator endpoint saw sealed item text. It is a
  disclosure about the evaluation, and the only way to know it is true is to
  publish the flag at all.

## 7. Access notes (things that will waste your time otherwise)

- The live JSON path is **`/api/jevbench/v{version}`** — the `v` is required.
  `/api/jevbench/1.5.4` returns 404.
- **GitHub Releases stop at v1.4.2.** v1.5.x exists only as commits plus frozen
  method files in `docs/` (`METHOD-v1.5.md` + addenda A1–A5 + `SHA256SUMS`).
  Checking releases alone will convince you the current version is v1.4.2.
- The board page is server-rendered; the numbers are in the HTML.
- Method doc and release notes disagree on which axis preset is "the headline"
  (the API marks **A**, `METHOD-v1.5.md` says **B**, 40/20/20/20). For Jev the
  two differ by 0.03, so it does not matter there — but quote the preset letter
  with any composite you publish, and prefer the axes.

## 8. How to use this with Decision Index

- Screen **Jev-contract fitness** here (typed outputs, calibration, speed, cost
  per 1,000 decisions, sealed generalisation, support signature), breadth with
  `decision-index.md`, then your own domain eval.
- Both reverse narrow card claims in the same direction: Laya and CLM look strong
  on targeted evals and score 6.04 / 7.40 broad skill (Decision Index 0.2.1).
  Keep narrow winners for their domains; do not infer general Jev parity.
- Re-fetch before recommending. This benchmark is versioned, its sealed pool
  rotates, and the formula has changed twice in two weeks.
