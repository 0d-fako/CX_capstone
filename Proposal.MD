# SMIA — Social Media Intelligence Agent

*CareerEx AI & Automation — Capstone Project Proposal*

**Author:** Olumide Fakorede
**Cohort:** AI & Automation Cohort 4

---

## 1. Title

SMIA — Social/Media Intelligence Agent for GTM & Product Strategy

## 2. Problem Statement

Venture studios like VG Platform launch multiple new brands and products a year. Before any build starts, the team needs a read on the competitive landscape — what's working, what formats and themes are winning, where the gaps are. Today that research is manual, one-off, and done at kickoff only.

The real problem isn't lack of research — it's that the research is never repeated. Markets move weekly; strategy decisions get frozen at the moment of the first (manual) competitor scan and are rarely revisited once the build is underway.

## 3. Proposed Solution

An agent that takes a plain-language description of a business idea, extracts the relevant context (product, industry, geography, target customer, problem it solves), and proposes a candidate set of competitors for the user to confirm or edit.

Once confirmed, the agent continuously observes those competitors' social media activity and turns raw posts into structured competitive intelligence, delivered two ways: a weekly digest and a chat window for ad hoc questions against the same underlying data.

## 4. Key Features

- Intent intake: natural-language business description → structured market context
- Competitor discovery: suggested competitor set, user confirms/edits before tracking starts
- Weekly digest: cadence vs. baseline, format mix, engagement alerts, whitespace watch (see attached mock-up)
- Chat window: ask follow-up questions grounded in the same observed data, not a generic LLM answer
- Traceability: every figure in the digest ties back to an observed post, with a fact-check pass before delivery

## 5. Target Users

Venture studio product and GTM teams (e.g. VG Platform) evaluating or building new brands. Secondary users: founders and marketing teams who want an ongoing competitive read without hiring an analyst.

## 6. How It Will Work

- User describes their business idea in natural language
- Agent extracts context and proposes candidate competitors for confirmation
- Agent pulls and stores each confirmed competitor's social activity on a schedule
- Analysis layer computes cadence vs. baseline, format mix, engagement anomalies, and whitespace (theme/format combinations nobody is using)
- Weekly digest is generated, fact-checked, and delivered (Slack/email)
- Chat window lets the team query the same dataset between digests

## 7. Tools & Technologies

- Backend: Node.js
- LLM: Claude/GPT for context extraction, summarization, and digest drafting
- Social data: platform APIs / approved scraping tools
- Storage: structured DB (Postgres/Mongo) for observed posts and computed metrics
- Scheduling: cron/queue for weekly cadence
- Delivery: Slack/email integration; lightweight chat UI

## 8. Timeline

Total build window: 1–2 weeks after proposal approval.

- Days 1–2: intent intake + competitor discovery/confirmation flow
- Days 3–5: social data collection pipeline
- Days 6–8: analysis engine (cadence, format mix, alerts, whitespace)
- Days 9–11: digest generation and formatting
- Days 12–14: chat interface, delivery integration, end-to-end testing

## 9. Expected Outcome

A working MVP that produces a real weekly digest for a chosen vertical (e.g. Nigerian fintech), demonstrating GTM intelligence a venture studio could act on when scoping a new build — plus a chat interface for follow-up questions.

## 10. Success Criteria

- Digest generated automatically on a weekly schedule with no manual pull
- At least 3 competitor accounts tracked with real, non-mock data
- Alerts and whitespace-watch sections populated from actual observed shifts
- Chat window answers grounded in the same dataset, not generic LLM output

## 11. Risks & Limitations

- Platform API access and rate limits may constrain data freshness or coverage
- Data quality/consistency varies across social platforms
- LLM-generated numeric claims risk hallucination — mitigated with an automated fact-check pass against source data
- Scope creep: keep v1 to one vertical and one digest cadence

## 12. Future Improvements

- Multi-tenant support for multiple studios/portfolios at once
- Additional platforms and richer sentiment analysis
- Auto-generated creative briefs from whitespace findings
- Direct integration into the studio's build/GTM workflow

## 13. Final Deliverables

- Working agent producing scheduled weekly digests
- Chat interface backed by the observed dataset
- Sample digest for a real tracked vertical
- Short handbook documenting how the team should use the tool's output