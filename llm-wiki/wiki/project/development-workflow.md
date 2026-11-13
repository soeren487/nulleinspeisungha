---
type: Process
title: Development workflow
description: Which model does which activity, and where issues, domain docs and project knowledge live.
tags: [project, process]
generated: { by: claude-code/claude-sonnet-5-5, at: 2026-11-13T12:00:00Z }
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

The main session orchestrates (it plans and reviews, as Opus) and may spawn sub-agents on the matching model for each activity: Sonnet implements and writes the tests, Haiku runs the tests and linters.[^soeren-principles] Haiku originally wrote the tests as well; Soeren moved test writing to Sonnet after ticket 02.[^soeren-principles]

# Where things live

| What | Where |
| --- | --- |
| Issues and specs | Local markdown under `.scratch/<feature-slug>/`; see [issue-tracker.md](../../../docs/agents/issue-tracker.md)[^agent-docs] |
| Triage state | A `Status:` line per issue file, using the five default labels; see [triage-labels.md](../../../docs/agents/triage-labels.md)[^agent-docs] |
| Domain glossary and decisions | `CONTEXT.md` and `docs/adr/` at the repo root, created when first needed; see [domain.md](../../../docs/agents/domain.md)[^agent-docs] |
| Tests and linters | `uv run pytest`, `uv run ruff check . && uv run ruff format --check .`; see `CLAUDE.md` |
| CI | `.github/workflows/ci.yml`: tests and linters on the pinned Home Assistant, the tests on the oldest supported Home Assistant, hassfest and HACS validation; on pushes to `main`, on pull requests and weekly |
| Releases | Version bump in `manifest.json` and `pyproject.toml`, `CHANGELOG.md`, tag, push, GitHub release; the agent does all of it when a release is asked for |
| Development tools | `tools/replay.py` (replay recorded days through the charging planner) and `tools/export_history.py` (export such days from Home Assistant); see `tools/README.md`. Real exports go to `llm-wiki/private/` |
| Project knowledge and research | This wiki; conventions in [SCHEMA.md](../../SCHEMA.md) |

# Related

- [Overview](overview.md)

[^soeren-principles]: Development principles stated by Soeren
[^agent-docs]: Agent skill configuration
