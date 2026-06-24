# Open questions & things to verify early

Decisions and empirical checks that should be resolved before the phases that
depend on them. Grouped by urgency.

## Decisions to make

### Q1. Implementation language / stack — *blocks Phase 1 code*
Recommended Python; see [ADR-0001](../docs/decisions/0001-language-and-stack.md).
Needs maintainer sign-off because it's foundational. (Scaffolding is currently
language-neutral, so nothing is blocked yet.)

### Q2. Git ↔ session tagging — *blocks Phase 4*
How precise must cross-session blame be on day one? Proposed: best-effort
reconciliation by default + opt-in post-commit hook. See
[ADR-0004](../docs/decisions/0004-git-session-tagging.md).

### Q3. Report surface — *Phase 1/5*
Terminal Markdown only for MVP. Later: is HAID primarily a CLI, a Claude Code
skill (`/haid:report`) that an agent narrates, or both? Affects how much
interpretation is pre-rendered vs. left to the invoking agent.

### Q4. Trend-score definition — *Phase 5*
What exactly is the "personal efficiency" number, and how is it normalized so it's
comparable across a user's own weeks without becoming a Goodhart target? Self-
comparison only — no cross-user ranking.

## Empirical behaviors — status after the Phase-0 data analysis

Most of these are now **resolved** by [data-inventory.md](../docs/data-inventory.md)
(analysis of 38 real sessions). Remaining open items flagged below.

### V1. Compaction — ✅ RESOLVED
`system` record `subtype:"compact_boundary"` with `compactMetadata
{trigger, preTokens, postTokens, preCompactDiscoveredTools, durationMs}` +
`logicalParentUuid`. Summary is a `user` record with `isCompactSummary:true`;
context re-injected via `compact_file_reference` attachment. Pre-compaction turns
are retained. → the "acted on a dropped instruction" detector is feasible.
*Remaining:* confirm summaries are reliably diffable against the original turns.

### V2. Truncation — ✅ RESOLVED (built 2026-06-06)
Reads carry `truncatedByTokenCap`; overflow lands in `tool-results/` (filenames vary:
short ids AND `toolu_<id>.txt`). **`toolUseResult.persistedOutputPath` is the
authoritative pointer** (absolute path) — `src/haid/session/overflow.py` follows it with
a sidecar `tool-results/<basename>` fallback for moved-tree/cross-context reads; genuinely
missing files are surfaced. `Edit` results include full `oldString`/`newString` +
`originalFile` (intact). *Remaining:* exact overflow threshold (not needed — we follow the
explicit path).

### V3. Subagent stitching — ✅ RESOLVED + BUILT (2026-06-06)
`.meta.json` = `{agentType, description, toolUseId}`; `toolUseId` links to the parent
`Agent` tool_use; records carry `agentId` + `isSidechain`. Built in
`src/haid/session/subagents.py`, validated on 65 transcripts. **Findings:** stitch
**top-level only** (`subagents/` can hold a nested `workflows/` tree with thousands of
workflow-agent files — 2581 in one session); `meta.toolUseId` is **often `null`**
(27/37 boxBot — parsed but unattributable, surfaced not dropped); every subagent that
*has* an id links exactly (10/10). *Remaining (deferred, none observed yet):*
nested sub-subagents, and cross-file/**resume** linkage (parent call in a sibling
session — resolve in the multi-session aggregation layer).

### V4. Resumed-session duplication / branching — ✅ RESOLVED + BUILT (2026-06-06)
Dedup is by record **`uuid`** (no collisions; ccusage-style). The transcript is a
**forest**, not a line — see [claude-code-data-format.md](../docs/claude-code-data-format.md)
§Threading and [phase1-build.md](archive/phase1-build.md) §0.5 for the four branch shapes
(structural / rewind / resume-trunk / interrupt), the `leafUuid`+timestamp active-branch
resolution, and the timeline-scoping rule. Built in `src/haid/session/forest.py`,
validated on 65 transcripts (12 boxBot rewinds, 0 false positives).

### V5. Token/usage fields — ✅ RESOLVED
All assistant records carry input/output/cache_creation/cache_read + `cache_creation`
ephemeral split + `iterations[]` + `server_tool_use`. Complete across all 6
versions in the corpus.

### V6. Bash/PowerShell success vs. failure — ✅ RESOLVED (2026-06-06)
No heuristic needed after all. **A call failed iff its `tool_result` content block has
`is_error: true`** — verified across the corpus: 332 error results (Bash 268, Edit 45,
…), all carrying an `"Exit code N\n…"` content prefix and (notably) **NO `toolUseResult`
dict**, which is why an index keyed on that dict silently drops every failure. The
structured `toolUseResult` indeed has no return-code field, but `is_error` is the
authoritative, uniform signal across all tools (Bash included). `stderr` is noisy and
benign (e.g. *"Shell cwd was reset"*) — **not** used; `interrupted`/`returnCodeInterpretation`
are rare and partly benign ("No matches found" = grep exit-1). Implemented in
`graph/build.py` (`status="error"`); the next-turn-narration cross-check is unnecessary.

### V7. Diff source for scoring — ✅ RESOLVED (2026-06-07): replay-only, NO git
The scored diff for an analysis window is reconstructed from the **transcript alone**
(`src/haid/bridge/`): Edit `oldString`→`newString` + `originalFile`, Write `content`, Bash
heredoc content. **Git was rejected as the diff source** after measuring the
bash-write-to-*source* gap at **~0–1%** across three real projects (0/110 HAID, 0/128
c7-connector, 5/198 boxBot — 3 of those 5 don't even count): git's marginal coverage is tiny
while its cost is high (commit↔episode misalignment with no commit discipline; mutable/rebased
history read long after; it bundles human edits the agent didn't make = wrong attribution for
*agent* scoring). Git stays optional/Phase-4 only (blame/anchors, or a gated opt-in diff
*verifier*) — never the diff source, never required. (Also informs Q2 below: git is confirmed
Phase-4-only and never the diff source.)

### V8. Bash-write content for the diff — ✅ RESOLVED (2026-06-07)
**Heredoc** bash-writes (`cat > f <<EOF … EOF`) now have their content recovered
(`graph/bash_write.parse_heredoc_write` → `ToolCall.write_content`) and feed the reconstructed
diff. Other shell writes (`sed -i`, plain `>`/`>>`) remain content-unrecoverable but are
**detected and flagged** (`BridgeResult.caveats`), never silently dropped. Residual measured at
~0–1% (see V7).

## Design areas to refine in focused sessions

These are converged in structure (docs exist) but have specifics flagged ⟳ to work
out in dedicated sessions.

### D1. Scoring rubric ([scoring-rubric.md](../docs/scoring-rubric.md))
**Largely resolved for difficulty (2026-06-04).** The score is **relative** (placement
against a reference ladder), not absolute SEH; mined review-signals as ground truth and
absolute grounding were **falsified/dropped** (pilot report archived on the `archive/experiments` branch).
Validated mechanism: **dense all-pairs of a small blinded anchor set → bulletproof
ordering → place new diffs against it** (cheap Haiku reproduces it, ρ≈0.87);
cross-method convergence (pairwise ≡ coarse classification) replaces human labels. Unit
= a session-sized diff; calibration units are PR diffs (`pass2 --mode pr`) **and**
personal-project commit diffs (`--mode commit`). Canonical docs:
[difficulty-ladder.md](../docs/difficulty-ladder.md) + [axis-calibration-playbook.md](../docs/axis-calibration-playbook.md).
**Cleanliness calibrated (2026-06-05)** ([cleanliness-ladder.md](../docs/cleanliness-ladder.md)).
**Originality calibrated then DROPPED** — it failed to earn its place (saturates at "mid",
ρ=+0.68 vs difficulty, no resolution in the recombination space where SE actually lives;
see scoring-rubric.md Axis decision). Final achievement = `f(volume[LOC], difficulty,
cleanliness)`. **Built (2026-06-05):** the relative scorer (`src/haid/scoring/` —
placement vs locked ladders, replay-validated to the calibration ρ=0.866) and the
deterministic **volume** measure (`volume.py`, confirmed ⊥ difficulty). **Cost built
(2026-06-06):** `cost.py`, normalized tokens. **Combiner built (2026-06-06):** `value.py` —
`achievement = LOC**α · D(difficulty) · C(cleanliness)`, `value = achievement / normalized_tokens`
(α=0.5; convex BT-latent difficulty top/median=10x; steep penalty-only cleanliness γ=2 floor=0.001;
linear cost). 14 tests; `haid value` CLI. **Bridge built (2026-06-07):** the window→diff/usage
extractor that feeds real sessions in is done (`src/haid/bridge/`, replay-only; `haid value
--project/--session`; see V7/V8). Still open: the **diagnosis router**; the **skill/plugin glue**
that runs the comparison subagents for the live backend; and the **episode-grain** bridge
refinement (per-episode diffs, needs Phase-2 episodes). **Settled forks (don't relitigate):** volume sub-linear
(not "2x lines = 2x"); difficulty convex via latent; cleanliness a major penalty-only axis (no
symmetric norm, no bonus); cost linear because the fixed-cost penalty lands on value not achievement.

### D2. Intent taxonomy ([intent-taxonomy.md](../docs/intent-taxonomy.md))
**Classifier BUILT (2026-06-08)** — `src/haid/intent/` + `haid tag` (move × work-type + purpose,
manifest/backend pattern, walks all branches). Deterministic priors were built then **dropped**
(redundant with the model; the one out-of-band re-edit signal moves to Phase 3). Still open:
final category wording (esp. correction vs refinement — validate on the live pass); the
classifier prompt + few-shot; context budget is as-built (head+tail 400 ch / last 40 turns) but
tunable; thread-id/hierarchy on purpose snapshots vs. inferring threads at wrap-up; multi-
instruction messages.

### D3. Visualization ([visualization.md](../docs/visualization.md))
ELK vs. hand-rolled orthogonal routing; track ordering / crossing-minimization;
file-read-and-edited rendering; scale/collapsing for huge sessions; color budget;
log-scaled token widths; clickable interactions.

### D4. Earlier open items still live
Region-identity granularity (symbol vs. content-window), the Bash-failure rule (V6),
attachment modeling (first-class nodes vs. folded). *(Resumed-session dedup / branching
(V4) and subagent stitching (V3) are now resolved + built — see above.)*

## Test fixtures needed
- A small **anonymized** real session for parser tests → `tests/fixtures/`.
- A **compacted** session (for V1).
- A session with **subagents** (for V3) — the c7-connector sessions have these.
- Ideally a reconstruction of the **canonical case** (spec→test→impl→cleanup) for
  the Phase 3 acid test.
