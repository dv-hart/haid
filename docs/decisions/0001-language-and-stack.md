# ADR-0001: Implementation language & core stack

**Status:** Accepted — **Python** (confirmed by maintainer, 2026-06-01).

## Context

HAID parses Claude Code JSONL, builds a graph, runs graph queries + light LLM
classification, and ships as a Claude Code skill/plugin. The language choice
determines which existing libraries we can reuse (see
[tooling-landscape.md](../tooling-landscape.md)).

## Options

### A. Python (recommended)
- **Parsing:** own typed parser (Pydantic/dataclasses) modeled on the Rust
  `claude-code-transcripts` `Entry` variants + its schema-drift validator;
  streaming à la `ccusage-py`.
- **Graph:** networkx — `DiGraph`, native `predecessors()`/`ancestors()` for the
  backwards traversal that powers `why()`.
- **Persistence:** SQLite (stdlib).
- **Packaging:** plugin + skill, analysis logic as a `uvx`-installable CLI in
  `scripts/` (the pattern claude-code-log uses).
- **Pros:** best fit for the analysis/coaching/LLM domain; networkx ergonomics;
  strong data tooling; easy for contributors.
- **Cons:** must hand-roll the typed parser (mitigated by porting the Rust crate's
  layout); Node-based skill ecosystem is slightly more idiomatic.

### B. TypeScript
- **Parsing:** depend on `@constellos/claude-code-kit` (Zod schemas +
  `parseTranscript` + `getAgentEdits()`), *pending license verification*.
- **Graph:** graphology + graphology-traversal.
- **Pros:** an off-the-shelf typed parser incl. an edit-analysis helper; same
  runtime as much of the CC tooling ecosystem.
- **Cons:** stale satellite packages in graphology; dependency on an [unverified]-
  license package; less natural for heavier analysis/LLM glue.

## Decision

**Recommend Python (Option A).** The tool is fundamentally a data-analysis +
coaching engine; Python's graph and data ergonomics and the low contributor
barrier outweigh TS's off-the-shelf parser, especially since we want a
schema-drift-validating parser of our own anyway.

> This is recorded as **Proposed** rather than Accepted because it is the one
> genuinely consequential structural choice and the maintainer may prefer the
> Node ecosystem. The current scaffolding (docs/plans/folders) is
> language-neutral; nothing is blocked on confirming this.

## Consequences

- `scripts/` and `src/` will hold a Python package; add `pyproject.toml`,
  `uvx`-installable entry point.
- Port (don't depend on) the Rust crate's record-type enum + drift validator.
- Reference ccusage's discovery/dedup logic; reimplement, don't import.
- If reversed to TS, re-evaluate `@constellos/claude-code-kit` and graphology.
