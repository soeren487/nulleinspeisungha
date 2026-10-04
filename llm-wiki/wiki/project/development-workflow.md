---
type: Process
title: Development workflow
description: Which model does which activity, and where issues, domain docs and project knowledge live.
tags: [project, process]
generated: { by: claude-code/claude-opus-5-5, at: 2026-10-04T20:30:00Z }
sources:
  - id: soeren-principles
    resource: instruction from Soeren in the Claude Code session of 2026-10-04
    title: Development principles stated by Soeren
    author: human:soeren
  - id: agent-docs
    resource: ../../../docs/agents/
    title: Agent skill configuration
---

# Model per activity

| Activity | Model |
| --- | --- |
| Planning, review | Opus 5.5 |
| Implementation, including writing the tests | Sonnet 5.5 |
| Running the tests and linters | Haiku 4.5 |

The main session orchestrates and may spawn sub-agents on the matching model for each activity.[^soeren-principles] Haiku originally wrote the tests as well; Soeren moved test writing to Sonnet after ticket 02.[^soeren-principles]

# Where things live

| What | Where |
| --- | --- |
| Issues and specs | Local markdown under `.scratch/<feature-slug>/`; see [issue-tracker.md](../../../docs/agents/issue-tracker.md)[^agent-docs] |
| Triage state | A `Status:` line per issue file, using the five default labels; see [triage-labels.md](../../../docs/agents/triage-labels.md)[^agent-docs] |
| Domain glossary and decisions | `CONTEXT.md` and `docs/adr/` at the repo root, created when first needed; see [domain.md](../../../docs/agents/domain.md)[^agent-docs] |
| Project knowledge and research | This wiki; conventions in [SCHEMA.md](../../SCHEMA.md) |

# Related

- [Overview](overview.md)

[^soeren-principles]: Development principles stated by Soeren
[^agent-docs]: Agent skill configuration
