# API contract — TypeSafe SystemOne (Jev)

Source of truth: https://docs.typesafe.ai/api, https://docs.typesafe.ai/models,
https://docs.typesafe.ai/sdk. **Verified 2026-10-02.** When something here
disagrees with the live docs, the docs win — `https://docs.typesafe.ai/llms.txt`
is the page index and every page is available as Markdown by appending `.md`.

## Endpoint

```http
POST https://api.typesafe.ai/v1/systemone
Authorization: Bearer <API_KEY>
Content-Type: application/json
```

`GET /v1/models` lists the aliases your key can use (`name`, `description`,
`release_date`). Versioned ids such as `jev-1.13.0` are accepted in `model` even
though they are not listed.

## Request

```json
{
  "state": "string | object | array",
  "model": "jev-latest",
  "questions": {
    "<id>": { "type": "choice|score|noul", "instructions": "...", "criteria": {} }
  }
}
```

`model`, `state` and `questions` are **all required** by the HTTP API
(`questions` needs at least one entry). Only the SDK defaults `model`.

### state
- String: one text. Object: named fields/records/app state. Array: a sequence of
  messages or records. An object is the default choice for anything with parts.
- **Text only.** No image, audio or video. English is the training language;
  other languages including CJK are accepted with lower accuracy — test and watch
  confidence.
- Budget: **64k tokens per request**, and **32k for `state` + the single longest
  question**. State is ingested once and every question is evaluated against it
  in parallel.

### questions.<id>
- `<id>` is yours; answers come back under the same id. **Not sent to the model.**
  Always write the whole question in `instructions`.
- `instructions`: string, object or array. Put the question in one field and the
  data it refers to in others; refer to state parts by **backticked dot-and-index
  path** — `` `ticket.messages[0].text` ``, `` `order.charges` ``.
- `criteria`, by type (each entry may be string | object | array | null):
  - **noul**: optional `{true, false}` descriptions (`NoulCriteria` in the SDK).
  - **choice**: required map option → description, **max 255 options**. `null`
    is fine when the name stands alone. Add `other` / `none_of_the_above` when
    the list may not cover the input.
  - **score**: required ordered array of **2–10** level descriptions, low → high.
    Level number = array index from 0.
- Structure is accepted in `instructions`, in Choice option descriptions, in Score
  level descriptions and in `criteria.true` / `criteria.false` (`EntryType`).

### model
- `jev-latest` → `jev-1.13.0` (stable default; SDK default).
- `jev-preview` → currently the same `jev-1.13.0`; there is no preview build.
- Pin `jev-1.13.0` when thresholds are tuned, and log `response.model`. An alias
  moves on release, and a hosted answer can change without a version bump —
  Decision Index observed Jev's own output drift on 20 choices between two runs
  of the same week.
- **Above 255 candidate choices is not one request.** TypeSafe's own high-
  cardinality demo (Wikiracing, thousands of links per step) scores candidates
  independently and then makes an explicit choice over the survivors in a second
  stage, and notes the occasional slowdown this causes. A shortlist also bounds
  your recall: measure recall@k of the prefilter separately from the decider.

### Limits and price (as published 2026-10-02)
- **$42 per Btok input = $0.042 / Mtok. Output tokens are free.**
- **Rate limits: 100k tokens/second and 40 requests/second**, over either of
  which you get `429`. The docs state plainly that these are **adjusting
  dynamically and can change without notice**; higher limits are on custom and
  enterprise plans.
- ~950 input tokens per decision in JevBench's workload, i.e. **≈ $0.032–0.040 per
  1,000 decisions**. Cost is per *question*, not per call: one call with 13
  questions over a 54k-character document is one document's tokens plus 13 small
  questions, and 13 single-question calls pay for the document 13 times.
- Jev is **not** fine-tuned per customer; one set of weights serves every account,
  and `models.md` states it is not trained on customer requests or responses.
  ZDR is available on enterprise terms via `sales@typesafe.ai`.

## Response

```json
{
  "model": "jev-1.13.0",
  "answers": { "<same_id>": { "type": "..." } },
  "usage": { "input_tokens": 296, "output_tokens": 20 }
}
```

### noul answer
`{ "type": "noul", "noul": 0.95 }` — 0 = no, 1 = yes, 0.5 = unsure. No
`confidence`; the value *is* the distribution.

### choice answer
`{ "type": "choice", "choice": "billing", "probabilities": {"billing": 0.88, …}, "confidence": 0.81 }`
`probabilities` sums to 1 across every option you defined. **`confidence` is a
function of that same vector**, not extra information: on all eight worked JSON
responses published in the API and Choice pages it equals
`(n · p_max − 1) / (n − 1)` — the top-1 probability rescaled away from uniform,
`n` being the number of options (`{0.88, 0.12, 0}` over three → 0.81 published;
`{0.74, 0.26, 0, 0, 0}` over five → 0.67; `{0, 0.95, 0.05}` over three levels →
0.92; one-hot → 1.0). The docs' interactive explorer uses that expression and
calls it an approximation, and two of its five cases do not match it, so treat
this as a close description rather than a promised contract. Two consequences
worth designing around: it is *not* comparable across questions with different
option counts (`conf ≥ 0.5` ⇒ p_max 0.75 / 0.667 / 0.625 / 0.55 at
n = 2 / 3 / 4 / 10), and it ignores the tail. If you want a specific statistic,
compute it from `probabilities` yourself — SKILL.md §3 has the worked evidence.

### score answer
`{ "type": "score", "score": 1.05, "legend": {"0": "Calm", …}, "probabilities": {"0": 0.0, "1": 0.95, …}, "confidence": 0.92 }`
`score` = Σ level·prob, so it can land between levels; normalise with
`score/(len(criteria)-1)`. The Python SDK keys `probabilities` and `legend` by
`int`, not `str`.

### Errors
`401` bad key · `422` validation (the body names the field) · `429` rate limited ·
**`529` overloaded**. Retry 429/529 with exponential backoff and honour
`retry-after`. The SDKs retry by default.

## SDKs

```bash
pip install typesafe-sdk            # or: uv add typesafe-sdk
pip install "typesafe-sdk[http2]"   # HTTP/2 support (v0.7.2+)
npm  i @typesafe-ai/sdk
```

- **Python `typesafe-sdk`** — latest **0.7.2** (2026-09-26). 0.7.0 moved
  serialisation from `msgspec` to **pydantic** and added
  `system_one(..., response_model=MyModel)` for typed responses; 0.6.0 made
  `Score.criteria` an ordered sequence rather than an int-keyed dict;
  `AsyncTypeSafeClient` exists. Exports `Choice`, `Score`, `Noul`, `NoulCriteria`,
  `TypeSafeClient`, `AsyncTypeSafeClient`, `RetryPolicy`.
- **JavaScript `@typesafe-ai/sdk`** — **0.6.0** (2026-09-15); helpers
  `choice()`, `noul()`, `score()`.
- **Environment**: `TYPESAFE_API_KEY`, `TYPESAFE_BASE_URL` (default
  `https://api.typesafe.ai`), `TYPESAFE_DEFAULT_MODEL` (default `jev-latest`),
  `TYPESAFE_LOG_LEVEL`. Default per-operation timeout 10 s.
  `RetryPolicy(max_retries=2, backoff_initial=0.5, backoff_max=5.0,
  backoff_jitter=0.25, http_statuses={408, 429, 5xx}, respect_retry_after=True)`.
- **`TYPESAFE_BASE_URL` is how you point the official SDK at a local clone**
  (`decider`, `kev`, Laya's `/v1/systemone`, …) without changing a line of
  calling code.
- **Do not invent request fields.** `beam_width` and `weight` appear in docs
  *examples* marked illustrative; neither exists in `openapi.json`. `beam_width`
  in the cookbooks is client-side beam-search state, and `weight` is a
  composite-scoring concept, not a field.

## Gateways and framework integrations

| Integration | How to call it |
|---|---|
| OpenRouter | Model id is **`~typesafe/jev-latest`** (leading `~`), resolving to `typesafe/jev-1.13`, permaslug `typesafe/jev-1.13-20260917`. Point the stock SDK at `base_url="https://openrouter.ai/api"` with your OpenRouter key. **Gateway context is 32,000, not the native 64k** — a state that fits natively is rejected on the gateway. |
| Vercel AI Gateway | `https://ai-gateway.vercel.sh/typesafe` — `POST /typesafe/v1/systemone`, `GET /typesafe/v1/models`. Vercel also exposes a provider-neutral **evaluation** modality; it is the same capability and requires AI SDK 7+. |
| Pydantic AI Gateway | `base_url="https://gateway-us.pydantic.dev/proxy/typesafe"`, `model="jev-latest"`. |
| AI SDK | `@ai-sdk/typesafe-ai` (first-party, in `vercel/ai`), `ai` v7. Key is **`TYPESAFE_AI_API_KEY`**, not `TYPESAFE_API_KEY`. Question types are `choice` / `score` / **`boolean`** — `boolean` maps to Jev's `noul`. Confidence lives in `providerMetadata.typesafe.confidence[questionId]`, and the SDK documents that **probabilities are rounded to 2 dp and may not sum to exactly 1** — do not assert `sum == 1`. |
| Pydantic AI | `pydantic-ai-slim[typesafe]`. `TypeSafeModel` is a subclass of a new `DecisionModel` base: tools and output types become **routes** (one Choice over every tool + every output type — so the 255 cap bites sooner), and `FallbackModel` escalates to a real LLM when a question comes back unfillable or low-confidence. |
| LangChain | **`langchain-typesafe`** (first-party LangChain package, `0.0.1a3` alpha). Breaking change vs earlier alphas: **`questions` moved onto `invoke(state, questions)`** instead of being configured on `TypeSafeClassifier`. Returns `.nouls` / `.choices` / `.scores` plus `model`, `usage` and TypeSafe's `request_id`; emits LangSmith traces. |
| Effect | `@effect/ai-typesafe` (community) — a TypeSafe `DecisionModel` provider. |
| n8n | `@typesafe-ai/n8n-nodes-typesafe-ai` (first-party) — **Evaluate** and **Route** nodes. |
| MCP | **No official TypeSafe/Jev MCP server as of 2026-10-02**; the `typesafe-ai` GitHub org has none. |

## Baselines: run the same questions through an LLM

`github.com/typesafe-ai/system-one-adapter-python` is TypeSafe's own drop-in
replacement for `typesafe_sdk`'s `system_one`, backed by OpenAI / Anthropic /
Gemini / any OpenAI-compatible endpoint:

```python
from system_one_adapter import SystemOneAdapterClient, Noul
client = SystemOneAdapterClient(structured_outputs=True,
                                llm_answer_mode="probabilities")
resp = client.system_one(state="...", questions={"urgent": Noul(instructions="...")},
                         provider="openai", model="gpt-4o-mini")
```

The response is a `typesafe_sdk.SystemOneResponse` subclass, so **one question
set can be scored against Jev and against an LLM** — the only honest way to
answer "is Jev worth it here?".

## Competing decision models on the same wire shape

`https://ai-gateway.vercel.sh/v1/models` exposes exactly four models of type
`evaluation`, and two of them are not Jev:

| id | ctx | $/Mtok in | notes |
|---|---:|---:|---|
| `typesafe-ai/jev` | 32,000 | 0.042 | output free; `no_training: all` |
| **`liquid/d1`** (Liquid AI, 2026-09-29) | **65,536** | **0.040** | described as TypeSafe-compatible System One (Choice/Score/yes-no); vendor detail unverified |
| `convaiinnovations/laya` | 8,192 | **0** | the open Laya alter, **hosted free through 31 Oct 2026**, then billed |

Neither competitor advertises zero data retention on that gateway. Check terms
yourself; open weights or EU hosting is not a compliance answer.

## Demos and the source of the headline numbers

- Playground: https://console.typesafe.ai/playground (every docs example links a
  share URL). Keys: https://console.typesafe.ai/keys.
- **TypeSafe's own workflow evals: https://evals.typesafe.ai/** — four workflows
  (invoice processing, customer service, agent-trace observability, security
  incidents) comparing TypeSafe against OpenAI and Anthropic models on mean
  accuracy vs cost and time. The headline **193.6× faster / 444.6× cheaper** and
  the **70–500 ms** end-to-end range come from this launch material
  (`typesafe.ai/blog/introducing-system-one-models-and-jev`), **not** from the
  documentation. The docs only claim "most queries complete in about 100 ms".
  TypeSafe's own caveats on those numbers: the reference is the **average of
  GPT-6 Astra and Fable 5.1** driven through TypeSafe's own System One LLM
  wrapper over OpenRouter, the workflows were written by their own capabilities
  team, the reference "biases answers towards OpenAI and Anthropic's models",
  OpenRouter routing may favour complex queries, and they expect "these are on
  the higher end of real world gains". Quote them as vendor claims with that
  context attached.
- Cost anchor from the same post: their Doom demo ran ~10 queries/second at about
  **$7 per hour**.
