# SMIA — 3-Day MVP Build Guide (Claude Code in VS Code)

**Builds:** Architecture v4 (doc 09). Replaces the 3-hour guide (08, now archived).
**Goal:** by the end of day three, a person describes a real venture idea in a chat, the system finds and verifies the competitors, confirms the set with them, collects the posts, and hands back a playbook whose every number traces to a tool call. Then that person answers two questions and you write the answers down.

**Scope discipline.** Day one is the irreversible part: the collector and the time series. Day two is the deterministic tools and the agent core. Day three is the research chat and the user test. Not in these three days: Slack chat surface, scheduler, review buttons, first-party account APIs, anomaly detector, embeddings, RLS. Each has a named trigger in doc 09 §10.

**How to use this guide.** Each step has the concept, the prompt to paste into Claude Code, a verify command with the expected result, and what you should understand before moving on. Claude Code writes the code; the checkpoints make sure it is yours.

---

## Repo layout you are building toward

```
smia/
├── CLAUDE.md                     # architecture rules Claude Code reads every session
├── pyproject.toml                # uv-managed, pinned
├── .env                          # keys, never committed
├── config/
│   ├── thresholds.yaml           # eligibility, weights, confidence tiers, credit caps (versioned)
│   └── dimensions.yaml           # promoted dimensions (starts with format only)
├── src/smia/
│   ├── settings.py
│   ├── cli.py                    # chat, collect, digest, playbook, review, activate
│   ├── db/models.py, migrations/
│   ├── collectors/base.py, scrapecreators.py   # collect() and resolve_handle()
│   ├── ingestion/normalize.py    # raw_captures, posts upsert, snapshots, rule format label
│   ├── metrics/                  # deterministic, unit-tested: engagement.py, cells.py, benchmarks.py
│   ├── labeling/label.py         # Haiku on-demand labelling with cache
│   ├── tools/
│   │   ├── analysis.py           # query_posts, get_post, benchmarks, cell_stats, label_posts, reviewer_notes, prior_report
│   │   └── discovery.py          # resolve_handle, create_prospect, collect_now (+ server web_search / web_fetch config)
│   ├── agent/
│   │   ├── run.py                # tool runner loop, approval hook, trace to agent_runs
│   │   ├── session.py            # chat_sessions state machine
│   │   ├── brief.py              # system prompt + user turn per mode
│   │   ├── prompts/analyst_v1.md, research_v1.md
│   │   └── validate.py           # grounding + injection checks (full and light)
│   └── delivery/slack.py, review.py
└── tests/
```

---

## Step 0 — Prerequisites (before day one)

1. Neon project created; copy the direct connection string.
2. Anthropic API key and ScrapeCreators key in hand.
3. Slack incoming webhook to a private test channel.
4. One known competitive set for smoke-testing the collector on day one: three handles across the three platforms. Any real brands will do.
5. The docs folder in the repo. Claude Code reads it.
6. One person with a real venture idea who has agreed to sit with you on day three and answer two questions honestly. Ideally the studio strategist, not you.

---

# Day 1 — The time series

## Step 1 — CLAUDE.md (10 min)

**Concept.** Claude Code reads it every session. It encodes the v4 rules so every later prompt is checked against the design for free. A draft already exists at the repo root.

**Prompt:**
> Read CLAUDE.md and docs/09_SMIA_Architecture_v4_Analyst_Agent.md. Confirm CLAUDE.md matches doc 09 sections 2, 5, 6, 6a and 7. If anything is missing or contradicts, fix CLAUDE.md, keep it under 80 lines, and tell me what you changed.

**Verify:** read it end to end. Ask "explain rule N and the bug it prevents" for any that surprise you.

**Understand:** why "every number from a tool call" is enforced by a validator and not by the prompt, and why creating a tenant needs a user gate.

## Step 2 — Schema (25 min)

**Concept.** The database is the design. Raw payloads are kept so you can reprocess forever without re-spending credits. Snapshots are one row per post per day because the curve is the signal. `post_labels` replaces a frozen taxonomy. `chat_sessions` and the tenant `status` column are what let a research conversation become a tenant without a form.

**Prompt:**
> Set up the project with uv: pyproject.toml (pydantic v2, pydantic-settings, httpx, tenacity, sqlalchemy 2, alembic, psycopg[binary], anthropic, pyyaml, pytest), a settings module reading .env, and SQLAlchemy models for the eleven MVP tables in docs/09 section 4 with exactly those columns, including tenants.status and venture_brief, targets.role and follower_count, and chat_sessions. Unique on posts (tenant_id, platform, post_id); unique on metric_snapshots (post_id, captured_on); unique on post_labels (post_id, dimension, definition_hash). One Alembic migration. Load config/thresholds.yaml with the defaults from docs/05 sections 3 to 5 plus session_credit_cap: 60 and stamp a thresholds_version.

**Verify:**
```
uv sync && uv run alembic upgrade head
```
In the Neon console: eleven tables, no rows yet.

**Understand:** why raw and normalized posts are separate, why `post_labels` has a definition hash, and what `chat_sessions.stage` is for.

## Step 3 — Collector and handle resolution (35 min)

**Concept.** The only file allowed to know ScrapeCreators exists. It maps vendor JSON into your `RawCapture` envelope and it can verify that a handle exists before anyone spends credits collecting it. Vendor field names are the risky part, so this step has a live smoke test.

**Prompt:**
> Implement collectors/base.py with the Collector protocol (collect(target) -> list[RawCapture], resolve_handle(platform, handle) -> HandleInfo | None) and the RawCapture Pydantic envelope (platform, handle, post_id, posted_at, content, media_type, media_duration_s, url, metrics{likes,comments,shares,views}, raw_json). Implement collectors/scrapecreators.py with httpx and one tenacity retry, per docs.scrapecreators.com endpoints for instagram user posts and profile, tiktok profile videos and profile, and twitter user tweets and profile. HandleInfo carries display_name, follower_count, post_count. Parse defensively: an unknown field never crashes a batch, log and skip bad items. Add a smoke test that resolves and fetches one real handle per platform, prints the HandleInfo and first RawCapture, and lists which fields came back empty.

**Verify:** run the smoke test on your three known handles. Read the printed envelopes. If a field is empty, paste the raw JSON back: "adjust the parser, here is the actual response shape."

**Understand:** why the envelope is yours and not the vendor's, and what one `resolve_handle` call costs.

## Step 4 — Ingestion, snapshots and the collect stage (25 min)

**Concept.** Upsert on `(tenant_id, platform, post_id)` makes every rerun safe. Each run writes today's metrics row per post. Format is labelled by rule at ingest. A canary records when an active target returns nothing. The same function serves the nightly stage later and `collect_now` in the chat tomorrow.

**Prompt:**
> Implement ingestion/normalize.py: for a tenant, target and list of RawCapture, write raw_captures, upsert posts, upsert today's metric_snapshots row, and write a post_labels row with dimension "format" and source "rule" using: video with media_duration_s >= 90 is long_video, other video is short_video, image, carousel, text and link map directly. Implement pipeline.collect(tenant_id) that for each active target calls the collector, canary-checks for zero items, ingests, stamps last_checked_at, writes a pipeline_runs heartbeat, and returns per-target counts and credits used. Add CLI `collect --tenant X` and a dev-only CLI `seed-tenant name industry handles...` that creates a prospect tenant for testing. Unit-test the format rule and upsert idempotency.

**Verify:**
```
uv run smia seed-tenant smoke "test" instagram:handle1 tiktok:handle2 twitter:handle3
uv run smia collect --tenant smoke && uv run smia collect --tenant smoke
```
Post count unchanged on the second run. Snapshot count unchanged today, but you can explain why tomorrow adds a row per post.

**End of day one:** run `collect` on the smoke tenant again tomorrow morning. That second snapshot is your first curve.

---

# Day 2 — The tools and the agent core

## Step 5 — Deterministic metrics (45 min)

**Concept.** This is how the system knows what is right. Relative engagement normalises every post against its own account. Cell stats aggregate any dimension with recency weighting. Eligibility decides whether a claim is allowed at all. Pure SQL and Python, unit-tested, no model.

**Prompt:**
> Implement metrics/engagement.py, metrics/cells.py and metrics/benchmarks.py per docs/05 sections 3 to 6 and docs/09 section 5, reading thresholds from config/thresholds.yaml. relative_engagement uses the latest snapshot divided by the account's trailing-90-day median (guard median >= 1) and returns maturity "initial" until a post has 7 days of snapshots. cell_stats(dimension, window_days, cross_dimension=None) returns per label: n, recency-weighted median RE, trend when n >= 5 in both windows, share, confidence label, or "insufficient" with n. benchmarks(window_days) returns per target: posts per week, format mix, RE_med, trend arrow, n. Unit tests with synthetic data: RE = 1.0 for a median post, recency weighting shifts the median, a 4-post cell is insufficient, a 12-post cell is directional, a 30-post cell is established.

**Verify:** `uv run pytest` green. Call `cell_stats("format", 90)` from a REPL against the smoke tenant and read it. Most cells will be insufficient. That is correct and it is the honesty feature.

**Understand:** the evidence ladder, and why every number in a report can be recomputed by hand.

## Step 6 — On-demand labelling (30 min)

**Concept.** The agent will propose dimensions no fixed taxonomy had. `label_posts` lets it test them: Haiku labels a sample against a definition the agent wrote, labels are stored with a definition hash, and `cell_stats` computes over them deterministically. The cache means the same definition never pays twice.

**Prompt:**
> Implement labeling/label.py: label_posts(tenant_id, dimension, definition, labels, post_ids) calls claude-haiku-4-5 with a strict tool schema whose enum is labels plus "other", one call per batch of up to 25 posts, content inside a delimited block marked as untrusted data, truncated per post. Skip posts already labelled for this (dimension, definition_hash). Write post_labels with source "model", model_id and run_id. Cap at 200 posts per call. Return counts per label. Use the Batch API when more than 100 posts are pending.

**Verify:** label 30 smoke-tenant posts on a dimension you invent, for example `has_person_on_camera: yes | no`. Read ten labels against the posts. Note disagreements; that is your first golden set. Cost should be cents.

**Understand:** why labels are keyed on the definition hash, and how a dimension gets promoted (doc 09 §9).

## Step 7 — Analysis tools and the agent loop (60 min)

**Concept.** One tool-use loop. The SDK runs it; you supply tools, brief and trace. The system prompt is stable and cached; the brief varies. Every tool call is written to `agent_runs` with an id, and drafts cite those ids as evidence keys. Today you prove the loop on the smoke tenant with the scheduled-mode brief; tomorrow the same loop gets the discovery tools and the conversational brief.

**Prompt:**
> Implement tools/analysis.py as @beta_tool functions wrapping metrics, db and labeling per docs/09 section 5: query_posts, get_post, benchmarks, cell_stats, label_posts, reviewer_notes, prior_report. Tenant id is bound at construction, never a tool argument. Post content in results goes inside a delimited untrusted block. Implement agent/run.py using client.beta.messages.tool_runner with claude-opus-5, adaptive thinking, effort high, streaming, prompt caching on tools and system, plus the server-side web_search tool with max_uses 5. Record every tool call (id, name, input, output) and usage to agent_runs. Implement agent/brief.py building the scheduled-mode system prompt from agent/prompts/analyst_v1.md (analyst role, the may/may-not table from docs/09 section 6, the digest and playbook templates from docs/04 with D/I/G markers, evidence-key rule, untrusted-content rule) and a user turn with tenant, industry, venture_brief, period, kind and reviewer notes. CLI `playbook --tenant X` and `digest --tenant X` print the draft and run id.

**Verify:**
```
uv run smia playbook --tenant smoke
```
Read the draft. Read `agent_runs.tool_calls` for that run. Pick three numbers in the draft and find the tool call each came from. Check `usage` and write down the cost.

**Understand:** what the agent chose to investigate and why, and the difference between what it proposed and what the tools computed.

## Step 8 — The validator (40 min)

**Concept.** The trust story is enforced here, not in the prompt. A draft that cites a number no tool returned does not reach a human. Chat turns get the light version of the same checks.

**Prompt:**
> Implement agent/validate.py per docs/09 section 7 with two modes. Full (reports): every [E#] key resolves to a tool call id in this run; every numeric token in D and I sections appears in the cited tool's output within rounding tolerance; no URL absent from tool results; heuristic flag for reader-directed imperatives not in the brief; every hook names an eligible cell and carries the AI-generated label; any cell a tool returned as insufficient appears only under "collecting". Light (chat turns): numbers trace, URLs exist in tool results, external context is labelled. Return a structured report. In run.py, on full-mode failure append the report and run one automatic revision; on second failure mark the run "ungrounded" and still surface it, flagged. Unit tests with a passing draft and one failing draft per rule.

**Verify:** `uv run pytest` green. Rerun `playbook`; the run's `validation` column shows the report. Edit a number in a saved draft and run the validator on it; it must fail.

**Understand:** which rule catches an injected instruction, and which catches a hallucinated percentage.

---

# Day 3 — The research chat and the user test

## Step 9 — Discovery tools and the session (50 min)

**Concept.** Discovery is three tools and one gate. `resolve_handle` makes sure a guessed account exists before it is proposed. `create_prospect` is intercepted by the tool runner's approval hook and shown to the user as a table; nothing is written until they confirm. `collect_now` reuses yesterday's collect stage under a credit cap. The session record tracks the stage so the surface can show progress and a session can be resumed.

**Prompt:**
> Implement tools/discovery.py: resolve_handle(platform, handle) via the collector; create_prospect(name, industry, geography, venture_brief, targets[{platform, handle, role, why}]) that creates a tenant with status prospect and its targets and binds the session to it; collect_now() that runs pipeline.collect for the session's tenant, enforces session_credit_cap from thresholds.yaml, updates chat_sessions.credits_used, and returns per-target counts. Add web_fetch alongside web_search with max_uses 8 for research mode. Implement agent/session.py: create, load, append message, set stage per docs/09 section 6a. In run.py add an approval hook: when the model calls create_prospect, render the proposed set as a table, return it to the surface, and only execute after the surface reports confirmation, applying any edits the user made. Bind the analysis tools to the session's tenant once one exists.

**Verify:** unit tests for the credit cap and for the approval hook (a fake surface that rejects, edits, then confirms). Resolve three handles you know and one you made up; the made-up one returns not_found.

**Understand:** why the gate lives in the runner hook and not in the prompt, and what happens to credits if the user walks away mid-session.

## Step 10 — The research brief and the chat REPL (60 min)

**Concept.** Same agent, conversational brief. At most three interview questions. Three competitor roles. Confirm, collect, playbook, then follow-ups. The REPL is the whole product for today.

**Prompt:**
> Write agent/prompts/research_v1.md per docs/09 section 6a: the interview limit of three questions and what they may cover; the three roles direct, adjacent, aspirational; verify every handle before proposing it; five to eight accounts, twelve at most; present the set as a table with a one-line reason per account and then call create_prospect; after collect_now, run the playbook template from docs/04 with the venture brief in context, stating plainly which sections are "collecting" and which rest on external context; answer follow-ups with the analysis tools; label every web-sourced statement as external. Implement CLI `smia chat` as a REPL: new or resumed session, streams the agent's text, renders the confirmation table and takes confirm / edit / cancel, shows collect_now progress, runs the full validator on the playbook and posts it to Slack via delivery/slack.py, and runs the light validator on every other turn.

**Verify:** run `smia chat` with a throwaway idea, for example a subscription coffee brand in Lagos. Watch it interview, propose, wait for your confirmation, collect, and produce a playbook. Then ask two follow-ups. Read `chat_sessions.messages` and `agent_runs` for the session. Write down total credits and model cost for the session.

**Understand:** where in the transcript the agent proposed something and where a tool verified it, and what the playbook honestly could not say on day one.

## Step 11 — Review capture (20 min)

**Concept.** The reviewer's decision is data, not a Slack reaction, because it feeds the next brief.

**Prompt:**
> Implement delivery/review.py with CLI `review <report_id> approve|revise --notes "..."` writing review_feedback and updating reports.status, and CLI `activate --tenant X` flipping a prospect to active. reviewer_notes returns the last five notes for the tenant, most recent first. Revise on a playbook reruns it with the notes in the brief and posts the new draft.

**Verify:** review the throwaway playbook with a revise note, confirm the rerun reflects it.

## Step 12 — The user test (45 min, not optional)

Sit with the person who owns a real venture idea. Open `smia chat`. Do not explain the architecture. Let them type. When the playbook lands, ask two questions and write both answers down verbatim:

> 1. "Would you have found this competitive set and this angle yourself in a day?"
> 2. "Does anything you planned to do next change because of this?"

Then ask what they wanted that they did not get. Record all three in `review_feedback` against the playbook's report id.

Those answers are the MVP. Yes to both means phase 2 starts with the Slack chat surface. No to the first means the discovery brief and tools need work. No to the second means the playbook brief needs work. Either way the fix is the brief and the tools, not infrastructure.

## Step 13 — Runbook and commit (15 min)

**Prompt:**
> Write RUNBOOK.md: the commands in order (chat, collect, playbook, digest, review, activate), what each writes, how to rerun safely, the cost per session and per run observed, today's known limits (terminal only, manual runs, initial-maturity RE, no buttons, no first-party data), and the user-test answers verbatim.

Commit everything.

---

## Day 4 and after, in trigger order

1. **Slack chat surface** — Bolt in Socket Mode: slash command opens a thread, replies are turns, the confirmation table gets Confirm and Edit buttons. No public URL needed. About two hours.
2. **Scheduler** — when the first prospect is activated. n8n schedule plus Slack trigger if an instance is running, otherwise GitHub Actions cron per doc 07.
3. **First-party account data** — Meta Graph and TikTok Business collectors behind the same protocol, ownership `own`. The best labelled dataset you will have.
4. **Approve/Revise buttons** — in the same Slack app.
5. **Promotion loop** — the monthly query from doc 09 §9 and a nightly labelling stage for promoted dimensions.
6. **Anomaly detector, patterns corpus, embeddings** — each only on its trigger in doc 09 §10.

**Timing honesty.** If the vendor response shape fights you in step 3, take the time from step 6, not from step 8 or step 9. If day three ends with the chat producing a playbook for a throwaway idea and the validator green, but the user test slips to day four, the MVP still succeeded. If the discovery step keeps proposing handles that do not exist, that is a brief problem: tighten the verification instruction before touching code.
