# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project wiki

`llm-wiki/wiki/` is the project's knowledge base, written and maintained by the agent. It exists so that knowledge is worked out once and then looked up, not re-derived each session.

- **Look up first.** Before any research for or in the project (web search, reading raw sources, a wide code search), read `llm-wiki/wiki/index.md`, then only the pages it points to. Research only what the wiki does not answer.
- **File what you learn.** When research, an ingested source, or a conversation produces knowledge that would be needed again, write it to the wiki in the same turn, and update the section index and `log.md`.
- **Trust signals.** A page that is `status: draft` or past its `stale_after` is a lead to re-check, not a fact.
- **Sub-agents** get the relevant wiki pages as starting context and return findings for the orchestrator to file.
- **Raw sources** in `llm-wiki/raw/` are immutable.

Read `llm-wiki/SCHEMA.md` before writing a page, ingesting a source, or linting. Do not read `llm-wiki/llm-wiki.md` or `llm-wiki/SPEC.md` (about 12k tokens together) unless the schema itself is being changed; `SCHEMA.md` distils them.

## Agent skills

### Issue tracker

Issues and specs live as local markdown files under `.scratch/<feature-slug>/`. See `docs/agents/issue-tracker.md`.

### Triage labels

The five canonical triage roles use their default names (`needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`), recorded as a `Status:` line in each issue file. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: one `CONTEXT.md` and `docs/adr/` at the repo root. See `docs/agents/domain.md`.
