# ADR-0004: Git ↔ session tagging for cross-session blame

**Status:** Proposed. Decide before building cross-session attribution; not needed
for the single-session MVP.

> **Scope note (2026-06-07):** this ADR is about cross-session *blame/anchoring* only. Git is
> **not** HAID's diff source — the analysis-window diff is reconstructed from the transcript
> alone (replay-only; [src/haid/bridge/](../../src/haid/bridge/)), a decision made after
> measuring the bash-write-to-source gap at ~0–1%. Git here is an optional Phase-4 blame/anchor
> layer (and at most a gated diff *verifier*), never required and never the diff source.

## Context

Git is the only source of **ground-truth on-disk state** (including non-tool
changes the transcript can't see) and the only source of **stable cross-session
anchors** (commit SHAs, blame). For a multi-session blame chain to resolve cleanly
across files — rather than "gesture" across them — sessions must be tied to
commits. Without that tie, attribution degrades to reconstructing from file mtimes
against JSONL timestamps, which is fragile.

The transcript records `gitBranch` and a `git_head` at session start/end (verified
fields), which gives a coarse tie for free, but not a per-edit one.

## Options

### A. Post-commit hook writes session attribution (recommended)
A git hook (or a `PostToolUse(Bash: git commit)` Claude Code hook) records, per
commit: the active `session_id`, and a content-hash → session-region map for added
lines. (Mirrors the "agentblame" approach.)
- **Pros:** precise, automatic, survives rebases reasonably; clean per-line
  attribution. **Cons:** requires installing a hook; opt-in.

### B. Best-effort reconciliation, no tagging
After the fact, diff each commit and match added lines to session-produced regions
by content hash; unmatched added lines = human/out-of-band.
- **Pros:** zero setup; works on existing history. **Cons:** ambiguous when
  multiple sessions touch the same lines; misses intra-session ordering.

### C. Coarse branch/HEAD tie only
Use the transcript's `gitBranch` + `git_head_start/end` to bracket each session
between commits.
- **Pros:** free, already in the data. **Cons:** can't attribute individual lines
  within a multi-session commit window.

## Decision (proposed)

Ship **B (best-effort reconciliation)** as the default so the tool works with zero
setup on existing repos, and **offer A (post-commit hook) as an opt-in upgrade**
for users who want precise cross-session blame. Always use **C** as the free
coarse bracket underneath both. This matches HAID's "nothing required, more if you
opt in" philosophy and its trust discipline (explicit `UNRESOLVED` where B can't
disambiguate).

## Consequences

- MVP can ignore this entirely (single-session, tool-stream only).
- The cross-session phase implements B + C; A becomes one of the *recommended
  hooks* HAID itself suggests in reports.
- Unmatched-commit-lines share ("X% of committed lines came from outside any
  audited session") becomes a reportable metric.

## Related

- [ADR-0003](0003-line-identity-anchoring.md) (anchors), and the cross-session
  section of [session-graph-design.md](../session-graph-design.md#cross-session-graph).
