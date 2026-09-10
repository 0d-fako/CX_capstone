> **Status (Sept 2026): partially superseded by `09_SMIA_Architecture_v4_Analyst_Agent.md`.** What survives: the collector contract and `RawCapture` envelope (§3), the raw-versus-normalized split and daily snapshots (§4), Postgres as source of truth, buy-the-data-layer. What changed: six layers are now four; the LangGraph agent (§7) is replaced by a tool-use loop on the Anthropic SDK; the enrichment stage (§5) becomes on-demand labelling; pgvector, Qdrant and OpenRouter are out of the MVP. See doc 09 §12.

Project ALPHA  |  Social Media Intelligence Agent  |  Technical Design

**PROJECT ALPHA**

Technical Design Document

**Social Media Intelligence Agent**

*Architecture, data model, tooling, and agent design*

| **Field** | **Detail** |
| --- | --- |
| Document | Technical Design Document (TDD) |
| Product | Social Media Intelligence Agent (SMIA) |
| Companion to | SMIA Product Requirements Document v0.1 |
| Status | Draft for stakeholder discussion |
| Version | 0.1 |
| Prepared | July 2026 |
| Audience | AI and Automation, Engineering, Strategy Ops |

# **1. System overview**

SMIA is a greenfield, multi-tenant intelligence system. It continuously collects the external market presence of tracked brands and competitors across social and web surfaces, enriches that data, and produces reviewed intelligence outputs and content playbooks.

The core design principle is **separation of concerns**. The system splits into six layers, each with one job, each hidden behind an interface. The collector does not know about embeddings, the intelligence layer does not know how data was fetched, and the API does not touch the database directly. This is what allows any single component, most importantly the data source, to be swapped without disturbing anything downstream.

A second principle governs the split of work: **pipeline first, agent second**. Roughly 80 percent of the system is a deterministic pipeline of scheduled steps with model calls inside it. Only the synthesis and playbook brain is a true agent. This keeps the high-volume, must-be-reliable work out of the nondeterministic layer.

# **2. Architecture: the six layers**

| **Layer** | **Responsibility** | **Agent?** |
| --- | --- | --- |
| 1. Source adapters | Fetch raw data per platform. The only layer that knows about a specific provider. | No |
| 2. Ingestion and normalization | Deduplicate, validate, map raw JSON to the common schema. | No |
| 3. Enrichment | Cheap-model classification, embedding generation, metric snapshots. | No |
| 4. Storage | Postgres as structured source of truth; vector store for semantic retrieval. | No |
| 5. Intelligence | The single agent loop. Reasons over data, drafts reports and playbooks. | Yes |
| 6. Delivery and review | Push outputs; enforce human review before client-facing output leaves. | No |

Orchestration (cron or a fresh n8n instance) drives layers 1 through 3 on a schedule. A config table holds tenants, industries, and target lists as data, not code, which is the mechanism behind self-serve onboarding for any industry.

## **Why the boundaries matter**

- **Source adapters: **the only place that knows about ScrapeCreators. Every adapter implements the same contract, so a future Playwright or twscrape collector drops in without changes above it.

- **Ingestion: **a pure transformation stage with no intelligence. A provider changing its response format is a one-file fix here, never a ripple through the system.

- **Enrichment: **where the tiered-model cost strategy lives. Classification and embedding are separate steps writing to separate stores, so embeddings can be turned on later without disturbing classification.

- **Intelligence: **the only agentic layer. It never scrapes and never touches the vector store directly; it calls tools that wrap those layers.

# **3. Data sourcing strategy**

Data sourcing is the part of the system most likely to break, so it is designed for replaceability. Instagram, TikTok, and X are the priority platforms, and they are also the three hardest to collect: Instagram gates content behind a login wall and rotates identifiers frequently, X requires authenticated sessions since the 2023 API lockdown, and TikTok is JavaScript-rendered.

## **Approach: buy the data layer, build the intelligence layer**

The first release uses a single unified third-party API, **ScrapeCreators**, which covers all three priority platforms under one key with a simple one-request-one-credit model. This validates the product fast without building or maintaining scrapers. At pilot scale (around 15 tracked accounts checked daily, roughly 1,350 to 1,500 requests per month) this is a low double-digit monthly cost.

Per-platform insourcing is a later optimisation, decided by unit economics and executed behind the same collector interface:

| **Platform** | **Launch** | **Later insourcing option** |
| --- | --- | --- |
| TikTok | ScrapeCreators | Playwright intercepting internal JSON, small residential proxy pool. Friendliest of the three. |
| X | ScrapeCreators | twscrape with a rotating burner-account pool, if risk tolerance allows. |
| Instagram | ScrapeCreators | Keep buying. Hardest to self-collect and breaks most often; a small monthly line item beats weekly maintenance. |

## **The collector interface contract**

Every source, bought or built, implements one function. This single contract is the most important decision for the add-more-platforms-later goal.

interface Collector:
    def collect(target: Target) -> list[RawCapture]

# RawCapture envelope (normalized on the way out):
#   platform, handle, post_id, posted_at,
#   content, media_type, metrics, raw_json

If self-building, the key technique is to **intercept JSON, not parse HTML**. These platforms render from internal JSON APIs; capturing those responses is far more stable than scraping the DOM, which changes weekly. Residential proxies, human-like pacing, and a canary check that alerts on empty or malformed returns are the supporting requirements.

# **4. Data model**

PostgreSQL is the structured source of truth. The schema is multi-tenant from the first migration and reserves the embedding reference so no migration is needed when the vector layer is switched on.

| **Table** | **Purpose and notable design choice** |
| --- | --- |
| tenants | One row per lens. Holds name and industry. Industry flows into the agent prompt context. |
| targets | Monitored accounts and URLs per tenant. Platform, handle, and check frequency. |
| raw_captures | Untouched provider payload as JSONB. The audit trail and safety net; reprocess from here instead of re-fetching. |
| posts | Clean normalized post. The lean, queryable core, one row per unique post. |
| post_enrichment | Separate table holding tone, format, theme, and a vector_id pointer. Keeps posts lean and the vector store swappable. |
| metric_snapshots | One row per post per day. Captures engagement velocity, not just final totals. |
| reports | Generated digests and playbooks with a status field driving the review gateway. |

## **Design rationale**

- **raw_captures separate from posts: **original provider data is preserved, so a new attribute can be backfilled by reprocessing rather than re-spending credits.

- **post_enrichment holds only a vector_id: **the vector lives in the vector store; Postgres holds the pointer. This keeps the store swappable and the posts table lean.

- **metric_snapshots over time: **the shape of the engagement curve is usually a stronger signal than the final number, and is only visible with daily snapshots.

- **tenant_id everywhere: **multi-tenancy present from the first migration; the mechanism behind any-industry self-serve.

# **5. Enrichment and cost strategy**

Model access is consolidated through **OpenRouter**, one key routing to many models. This maps directly onto a tiered-model cost strategy.

| **Workload** | **Model tier** | **Rationale** |
| --- | --- | --- |
| Post classification (high volume) | Cheap open model | Hundreds of posts weekly at fractions of a cent each |
| Weekly synthesis and playbooks | Stronger reasoning model | Low frequency, high stakes, judged by users |
| Embeddings | Cheap open embedding model | Fractions of a cent per thousand posts |

This split typically cuts model spend by a large margin versus running everything on a frontier model. Note: the exact open model name and its current pricing should be verified live when testing, since availability shifts month to month.

## **Embeddings: schema-ready now, populated at pilot**

Embeddings are not needed for classification, metric trending, or the weekly digest. They are needed for deep playbook retrieval across months, theme clustering to spot emerging patterns, and cross-industry pattern transfer. The schema reserves the embedding reference from day one; population begins at the pilot phase once enough posts have accumulated.

# **6. Storage: Postgres plus a vector store**

Two stores with distinct jobs, rather than forcing one to do both.

- **Postgres: **source of truth for everything structured and relational. Anything filtered, joined, aggregated, or trended lives here and is answered in SQL.

- **Vector store: **one vector per post plus filterable metadata. Powers semantic retrieval for playbooks, clustering, and cross-industry transfer.

## **pgvector now, Qdrant at scale**

The lean start is **pgvector inside Postgres**: one database to operate, embeddings as a column. **Qdrant** earns its place once vector counts reach the hundreds of thousands and faster filtered similarity search and easier horizontal scaling matter. Both sit behind a small vector-store interface (an upsert and a search function), so the migration never touches the agent.

# **7. Intelligence layer: the LangGraph agent**

The intelligence layer is the single agent loop, built on **LangGraph**. LangGraph is chosen because the review gateway is a human-in-the-loop pause with iteration, which it models natively, and because it sits within the same ecosystem as Langfuse for tracing.

## **What LangGraph provides**

- **Review as a first-class construct: **the interrupt mechanism pauses the graph, persists full state, and resumes from exactly where it stopped after a human acts.

- **Iteration as a clean cycle: **a revise instruction routes back to the draft node with feedback in state, an explicit conditional edge rather than tangled control flow.

- **Durability: **checkpointing means a crashed run resumes from its last checkpoint instead of re-spending tokens, and every node is a traced step in Langfuse.

## **The state graph**

The happy path runs: entry (weekly cron or on-demand request), gather context from Postgres, retrieve from the vector store, draft the report with the strong model, then the human review interrupt. Two edges leave review: approve flows to delivery; revise loops back to draft carrying feedback. Persistent state carries context, the current draft, accumulated feedback, and a revision counter.

entry -> gather_context -> retrieve -> draft -> review
review --(approve)--> deliver -> end
review --(revise, +feedback)--> draft

state: { context, draft, feedback, revision_count }
guard: auto-escalate after N revisions

The revision counter bounds the loop and signals when a report repeatedly needs rework, which is useful data on whether the draft prompt needs tuning. Each node is a plain function taking state and returning state, so the graph is also the build task breakdown: gather is SQL, retrieve is a vector-store call, draft is one OpenRouter call, review is the interrupt plus a Slack surface, deliver is a push plus a status write.

## **What stays outside the graph**

Collection, ingestion, and enrichment do not belong in the graph. They are the deterministic pipeline, driven by cron, running whether or not the agent wakes. The graph only reads what the pipeline has produced. A scraper inside the graph would be the sign something has drifted.

# **8. Tooling stack**

| **Concern** | **Choice** | **Later / at scale** |
| --- | --- | --- |
| Model access | OpenRouter, one key, tiered models | Same; swap model strings freely |
| Agent harness | LangGraph (native HITL and iteration) | Same; split into two loops only if it strains |
| Data sourcing | ScrapeCreators behind collector interface | Playwright (TikTok), twscrape (X) |
| Orchestration | Cron plus a Python worker | n8n for visual control; Inngest or Trigger.dev for durable retries |
| Structured store | PostgreSQL | Same |
| Vector store | pgvector | Qdrant, behind one interface |
| Observability | Langfuse from early on | Same; per-tenant token tracing |

Each tool owns one layer and hides behind an interface, so every choice is reversible without disturbing the rest.

# **9. Indicative cost at pilot scale**

| **Item** | **Indicative monthly** |
| --- | --- |
| Unified social API (ScrapeCreators) | Low double digits USD at pilot volume |
| Model calls (classification plus synthesis, tiered) | Low, dominated by the few strong-model runs |
| Embeddings | Negligible at pilot volume |
| Postgres plus pgvector hosting | Modest single instance |
| Observability (Langfuse, self-hostable) | Low to zero if self-hosted |
| Indicative total, pilot | Roughly 50 to 150 USD per month |

*If self-building collection, residential proxies and burner-account maintenance add cost and, more significantly, a recurring few hours of monthly patching. This is why buying the data layer is the launch choice.*

# **10. Build sequence and next step**

The rollout matches the PRD phases and the governance Prototype, Pilot, Production requirement.

| **Phase** | **Technical scope** |
| --- | --- |
| Concierge | No product. Manual collection for one competitive set, model-assisted report, human-reviewed each run. Validates insight value. |
| Pilot | Collector interface with ScrapeCreators, ingestion to Postgres, cheap-model classification, metric snapshots, weekly digest live internally. Embeddings switched on. |
| Production | LangGraph agent with review gateway, vector retrieval, playbook generation, multi-tenant lenses, alerting, self-serve onboarding. |

## **Blocking dependency**

The playbook output template is the blocking design dependency. The draft node needs the exact structure it generates; that structure defines the minimum data the gather and retrieve nodes must supply, which defines what enrichment must classify, which defines what collection must capture. The template sits at the top of a dependency chain running all the way back to the collector. Agreeing it first lets every layer below be sized correctly rather than guessed at.

**Companion document: ***Social Media Intelligence Agent Product Requirements Document v0.1.*

Draft for stakeholder discussion  Page
