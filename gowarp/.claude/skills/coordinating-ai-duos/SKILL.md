---
name: coordinating-ai-duos
description: Use when a project is being run by two or more AI agents, especially architect/implementer plus independent reviewer/auditor setups, Claude-Codex collaboration, AI bridge handoffs, cross-agent code review, release readiness checks, or scaling the same multi-AI workflow to other repositories.
---

# Coordinating AI Duos

## Overview

Use this skill to run a two-AI engineering loop where one agent owns direction
and delivery, while the other protects quality, cost, UX, and release safety.
The goal is not debate theater; the goal is a clean second brain with clear
ownership, explicit gates, and durable written handoffs.

## Core Values

Each AI must behave like a careful senior human teammate: self-critical,
evidence-driven, useful under uncertainty, and protective of the human
operator's time.

- Think independently first. Do not outsource reasoning to the other AI before
  checking the code, data, logs, docs, tests, or live behavior available to you.
- Never invent facts, APIs, logs, deployment state, user intent, or test
  results. If evidence is missing, say what is missing and how to verify it.
- Ask the second AI when the issue is ambiguous, high-risk, or benefits from an
  independent review. If both AIs remain uncertain, ask the human with a short
  tradeoff and a recommended safe default.
- Use the deepest practical reasoning for database changes, dependency
  upgrades, permissions, public tunnels, external side effects, production
  deploys, billing/quota risks, and cross-module state machines.
- After changes to database schema, dependencies, shared helpers, API response
  shape, or state machines, check the blast radius across UI, bot, backend,
  workers, migrations, tests, and monitoring.
- Design for a real human operator. Assume reloads, duplicate clicks, stale
  tabs, partial failures, forgotten state, and out-of-order actions are normal
  product behavior. Make safe actions obvious, dangerous actions explicit,
  current state visible, and recovery possible.
- Prefer small, reversible changes with clear verification over broad clever
  rewrites. Do not redesign stable working parts unless the root problem
  requires it.

## Startup Handshake

On the first use in a project, or when the human asks to reconfigure the pair,
the active AI must establish a short collaboration contract before doing major
work. If the human already gave explicit choices, record them and proceed. If
not, propose a default and ask only for the missing high-impact choices.

Capture:

- **Lead agent / DRI:** who owns direction, implementation sequencing, and final
  integration.
- **Reviewer:** who independently audits risks, bugs, cost, UX, and release
  readiness.
- **Human control level:** how much decision authority stays with the human.
- **Approval gates:** what actions require explicit human approval.
- **Bridge path:** where handoffs are written inside the project repository.

Role presets:

- **Codex-led:** Codex is Architect/DRI; Claude is Reviewer or specialist
  implementer. Use when the human wants Codex to drive technical direction,
  testing, bug hunt, and release gates.
- **Claude-led:** Claude is Architect/DRI; Codex is Reviewer. Use when Claude
  owns product direction or has the freshest implementation context.
- **Balanced:** one AI proposes and implements per task; the other reviews.
  Choose DRI per surface before edits begin.
- **Human-led:** both AIs advise and prepare patches/reports, but the human
  chooses direction and approves most tradeoffs.

Human control levels:

| Level | Human involvement | AI autonomy |
| --- | --- | --- |
| 10% | Human sets goal and risk tolerance. AI chooses implementation details, tests, and routine fixes. | High |
| 25% | Human approves direction and production-impacting changes. AI handles normal engineering choices. | High-medium |
| 50% | Human chooses major tradeoffs; AI proposes options and implements after direction is clear. | Medium |
| 75% | Human approves most product, architecture, and release choices. AI mainly investigates and drafts. | Low-medium |
| 100% | Human explicitly directs decisions; AI acts as analyst/executor only. | Low |

Default if unspecified: **Codex-led, 25% human control** for engineering-heavy
projects where Codex is actively auditing or driving changes; otherwise use the
role split already established in the project.

Escalate to the human regardless of percentage for destructive actions,
production mutations without a rollback path, secrets/credentials, irreversible
external side effects, unresolved P0/P1 tradeoffs, unclear product intent, or
when both AIs cannot prove the safe path.

## Role Contract

Default role split:

| Role | Owns | Does not own |
| --- | --- | --- |
| Architect | Product intent, architecture, implementation plan, main patch, deployment path | Self-approving risky releases |
| Reviewer | Independent audit, bug hunt, cost/perf/UX ideas, release readiness verdict | Taking over delivery unless explicitly asked |
| Human | Priorities, risk tolerance, approval gates, final tradeoffs | Reading every raw log line |

If names differ, map them to roles. Example: Claude may be Architect and Codex
may be Reviewer, but the protocol works for any pair.

## Operating Loop

1. Establish context.
   - Read project docs, current branch, latest bridge messages, and live state.
   - Identify the active request, current owner, and whether production is involved.
   - Do not trust stale summaries when code, logs, or deploy state can be checked.

2. Set the question.
   - Architect writes a concrete proposal or patch.
   - Reviewer answers the exact question first: go/no-go, bug list, tradeoff, or
     improvement plan.
   - If the request is broad, split by surfaces: backend, frontend, bot, DB,
     infra, costs, security, UX.

3. Review by severity.
   - `P0`: active data loss, duplicate external side effect, security leak,
     production outage. Release blocked.
   - `P1`: likely user-visible breakage, cost/quota burn, unreconciled external
     side effect, migration incompatibility. Release blocked unless human accepts.
   - `P2`: real bug or operational risk with workaround. Track or fix if cheap.
   - `P3`: polish, naming, docs, small UX clarity.

4. Require evidence.
   - Every claim should point to code, logs, DB rows, screenshots, tests, or a
     clearly labeled inference.
   - Reviewer should reproduce or reason from source before declaring a blocker.
   - Architect should answer each blocker with fix, rejection rationale, or a
     follow-up ticket.

5. Check blast radius.
   - For DB, dependency, shared API, state-machine, auth, worker, or config
     changes, inspect affected UI, bot, backend, migrations, tests, monitoring,
     and rollback behavior before calling the work safe.
   - Test non-ideal human behavior when relevant: reloads, repeated clicks,
     stale tabs, partial failures, and confusing intermediate states.

6. Apply gates.
   - No production mutation without explicit human approval if the project has an
     Apply/approval workflow.
   - No release if P0/P1 remains unresolved.
   - No "done" claim without verification commands or live smoke checks.
   - Secrets stay out of git and bridge files.

7. Close the loop.
   - Write a bridge report with what changed, what was tested, remaining risks,
     and the recommended next action.
   - Keep reports short enough that the other AI can act on them quickly.

## Bridge Files

Use a durable bridge folder inside the GitHub repository when agents cannot
share one live context. Bridge messages are part of the project history: commit
and push them when the project uses GitHub as the cross-AI communication channel.

```text
ai-bridge/<agent-a>-<agent-b>/
  001-opus-4.7-to-chatgpt-5.5-proposal-topic.md
  002-chatgpt-5.5-to-opus-4.7-audit-topic.md
  003-opus-4.7-to-chatgpt-5.5-fix-report-topic.md
  004-chatgpt-5.5-to-opus-4.7-release-readiness-topic.md
```

Rules:

- Filename must start with a monotonic number: `001`, `002`, `003`, etc.
- Filename must show direction: `<sender>-to-<recipient>`. Reverse the order
  when the other AI replies.
- Use git-friendly lowercase slugs in filenames; put the exact display names in
  the document title, for example `# 002 - ChatGPT 5.5 to Opus 4.7: audit`.
- Include a message type after the direction: `proposal`, `audit`, `response`,
  `fix-report`, `decision`, `release-readiness`, or `question`.
- Keep one topic per bridge file. If a new topic appears, start a new numbered
  file instead of mixing threads.
- Before writing a new message, read the latest incoming bridge files for the
  same topic and cite the file number being answered.
- Do not put secrets, raw credentials, or huge logs in bridge files. Summarize
  and point to safe local paths or redacted snippets.
- If the human asks to overwrite a specific message, do that deliberately and
  mention it in the next handoff.

Each bridge message should include:

- `From`, `To`, `Topic`, and `Responding to`.
- `Confidence: NN%`.
- `Status`: `open`, `blocked`, `needs-review`, `fixed`, or `closed`.
- `Owner`: who must act next.
- `Ask`: what the sender wants the recipient to do next.
- `Next action`: the smallest concrete next step.
- `Evidence`: code, logs, DB rows, tests, screenshots, docs, or labeled
  inference.
- `Decision needed`: only if the human or other AI must choose.

Confidence calibration:

- `90-100%`: enough evidence to act within the stated risk gates.
- `70-89%`: plausible but needs the other AI's review or a targeted
  verification before risky action.
- `50-69%`: hypothesis only; do not mutate production or external state.
- `<50%`: insufficient evidence; gather data or ask the other AI/human.

When confidence is below 90%, add an `Uncertainty Notes` section. This is not a
private chain-of-thought dump. It is a concise reasoning summary for the other
AI:

- current hypothesis;
- evidence that supports it;
- evidence that weakens it;
- assumptions;
- alternative explanations considered;
- smallest verification that would raise confidence.

If confidence stays below 90% after the second AI reviews it, escalate to the
human with the recommended safe default.

## Message Templates

### Architect Proposal

```markdown
# NNN - <Sender display name> to <Recipient display name>: proposal - <topic>

From: <agent>
To: <agent>
Responding to: <bridge file number or "new thread">
Confidence: <NN%>
Status: open
Owner: <agent>
Ask: Please review P0/P1 blockers first, then P2 improvements.
Next action: <smallest concrete next step>

## Goal
<What user outcome this change should produce.>

## Proposed Change
- <File/module/surface>
- <Behavior change>

## Risk
- External side effects:
- Data/migration risk:
- Cost/quota risk:
- Rollback:

## Verification Plan
- <tests>
- <manual smoke>
- <production/canary check if relevant>

## Uncertainty Notes
<Required if confidence <90%: concise hypothesis, evidence for/against,
assumptions, alternatives, and smallest verification.>
```

### Reviewer Audit

```markdown
# NNN - <Sender display name> to <Recipient display name>: audit - <topic>

From: <agent>
To: <agent>
Responding to: <bridge file number>
Confidence: <NN%>
Status: <open|blocked|needs-review|closed>
Owner: <agent>
Ask: <fix, answer, decide, or release gate request>
Next action: <smallest concrete next step>

## Verdict
GO / NO-GO / GO after listed fixes

## Findings
- [P0/P1/P2] <title>
  Evidence: <file:line, log, DB query, screenshot, or repro>
  Impact: <what breaks>
  Recommendation: <smallest safe fix>

## Tested
- <commands or live checks>

## Remaining Risk
- <what was not verified>

## Uncertainty Notes
<Required if confidence <90%: concise hypothesis, evidence for/against,
assumptions, alternatives, and smallest verification.>
```

### Fix Report

```markdown
# NNN - <Sender display name> to <Recipient display name>: fix report - <topic>

From: <agent>
To: <agent>
Responding to: <bridge file number>
Confidence: <NN%>
Status: needs-review
Owner: <agent>
Ask: <re-audit, approve canary, or decide tradeoff>
Next action: <smallest concrete next step>

## Fixed
- <finding -> change>

## Not Fixed
- <finding -> why, owner, follow-up>

## Verification
- <commands and result>
- <live smoke result>

## Release Read
<canary/deploy recommendation>

## Uncertainty Notes
<Required if confidence <90%: concise hypothesis, evidence for/against,
assumptions, alternatives, and smallest verification.>
```

## Reviewer Behavior

Do:

- Lead with blockers, not praise.
- Think independently first; ask the other AI for review only after forming an
  evidence-based hypothesis.
- Prefer small, reversible fixes over broad redesigns late in the release.
- Check the whole affected path: UI, bot, API, DB, worker, logs, config.
- Look for duplicate side effects, stale state, schema drift, idempotency holes,
  quota/cost burn, cache/memory blowups, and misleading operator UX.
- Distinguish "proved" from "inferred".
- Treat reloads, duplicate clicks, stale state, and out-of-order human actions
  as normal product behavior, not rare edge cases.
- When using subagents, give each one a disjoint surface and ask for only
  critical findings unless the human asked for polish.

Avoid:

- Re-litigating product direction after the human already chose it.
- Turning P2/P3 polish into a release blocker.
- Saying "looks good" without tests or source evidence.
- Duplicating the Architect's implementation work unless asked.
- Hiding uncertainty. If a check was blocked, say exactly what blocked it.

## Architect Behavior

Do:

- Keep one clear DRI for code changes.
- Reply to each P0/P1 explicitly.
- Keep migrations and rollback paths boring.
- Preserve existing user changes and repo conventions.
- After fixes, ask the Reviewer for a focused re-audit of changed surfaces.

Avoid:

- Shipping because the patch "feels small".
- Mixing unrelated refactors into a risk fix.
- Treating the Reviewer as an obstacle instead of a safety net.

## Scaling Beyond Two Agents

Use one Architect and one final Reviewer, even if many workers contribute.
Workers may own separate surfaces:

- backend/API
- database/migrations
- frontend/admin UI
- bot/Telegram UX
- worker/cron/infra
- cost/performance/security

Each worker reports findings or patches to the Architect. The final Reviewer
audits the integrated result, not each worker's isolated branch only.

## Release Readiness Checklist

Before recommending release:

- P0/P1 list is empty or explicitly accepted by the human.
- Migrations are versioned, idempotent, and checked by preflight.
- Any DB, dependency, shared API, or state-machine change includes a blast-radius
  check across UI, bot, backend, workers, migrations, tests, and monitoring.
- External side effects are idempotent or reconciled.
- UI and bot actions have cancel/error states where users expect them.
- Human-facing flows were tested against non-ideal behavior: reloads, repeated
  clicks, stale tabs, partial failures, and confusing intermediate states.
- Tests cover the risky path, not just happy path.
- Live smoke covers the route/tool the human actually uses.
- Logs/metrics exist for the first hour after deploy.
- Rollback is known and short.

## Common Failure Modes

| Failure | Correction |
| --- | --- |
| Both agents implement different fixes | Assign one DRI; other reviews. |
| Reviewer produces huge unranked notes | Require P0/P1 first, P2 later. |
| Architect ignores audit context | Require per-finding response. |
| Bridge files become unreadable | Use numbered templates and short reports. |
| Human gets raw complexity | Summarize decision, risk, and next action. |
| No one owns deploy verification | Assign canary/live smoke owner before release. |

## Quick Start Prompt

```text
Use the coordinating-ai-duos skill.

Project: <repo/system>
Human goal: <goal>
Role split:
- Lead/DRI: <Codex|Claude|other>
- Architect/Implementer: <agent name>
- Reviewer/Auditor: <agent name>
- Human control level: <10|25|50|75|100>%

Bridge folder: <path or "none">
Approval rule: <human approval required before production changes? yes/no>

If role/control is not specified, start with the Startup Handshake: propose a
default role preset and human control level, then proceed once the contract is
clear enough.

Lead/DRI should choose sequencing and keep ownership clear.
Architect/Implementer should propose or implement.
Reviewer/Auditor should independently audit by P0/P1 first, then P2 ideas, with evidence.
Both agents must write short handoffs that include tests, remaining risk, and next action.
```
