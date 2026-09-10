> **Status (Sept 2026): current, amended by `09_SMIA_Architecture_v4_Analyst_Agent.md`.** Every definition here (relative engagement, recency weighting, eligibility, confidence tiers, the mix, whitespace and transfer formulas) is implemented as a deterministic, unit-tested function in the tool layer. §7's may/may-not contract is amended by doc 09 §6: the agent may propose dimensions and hypotheses and choose what to investigate; it still may not introduce a number a tool did not return.

# SMIA Intelligence Framework v1 — How the Playbook Decides

**Purpose:** The decision logic that turns collected posts into recommendations. This answers the question every stakeholder should ask of an AI system: *how does it know what's right?* The answer, made precise below: **it doesn't guess — every recommendation is a deterministic score with named thresholds, and the AI's only job is to narrate scores it cannot alter.**

**Companion to:** Playbook Template v1 (defines *what* the playbook says) · AI System Design (defines *where* this logic runs). This document defines *how each claim is computed*.

---

## 1. The core principle: scores decide, the agent narrates

There are two ways to build an "intelligence" layer. The first hands the model raw data and asks "what should this brand do?" — fast to build, impossible to audit, inconsistent week to week, and vulnerable to a competitor's post nudging the model's judgment. The second computes every judgment as a deterministic score in SQL, then hands the model *scored, ranked evidence* and asks it only to explain. SMIA is the second kind.

Consequences of this choice:

- **Auditable.** "Short educational video is your top cell" traces to a number anyone can recompute. A stakeholder who disagrees argues with a threshold, not with a vibe.
- **Consistent.** The same data produces the same recommendation on every run. Model upgrades change the prose, never the verdict.
- **Testable.** Scoring functions get unit tests; the golden set tests classification; the grounding validator confirms the narrative matches the scores. Nothing "right" is unmeasurable.
- **Injection-proof at the judgment layer.** A competitor's caption can try to talk to the model, but the model isn't making the call — the SQL is.

The agent adds what only language models can: readable synthesis, explanation of *why* an anomaly likely happened, connective tissue between findings, and drafting hooks. It never introduces a number, a ranking, or a recommendation that the scoring layer didn't produce.

---

## 2. The evidence ladder

Every playbook claim climbs the same five rungs. A claim that can't complete the climb doesn't ship.

```
1. OBSERVATION      a post exists, with metrics and labels        (pipeline)
2. NORMALIZATION    relative engagement — comparable across accounts  (§3)
3. AGGREGATION      cell scores over format × tone × theme        (§4)
4. ELIGIBILITY      evidence thresholds — may we say anything?    (§5)
5. RECOMMENDATION   ranked, confidence-labelled guidance          (§6)
──────────────────────────────────────────────────────────────────
   NARRATION        the agent writes prose over rungs 1–5 only
   REVIEW           a human approves before anything ships
```

## 3. Rung 2 — the yardstick: relative engagement (RE)

Raw likes reward big accounts, not good content. Every judgment in SMIA therefore uses:

**RE(post) = (likes + comments + shares) ÷ median engagement of the same account's posts over the trailing 90 days**

RE = 1.0 means "normal for this account"; 3.0 means "three times its normal pull." This makes a 2k-follower fintech and a 500k-follower bank comparable, uses only observable data, and is computed entirely from `metric_snapshots`. Refinements, both deterministic:

- **Maturity window:** a post's RE is read at day 7 after posting (or latest available), so fast-spiking and slow-building posts are compared at the same age.
- **Recency weighting:** in aggregates, each post carries weight `w = 0.5^(age_days / 28)` — a 28-day half-life. Last month matters more than last quarter; nothing is ever fully forgotten.

## 4. Rung 3 — cell scores

The unit of tactical judgment is the **cell**: a combination of format × tone (× theme where sample permits). Per tenant, per cell:

- `n` — weighted post count in the cell (competitive set, trailing 90 days)
- `RE_med` — weighted median RE of the cell's posts
- `trend` — RE_med of the last 28 days ÷ RE_med of the 28 before ("is this rising?")
- `share` — the cell's fraction of the set's total posts ("how crowded?")

These four numbers per cell are the entire factual substrate of the playbook. They are computed once per pipeline run, cached, and every section below reads them.

## 5. Rung 4 — eligibility: when are we allowed to say anything?

The fastest way to lose trust is a confident claim on four posts. Hard gates, checked before any claim is rendered (values are config, flagged as a stakeholder decision):

| Claim type | Minimum evidence | Below minimum, the playbook says |
|---|---|---|
| "X is working" (cell claim) | n ≥ 10 | cell listed under "collecting — too early to call" |
| Cadence recommendation | ≥ 4 weeks history and ≥ 3 active competitors | observed range only, no recommendation |
| Whitespace call | ≥ 4 weeks history, and demand evidence per §6.3 | "unproven gap" — explicitly *not* whitespace |
| Cross-industry transfer | pattern n ≥ 15, lift ≥ 1.5, from a different industry | not surfaced at all |
| Trend statement ("rising") | n ≥ 5 in *each* compared window | no trend arrow shown |

**Confidence labels** ride on every claim: **Established** (n ≥ 25) · **Directional** (10 ≤ n < 25) · below 10 the claim doesn't exist. The playbook shows the label; honesty about thinness is itself a trust feature.

## 6. Rung 5 — the recommendation formulas, section by section

**6.1 "What's working" — top cells.** Rank eligible cells by `RE_med`, tie-break on `trend`. Report the top 3 with (n, RE_med, trend). Exemplars are the top-RE posts *within* winning cells — retrieved by id, never chosen by the model.

**6.2 Format mix — dampened, not winner-take-all.** Naively allocating everything to the best cell overfits and ignores that saturation erodes returns. The recommended mix blends evidence with the set's revealed behavior:

`mix(format) = 0.5 × share(format) + 0.5 × performance_weight(format)`, where `performance_weight = RE_med(format) ÷ Σ RE_med(all formats)`, then normalized to 100% and rounded to 5s. The 0.5 damping factor is config. Any format the tenant should do *less* of (share high, RE_med < 1.0) is named explicitly — that sentence is usually the most valuable in the section.

**6.3 Whitespace — presence low, demand proven.** For each cell:

- `presence = share(cell)` — how much the set already does this
- `demand = max(RE_med(cell) if n ≥ 3, patterns-corpus RE for the same cell in adjacent industries)`
- `whitespace_score = demand × (1 − presence_percentile)` — strong evidence it works, weak occupation

Top 3 scores with `demand ≥ 1.3` are whitespace opportunities, each shipped with its evidence and a concrete first experiment (the cell itself *is* the experiment). Cells with low presence and **no** demand evidence are listed as "unproven, not whitespace" — the distinction that keeps this section credible.

**6.4 Cadence.** Bucket competitors by posts/week; report median RE per bucket; recommend the bucket where RE_med holds ≥ 1.0, bounded by ±50% of the tenant's current cadence (no "triple your output" advice from a correlation). If no bucket separates, report the observed range and say the data doesn't support a cadence claim — allowed and encouraged.

**6.5 Transfers.** Query the anonymized patterns corpus for records passing §5's gate where the tenant's own cell has n < 3 (genuinely untested locally). Rank by lift × log(sample). Each rendered as: pattern, source industry, lift, n — and a one-line agent-written plausibility note, clearly marked as interpretation.

**6.6 Hooks — the only generative section.** The model writes example hooks, but each must be pinned to a winning cell (§6.1) or a whitespace cell (§6.3), tagged with that cell, and labelled AI-generated. A hook without a pin fails validation. Creativity is constrained to *where the evidence points*.

## 7. What the agent is allowed to do — the exact contract

| The agent MAY | The agent MAY NOT |
|---|---|
| Order the narrative, choose emphasis, write transitions | Introduce any number not in a tool result |
| Explain *why* an alert or trend plausibly happened (marked as interpretation) | Promote a "collecting" cell into a claim |
| Draft hooks pinned to scored cells | Rank, re-rank, or override any scored ranking |
| Flag tensions between findings ("cadence up but RE down") | Soften or omit a confidence label |
| Address reviewer feedback in revisions | Alter a threshold or damping factor |

Enforcement is layered and already in the repo: tools return scored aggregates (not raw judgment calls) · `validate.py` traces every numeric claim to a tool result · human review catches what code can't. The prompt states the contract; the pipeline makes it unnecessary to trust the prompt.

## 8. How the framework improves — the learning loop

"Right" isn't static; the framework is built to be tuned by its own evidence, not by opinion:

- **First-pass approval rate** (already captured in `review_feedback`) is the top-line quality signal. Below 70%, the narration prompt gets attention; scoring changes only with data.
- **Recommendation follow-through:** when a tenant acts on a whitespace call or format shift, tag the tenant's own subsequent posts against the recommended cell and report the observed RE next cycle. This closes the loop the PRD's "decisions influenced" metric wants — and eventually lets damping factors and thresholds be *fit* rather than chosen.
- **Threshold changes are versioned config**, stamped into every report like prompt and taxonomy versions — a recommendation can always be traced to the exact rules that produced it.
- **Quarterly calibration:** sample 20 shipped claims, check whether "Established" claims held up in the following month's data. If Established claims fail often, thresholds rise.

## 9. Decisions this framework needs from stakeholders

Five values above are judgment calls that belong to the product owner, not engineering (they map to Playbook Template v1 §6): the eligibility minimums in §5 · the 28-day recency half-life · the 0.5 mix-damping factor · the 1.3 whitespace demand bar · the confidence-tier boundaries. Defaults are proposed and the system runs on them from day one; each is one config line to change and every change is versioned.
