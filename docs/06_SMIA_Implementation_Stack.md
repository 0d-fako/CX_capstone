> **Status (Sept 2026): partially superseded by `09_SMIA_Architecture_v4_Analyst_Agent.md` and `10_SMIA_3_Day_MVP_Build_Guide.md`.** What survives: Python 3.12, uv, Pydantic v2, SQLAlchemy 2 and Alembic, httpx and tenacity, Neon, GitHub Actions for CI, the named-upgrade-trigger style (§7). Out of the MVP: LangGraph and PostgresSaver, the taxonomy table, the `cell_scores` stage, embeddings, OpenRouter, the Slack Bolt app. Model access is the Anthropic SDK directly. The repo layout in §3 is replaced by the one in doc 10.

# SMIA — Implementation Stack & Build Blueprint

**Basis:** SMIA PRD v0.1, TDD v0.1, AI System Design, Playbook Template v1
**Purpose:** The concrete answer to "what do we actually install, import, and deploy." Every choice below maps to a layer in the TDD, respects the ≤ $150/month pilot ceiling, and is reversible behind the interfaces the architecture already defines. Vendor facts (pricing, limits) verified July 2026; re-verify at build time since these shift.

---

## 1. The stack at a glance

| Layer | Choice | The one-line why |
|---|---|---|
| Language & runtime | **Python 3.12**, single monorepo | LangGraph is Python-first; one language across pipeline + agent means one deploy, one test suite, one on-call brain. |
| Package/env manager | **uv** | Fast, lockfile-based, replaces pip+venv+pip-tools with one tool. |
| HTTP client | **httpx** + **tenacity** | Async-capable client; declarative retries with backoff for collectors and model calls. |
| Data contracts | **Pydantic v2** | The `RawCapture` envelope and every layer boundary enforced as code, not convention. |
| Database | **PostgreSQL 16 + pgvector** | One store for truth + vectors, per TDD. |
| ORM & migrations | **SQLAlchemy 2.0 + Alembic** | Migrations are how "multi-tenant from row one," RLS, and taxonomy versioning become real. |
| Scheduling | **cron (supercronic in Docker)** + **healthchecks.io** | Dumb scheduling for the deterministic pipeline; a dead-man switch so a silent cron failure pages someone. |
| Data source | **ScrapeCreators** REST (raw httpx, no SDK exists) | $10/5k credits, 1 credit/request, pay-as-you-go; pilot volume ≈ $6–15/month. |
| Model gateway | **OpenRouter** via the OpenAI SDK | One key, OpenAI-compatible, per-request `models` fallback arrays billed only on success. |
| Structured outputs | OpenRouter **structured outputs / JSON schema** (+ Pydantic validation) | Classification must return valid taxonomy labels or fail loudly. |
| Agent framework | **LangGraph** + **PostgresSaver** checkpointer | Native `interrupt()` for the review gateway; checkpoints live in the same Postgres. |
| Observability | **Langfuse** (cloud free tier → self-host later) | Traces, per-tenant token cost, prompt versioning, eval datasets — four needs, one tool. |
| Review surface | **Slack Bolt for Python** (Block Kit buttons) | Approve/Revise as buttons in the channel reviewers already live in. |
| API | **FastAPI** | On-demand playbook trigger, tenant onboarding, health endpoints. |
| Hosting | **One VPS** (Hetzner CX32-class or Fly.io/Railway equivalent) + **Docker Compose** | Everything fits on one small box at pilot; ~$10–20/month. |
| Secrets | `.env` at concierge → platform secrets manager at pilot | Proportionate; keys never in the repo. |
| CI/CD | **GitHub Actions**: ruff, mypy, pytest, eval harness, deploy | The shadow-eval gate from the system design runs here. |

Deliberately **not** in the stack: Kubernetes, Airflow/Dagster/Prefect, Kafka, Redis, a frontend framework, and the broader LangChain abstraction layer (LangGraph is used directly; chains and agents-as-a-service add indirection this system doesn't need). Each of these solves a scale problem SMIA does not have; adopting any of them at pilot would violate lean-first. The upgrade triggers are named in §7.

---

## 2. Two findings from vendor verification that adjust the design

**a) OpenRouter free models are not production infrastructure.** Free-tier endpoints are rate-limited (~20 req/min, ~200 req/day), can be rerouted or removed without notice, and failed calls still burn quota. Pilot classification volume (~50–100 posts/day) technically fits, but the digest SLO cannot rest on a tier with no availability promise. **Position:** free models are for development and prompt iteration only; production classification runs on paid cheap open models (current candidates: DeepSeek V4 class, Llama 3.3 70B, Qwen — fractions of a cent per post, 10–50x cheaper than frontier). Also be aware some providers serve quantized weights at lower prices, which silently changes output quality — pin provider preferences in config for the classification tier so the eval golden set is measuring one thing.

**b) ScrapeCreators is a solo-founder, bootstrapped vendor with a published ~98.2% success rate.** Two consequences. First, 98.2% per-request success is *below* our 95% daily completeness SLO only if we don't retry — so the collector must retry failures once with backoff (each retry costs a credit; budget for ~2–4% overhead). Second, single-vendor + single-maintainer is a concentration risk the TDD's pluggable interface exists to absorb: keep a second unified vendor (e.g. an Instagram-specialist API) smoke-tested behind the same `Collector` contract so a vendor outage is a config change, not an incident. This costs one afternoon and ~$0 standing.

---

## 3. Repo structure — the architecture made literal

One repo, one deployable image, three entrypoints (pipeline worker, agent worker, API). The directory tree *is* the six-layer diagram; a PR that imports across these boundaries in the wrong direction should fail review.

```
smia/
├── pyproject.toml              # uv-managed; pinned deps
├── docker-compose.yml          # postgres, worker, api (langfuse optional)
├── .github/workflows/          # lint, test, eval-gate, deploy
├── config/
│   ├── models.yaml             # tier → [primary, fallback] model slugs + provider prefs
│   ├── schedule.yaml           # cron expressions per pipeline stage
│   └── taxonomy_seed.yaml      # v1 formats + tones (themes are per-tenant, in DB)
├── src/smia/
│   ├── collectors/
│   │   ├── base.py             # Collector protocol + RawCapture (Pydantic) — THE contract
│   │   ├── scrapecreators.py   # httpx + tenacity; the only file that knows the vendor
│   │   └── canary.py           # empty/malformed-response detection → alert
│   ├── ingestion/
│   │   └── normalize.py        # dedupe on (platform, post_id); upsert = idempotency
│   ├── enrichment/
│   │   ├── classify.py         # rules-first format; cheap model for tone/theme
│   │   ├── embed.py            # embedding calls → pgvector upsert
│   │   └── router.py           # OpenRouter client, tiers, fallback arrays, Langfuse tracing
│   ├── detection/
│   │   └── anomaly.py          # rolling baselines over metric_snapshots; writes alerts
│   ├── patterns/
│   │   └── distill.py          # nightly anonymized cross-tenant pattern records
│   ├── agent/
│   │   ├── graph.py            # LangGraph: gather → retrieve → draft → interrupt → deliver
│   │   ├── tools.py            # query_posts, semantic_search, get_benchmarks, get_alerts, search_patterns
│   │   ├── prompts/            # versioned .md prompt files; version stamped into reports
│   │   └── validate.py         # grounding + injection output checks before review
│   ├── delivery/
│   │   ├── slack.py            # Bolt app: digest push, Approve/Revise buttons, resume-by-thread_id
│   │   └── api.py              # FastAPI: POST /playbook, POST /tenants, GET /health
│   ├── db/
│   │   ├── models.py           # SQLAlchemy models incl. RLS policies
│   │   ├── vector.py           # upsert()/search() — the pgvector↔Qdrant seam
│   │   └── migrations/         # Alembic
│   └── evals/
│       ├── golden/             # labelled posts (the golden set)
│       └── harness.py          # classification accuracy + shadow-eval runner
└── tests/
```

---

## 4. Layer-by-layer: the load-bearing implementation details

**Collectors.** ScrapeCreators is plain REST — one `x-api-key` header, query params, JSON back in 2–4s, no rate limits, no SDK. The adapter is ~150 lines of httpx: call the per-platform post endpoints for each target, wrap each item in `RawCapture`, retry once on failure via tenacity, write the untouched payload to `raw_captures`. The canary check runs after each collection cycle: zero items for an active target, or a payload failing Pydantic validation, fires a Slack alert. Capture **media duration** where present (the playbook template's one addition to the envelope).

**Pipeline orchestration.** No workflow engine. Each stage (`collect`, `ingest`, `enrich`, `snapshot`, `detect`, `distill_patterns`) is a CLI entrypoint (`python -m smia.pipeline.collect`); supercronic runs them in sequence overnight per `schedule.yaml`; every stage pings healthchecks.io on completion so a stage that *doesn't run* pages within the cycle — this is the "alerting on the alerter" requirement made concrete. Idempotency comes free from upsert-on-`(platform, post_id)`, so re-running a failed night is safe. n8n re-enters later only if non-engineers need to own schedules visually; Inngest/Trigger.dev only if retry orchestration outgrows cron — both are named upgrades, not defaults.

**Enrichment.** Format classification is rules-first (media_type + duration decide most posts; the model only sees ambiguous cases — the cheapest win in the pipeline). Tone/theme go to the cheap tier via `router.py` with a JSON-schema-constrained response validated against the tenant's taxonomy version; invalid labels raise, they are never coerced. The OpenRouter request carries a `models: [primary, fallback]` array so tier fallback is one config line, billed only on the successful run. Every call is traced to Langfuse with `tenant_id` metadata — that single tag is what makes $/tenant/month a dashboard query instead of a spreadsheet.

**Storage.** Alembic migration 0001 creates all tables *with* `tenant_id` and RLS policies enabled; migration 0002 enables pgvector and the HNSW index. The `db/vector.py` seam exposes only `upsert(id, vector, metadata)` and `search(vector, filters, k)` — the Qdrant migration surface is exactly this file. LangGraph's PostgresSaver checkpoints live in the same database: one backup covers data, agent state, and audit trail.

**Agent.** The graph is five plain functions over a typed state dict. `draft` runs the strong tier ReAct-style over the five tools in `tools.py`; tools enforce tenant filters internally (the model never authors a `tenant_id`). `interrupt()` pauses at review; the Slack **Approve** button resumes the checkpointed thread to `deliver`, **Revise** opens a Block Kit modal whose text resumes it back to `draft` with feedback appended and `revision_count` incremented. `validate.py` runs between draft and interrupt: every numeric claim must match a tool result within tolerance, no URLs absent from source data, no reader-directed instructions — the injection/grounding gate from the system design as ~100 lines of checks.

**Evals in CI.** `harness.py` runs the golden set against the classification prompt on every PR that touches prompts or `models.yaml`, and runs shadow evaluation (new prompt vs last 4 weeks of gathered contexts) before a prompt version is promoted. A prompt change that drops golden-set accuracy below threshold fails the build. This is the feedback loop with teeth.

---

## 5. Deployment: one box, three containers

```
┌─ VPS (~$15/mo) ── Docker Compose ─────────────────────────────┐
│                                                               │
│  postgres:16 + pgvector          (volume-backed, nightly      │
│      ▲            ▲               pg_dump to object storage)  │
│      │            │                                           │
│  worker (supercronic)         api (FastAPI + Slack Bolt)      │
│   pipeline stages + agent      /playbook /tenants /health     │
│      │            │            Slack events webhook           │
│      ▼            ▼                                           │
│  OpenRouter   ScrapeCreators   Langfuse Cloud   Slack   HC.io │
└───────────────────────────────────────────────────────────────┘
```

Langfuse starts on its cloud free tier (zero ops); self-host it as a fourth compose service only if trace volume or data-residency (NDPA) demands it. Deploys are `git push` → Actions builds the image → SSH pull + `docker compose up -d`. No orchestrator until there is more than one box, and there is no reason for more than one box before ~100 tenants.

**Cost check against the $150 ceiling:** VPS $10–20 · ScrapeCreators ~$6–15 (1,500 requests + retry overhead) · OpenRouter classification <$5 · strong-tier synthesis runs $10–30 · embeddings ~$1 · Langfuse $0 · healthchecks.io $0. **Total ≈ $35–70/month**, comfortable margin for the on-demand playbook runs that spike strong-model usage.

---

## 6. Build order (maps to Concierge → Pilot → Production)

**Concierge (week 0 — no code):** Claude/ChatGPT + a spreadsheet + manual ScrapeCreators calls from the docs playground. Produce two weekly radar reports by hand. This validates the playbook template's sections against a real competitive set *before* the template freezes — the cheapest possible test of the blocking dependency.

**Pilot (weeks 1–4):** repo scaffold + compose + migration 0001/0002 → ScrapeCreators adapter + canary → ingestion + snapshots → rules-first format classifier, then cheap-tier tone/theme with the golden set started the same week (label the first 200 posts as they arrive — free labour now, impossible to retrofit later) → anomaly detector → weekly digest as a *deterministic template* (SQL aggregates + alerts, no agent yet) pushed to Slack. A useful digest ships in week 3–4 with zero agentic risk.

**Production (weeks 5–10):** LangGraph graph + tools + validate → Slack review buttons wired to interrupt/resume → embeddings on + `semantic_search` → playbook generation per template v1 → patterns distillation + `search_patterns` → FastAPI onboarding endpoint (tenant + targets + model-proposed theme set → human approves → 48h baseline run).

---

## 7. Named upgrade triggers (so lean-first has an exit ramp)

| Pressure observed | Upgrade | Not before |
|---|---|---|
| Vectors > ~500k or filtered-search latency hurts | Qdrant behind `db/vector.py` | pgvector measurably slow |
| Cron retries/dependencies get hand-managed | Inngest / Trigger.dev (or n8n for visual ownership) | a real missed-run incident |
| ScrapeCreators outage or unit economics flip | Playwright (TikTok) / twscrape (X) behind `Collector` — governance sign-off first | vendor actually failing |
| Digest quality plateaus with one loop | Second LangGraph graph (playbook vs digest) | first-pass approval stalls < 70% |
| >1 box needed / team can't share one VPS | Managed Postgres + container platform | ~100 tenants |
| Reviewers outgrow Slack buttons | Minimal web review UI (FastAPI + HTMX) | reviewer complaints, not anticipation |

Everything above deploys the architecture already agreed: the interfaces in the TDD are what make each row of this table a swap instead of a rewrite.
