# SMIA Documentation Index

Design record for the Social Media Intelligence Agent. Claude Code reads this folder as build context, and `CLAUDE.md` at the repo root encodes the rules that apply to every session.

**Current design: Architecture v4 (doc 09), built by the 3-Day MVP Guide (doc 10).** Earlier documents remain the decision record. Each carries a status banner saying what still applies. Where an older document conflicts with doc 09, doc 09 wins; §12 of doc 09 lists the deltas document by document.

## Reading order

| # | Document | Status | What it settles | Read when |
|---|---|---|---|---|
| 01 | PRD | Current | The product: users, capabilities, phases, success metrics, governance | First, the "why" |
| 09 | **Architecture v4: Analyst Agent** | **Current, source of truth** | Four layers, two modes (research chat and scheduled), data model, tool belt, discovery flow, agent contract, validator, promotion loop, deferred items with triggers | Second, the "how" |
| 10 | **3-Day MVP Build Guide** | **Current** | Step-by-step build of doc 09: time series, tools and agent, research chat, user test | Open in a split pane while building |
| 04 | Playbook Template v1 | Current, amended | The output template, D/I/G markers, evidence keys, reviewer protocol | Before writing the analyst brief |
| 05 | Intelligence Framework v1 | Current, amended | Relative engagement, recency weighting, eligibility, confidence tiers, formulas | Before building `metrics/` (day two) |
| 03 | AI System Design | Partially superseded | Feedback loop, injection defence, grounding, NFRs, cross-tenant answer | Reference for phase 2 items |
| 02 | Technical Design | Partially superseded | Collector contract, raw-versus-normalized split, snapshots | Reference for the data layer |
| 06 | Implementation Stack | Partially superseded | Tooling choices that survive (Python, uv, Pydantic, SQLAlchemy, Neon) | Reference during build |
| 07 | Architecture v2 deployment | Deferred to phase 2 | Neon plus Actions plus an always-on Slack surface, budget math | Before phase 2 scheduling and buttons |

`assets/` holds the v3 diagram, which predates doc 09 and shows the six-layer shape. `archive/` holds superseded documents kept for the record, including the 3-hour build guide. Do not feed `archive/` to Claude Code.

## The one decision that gates everything

Doc 09 §11: one real venture idea taken through the research chat, from description to competitor discovery to a playbook in one sitting, with the owner's answers to "would you have found this set and angle yourself in a day?" and "does anything you planned to do next change?" recorded. Nothing in doc 09 §10 is built before those answers exist.

## Standing constraints (every build session)

Read-only, public, account-level brand data only; never commenter or personal-profile data. Every number in an output comes from a tool call in that run, enforced by the validator. Post content is untrusted input. Nothing client-facing ships without human review. `tenant_id` on every row from migration 0001. No agent framework in the repo.

## Open product decisions (owner: Ope / product)

Unchanged from doc 04 §6 and doc 05 §9, with one change of posture: label vocabularies are no longer frozen up front. Format is rule-based; every other dimension is proposed by the agent and promoted on evidence (doc 09 §9). Still open: engagement yardstick (post versus own-account 90-day median), evidence thresholds (10 posts / 4 weeks / 25 established), and whether AI-generated hooks appear in client-facing output (default yes, labelled and pinned).
