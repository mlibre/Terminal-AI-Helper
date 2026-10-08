# Multimodal decision models — Image JevBench and AudioJevBench

**Verified 2026-10-03** on `benchmarkheaven.com/image-jev-bench` (v0.1.5) and
`/audio-jev-bench` (v0.1, dated 29 Sep 2026). Same operator as JevBench
(`references/jevbench.md`); board methodology and disclosures there apply.

Why this is a separate file: **Jev is text-only**. The Jev wire shape
(`state` + Choice/Score/Noul + probabilities + confidence) has been cloned by
multimodal models that take an image or an audio stream as well, and the two
boards are how a Jev-shaped *multimodal* decision model is compared. Both reuse
the Jev-class definition — **at most 2× Jev 1.13.0's cost and median latency**
(Image) or **≤ 1.0 s median latency and ≤ $0.25/1k decisions** (Audio) — and the
Capability Score = mean(Intelligence, Calibration). Numbers are **not**
comparable to JevBench's, because the items are different; the cost/latency
reference point is.

## 1. Image JevBench v0.1.5

Decision models that judge **images** (intent, object presence, screenshot
state, visual urgency) through the Jev question shape. 38 Jev-class systems
ranked. Board: `benchmarkheaven.com/image-jev-bench`.

| # | System | Capability | I | C | $/1k | p50 |
|---|---|---:|---:|---:|---:|---:|
| 1 | **Wity-1** (closed decision API, base model undisclosed) | **85.7** | 83.7 | 87.7 | **0.0074** | 0.71 s |
| 2 | JPT-9B (kirp / `llm2jev`, Qwen3.5-9B) | 83.6 | 75.7 | **91.5** | 0.053 | 0.46 s |
| 3 | Imajev-4B (Qwen3.5-4B) | 82.1 | 73.8 | 90.5 | 0.020 | 0.35 s |
| 4 | shisa-de-1 (Gemma 4 26B-A4B) | 82.1 | 72.4 | 91.8 | 0.056 | 0.48 s |
| 5 | NeoHorse Jev 4B | 82.0 | 72.9 | 91.2 | 0.041 | 0.43 s |
| 6 | imajev 9B (Qwen3.5-9B) | 82.0 | 74.7 | 89.3 | 0.063 | 0.53 s |
| 7 | Surogate Rune 26B-A4B v3 | 81.3 | 74.9 | 87.8 | 0.055 | 0.48 s |
| 8 | JevAny-27B RLCR | 80.1 | 70.2 | 90.0 | 0.055 | 0.47 s |
| 9 | Jevify Gemma 4 26B-A4B | 79.8 | 71.6 | 88.0 | 0.053 | 0.46 s |
| 10 | JevAny-27B SFT | 79.8 | 69.6 | 90.0 | 0.054 | 0.47 s |
| 11 | JPT-4B (Qwen3.5-4B) | 77.1 | 66.9 | 87.3 | 0.043 | 0.40 s |
| 12 | Jev-Omni (Gemma 4 12B IT) | 76.9 | 63.9 | 89.9 | 0.022 | 0.30 s |

Three things to take from it:

- **The leader is not open.** Wity-1 is a closed decision API at $0.0074/1k —
  0.23× Jev's price at base-model-reference pricing, and 0.82× even at
  Qwen3.6-35B-A3B list prices, so it clears the cost cap on either basis. Its
  Capability Score itself does not depend on price; only the class membership
  does. Verify the endpoint before designing around it.
- **Calibration, not Intelligence, separates the field.** Every row below #1
  scores 87–92 calibration against 64–76 intelligence — the inverse of the text
  board, where the fast 4B rows are the badly calibrated ones. A model whose
  confidence you will auto-act on wants this board's column, not JevBench's.
- **The Jev-clone names carry over and some move.** Imajev-4B is #3 here and
  #10 on the text board; Jev-Omni is #12 here and #5 there; JevAny-27B and
  Rune hold up. A model that is unremarkable on text can be strong on images,
  so a text board does not rank an image candidate.

## 2. AudioJevBench v0.1 (29 Sep 2026)

Decision models that make the judgments a **voice agent** needs straight from
the caller's audio — intent, escalation, command safety, sentiment, urgency,
speaker verification, sound events. 17 systems, 988 scored items (112 public +
876 sealed). Board: `benchmarkheaven.com/audio-jev-bench`.

Ranked, full coverage, Jev-class (≤ 1.0 s median latency and ≤ $0.25/1k):

| # | System | Capability | $/1k · median |
|---|---|---:|---|
| 1 | **Qwen3-Omni-30B-A3B-Instruct** (self-hosted GPU) | **83.2** | $0.049 · 660 ms |
| 2 | Qwen2.5-Omni-7B | 68.1 | $0.063 · 676 ms |
| 3 | Qwen2.5-Omni-3B | 53.8 | $0.060 · 522 ms |
| 4 | Voxtral Mini 3B (2507) | 47.8 | $0.070 · 383 ms |
| 5 | Ultravox v0.5 (Llama 3.2 1B) | 31.4 | $0.019 · 224 ms |
| 6 | MOSS-Audio-4B-Instruct | 27.9 | $0.061 · 396 ms |
| 7 | Qwen2-Audio-7B-Instruct | 12.8 | $0.11 · 653 ms |

Two traps on this board:

- **Hosted APIs are scored on the 112 public items only**, because sealed items
  never go to a third-party API. Gemini 3.8 Flash shows Capability **94.4** at
  $1.53/1k and 1.7 s, Gemini 3.5 Flash 83.1, Gemini 3.1 Flash-Lite 72.1 — all
  public-only and outside the Jev-class cost/latency band. The board shows them
  regardless; they are **not** comparable with the ranked rows.
- **Sound classifiers answer only the sound-event family** and are listed as
  partial rows, not decisions.

Consequence for the Jev contract: audio and image decision models are not Jev
(drop-in `/v1/systemone`) in general. JevAny, xor, Rune and NeoHorse expose an
image path on their own stacks (see `open-alters.md`); an *audio* decision model
is a different deployment (a streaming omni/audio model prompted with the same
rubric shape), and the wire contract is yours to write. The reusable part is the
question design and the threshold discipline, not the endpoint.