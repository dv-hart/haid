# ADR-0003: Line/region identity via layered anchors

**Status:** Accepted.

## Context

The re-touched-lines metric and the cross-session blame-chain both require
tracking the identity of a logical code region across many edits. But line numbers
**drift** as files change within and across sessions, so a raw line number is not
an identity. This is the single most important correctness decision for the
strongest rework signal (re-touched lines) and for cross-session attribution.

## Decision

Identify a region by a **layered `AnchorSet`**, resolved by priority. A region's
`current_span` ([start,end]) is a **projection** onto a specific file version,
**never** its identity.

1. **Content-hash anchors (primary).** Normalized hash (trim whitespace, ignore
   pure-format changes) of the region, plus a small **context fingerprint** (hashes
   of 2–3 lines above/below). Immune to line-number drift; the context fingerprint
   locates the neighborhood even after the region's own content changes.
2. **`structuredPatch` threading (within a session).** Verified in real data:
   `Edit`/`Write` *results* already include a `structuredPatch` (unified-diff hunks
   `{oldStart, oldLines, newStart, newLines, lines}`) and `originalFile`. The
   pre→post line mapping is handed to us — we emit the deterministic `derives-from`
   edge straight from the hunk ranges, **no diff implementation needed**. Fall back
   to a computed Myers diff only if `structuredPatch` is absent (older records).
   Additionally, the per-edit `userModified` flag signals the user hand-edited the
   file out-of-band before the tool ran — a partial, free fix for the otherwise-
   invisible non-tool-change gap.
3. **Git-blame anchors (cross-session / ground truth).** Anchor regions to commits
   (`anchors-to`) and reconcile via `git blame -L` / content-hash matching against
   commit diffs. Survives edits made outside any session (human commits, formatters,
   `sed -i`) that transcript-diffing cannot see.

**Granularity:** anchor at the **enclosing symbol** (function/class) when the file
parses; fall back to a content-hashed line window otherwise. Symbol-level regions
drift far less and give human-meaningful blame ("the `authMiddleware` function was
rewritten 4 times").

### Resolution order

```
resolve_region(file, line_no, at_commit):
  1. exact content-hash hit            → same region        (high conf)
  2. context-fingerprint hit           → region moved/edited → follow derives-from (med)
  3. in-session Myers line-map known   → mapped region      (high)
  4. git blame → commit → anchored region                   (med)
  5. give up explicitly → NewRegion(origin=UNRESOLVED)
```

## Consequences

- Region identity is stable; `current_span` is computed on demand for display only.
- No diff engine is needed for the common path — `structuredPatch` + `originalFile`
  come with each edit. A Myers fallback is only for records lacking them.
- Cross-session blame degrades gracefully and explicitly to `UNRESOLVED` /
  `ORPHAN(pre-session)` rather than guessing — consistent with the trust discipline.
- Adds a dependency on a diff implementation and (optionally) a lightweight symbol
  parser per language; both can start minimal (line-window only) and improve.

## Related

- [ADR-0004](0004-git-session-tagging.md) — git anchors depend on session↔commit
  tagging to be clean.
- [session-graph-design.md](../session-graph-design.md#line-identity-across-edits).
