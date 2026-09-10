> **Status (Sept 2026): deferred to phase 2.** The shape stands (Neon as state plane, GitHub Actions as compute, Vercel or an n8n instance as the always-on Slack surface) and the budget math is still valid. One change: no LangGraph checkpointer. Agent runs are short and stateless; a revision after review is a fresh run with the reviewer's notes in the brief. Build this after the MVP user test in doc 10 step 11 returns yes.

# SMIA — Zero-Cost Architecture v2 (Neon + Vercel)

**Supersedes:** the Supabase/webhook-only Zero-Cost Variant.
**Constraint set:** company-default services (Neon for Postgres, Vercel for hosting), zero standing infrastructure cost, existing Claude API key, 8,000 prepaid ScrapeCreators credits.
**Vendor limits verified July 2026** (Neon free plan: 100 CU-hours + 0.5 GB per project, scale-to-zero, pgvector supported. Vercel Hobby: daily-only cron with within-the-hour precision, ~30s default function timeout).

---

## 1. The design in one sentence

**Neon is the state plane, GitHub Actions is the compute plane, Vercel is the serving plane** — three free tiers, each doing exactly the job its limits are shaped for, wired together by LangGraph checkpoints in Neon and `repository_dispatch` calls to GitHub.

The single most important consequence of this rearrangement: **the one-click Slack review buttons come back.** The previous variant lost them because nothing was always-on to receive Slack's button webhook. Vercel is precisely an always-on HTTPS surface. The button handler does milliseconds of work — verify Slack's signature, record the decision, fire a `repository_dispatch` to GitHub — so Vercel's Hobby timeout is irrelevant to it, and the review loop regains full interactivity at $0.

What deliberately does **not** run on Vercel: the pipeline and the agent. A nightly collection cycle takes minutes and the agent's draft node can take several — both far beyond Hobby function limits, and both batch-shaped rather than request-shaped. Forcing them into serverless functions would mean chunking, self-invoking hacks, and fighting the platform. GitHub Actions runs long Python happily for free. The rule: **Vercel serves requests; Actions does work; Neon remembers everything.**

---

## 2. The architecture

```
                        ┌──────────────── VERCEL (serving plane, $0*) ────────────────┐
  Slack buttons ──────► │  /api/slack/interact   verify sig → write decision → fire   │
  Slack /playbook cmd ► │  /api/slack/command    repository_dispatch to GitHub        │
  Onboarding UI/API ──► │  /api/tenants          insert tenant+targets (pooled conn)  │
  Status page ────────► │  /api/health           read pipeline heartbeats from Neon   │
                        └───────────────┬──────────────────────────┬──────────────────┘
                                        │ repository_dispatch      │ pooled reads/writes
                                        ▼                          ▼
        ┌────────── GITHUB ACTIONS (compute plane, $0) ──────┐   ┌───── NEON (state plane, $0) ─────┐
        │ nightly.yml   collect→ingest→enrich(batch)→        │   │  Postgres 16 + pgvector          │
        │               snapshot→detect→distill  ────────────┼──►│  all tables + RLS                │
        │ weekly.yml    agent graph → draft → interrupt ─────┼──►│  LangGraph checkpoints           │
        │ review.yml    (dispatched) resume thread ──────────┼──►│  taxonomies / review_feedback    │
        │ playbook.yml  (dispatched) on-demand run           │   │  scale-to-zero between runs      │
        │ eval.yml      golden set + shadow evals on PRs ────┼──►│  ← run against a Neon BRANCH     │
        └──────────┬─────────────────────────────────────────┘   └──────────────────────────────────┘
                   │ chat.postMessage (drafts with buttons, digests, alerts)
                   ▼
                 SLACK
        (ScrapeCreators: 8,000 prepaid credits ── Claude API: Haiku 4.5 batch / Sonnet 4.6)
```

\* $0 *additional* if the company already holds a Vercel team plan; see the honesty notes in §5.

## 3. The review loop, fully interactive again

1. Sunday's `weekly.yml` runs the LangGraph graph on an Actions runner: gather → retrieve → draft → `validate` → `interrupt()`. State checkpoints to Neon; the runner dies; nothing is lost.
2. The workflow posts the draft to `#smia-review` via `chat.postMessage` with Block Kit **Approve** / **Revise** buttons carrying the `thread_id`.
3. A reviewer clicks. Slack POSTs to Vercel's `/api/slack/interact`. The function verifies the signing secret, writes the decision and any modal feedback to `review_feedback` in Neon, fires `repository_dispatch` (event `review`, payload `{thread_id, action, feedback}`), and returns 200 — total work well under a second.
4. `review.yml` wakes on the dispatch, reconnects to the Neon checkpoint by `thread_id`, and resumes the graph on a fresh runner: **approve** → deliver to the tenant channel + status write; **revise** → back through draft with feedback appended and `revision_count` incremented → new draft posted with buttons.

Same state machine as the VPS build, same audit trail, same feedback capture — the checkpointer bridging stateless runners is what makes a durable agent loop possible on free compute. On-demand playbooks work identically through a `/playbook` Slack slash command hitting `/api/slack/command` (end-to-end ~5–8 minutes including runner spin-up: inside the "tool, not batch job" budget).

## 4. Neon specifics worth designing around

**Quota math.** SMIA's database is busy for minutes a day, idle otherwise — the exact workload scale-to-zero was built for. Nightly pipeline + weekly agent + sporadic Vercel reads ≈ 1–2 active hours/day at 0.25 CU ≈ **30–60 CU-hours/month against a 100 CU-hour allowance**. Storage: pilot writes a few MB/month of JSONB into `raw_captures`, so the 0.5 GB cap is years away; if it ever nears, the retention policy from the system design (archive raw payloads after 12 months) is the release valve. Cold starts (~500ms) are irrelevant to batch and negligible for a Slack button.

**Two connection strings, used deliberately.** Vercel functions use Neon's **pooled** connection string (pgBouncer) — serverless platforms multiply connections and the pooler absorbs it. Actions workflows and Alembic migrations use the **direct** string, since migrations and session-level settings (including the RLS `SET app.tenant_id` pattern) don't play well through transaction pooling. This is a one-line-per-environment config distinction that prevents a class of confusing bugs.

**Branching is a free eval environment.** Neon branches are copy-on-write and instant. `eval.yml` creates a branch of production, runs the golden set and shadow evaluation against a *real* schema and real accumulated data, and deletes the branch — the shadow-eval gate from the system design gets a production-faithful sandbox at no cost and no risk. The Neon↔Vercel integration does the same for preview deployments of the serving endpoints.

**Everything else is just Postgres.** RLS policies, pgvector + HNSW, LangGraph's PostgresSaver, Alembic migrations — all standard, all unchanged from the blueprint.

## 5. Vercel specifics and two honesty notes

The serving functions stay Python (Vercel's Python runtime), importing from the same `smia` package — thin handlers, one language across the monorepo, per the stack's founding decision. Vercel cron is *not* used as the scheduler: Hobby allows only daily jobs with within-the-hour precision, and GitHub Actions cron already owns scheduling. Vercel's job is purely reactive traffic.

**Honesty note one — plan terms.** Vercel's Hobby tier is for non-commercial use; SMIA is company work. If the company already runs a Vercel team (implied by "company default"), deploying SMIA's endpoints there costs **$0 additional** and Pro limits (300s functions, precise cron) apply anyway. If not, the Pro seat (~$20/month) is the one line item this variant can't engineer away — worth confirming before assuming zero.

**Honesty note two — Neon free plan posture.** Neon's own guidance is that the free plan suits prototyping, not production guarantees (compute suspends if the monthly CU quota exhausts). At pilot volume SMIA sits at half the quota, but the graduation trigger is explicit: the day SMIA is declared production for real tenants, move the project to Launch — usage-based with no minimum, realistically **$1–3/month** for this workload shape. That is the entire cost cliff.

## 6. Budget summary

| Item | Monthly | Notes |
|---|---|---|
| Vercel serving plane | $0* | On existing company team; *see §5 note one |
| Neon free plan | $0 | ~30–60 of 100 CU-hours; Launch later ≈ $1–3 |
| GitHub Actions | $0 | ~300–400 of 2,000 free private-repo minutes |
| ScrapeCreators | $0 new | 8,000 prepaid credits ≈ 16–17 months at 15 targets/day |
| Claude API (existing key) | ~$2–5 credit draw | Haiku 4.5 Batch for classification; Sonnet 4.6 for synthesis |
| Embeddings | $0 | sentence-transformers inside the Action → pgvector |
| Langfuse cloud / Slack / healthcheck | $0 | Free tiers; Actions failure emails as dead-man switch |

**Standing infrastructure: $0. Total credit draw: ~$2–5/month.**

## 7. What changed in the repo, and the graduation path

Deltas from the blueprint repo: an `api/` directory of thin Vercel Python handlers (Slack interactivity, slash command, tenants, health) replacing the Bolt app and FastAPI service; four workflow files under `.github/workflows/`; `vercel.json`; two `DATABASE_URL`s (pooled/direct). Deleted relative to v1 of this variant: the manual `workflow_dispatch` review form — buttons made it obsolete. Everything else — collectors, ingestion, enrichment, detection, agent graph, tools, validation, evals — is byte-identical to the main blueprint.

Graduation is now *smaller* than before: this variant already has interactive review and an API surface, so scaling up is (a) Neon Free → Launch when production is declared, (b) more Actions minutes or a small always-on worker if tenant count multiplies nightly runtime, and (c) nothing else. The VPS from the main blueprint may never be needed at all — which makes this less a budget workaround and more the probable permanent shape of the system.
