# Patterns — composing Jev answers in code

Measured results below are TypeSafe's published cookbook runs. **Most of them
were produced on `jev-1.12`**, not the current `jev-1.13.0` — treat the direction
as evidence and the exact numbers as a snapshot.

## The four structural moves

1. **Speculative fan-out** — send every question your code might need in one
   call, including ones that only matter for some inputs, and let code decide.
   13 questions over the 54k-character GDPR article: **12.2× cheaper, 10.0×
   faster** than 13 separate calls, *with identical answers* — 5 repeats each way,
   most answers bit-identical, std dev exactly 0.0. Batching adds no noise.
   Coding agents default to one-question-per-call; correct them.
2. **Confidence-gated routing** — the answer says what, confidence says whether to
   act. High → auto, medium → confirm/flag, low → human or a reasoning model.
   Thresholds scale with the consequence and are fitted on your data.
3. **Composite scoring** — split a broad judgment into atomic Scores, normalise
   each `score/(n_levels-1)` to 0–1, weight and sum in code. Weights stay
   reviewable and a priority change is a constant, not a new prompt. Or train a
   CatBoost/logreg on the probabilities as features.
4. **Cascade** — cheap extractor (small LLM) → **verify every field with a Noul
   that returns P(something is wrong)** → escalate to an expensive reasoning
   model only when a verifier fires. TypeSafe's SDE cascade uses
   `gpt-5.4-mini` → Jev verifier → `gpt-5.5`, and gets most of the reasoner's
   quality at a fraction of the cost. They deliberately do **not** use structured
   outputs or tool calling for the extractor: a schema-following mistake is not
   the mistake an LLM makes here, and if it does fail the schema it is confused
   for a deeper reason that constrained decoding cannot fix.

## Selection patterns that generalise

- **Select, don't generate.** Find the candidates in code — a regex tuned to
  *over*-find, BM25, an embedding top-k — then let a Choice pick which candidate
  the question is about, and let code copy the pick verbatim. The returned value
  is then one of your own spans: it cannot invent a value or transpose a digit.
  This is the general form of "code owns generation, the model owns selection".
- **A Choice always has a winner.** Its probabilities sum to 1, so the highest
  option ranks first *even when none of them fit*. Any "which line of this
  document answers the question" design therefore needs a companion **Noul for
  existence** — otherwise the top line is an artefact of the normalisation, not an
  answer. Same trap whenever the real answer is "none of these": either add an
  explicit `none` option or ask a separate presence Noul.
- **Check candidate coverage.** The model cannot choose a value you did not
  offer. If recall of the candidate set is your real problem, more instructions
  will not fix it.
- **Granularity rollup.** When unsure, report a *coarser* label derived in code
  from the taxonomy rather than making a second call. Classifying SEC filings
  into 75 SIC industry groups with one Choice: at `confidence >= 0.9` the group
  was reported and was right 90 % of the time; below it the group was wrong 60 %
  of the time but its parent **division** was right 70 %. One request per
  document, and the answer's own confidence chose how specific to be.
- **Two-stage skim, then read.** Rank a wide roster from one-line descriptions,
  then re-ask about the top 3 with their real detail — and let the second pass
  reject all of them. Picking at most one skill out of 182, over 488 turns:
  agent alone loaded the wrong skill 16.8 % of the time and loaded one when
  nothing fit 9.8 %; with the two-pass suggestion, 7.3 % and 4.0 %; an oracle
  handed the right answer still failed 2.5 % / 1.2 %. The floor is not zero, and
  the second pass must be allowed to say "none".
- **Asymmetric mistakes need a third option.** When one error is much more
  expensive than its opposite, a yes/no question forces a bad trade. Entity
  alignment over 450 candidate pairs uses one Score plus three Nouls naming
  *which* fields disagree, and a third outcome ("a curator should look") —
  because a wrong merge drags every fact of both entities with it, while a missed
  merge only leaves a duplicate.
- **Rerank after a cheap filter.** BM25 shortlist of 30 over 40 legal queries,
  then one question per query-candidate pair: top-1 5 % → 18 %, top-10 38 % → 62 %.
  The filter's recall@k is the ceiling; the reranker only reorders what survives.
- **Hierarchies: walk, beam, and show the subtree.** One Choice per level with the
  children as options; the value of an option can be its **subtree**, so the model
  sees what lives under a branch before committing to it. Beam search keeps the
  best K paths by length-normalised geometric mean,
  `Π(edge_probabilities) ** (1 / decisions)`, and each API call evaluates K paths
  in parallel. Trim subtrees that get large to direct children plus a sample of
  leaves.
- **Function calling without JSON.** Function names are a Choice; each closed-set
  argument is its own Choice over `Literal` values; the confidence on each
  argument decides whether to fill it or ask.
- **Guardrails as a battery.** One request per message with a Noul per hazard
  (jailbreak, harm, diagnosis/dosage, self-harm…) plus a Score for how much harm
  complying would do, then four named routes: pass, review, block, crisis path.
  Run it on the way in *and* the way out. Their finding: "Ignore your
  instructions" is *scored as* a jailbreak rather than working as one.

## Two lessons that are really one

- **Never put the input state among the options.** If the options include what
  the user already typed, "the thing that is already there" becomes a legal
  answer and the call teaches you nothing. Real case: a terminal ranker offered
  `ls -l` and `ls -la`, asked which to suggest, while the user had typed `ls -l`.
  `ls -l` is correct by the stated criteria, is what the scorer ranked highest,
  and is useless — accepting it changes the line by zero characters. Filter the
  input itself out of the option set before asking.
- **A bounded decision ranks; it does not verify.** Anything code can check
  deterministically must be resolved in code *before* the model sees the
  candidates. Does the path exist, does the installed binary still accept this
  flag — these are facts, not judgment. Handing a scorer a stale candidate and
  asking it to pick produces a confident, well-calibrated, completely wrong
  answer: the model is not wrong about relevance, it was never asked whether the
  thing exists. Corollary for thresholds: once code has removed every candidate
  failing a hard check, low `confidence` means "all genuine, cannot tell which
  fits" (get a second opinion); if hard-failing candidates are still in the list,
  low confidence means "this set is junk" (filter, don't re-prompt).

## Turning answers into features

The AutoResearch cookbook turns free text into numbers for a supervised model:
29 Score questions × 2 columns + 9 Nouls × 1 column = **67 numeric columns**,
where **a Score answer contributes two features — the level it points at and how
spread out it is — and a Noul contributes one probability**. On 2,000 wine
reviews (held-out RMSE, lower is better): predict the training mean 3.09 →
CatBoost on word counts 2.47 → ask Jev for the score and rescale 2.15 → 18
questions from one proposal call 1.87 → 38 questions after five rounds of the
loop reading its own worst predictions **1.77**. Most of the gain is the first
proposal call; the loop is the last 0.1.

## Triage skeleton

```python
from typesafe_sdk import Choice, Noul, Score, TypeSafeClient

QUESTIONS = {
  "topic": Choice(instructions="Which team?", criteria={"billing": "...", "orders": "...", "account": "..."}),
  "refund_requested": Noul(instructions="Does `ticket.message` explicitly request a refund?"),
  "frustration": Score(instructions="How frustrated?", criteria=["Calm", "Frustrated", "Very angry"]),
}
with TypeSafeClient() as client:
    resp = client.system_one(state={"ticket": {...}, "policy": {...}}, questions=QUESTIONS)
a = resp.answers
if a["topic"].confidence < 0.75:
    return route_to_human()
if a["topic"].choice == "billing" and a["refund_requested"].noul >= 0.7:
    return route_to_billing()
prio = "high" if (a["frustration"].confidence >= 0.7 and a["frustration"].score >= 1.5) else "normal"
```

## When a second request is actually justified

Only when the second request's *state or options do not exist yet*: you need the
first answer to fetch more evidence, to build the state, or to choose the next
options. Three published cases — re-read the top 3 skills with their full text;
merge hard-wrapped lines into blocks, then classify the blocks; take one Choice
answer as the children of the next question. Anything else belongs in the first
call. Cost is per question, not per call, and extra questions barely move
latency.

## Measuring self-consistency

TypeSafe ran a 14-Noul rubric 15 times per condition with a fresh throwaway `uid`
field in `state` on every call, so each repeat is an independent draw rather than
a repeat of the same payload. The finding: LLM answers move from run to run even
at temperature 0, while TypeSafe's stayed put, and the LLM conditions disagreed
with each other on the judgment calls. Give your repeats fresh ids, and cache
keyed on the rubric hash so editing a question cannot silently replay an old
answer.

## Cookbooks worth stealing

`parallel_questions`, `rerank`, `semantic_find`, `autoformat`,
`pre_parsed_value_extraction`, `function_calling`, `skill_suggestion`,
`entity_alignment`, `citation_check`, `llm_guardrails`, `sde_cascade`,
`date_extraction`, `hierarchical_classification`, `autoresearch_feature_discovery`,
`classification_using_confidence`, `classifying_rag_passages`,
`consistency_noul`, `consistency_choice`. All at
`https://docs.typesafe.ai/cookbooks/*.md`.

- Judge layer: OBSERVE → JUDGE (Jev or clone, typed) → high ACT / medium verify
  + human / ambiguous → reasoning LLM → ACT. Semantic IF: `if safeguarding_risk
  > 0.95: escalate()`.
- Six-question gate: is it a judgment? bounded? atomic? state-contained?
  expert-seconds? machine-consumed? 5–6 yes = Jev; 3–4 = decompose first; 0–2 =
  something else.

## Rules

- Decompose broad → atomic. One condition per Noul, one dimension per Score.
- Keep questions **and** thresholds in one file, because those are what a human
  reviews. Agents write mediocre questions on the first pass; expect to edit them.
- Filter the input state out of the options, and resolve every checkable fact in
  code first.
- If all you want is the best option, take the argmax — thresholds are for risk,
  not for taste. Several acceptable alternatives also spread probability, and low
  confidence does not invalidate a harmless preference.
- A choice among typed answers guarantees the *interface*, never the truth.

## Rollout (shadow → automate safest branch first)

1. One bounded low-risk decision with enumerable answers; write the rubric (what
   belongs in each option) before calling.
2. Collect representative examples with expected answers, including ambiguous and
   adversarial cases, option-order swaps, long/wide inputs and abstentions.
3. For open models, screen with `decision-index.md` (breadth) and `jevbench.md`
   (Jev-contract fitness) before trusting a model card; then keep a domain eval.
   Never substitute a leaderboard score for your own distribution.
4. Shadow mode beside the current workflow, logging probabilities beside the
   current result.
5. Plot accuracy vs confidence, set thresholds from your data (top-p and top-2
   margin, per action by stakes). Report coverage separately: unanswered cases
   count wrong.
6. Automate the safest branch first; uncertain cases go to a human or a stronger
   model.
7. Pin and log model version, questions, criteria and thresholds; replay the same
   eval set after any change.
- Rule of thumb: deterministic code that already solves it correctly stays. If a
  statement can express it, it should not be a model call.
