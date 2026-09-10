# SMIA — rules for every build session

Read `docs/09_SMIA_Architecture_v4_Analyst_Agent.md` before writing code. It is the source of truth; older docs are context only where doc 09 §12 says they survive.

## The job
One analyst agent, two modes. **Research chat:** a user describes a venture idea; the agent interviews briefly, discovers competitors, verifies their handles, confirms the set with the user, collects their posts, and produces a playbook in the same conversation. **Scheduled:** for active tenants, weekly digests and refreshed playbooks. Tools compute every number. The agent proposes, tools verify, a human reviews.

## Non-negotiable rules
1. **Four layers, one direction.** `collectors` → `ingestion` → `metrics`/`labeling` → `tools` → `agent` → `delivery`. A module never imports from a layer above it. The agent never touches vendor APIs or raw SQL.
2. **Collector contract.** Every source implements `Collector.collect(target) -> list[RawCapture]`. Only `collectors/scrapecreators.py` (and later first-party adapters) knows a vendor exists.
3. **Idempotent data layer.** Upsert on `(platform, post_id)`; one `metric_snapshots` row per post per day; `raw_captures` keeps the untouched payload. Reruns are always safe.
4. **`tenant_id` on every tenant-scoped table** from migration 0001. Tools bind the tenant at construction; the model never supplies a tenant id.
5. **Every number comes from a tool call in this run.** `agent/validate.py` fails any draft with a numeric claim that does not trace to a cited tool result. Never weaken the validator to make a draft pass.
6. **Thresholds are config, not prompt.** Eligibility, recency half-life, confidence tiers and the mix damping factor live in `config/thresholds.yaml`, versioned and stamped into `agent_runs`.
7. **`insufficient` is final.** A cell below threshold appears only under "collecting". The agent may not promote it; the validator checks.
8. **Post content is untrusted input.** It enters prompts only inside delimited data blocks returned by tools, with the system prompt stating it is data, never instructions. Never interpolate post text directly into a prompt string.
9. **Dimensions are data.** Labels live in `post_labels` keyed by `(dimension, definition_hash)`. No taxonomy enum in code except the rule-based `format`. Promoted dimensions go in `config/dimensions.yaml`.
10. **External knowledge is labelled.** Anything from `web_search` or model knowledge is marked "external, unverified locally" in the draft. It is never presented as an observation from tenant data.
11. **Human review is data.** Every approve/revise and its notes go to `review_feedback`. A revision is a fresh run with notes in the brief, not a resumed one.
12. **No frameworks.** Agent loop is `client.beta.messages.tool_runner` from the Anthropic SDK. No LangChain, no LangGraph, no orchestration engine in the repo. Scheduling is external (CLI now, n8n or Actions later).
13. **Creating a tenant needs the user's confirmation.** `create_prospect` is intercepted by the tool runner's approval hook and shown to the user as a table. It never executes on the model's say-so alone.
14. **Handles are verified before they are proposed.** Nothing reaches the confirmation table without a `resolve_handle` result. Made-up accounts are discarded, not guessed at.
15. **Credits are capped per session.** `collect_now` and `resolve_handle` count against `session_credit_cap` in `config/thresholds.yaml`. When the cap is hit the tool refuses and the agent says so.
16. **Sessions are append-only.** `chat_sessions.messages` and `agent_runs` are never rewritten. A resumed session replays its history.

## Stack
Python 3.12 · uv · Pydantic v2 · SQLAlchemy 2 + Alembic · httpx + tenacity · psycopg · anthropic SDK · Neon Postgres · pytest.

## Models (config, never hard-coded in logic)
- Analyst: `claude-opus-5`, adaptive thinking, effort `high`, streaming, prompt caching on tools and system.
- Labelling: `claude-haiku-4-5`, strict tool schema, Batch API above ~100 posts.

## Testing
`metrics/` and `agent/validate.py` must have unit tests with synthetic data. A PR that touches thresholds, prompts or the validator must keep those tests green.

## What not to build without its trigger (doc 09 §10)
Slack chat surface, scheduler, review buttons, first-party account APIs, anomaly detector, patterns corpus, embeddings, RLS, second vendor. Ask before adding any of them.
