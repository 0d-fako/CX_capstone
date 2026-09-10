Project ALPHA  |  Social Media Intelligence Agent  |  PRD

**PROJECT ALPHA**

Product Requirements Document

**Social Media Intelligence Agent**

*A multi-tenant competitive and social intelligence engine for the venture platform*

| **Field** | **Detail** |
| --- | --- |
| Document | Product Requirements Document (PRD) |
| Product | Social Media Intelligence Agent (SMIA) |
| Programme | Project ALPHA, adjacent workstream |
| Status | Draft for stakeholder discussion |
| Version | 0.1 |
| Prepared | July 2026 |
| Audience | Leadership, Strategy Ops, AI and Automation, Marketing |

# **1. Overview and context**

This document defines the product requirements for the **Social Media Intelligence Agent** (SMIA), a new system that continuously watches the external market presence of selected brands and competitors, measures what is working, and translates the findings into decision-ready intelligence and content playbooks.

The system is deliberately scoped as a **greenfield build**. It does not inherit from the existing Project ALPHA PostgreSQL, n8n, or Slack infrastructure. This gives us a clean foundation optimised for lean operation and cross-industry scale.

## **Relationship to Project ALPHA**

Project ALPHA is the Client Experience Workflow. Its governing principle is that clients should never have to ask for updates, because communication is proactive and predictable. SMIA sits **adjacent to** Project ALPHA rather than inside it. The two systems share operating DNA (proactive push over pull, a weekly synthesis rhythm, and a human review gateway before anything reaches a client) but serve different owners. SMIA produces market intelligence; Project ALPHA governs client delivery communication. Keeping this boundary explicit protects scope as both systems grow.

## **Consolidation decision**

The previously scoped Competitive Intelligence Agent is **collapsed into** SMIA. Both answer the same question from different angles: what are the players in our space doing, and is it working. One agent now owns two collection surfaces, social accounts and competitor web presence, feeding a single intelligence layer. This is a stronger product than either alone, because it allows correlation across surfaces, for example a competitor changing a pricing page and shifting social tone in the same week.

# **2. Problem statement**

Marketing and venture teams operate against a wall of social noise. Understanding what a competitive set is doing, and whether it is working, is currently manual, slow, and inconsistent. When the platform spins up a new venture in a new vertical, the team starts from zero market literacy every time. There is no systematic way to move a proven tactic from one industry to another before it saturates.

## **Consequences today**

- Decisions on campaign timing, creative direction, and positioning rely on guesswork rather than observed evidence.

- Each new venture repeats the same slow market-scan effort from scratch.

- Whitespace, where competitors are absent or not experimenting, goes unnoticed and unexploited.

- Proven cross-industry tactics are never transferred systematically across the portfolio.

# **3. Goals and non-goals**

## **Goals**

- **Instant market literacy: **point the agent at a competitive set and produce a baseline intelligence report within 48 hours of onboarding.

- **Proactive intelligence: **teams receive a weekly digest without asking, in keeping with the ALPHA push-over-pull principle.

- **Cross-industry pattern transfer: **surface tactics proven in one vertical that are untested in another.

- **Any industry, self-serve: **a user opens the agent, states an industry, provides a competitor list, and receives monitoring and generated playbooks with no engineering involvement.

- **Lean but scalable: **run cheaply at pilot scale, and scale to many tenants without re-architecture.

## **Non-goals (for this release)**

- Real-time streaming monitoring. The system runs on a daily collection and weekly synthesis cadence by design, to reduce noise and cognitive load.

- Building bespoke scraping infrastructure at launch. Data collection starts with a unified third-party API; insourcing is a later optimisation.

- A full public product with billing and self-signup. The first release proves value internally with portfolio companies as design partners.

- Metrics the platforms do not expose (for example competitor watch time and precise follower growth). The insight layer is designed around observable signals.

# **4. Users and use cases**

| **User** | **Primary need** | **Key use case** |
| --- | --- | --- |
| Venture / portfolio marketing lead | Know what the competitive set is doing and what is working | Weekly digest, content playbook for their vertical |
| Studio strategy / validation | Cheap demand signal before committing to a venture | Pre-validation category scan, whitespace map |
| Leadership | Portfolio-wide view of market movement | Cross-industry pattern transfer, benchmarks |
| Marketing / content team | Proven hooks and formats to adapt | Content analysis, engagement trends, example hooks |

# **5. Product capabilities**

The system produces four intelligence outputs, sequenced below by decision leverage rather than ease of build. This ordering guides release priority.

| **Capability** | **What it produces** | **Priority** |
| --- | --- | --- |
| Opportunity signals | Whitespace and gaps where competitors are absent or not experimenting. Highest value for a studio, hardest to build, needs a good category taxonomy. | P1 (highest value) |
| Engagement trends | Which post types and formats are gaining traction. Easiest to build and earns daily usage. | P1 (quick win) |
| Competitive benchmarks | How a brand stacks against its set on cadence, format mix, and engagement. Table stakes for credibility. | P2 |
| Content analysis | Tone, format, and theme classification of posts. Useful but commoditised; wins only when tied to the other three. | P2 |

## **Signature deliverable: the content playbook**

On demand, a user can request a working playbook for their industry. The agent reasons over accumulated data and produces cadence recommendations, format mix, tone guidance, whitespace opportunities, and example hooks. The playbook is the product; the entire pipeline exists to feed it. Its output template is the **blocking design dependency** for the build, because it defines the minimum data every upstream layer must collect.

## **Delivery outputs**

- Weekly intelligence digest, pushed proactively to Slack or delivered by API.

- On-demand content playbook per tenant and industry.

- Monthly deeper benchmark report.

- Alerts on notable changes, for example a competitor shifting posting strategy or an engagement spike on a format.

# **6. Product principles**

| **Principle** | **What it means for the build** |
| --- | --- |
| Multi-tenant from row one | Every data record carries a tenant identifier from the first migration. Costs nothing now, avoids a painful backfill later. |
| Industry is config, not code | Industry and competitor lists are data rows. Adding a new vertical is a configuration change with zero engineering. |
| Proactive push over pull | Intelligence arrives on a schedule. Users should not have to ask, mirroring the ALPHA principle. |
| Human review before client-facing output | Nothing client-facing leaves without human approval, consistent with the AI Governance Framework. |
| Pluggable collection | Data sources sit behind one interface, so a source can be swapped without touching the rest of the system. |
| Lean first, scale on evidence | Start with the simplest thing that works. Add complexity only when the system visibly asks for it. |

# **7. Rollout phases**

The rollout follows the AI Governance Framework requirement of Prototype, then Pilot, then Production, with no skipped stages. The system is naturally read-only, which places it at the low end of the deployment risk matrix.

| **Phase** | **Scope** | **Governance stage** |
| --- | --- | --- |
| Concierge | One analyst plus the model manually producing a weekly radar report for one competitive set. Validates that the insight changes decisions before code is written. | Prototype |
| Pipeline | Automated collection and enrichment into the data store. Weekly digest goes live internally for portfolio companies. | Pilot |
| Product | Multi-tenant lenses, alerting, opportunity-gap engine, and self-serve onboarding. Decision on graduating to a client-facing deliverable. | Production |

# **8. Success metrics**

| **Metric** | **Target intent** |
| --- | --- |
| Time to baseline report for a new venture | Within 48 hours of onboarding a competitive set |
| Weekly digest adoption | Portfolio marketing leads read and act on it, not just receive it |
| Decisions influenced | Documented instances where an insight changed campaign timing, creative, or positioning |
| Cross-industry transfers surfaced | Count of proven tactics flagged as untested in another vertical |
| Cost per tenant per month | Tracked for the eventual spinout unit economics |
| Number of tenants served | Grows without re-architecture |

# **9. Governance, compliance, and risk**

SMIA operates under the VG Platform AI Governance Framework. Because it is read-only and works with publicly visible data, its practical risk is low, but two requirements apply directly.

- **Human-in-the-loop review: **internal synthesis operates at a Tier 2 posture. Any output that becomes client-facing must pass a Tier 3 human review before it leaves.

- **Compliance checklist before productisation: **all collection paths, including paid third-party APIs, operate against platform terms of service to some degree. For an internal tool the exposure is low. Before SMIA graduates into a client-facing product, the governance compliance checklist and any NDPA considerations must be resolved.

## **Key risks**

| **Risk** | **Mitigation** |
| --- | --- |
| Data access fragility | Start with a maintained unified API; insource per platform only when unit economics justify it. Canary checks alert on collection failure. |
| Scope creep between internal tool and client product | Governance tier and output polish differ. The internal framing is the release; productisation is an explicit later decision. |
| Promising metrics platforms do not expose | Design the insight layer around observable signals only. |
| Over-engineering | Single agent, deterministic pipeline for the bulk of the work, complexity added only on evidence. |

# **10. Open questions for stakeholders**

- **Internal tool or venture 0.5? **Is this an internal enabler, or a product the platform externalises after proving it with portfolio companies as first customers? This decision sets governance tier and output polish.

- **Platform priority order. **Instagram, TikTok, and X are the priority set. Which single platform proves the concept first?

- **Cadence. **Weekly strategic reports first, or move toward near-real-time alerting sooner?

- **Playbook template. **This is the blocking dependency. Agreeing the exact playbook structure unlocks the full pipeline design.

**Companion document: ***Technical Design Document (SMIA), which specifies the architecture, data model, tooling stack, and the LangGraph agent design referenced throughout this PRD.*

Draft for stakeholder discussion  Page
