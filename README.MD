# SMIA — Social Media Intelligence Agent

A marketing intelligence agent that gives venture studios (e.g. VG Platform) an ongoing competitive read to inform GTM and product strategy for new builds.

Describe a business idea → SMIA proposes competitors to track → you confirm the set → it collects their social activity → hands back a playbook in the same chat, then a weekly digest once the venture proceeds. Every number in an output traces to a tool call over observed data.

## Status

Capstone project — CareerEx AI & Automation cohort. MVP built: research chat in Slack and terminal, validated playbooks with .docx export, weekly digest. See [RUNBOOK.md](./RUNBOOK.md).

## Docs

- [Project Proposal](./Proposal.MD)
- [Design docs index](./docs/00_INDEX.md) — start here
- [Architecture v4: the analyst agent](./docs/09_SMIA_Architecture_v4_Analyst_Agent.md) — source of truth
- [3-day MVP build guide](./docs/10_SMIA_3_Day_MVP_Build_Guide.md)
- [Mock Weekly Digest](./SMIA_Mock_Weekly_Digest.docx) — illustrative example of the weekly deliverable
- [RUNBOOK.md](./RUNBOOK.md) — how to run it
- [CLAUDE.md](./CLAUDE.md) — the rules every build session follows

## Stack

Python 3.12+ · uv · Anthropic SDK (Claude Opus 5 analyst, Haiku 4.5 labelling) · Postgres on Neon · ScrapeCreators for collection · Slack (Socket Mode chat + webhook delivery) · python-docx exports

The proposal listed Node.js; the design docs settled on Python so the deterministic metrics, the agent loop and the collectors share one language and one test suite.
