# MVP specification

> **This is the canonical metric/pipeline spec.** Scope was amended 2026-06-06 to
> **multi-session by default** (and build progress is tracked) in
> [phase1-build.md](phase1-build.md) §0 — read it alongside this. The "one session"
> framing below was only ever about making the trustworthiness test cheap.

> Ship the smallest thing that tests the core risk: **are the diagnoses
> trustworthy?** This is useful on its own and is the cheapest way to find out
> before building anything fancier on top.

Scope: **one session, objective metrics only, light hedged interpretation.** No
LLM judgment, no episodes, no cross-session, no git. Deterministic and
reasoning-free by design — which is exactly what makes it trustworthy enough to
validate the premise.

## Pipeline

```
1. PARSE      JSONL → typed records (+ stitch subagents, resolve overflow)
2. GRAPH      records → session graph (nodes + edges)
3. METRICS    graph → 4 objective waste metrics
4. METRICS    → measured substrate: inspection view (Markdown) + JSON hand-off
```

## 1. Parse

Input: one `<session-uuid>.jsonl` (+ its `subagents/` and `tool-results/`).
Output: typed records. Requirements:

- Read line-by-line; tolerate a partial trailing line (active session).
- Branch on the `version` field; **validate each record and loudly flag unknown
  shapes** (port the Rust crate's schema-drift-validator idea). Never silently drop.
- Order records by `timestamp` within `agentId` scope (the spine); use
  `parentUuid` only for pairing/meta-branches.
- Pair tool calls with results via the **`tool_result` block's `tool_use_id`** (→ the
  `tool_use.id`) on the result-bearing `user` record (verified 100% across 7509 results;
  **there is no top-level `sourceToolUseID`**); `sourceToolAssistantUUID` links only to
  the calling *turn*. **There is no top-level `tool_result` type** — see
  [data-format](../docs/claude-code-data-format.md).
- Resolve overflow: load `tool-results/<shortid>.txt` / Bash `persistedOutputPath`
  when referenced; respect `truncatedByTokenCap` on reads.
- Discover + stitch `subagents/agent-*.jsonl` via `meta.json.toolUseId` ↔ parent
  `Agent` call; handle async subagents (`isAsync`, result is a launch receipt).
- Cache parsed output keyed by `(session_id, file_hash)`.

## 2. Build graph (L0 spine + L1 action/IO graph — all Tier 1)

Materialize: Session, Turn, ToolCall, File nodes; Region nodes only where an
Edit/MultiEdit touches lines. Edges: responds-to, reads, produces/edits, plus the
derived re-reads / retries / churns-with needed by the metrics. Compute each
ToolCall's `signature`. (Schema + tier/layer model:
[session-graph-design.md](../docs/session-graph-design.md).)

Edit/Write `produces`/`edits` line ranges come **directly from the result's
`structuredPatch`** (+ `originalFile`) — no diff engine. Region identity via the
layered AnchorSet (content-hash + `structuredPatch` threading; git anchors out of
MVP scope). See [ADR-0003](../docs/decisions/0003-line-identity-anchoring.md).

## 3. The waste metrics

**Four metrics, each at two scopes** (`session` and `window`) — the metric × scope model
(see [metrics-output-schema.md](../docs/metrics-output-schema.md)). Scope is never baked into
a name; the **cross-session signals are just `metric @ window`** (`rereads @ window` = the
re-establishment tax; `retouched @ window` = cross-session rework). Each `(metric, scope)`
returns instances + a rate + a token-weight + its own baseline placement, and carries its
explicit "legitimate" carve-out.

| Metric | Query (sketch) | `window` scope adds | Carve-out |
|--------|----------------|---------------------|-----------|
| **rereads** | re-read of an already-seen span, no edit since (per timeline) | file re-read across ≥2 sessions, never edited = **re-establishment tax** | re-read after an edit is legitimate |
| **retries** | connected `retries` of one signature, len ≥ 2 | same signature failing across ≥2 sessions | signature *changing* = adaptation, not waste |
| **retouched** | rewriting own produced lines | …rewritten across sessions (compounding) | editing pre-existing code is normal |
| **unused_context** | large reads never edited / cited / followed | …never edited *anywhere* in the window (later edits earn credit) | weight by tokens; ignore tiny reads |

Full definitions in [detectors.md](../docs/detectors.md).

## 4. Metrics output (`haid metrics`)

The measured **substrate**, not the user-facing report. The report and the
visualization are the *final product* (Phase 5 / Phase 1.5); they compose this
substrate with the later phases' why-analysis (intent-tagging P2, error-attribution
/ `why()` P3) and the value score. So `haid metrics` is **pure measurement**:

- **Objective measurement only**, plainly stated and measured ("`auth.ts` was read 4×
  with no edit between reads — ~6k tokens"), each with its **baseline placement**.
  No "this suggests…/try a hook" lines — inferring *why* and *what to fix* is the
  next phases' job, and this output is their input.
- **Aggregate window + per-session sections**; rank instances by token weight.
- **No silent caps** — if anything was skipped (malformed records, a subagent not
  stitched, top-N truncation, the single-author baseline), say so.
- **Markdown** = maintainer eyeball / DoD-validation view. **JSON** = the
  machine-readable hand-off to the Phase 2/3 subagent passes (the point, not a
  nice-to-have).

## Out of scope for MVP (deliberately)
- LLM-based classification, episodes, `why()` traversal.
- Behavioral-contradiction / Goodhart / co-churn detectors (Phase 3).
- Git, blame-chain (Phase 4). *(Cross-session is NOT out of scope — the analysis
  window is the multi-session unit; see [phase1-build.md §0](phase1-build.md). Only the
  git layer is deferred.)*
- Trend score, recommendations engine, packaging as a skill (Phase 5).
- Snapshot quality scores.

## Definition of done
- Runs on the maintainer's own real sessions end-to-end.
- On manual review, the flagged waste is **recognizably real** — a low
  false-positive rate is the whole point. If the metrics cry wolf, fix that before
  proceeding; a tool that misdiagnoses is worse than nothing.
