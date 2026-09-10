> **Status (Sept 2026): current, amended by `09_SMIA_Architecture_v4_Analyst_Agent.md`.** The template, D/I/G markers, evidence keys, minimum-evidence rules and reviewer protocol stand. §3 taxonomy v1 is now a set of candidate dimensions, not a frozen migration: format stays rule-based; tone and theme are proposed by the agent and promoted on evidence per doc 09 §9.

# SMIA Content Playbook — Output Template v1

**Status:** Draft for stakeholder sign-off
**Purpose:** This template is the blocking design dependency named in the PRD (§10) and TDD (§10). It defines exactly what the agent's `draft` node generates, and therefore what `gather` and `retrieve` must supply, what enrichment must classify, and what collection must capture. Agreeing this document sizes every layer beneath it and fixes **taxonomy v1**, which the classifier, the agent prompts, and the evaluation golden set all inherit.

---

## 1. Design rules the template obeys

Four rules govern every section below. They come directly from the PRD's non-goals and the AI System Design's grounding requirements.

**Observable signals only.** No section may depend on data the platforms do not expose. Watch time, precise follower growth, reach, and impressions are out. Likes, comments, shares, post timestamps, captions, media type, and point-in-time follower counts are in.

**Every number traces to a row.** Each quantitative claim in a generated playbook must be attributable to a tool result (SQL aggregate, vector retrieval, or patterns-corpus lookup). The template marks which sections are *descriptive* (pure data), *interpretive* (model reasoning over data), and *generative* (model-created content). This distinction is what makes the grounding metric checkable and tells the human reviewer where to concentrate scrutiny.

**Minimum evidence thresholds.** A recommendation may not rest on a sample too small to mean anything. Defaults (tunable in config): a format/tone/theme claim needs ≥ 10 observed posts in the period; a whitespace claim needs ≥ 4 weeks of collection history; a cross-industry transfer needs a pattern record with sample size ≥ 15. Where a section's threshold is unmet, the agent renders the section with an explicit "insufficient data — collecting" note rather than reasoning harder over less.

**Normalized engagement, not raw counts.** Raw likes favour big accounts and say nothing about content quality. The playbook's core metric is **relative engagement**: a post's (likes + comments + shares) divided by the posting account's trailing 90-day median for the same platform. A value of 2.0 means "twice this account's normal pull." This normalizes across account sizes using only observable data, and it is computable entirely from `metric_snapshots`. Raw counts still appear as context, never as the basis of a recommendation.

---

## 2. The template

Sections in delivery order. **[D]** descriptive, **[I]** interpretive, **[G]** generative.

### §0 — Header & provenance [D]
Tenant, industry, platforms covered, reporting period, targets tracked, posts analyzed, data-completeness note (any collection gaps in the period). Machine-stamped: `generated_at`, `prompt_version`, `model_id`, `taxonomy_version`, `report_id`. The provenance block is non-negotiable — it is what makes a quality regression attributable months later.

### §1 — Executive summary [I]
Three to five findings, one sentence each, each ending in a bracketed evidence key (e.g. `[E3]`) that resolves in the appendix to the underlying aggregate. Nothing appears here that is not expanded in a later section.

### §2 — Landscape snapshot [D]
The competitive set at a glance: per competitor, per platform — posting cadence (posts/week), format mix (% by format), median relative engagement, and trend arrow vs the prior period. One table. This is the "table stakes for credibility" benchmark capability from the PRD, rendered as the playbook's factual floor.

### §3 — What is working [I]
The heart of the diagnosis. For the tenant's competitive set and period:
- Top 3 format × tone combinations by median relative engagement (with n for each).
- Engagement-velocity note: which content types spike fast and decay vs build slowly (readable from snapshot curves).
- Two or three exemplar posts — handle, date, format/tone/theme labels, relative engagement — each with a one-line interpretive "why this worked."
Exemplars are retrieved, never invented; the draft node cites them by `post_id`.

### §4 — Cadence recommendation [I]
Recommended posts/week per platform for the tenant, justified against the set's observed cadence-vs-engagement relationship (e.g. "competitors posting 4–5×/week hold engagement; above 7 it decays"). Includes observed high-engagement posting windows (day/hour buckets) where the sample supports them, flagged "directional" otherwise.

### §5 — Format mix recommendation [I]
A target distribution across taxonomy formats (percentages summing to 100), with a one-line rationale per format tied to §3 evidence, and an explicit note of what the tenant should do *less* of relative to the set's habits.

### §6 — Tone & theme guidance [I]
Which tones outperform in this vertical and which themes are gaining traction (theme share of posts and relative engagement, trend over the period). Written as guidance ("lead with educational framing; promotional posts underperform the set median by 40%"), each claim keyed to evidence.

### §7 — Whitespace opportunities [I]
The P1 capability. A theme × format grid for the vertical where each cell is scored on two axes: **competitor presence** (post volume in cell) and **demand evidence** (relative engagement of what little exists in the cell, or the same cell's performance in the patterns corpus from adjacent industries). Whitespace = low presence + positive demand evidence. Top 3 cells rendered as opportunities, each with: the gap, the evidence, and a suggested first experiment. Cells with low presence and *no* demand evidence are listed separately as "unproven, not whitespace" — the honest distinction that keeps this section credible.

### §8 — Cross-industry transfers [I]
Two or three tactics from the anonymized `patterns` corpus: proven elsewhere (industry named, tenant never), untested in this vertical (verified by querying the tenant's set for the tactic's format/tone/theme signature). Each rendered as: pattern, where proven, observed lift, sample size, and why it plausibly maps to this vertical. Sourced exclusively through the `search_patterns` tool — this section is the only cross-tenant surface in the product and it reads only anonymized records.

### §9 — Example hooks [G]
Five to eight opening lines/hook concepts the tenant could adapt, each tagged with the format/tone/theme cell it serves and the exemplar or whitespace cell that inspired it. **Clearly labelled as AI-generated starting points, not observed content.** This is the template's only purely generative section, which concentrates reviewer attention exactly where hallucination risk lives.

### §10 — Watchlist [D/I]
Open alerts and recent shifts worth monitoring: competitors who changed cadence or format mix in the last 4 weeks, unresolved engagement anomalies. Pulled from the `alerts` table; the agent adds one line of interpretation per item.

### Appendix — Evidence & method [D]
The evidence-key table (`[E1]` → the aggregate/query behind it), taxonomy version with label definitions, metric definitions (relative engagement, cadence, velocity), thresholds applied, and known data gaps. Auto-rendered, not model-written.

---

## 3. Taxonomy v1

The template above consumes exactly three label dimensions. Fixing them here fixes the classification prompt, the golden set, and the whitespace grid.

**Format** (global, platform-agnostic, mutually exclusive — 7 labels):
`short_video` · `long_video` · `static_image` · `carousel` · `text_post` · `link_share` · `live_replay`

Format is largely derivable from platform metadata (media type, duration) before any model call — classify with rules first, model only for ambiguous cases. Cheapest possible enrichment win.

**Tone** (global, mutually exclusive primary tone — 7 labels):
`educational` · `promotional` · `entertaining` · `inspirational` · `conversational` · `topical` · `authority`

**Theme** (per-industry, 6–10 labels, versioned per tenant's vertical):
Themes cannot be a global list — "gas fees" is a fintech theme, not a fashion one. **Position:** at tenant onboarding, the strong model proposes a theme set from the first 48-hour collection batch plus the industry name; a human approves or edits; the set is frozen as that vertical's taxonomy version. Verticals already onboarded share their theme set (industry is config, not code — this is that principle applied to labels). Theme changes are versioned migrations triggering re-classification from `raw_captures`, per the taxonomy-registry design.

Every dimension includes an `other` escape label; a rising share of `other` is itself a drift signal that the taxonomy needs a revision.

---

## 4. The dependency chain, made explicit

This table is the point of the whole exercise: reading right to left, it is the build specification for every layer.

| Template section | Agent tool | Tables read | Enrichment required | Collector must capture |
|---|---|---|---|---|
| §2 Landscape | `get_benchmarks` | posts, metric_snapshots | format (rule-based ok) | platform, handle, posted_at, media_type, metrics |
| §3 What works | `query_posts`, `semantic_search` | posts, post_enrichment, metric_snapshots, pgvector | tone, theme, embedding | + caption/content text |
| §4 Cadence | `get_benchmarks` | posts, metric_snapshots | none | posted_at (with timezone) |
| §5 Format mix | `query_posts` | posts, post_enrichment | format | media_type, duration |
| §6 Tone/theme | `query_posts` | post_enrichment, metric_snapshots | tone, theme | content text |
| §7 Whitespace | `query_posts`, `search_patterns` | post_enrichment (grid), patterns | tone, theme, format | ≥ 4 wks history |
| §8 Transfers | `search_patterns` | patterns (anonymized) | nightly pattern distillation job | — |
| §9 Hooks | (generates from §3/§7 context) | — | — | — |
| §10 Watchlist | `get_alerts` | alerts | anomaly detector stage | daily metric snapshots |

Two consequences worth naming. First, the collector envelope in the TDD (`platform, handle, post_id, posted_at, content, media_type, metrics, raw_json`) already covers everything the template needs, **plus one addition: media duration** where the platform exposes it, to split short/long video. Second, §7 and §8 are the only sections that need the patterns corpus and multi-week history — meaning a *reduced* playbook (§§2–6, 9–10) is generatable within the 48-hour baseline-report window, and the full playbook matures as data accumulates. The 48-hour PRD promise and the whitespace capability stop competing.

---

## 5. What the reviewer checks (Tier 3 gateway)

The review surface renders the draft with the D/I/G markers visible. The reviewer's checklist, in effort order: (1) §9 hooks — generative, highest scrutiny, brand-safety and plausibility; (2) §7/§8 — interpretive leaps, verify the evidence keys actually support the claims; (3) §1 — summary faithful to the body; (4) spot-check two evidence keys against the appendix. Descriptive sections are machine-assembled and need only a glance. This turns "review the report" from a vague task into a ten-minute protocol, which is what makes the ≥ 70% first-pass approval target and eventual auto-approval graduation measurable.

---

## 6. Open items for sign-off

1. **Tone and format label sets** (§3) — the one decision that is expensive to change later, since the golden set and all historical classifications inherit it.
2. **Theme-per-vertical onboarding flow** — human-approves-model-proposal, as positioned above, or a fixed cross-industry starter list?
3. **Relative-engagement definition** — trailing 90-day account median as the normalizer; alternatives (follower-count normalization) rejected for volatility, but this is the number every recommendation stands on, so it deserves explicit agreement.
4. **Evidence thresholds** — the defaults in §1 (10 posts / 4 weeks / n≥15) are proposals; strategy ops should own these values in config.
5. **Hook count and labelling language** for §9 — marketing to confirm the "AI-generated starting point" framing is acceptable in a client-facing deliverable.

With these five settled, taxonomy v1 freezes, the classification prompt and golden set can be written, and every pipeline layer has its specification.
