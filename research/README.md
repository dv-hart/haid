# Research

Inputs to the design docs. The findings here have been **distilled into
`docs/`**; this folder preserves provenance and the raw ground-truth checks.

## Research passes run (Phase 0)

1. **Claude Code data storage** (web + docs) → distilled into
   [docs/claude-code-data-format.md](../docs/claude-code-data-format.md).
2. **Tooling landscape** (existing CC session tools, parsers, graph libs,
   packaging) → [docs/tooling-landscape.md](../docs/tooling-landscape.md).
3. **Session-graph design** (provenance-DAG prior art, line-identity, build vs
   persist) → [docs/session-graph-design.md](../docs/session-graph-design.md) +
   ADRs.
4. **Ground-truth verification** (this file) — inspecting *real* local `.jsonl`
   files to confirm/correct the web research.

## Ground-truth verification log

Inspected real sessions under
`~/.claude/projects/C--Users-jhart-Documents-DataVine-c7-connector/`
(Claude Code v2.x), 2026-06.

### Sidecar directory layout — CONFIRMED
Each `<session-uuid>.jsonl` has a sibling `<session-uuid>/` dir containing:
- `subagents/agent-<id>.jsonl` + `agent-<id>.meta.json` (one real session had 8
  such pairs).
- `tool-results/<shortid>.txt` (e.g. `b5sbdrfml.txt`) — overflow for large tool
  outputs. Filenames are short random strings, **not** `toolu_…` ids.

### Record types in a real 799-line session — CONFIRMED + CORRECTIONS
Observed `type` counts:
`assistant` 422 · `user` 191 · `ai-title` 61 · `last-prompt` 54 ·
`queue-operation` 32 · `attachment` 29 · `system` 10.

Corrections to the web research:
- ⚠️ **No top-level `tool_result` type.** Tool results ride on `user` records
  carrying `toolUseResult` + `sourceToolAssistantUUID` (each seen 178×). Pair tool
  calls via those, not via a `tool_result` line.
- ⚠️ **No `summary` record** in this (uncompacted) session — compaction shape
  still needs a compacted session to confirm.
- ✅ **`system` records log hooks** — fields `hookCount`, `hookInfos`,
  `hookErrors`, `preventedContinuation`, `stopReason`, `hasOutput`, `toolUseID`.
  Hook activity is auditable from the transcript.

Envelope fields confirmed present: `type`, `uuid`, `parentUuid`, `timestamp`,
`sessionId`, `cwd`, `version`, `gitBranch`, `userType`, `entrypoint`,
`isSidechain`, `promptId` (user), `requestId` (assistant),
`attributionMcpServer`/`attributionMcpTool` (MCP).

> Full distilled reference, with the verified/from-docs/correction legend, lives
> in [docs/claude-code-data-format.md](../docs/claude-code-data-format.md). Open
> items still to verify are tracked in
> [plans/open-questions.md](../plans/open-questions.md).
