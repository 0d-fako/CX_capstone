# SMIA — Zero-Cost Variant (Addendum to the Implementation Stack)

**Constraint:** run every use case with no new spending. Assets in hand: a Claude API key and 8,000 ScrapeCreators credits (~$16 of prepaid collection, credits never expire).
**Honesty note up front:** the Claude API is pay-per-token, so "zero" here means *zero new subscriptions and zero infrastructure spend* — model calls draw down existing credits at roughly **$2–5/month** at pilot volume (math in §4). Everything else in this variant is genuinely $0.

The architecture, repo structure, and interfaces from the main blueprint are **unchanged**. Only four stack rows swap. That is the point: this variant is the same system on free rails, and graduating to the VPS build later is additive, not a rewrite.

---

## 1. What changes

| Concern | Main blueprint | Zero-cost variant | Why it works |
|---|---|---|---|
| Compute & scheduling | VPS + supercronic (~$15/mo) | **GitHub Actions** scheduled workflows | Pipeline is ~5–10 min/day ≈ 300 min/mo; free tier is 2,000 min/mo on private repos. Failure emails are built in — the dead-man switch for free. |
| Database | Postgres on the VPS | **Supabase free tier** (Postgres + pgvector, 500 MB) | pgvector included; the daily pipeline write keeps the project from the 7-day inactivity pause. At ~15 posts/day the DB grows a few MB/month — a year of headroom. |
| Model gateway | OpenRouter, tiered open models | **Claude API directly** (the key in hand) | Haiku 4.5 ($1/$5 per MTok) is the classification tier; Sonnet 4.6 ($3/$15) is the synthesis tier. Same two-tier strategy, one vendor, no new account. |
| Review & delivery | Slack Bolt app with buttons (needs a server) | **Slack incoming webhook** (push only) + review by workflow re-run | Incoming webhooks need no listening server. Approve/revise happens via a manual `workflow_dispatch` run (see §3). |

Unchanged: the Python monorepo, Pydantic collector contract, SQLAlchemy/Alembic migrations with RLS, rules-first classification, anomaly detector, LangGraph graph with Postgres checkpointer (pointed at Supabase), Langfuse cloud free tier, taxonomy registry, golden set.

Dropped for now (and what replaces them): the FastAPI onboarding endpoint → tenants and targets are YAML in the repo, applied by migration on merge; the sub-5-minute interactive playbook → `workflow_dispatch` run taking ~5–8 min including runner spin-up, still well inside "tool not batch job."

---

## 2. The zero-cost deployment shape

```
GitHub Actions (free)
├─ nightly.yml   cron: collect → ingest → enrich(batch) → snapshot → detect ─► Slack webhook (alerts)
├─ weekly.yml    cron Sun: agent graph → draft → validate ─► post draft to #smia-review
├─ review.yml    workflow_dispatch(thread_id, approve|revise, feedback) → resume checkpoint ─► deliver / redraft
└─ playbook.yml  workflow_dispatch(tenant) → agent graph → draft ─► #smia-review

        │ SQL + checkpoints                    │ traces
        ▼                                      ▼
Supabase free (Postgres + pgvector)     Langfuse Cloud free
        ▲
        │ RawCapture upserts
ScrapeCreators (8,000 prepaid credits)
```

Two implementation notes that make this smooth. First, the LangGraph **PostgresSaver checkpointer works across ephemeral runners**: the weekly run drafts and pauses at `interrupt()`, its state persisted in Supabase; the review run days later resumes that exact thread by `thread_id` on a completely different machine. Checkpointing was designed for durability — here it doubles as the bridge between stateless free compute runs. Second, classification goes through the **Message Batches API**: nightly volume has zero urgency, and batch pricing halves Haiku to an effective $0.50/$2.50 per MTok. The pipeline submits the batch, and the next scheduled run collects results — a natural fit for a cron-shaped system.

## 3. The review loop without a server

The Bolt buttons need an always-on webhook receiver; free rails don't have one. The replacement costs one extra manual step and nothing else. The weekly draft lands in a private `#smia-review` channel via incoming webhook, with its `thread_id` and a pre-filled link to the `review.yml` dispatch form in the message footer. The reviewer reads, then triggers the workflow with `approve` (graph resumes → delivers to the real channel, writes `reports.status`) or `revise` plus feedback text (graph resumes → redraft → new draft posted, `revision_count` incremented). Same state machine, same audit trail, same feedback capture — the only loss is the one-click ergonomics, which is exactly the "reviewers outgrow Slack buttons" upgrade trigger from the main blueprint, inverted.

## 4. Budget math

**ScrapeCreators — 8,000 credits.** One competitive set of 15 targets checked daily ≈ 450/month, plus ~2–4% retry overhead and ~100 one-off credits for onboarding backfill and dev testing. **≈ 16–17 months of runway**, free in the sense that it's already paid. Stretch it further with adaptive frequency: the `targets` table already has a check-frequency column, so low-cadence accounts (posting weekly) get checked every 2–3 days — easily 20+ months, or headroom for a second tenant.

**Claude API.** Classification: ~15 posts/day × ~700 tokens in / 80 out on Haiku 4.5 batch ≈ **under $0.30/month**. Weekly digest: 4 Sonnet 4.6 runs × ~40K in / 5K out ≈ **$0.80/month**. On-demand playbooks: ~$0.40–0.80 each, a few per month ≈ **$1–3**. Monthly total ≈ **$2–5** against existing credits — and prompt caching on the (stable) system prompt and taxonomy block shaves it further. For reference, a brand-new Claude Platform account currently includes $5 in free credits, which alone would run this system for a month or two.

**Embeddings.** The Claude API doesn't provide an embeddings endpoint, and the design already defers embeddings to pilot anyway. When they're needed for playbook retrieval, run **sentence-transformers locally inside the GitHub Action** (a small open model like all-MiniLM) writing straight to pgvector — genuinely $0, no vendor. The `db/vector.py` seam means switching to a hosted embedding model later touches one file.

**Everything else:** GitHub Actions $0 · Supabase $0 · Langfuse cloud free tier $0 · Slack incoming webhook $0. **Standing infrastructure cost: $0.**

## 5. What this variant proves, and the graduation line

This variant covers every PRD use case: daily collection, enrichment, snapshots, deterministic alerts, weekly digest with human review, on-demand playbooks, benchmarks, and (once embeddings switch on) cross-industry transfer — for one to two tenants. Its real limits are seams, not walls: no interactive endpoint means onboarding is a PR, review is a two-step dispatch, and GitHub Actions cron can drift ±10–15 minutes (irrelevant at a daily cadence). The moment any of those pinch — more tenants, reviewers wanting one-click approve, a real onboarding flow — the graduation is the main blueprint's §5: rent the $15 VPS, `docker compose up`, add the Bolt app, repoint `DATABASE_URL`. Nothing in the codebase changes, because nothing in the architecture did.
