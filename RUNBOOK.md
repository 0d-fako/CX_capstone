# SMIA Runbook

How to run the MVP built from docs/09 and docs/10. Every command is `uv run smia <command>` from the repo root with a filled-in `.env` (copy `.env.example`).

## Setup once

```
uv sync --extra dev
uv run alembic upgrade head      # creates the eleven tables on Neon
uv run smia config               # shows which keys are missing; must say "missing keys: none"
uv run smia db-check             # "tables present: 11/11"
```

## The product: research chat

```
uv run smia chat                          # new session
uv run smia chat --session <id>           # resume
uv run smia chat --verbose                # also prints the analyst's interim text
```

Describe the idea (what, for whom, where, which platforms). The analyst asks at most three questions, searches the web for competitors, verifies each handle with the vendor (one credit each), then shows the proposed set. Answer `y` to confirm, `rm 2,5` to drop rows and confirm, or `n <note>` to decline with a note. On confirm it creates a **prospect tenant**, collects recent posts (about one credit per account), investigates, and submits a playbook through the validator. The playbook is stored in `reports` and posted to Slack. Ask follow-ups in the same session; every number carries a `[T#]` ref.

What each step writes: `chat_sessions` (messages, stage, credits), `tenants` + `targets` (on confirm), `raw_captures` + `posts` + `metric_snapshots` + `post_labels` (on collect), `agent_runs` (one row per turn with the tool trace), `reports` (on a validated submit).

## Slack chat (the preferred surface)

```
uv run smia slack                         # leave running; DM the SMIA app or mention it in a channel
```

Each thread is one session. The proposed set arrives with Confirm and Decline buttons; a text reply while it is pending counts as a decline with that note ("remove 3 and 5"). The playbook posts into the thread, is attached as .docx and .md (needs the `files:write` scope), and goes to the review channel. Every reply ends with the estimated spend for the turn and the session.

Slack app setup: Socket Mode on with an app-level token (`connections:write`) as SLACK_APP_TOKEN; bot token as SLACK_BOT_TOKEN with the template scopes plus `files:write`; events `message.im`, `app_mention`, `assistant_thread_started`; Interactivity on; App Home messages tab on.

## Downloads

```
uv run smia export --tenant <name> --kind playbook     # latest report for a tenant -> exports/*.docx and *.md
uv run smia export <report_id>
```

Terminal chat and the digest/playbook commands write the files to `exports/` automatically.

## Spend controls (config/thresholds.yaml, `spend:`)

- Prompt caching on every loop iteration and context editing that clears old tool results past 50k input tokens (keeping the last 6, never the create/collect/submit results).
- Effort per stage: `medium` for the idea-to-playbook turn, `low` for follow-ups, `high` for scheduled runs. Effort is the first lever; the model stays the same.
- `max_iterations_research` caps API calls per turn; `session_usd_cap` stops a turn gracefully once the session's estimated spend passes it and says so in the reply.
- Estimated USD per run is in `agent_runs.usage.est_usd` and in every Slack and terminal footer. Prices are in `prices_usd_per_mtok`; update them when models change.

## Scheduled mode (after a venture proceeds)

```
uv run smia activate --tenant <name>      # prospect -> active
uv run smia collect --tenant <name>       # nightly; idempotent, safe to rerun
uv run smia digest --tenant <name>        # weekly digest draft, validated, printed
uv run smia playbook --tenant <name>      # refreshed playbook
```

Scheduling is external for now: run `collect` nightly from n8n or GitHub Actions cron (doc 07). The first missed manual run is the trigger to set that up.

## Review

```
uv run smia review <report_id> approve
uv run smia review <report_id> revise --notes "lead with cadence, drop the X section"
uv run smia review <report_id> revise --notes "..." --rerun     # also reruns with the notes
uv run smia review <report_id> user_test --notes "verbatim answers to the two questions"
```

Notes are stored in `review_feedback` and fed into the next brief for that tenant through the `reviewer_notes` tool.

## Dev and diagnostics

```
uv run smia smoke <platform> <handle> [--raw]    # resolve + fetch one account (2 credits)
uv run smia seed-tenant <name> <industry> instagram:h1 tiktok:h2 twitter:h3   # dev tenant, no verification
uv run smia label --tenant <name> --dimension tone --definition "..." --labels a,b,c
uv run pytest -q                                   # 67 tests; DB tests run when .env has DATABASE_URL
uv run ruff check src tests
```

## Rerunning safely

- `collect` upserts on `(tenant_id, platform, post_id)` and `(post_id, captured_on)`. Running it twice in a day changes nothing; running it tomorrow adds one snapshot row per post.
- A failed agent run leaves an `agent_runs` row with `status = error` and no report. Run it again.
- A draft that fails validation is revised once automatically; if it fails again it is stored with `status = ungrounded` and still surfaced, flagged, so a reviewer sees it.
- Raw vendor payloads are in `raw_captures`; a parser fix can be replayed from there without spending credits.

## Costs observed (2026-09-11)

| Run | Model spend | Vendor credits |
|---|---|---|
| Digest on 3 accounts, 42 posts, 15 tool calls, 2.5 min | ~71k input (67k cached) + 7.4k output on Opus 5, well under $1 | 0 |
| Labelling 42 posts on one dimension with Haiku 4.5 | ~4.6k tokens, under a cent | 0 |
| Research session, idea to playbook (first run, before caching and the submit cap) | ~370k uncached + 555k cached input, 60k output; about $4, of which ~3 drafts were a validator bug | 26 |
| Same session shape with caching, context editing, effort medium and submit cap | expect well under $1.50; measure on the next run | ~25 |

Per-run usage is in `agent_runs.usage`; per-session credits in `chat_sessions.credits_used`.

## Known limits today

- Slack chat is Socket Mode from a laptop or a worker; no hosted deployment yet.
- One day of snapshots means every relative engagement is `initial` and no trend windows exist. This is stated in every report header.
- X samples are the account's most popular posts, not the latest; cadence on X is never claimed.
- Confirm/Decline buttons exist for the competitive set; report Approve/Revise is still the CLI command above.
- No first-party account data yet (Meta Graph, TikTok Business).
- No scheduler in the repo; nightly collection is manual until a prospect is activated.
- Reports post to Slack as text (tables render roughly) and as .docx/.md attachments.

## User test record

Fill in after step 12 of docs/10:

- Venture idea (as described):
- Session id:
- "Would you have found this competitive set and this angle yourself in a day?"
- "Does anything you planned to do next change because of this?"
- What they wanted that they did not get:
