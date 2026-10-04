# Wiki schema

How the project wiki is structured and maintained. Read this before an ingest, a lint pass, or creating a new page. For a plain lookup, `wiki/index.md` is enough.

This file distils `llm-wiki.md` (the pattern) and `SPEC.md` (Open Knowledge Format v0.2). Do not re-read those two unless this schema itself is being changed.

## Layout

```
llm-wiki/
  llm-wiki.md, SPEC.md   pattern and format reference; immutable
  SCHEMA.md              this file
  raw/                   source documents as received; immutable, never edited
    assets/              images belonging to raw sources
  wiki/                  the OKF bundle; written and maintained by the agent
    index.md             catalog of sections (carries okf_version)
    log.md               change history, newest first
    project/             what the project is, how it is developed, its current state
    concepts/            domain and technology pages (devices, protocols, regulations, components)
    sources/             one summary page per ingested raw source
    research/            findings from web or codebase research, and query answers worth keeping
```

Sections other than `project/` are created when their first page is written. Each section has its own `index.md`.

## What belongs where

| Knowledge | Home |
| --- | --- |
| Domain glossary (canonical terms) | `CONTEXT.md` at the repo root; the wiki links to it and uses its terms |
| Architecture decisions | `docs/adr/`; the wiki links to them, never restates them |
| Issues, specs, tickets | `.scratch/` (see `docs/agents/issue-tracker.md`) |
| How the agent should behave | `CLAUDE.md` and agent memory |
| Everything else worth knowing twice: research findings, source summaries, external facts, how subsystems fit together, project state | the wiki |

Do not copy what the code or git history already records. Describe what takes several files or a web search to work out.

## Page format

Every page in `wiki/` except `index.md` and `log.md` is an OKF concept: YAML frontmatter, then a markdown body.

```yaml
---
type: Research Finding          # required; see types below
title: Short display name
description: One sentence, reused verbatim in the section index.
tags: [tag, tag]
status: draft                   # draft | stable | deprecated; absent means stable
generated: { by: claude-code/claude-sonnet-5-5, at: 2026-10-04T07:52:47Z }
verified: { by: human:soeren, at: 2026-10-05T10:00:00Z }   # only when actually confirmed
stale_after: 2027-01-01T00:00:00Z                          # only for facts that age
sources:
  - id: vendor-manual
    resource: ../../raw/vendor-manual.pdf    # or a URL, or a repo path
    title: Vendor manual v2
    last_modified: 2026-03-01T00:00:00Z
---
```

Rules:

- **`type`** values in use: `Overview`, `Process`, `Concept`, `Component`, `Source Summary`, `Research Finding`, `Comparison`, `Reference`. Add a new one only when none fits, and list it here.
- **`generated.by`** is the model that wrote the content, as `claude-code/<model-id>`. Update `generated.at` on every meaningful content change. Timestamps are ISO 8601 UTC.
- **`verified`** is added only when a human confirmed the page (`human:soeren`) or a deterministic check did (`process:<id>`, for example a passing test). An agent re-reading its own page is not verification.
- **`stale_after`** is set on anything that ages: software versions, prices, regulations, API behaviour, firmware. Leave it off for timeless material.
- **`sources`** lists what the page derives from. Attribute individual claims with footnotes whose label is the source `id`: `The limit is 800 W.[^vendor-manual]`. A claim with no source is the agent's own inference and is marked as such in the prose.
- **Body**: headings, lists and tables rather than long prose. Lead with the conclusion. One topic per page; split a page that passes about 200 lines.
- **Links**: relative markdown links between pages (`../concepts/foo.md`). Link every mention of something that has its own page. A link to a page that does not exist yet is allowed and marks knowledge still to be written.
- **File names**: lowercase kebab-case. Research pages take no date prefix; the date is in `generated.at`.

## Index and log

- `index.md` files have no frontmatter (the root one carries only `okf_version`). Entries are `* [Title](path) - description`, grouped under headings. Every page appears in its section index; every section appears in the root index.
- `log.md` groups entries under `## YYYY-MM-DD`, newest date first. Each entry is one bullet starting with a bold verb: `**Ingest**`, `**Creation**`, `**Update**`, `**Deprecation**`, `**Lint**`, and links the pages touched.

## Operations

### Query (the default, every session)

1. Read `wiki/index.md`, then the relevant section index, then only the pages those point to.
2. Check `status`, `stale_after` and `verified` before relying on a page. Stale or `draft` content is a lead to re-check, not a fact.
3. Go to raw sources, the web or a wide code search only for what the wiki does not answer.
4. If answering took real work (several sources, a comparison, a non-obvious conclusion), file the answer as a page in `research/`.

### Ingest (a new file in `raw/`, or a URL the user hands over)

1. Read the source. For a URL, save a markdown copy in `raw/` first so the page has a stable source.
2. Write `sources/<slug>.md`: what the source is, its key claims, and how far to trust it.
3. Update or create the `concepts/` and `project/` pages the source touches. Where it contradicts an existing page, say so on that page and name both sources; do not silently overwrite.
4. Update the section indexes and the root index.
5. Add a log entry.

Ingest one source at a time and report the pages touched.

### Research filing (after web or codebase research)

Write the finding to `research/<slug>.md` with its sources and a `stale_after` where it ages, update affected concept pages, index, log. A sub-agent doing research is given the relevant wiki pages as its starting context, writes its `research/` page itself and returns only a short summary; the orchestrator updates concept pages, index and log.

### Lint (on request)

Check and report: pages missing from an index, index entries with no page, links to missing pages, pages past `stale_after`, `draft` pages older than a month, contradictions between pages, concepts mentioned on several pages that lack their own page, and wiki text that restates `CONTEXT.md` or an ADR. Fix the mechanical ones, list the rest, add a log entry.
