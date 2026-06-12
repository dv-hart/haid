# Session graph design

The single data structure the whole tool is built on. This document specifies
the node/edge taxonomy, the construction tiers/layers, the episode abstraction,
and the two core operations.

> **Grounded in real data.** This design has been validated against 38 real
> sessions (Claude Code 2.1.92–2.1.156). The exact fields each node/edge is built
> from are catalogued in [data-inventory.md](data-inventory.md); a worked
> records→graph walkthrough is in
> [data-structure-report.md](data-structure-report.md).

## Design stance

1. **The graph is a provenance/lineage DAG, not the raw turn tree.** The JSONL
   `uuid`/`parentUuid` tree is *input*. The structure HAID builds is a directed,
   mostly-acyclic provenance graph layered on top: instructions cause tool-calls,
   tool-calls read/produce files, files churn.
2. **Tool-call is the primary analysis grain.** Not turn (too coarse — one
   assistant turn fires 5 Reads + 2 Edits and you lose per-action attribution),
   not line (too fine to be a first-class node). Tool-call is where token cost,
   file I/O, and causality converge. Line-regions are nodes too, but *derived and
   lazy* (see §Line identity).
3. **"No traceable origin" is a first-class, typed outcome — never `null`.**
   Papering over a gap with a best-guess parent is the one thing that makes an
   audit tool lie. The orphan rate is itself a headline metric.
4. **Read order is `timestamp` within an agent scope; `parentUuid` is for
   pairing, not ordering.** Empirically the conversation is ~96% linear (only
   273/6,744 parent links branch), and it branches exactly where one assistant
   turn fires multiple tools or at meta-events (subagents, compaction). So the
   spine is timestamp order within an `agentId` scope; `parentUuid` +
   the `tool_result` block's `tool_use_id` pair calls to results and attribute
   meta-branches. Don't infer reading order from the parent chain.

## Construction: tiers and layers

Two cross-cutting ideas govern *how* the graph is built. They answer the central
question "how much can we auto-tag vs. let review agents infer?"

### Determinism tiers (per node/edge)

Every node and edge is tagged with how it was produced. The skeleton is
deterministic; agents only annotate.

- **Tier 1 — auto-tagged, confidence 1.0 (no model).** Built from a literal field.
  Covers nearly the whole graph: `responds-to`, call↔result pairing
  (`tool_result.tool_use_id`), `reads`/`produces`/`edits` (incl. exact line ranges from
  `structuredPatch`), token weights (`usage`), subagent linkage (`meta.toolUseId`),
  compaction boundaries (`compactMetadata`), attribution, hooks, mode transitions,
  external edits (`userModified`/`edited_text_file`).
- **Tier 2 — rule-based, confidence ~0.9 (deterministic heuristic, still no
  model).** Computed from Tier-1 facts by a fixed rule: re-reads, re-touched
  lines, retry loops, co-churn, command-signature normalization, `is_test()`. Each carries a
  rule id. (Bash success/failure was *expected* to live here, but turned out to be
  Tier 1 via the `is_error` flag — see §ToolCall.)
- **Tier 3 — agent-inferred, confidence <1.0 (review agents).** Labels and
  judgment only, never structure: instruction intent / `is_correction`, ambiguous
  `triggers` resolution (the ORPHAN cases), behavioral contradiction, Goodhart
  confession, "was this context used," quality scoring.

The mapping from each graph element to its exact source field and tier is the
table set in [data-structure-report.md](data-structure-report.md#4-tier-1--deterministic-construction-confidence-10).

### Build layers (dependency order)

Build the graph in layers, each depending only on the one below — so the
deterministic core stands alone and the inferred overlay is cleanly separable.

- **L0 — Event spine.** Every record, timestamp-ordered within agent scope. Total
  fidelity, zero interpretation.
- **L1 — Action/IO graph (all Tier 1).** ToolCall ↔ File/Region via
  reads/produces/edits, token weights, subagent rollups. The **MVP is L0 + L1 +
  the Tier-2 metrics**.
- **L2 — Episode/instruction layer (Tier 1 boundaries + thin Tier 3).** Segment
  instructions, draw episode spans, detect corrections (deterministic proxies
  first). **NOTE (grain, 2026-06-08):** the *scoring* **episode** is now defined as a
  **group of whole sessions** on a shared component/topic — the session is atomic and is
  never subdivided (one session = one context window = the only clean cost boundary). The
  authoritative definition is [agent-analysis.md §1](../plans/agent-analysis.md); the
  older instruction-trace-slice framing in this section is superseded where the two differ.
- **L3 — Interpretation overlay (Tier 3).** `triggers` resolution for ambiguous
  cases, waste judgment, contradiction/Goodhart, quality — all confidence-tagged.

The two analysis passes map onto the layers: the signature-scanning pass lives
entirely in L1+Tier-2; the user-anchored pass is L2+L3.

## Node taxonomy

Every node has a global `id` (stable across rebuilds) and `kind`.

### `Session`
`id` (uuid) · `cwd` · `git_head_start/end` · `started_at/ended_at` ·
`agent_type` (`main`|`subagent`) · `parent_session_id?` · `model` ·
`total_tokens` (rollup).

### `Turn` — the spine
`id` (record uuid) · `role` (`user`|`assistant`|`tool_result`) · `parent_uuid?` ·
`ts` · `text` · `tokens` `{in,out,cache_read,cache_create}` (assistant only) ·
`is_meta` (sidechain/compaction/system-injected). Kept for `responds-to` and
token attribution; analysis does not happen here.

> Note on `role: tool_result`: in the current on-disk format tool results ride on
> `user` records carrying a `toolUseResult` dict, paired to their call by the
> **`tool_result` block's `tool_use_id`** (there is no `tool_result` record type, and
> no top-level `sourceToolUseID` — see
> [data-format](claude-code-data-format.md)). HAID normalizes these into a distinct
> logical turn role at parse time.

### `ToolCall` — primary analysis grain
`id` (tool_use id) · `tool` (Read/Edit/Write/MultiEdit/Bash/Grep/Glob/Task) ·
`turn_id` · `ts` · `params` (raw input — target is a *literal* field:
`file_path`/`command`/`pattern`/`url`) · `status` (`ok`|`error`|`rejected`) ·
`result_summary` · `result_bytes` (proxy for context cost) · `signature` ·
`target_file_id?` · `read_span?` ((start, end) half-open, 1-based) ·
`derived_read` · `derived_write` · `write_op?` (`edit`|`overwrite`|`append`).

> **Shell IO is first-class (BUILT).** A `Bash` call is a read or a write when its
> command is one (`cat`/`sed -n`/`head`/`tail` → read; `sed -i`/`>`/`>>`/`tee`/`cp`/`mv`
> → write), parsed at build time by `graph/bash_read.py` / `graph/bash_write.py`. Such a
> call keeps `tool == "Bash"` (so per-tool counts and the command `signature` are
> untouched) but carries `derived_read`/`derived_write` + `target_file_id` (+ `read_span`
> or `write_op`). **The predicate to gate on is `is_read(tc)` / `is_write(tc)`** (native
> tool OR derived) — not `tool == "Read"`. Conservative, high-precision parsers: they
> refuse `grep`/`ssh`/globs/pipelines/command-substitution rather than guess. **Heredoc
> writes are the exception**: `parse_heredoc_write` recovers `cat > f <<EOF … EOF` content
> (it's inline) into `ToolCall.write_content`, so a heredoc write is a content-bearing write,
> not a content-less one.
> ⚠️ A derived **write** has `result_bytes ≈ 0` (a `sed -i`/`>` produces no stdout — the
> changed bytes went to the file, never back into context), so its token weight is ~0.
> That is the honest *authoring cost* (the model didn't emit that content), **not** the
> change magnitude — see [visualization.md](visualization.md#derived-shell-writes) and the
> `retouched` caveat below.

`signature` is load-bearing — a normalized hash that powers retry/redundancy
queries as O(group-by) instead of O(n²):
- Bash → normalized command (strip volatile args/timestamps).
- Read → `(file_id, line_range)` — exact range from result `file{startLine,numLines}`.
- Edit → `(file_id, old_string_hash)`.

> **`status` is Tier 1 for ALL tools, Bash included (corrected 2026-06-06).** The
> *structured* `toolUseResult` has no exit-code field (only `stdout`/`stderr`/
> `interrupted`/`noOutputExpected`/occasional `returnCodeInterpretation`) — which is why
> this was originally expected to need a Tier-2 heuristic. But failure is surfaced
> directly by **`is_error: true` on the `tool_result` content block**, uniform across all
> tools (error results carry an `Exit code N` prefix and no `toolUseResult` dict). So no
> stderr/narration heuristic is needed — see [open-questions V6](../plans/open-questions.md).

### `File` — the cross-session spine
`id` = `repo_id + ":" + repo_relative_path` (deliberately **not** absolute path,
so two sessions touching the same file share one node) · `repo_id` (git remote
hash, or cwd hash if none) · `path` · `lang` · `exists_at_end`.

### `Region` (line-span) — derived, lazy
`id` = `file_id + ":" + anchor_hash` · `file_id` · `anchor` (AnchorSet — see line
identity) · `current_span?` (a *projection* onto a file version, not identity) ·
`logical_label?` (enclosing function/class if parseable). **Only materialized
when an Edit/MultiEdit touches lines**, or on demand for a blame query. Never
enumerate all lines.

### `Instruction` — extracted, not raw
`id` (turn id + segment index) · `turn_id` · `text` (the imperative span) ·
`intent?` (`feature`|`fix`|`refactor`|`question`|`correction`|`meta`) ·
`is_correction` · `ts`. One user turn may carry multiple instructions or none.
Extracting this as its own node (vs. overloading Turn) is what makes episode
detection and "why" queries clean.

### `Episode` — computed subgraph handle (supernode)
`id` · `trigger_instruction_id` · `end_reason`
(`next_instruction`|`correction`|`session_end`) · `member_node_ids` (the
subgraph) · `span_ts` · `token_total` · `outcome`
(`resolved`|`corrected`|`abandoned`). Both a node (attach metrics, draw it) and a
subgraph view (its members). See §Episodes.

**Granularity verdict:** three always-materialized grains — Turn (spine),
ToolCall (analysis), File (cross-session spine); Region and Episode derived;
Instruction extracted. No eager line nodes.

## Edge taxonomy

Directed. Common fields: `src`, `dst`, `type`, `ts`, `weight` (token/byte cost),
`confidence` (0–1, for inferred edges).

**Direction convention** (keep consistent or traversal becomes a swamp):
- **Causal/structural edges point at the cause** → "why/blame" is a forward DFS.
- **I/O edges point at the target.**

| type | src → dst | points at | key attrs | confidence |
|------|-----------|-----------|-----------|-----------:|
| `responds-to` | Turn/ToolCall → Turn/ToolCall | cause | — | 1.0 |
| `triggers` | Instruction → ToolCall/Turn | effect | — | inferred |
| `reads` | ToolCall → File/Region | target | line_range, bytes, nth_read, via | 1.0 |
| `produces` | ToolCall → File/Region | target | op, ±lines, via | 1.0 |
| `edits` | ToolCall → Region | target | old/new hash, revision, via | 1.0 |
| `retries` | ToolCall → ToolCall | prior | attempt_n, params_delta | 1.0 |
| `re-reads` | ToolCall → ToolCall | prior | gap_turns | 1.0 |
| `churns-with` | Region ↔ Region | both | co_edit_count, same_episode | inferred |
| `derives-from` | Region → Region | prior | edit_distance, cross_session | inferred |
| `anchors-to` | Region → Commit | target | — | 1.0 |

Notes on the subtle ones:
- `produces`/`edits` line ranges are **not computed by us** — Edit/Write results
  ship a `structuredPatch` (unified-diff hunks `{oldStart, oldLines, newStart,
  newLines, lines}`) plus `originalFile`. We read ranges straight off it; no diff
  engine needed. (This is why these edges are Tier 1.)
- `via` distinguishes the **channel**: absent (or `tool`) for native Read/Edit/Write,
  `"shell"` for a Bash-derived read/write. A `via:"shell"` **write** carries `op`
  (`edit`/`overwrite`/`append`); for most shell writes (`sed -i`, redirected stdout) the
  content isn't recoverable, so there's **no Region** and **no `±lines`/bytes** and downstream
  treats them as file-level, ~0-weight edges (present but cheap), not authored content. The
  **exception is heredoc writes** (`cat > f <<EOF`), whose content IS recovered into
  `ToolCall.write_content` — the reconstruction bridge replays it into the diff, and other
  shell writes are detected and **flagged** rather than silently dropped.
- `triggers` is *positional and free inside an episode* (actions after instruction
  I and before the next boundary belong to I) — that part is Tier 1. It only
  becomes *inferred* (Tier 3, confidence < 1.0) for actions with no clean
  positional owner (pre-instruction or boundary-crossing — the ORPHAN candidates).
  "Why" queries terminate here.
- `re-reads` is **only created when no `edits` intervene** between the two reads.
  A re-read *after* an edit is legitimate, not waste — so the guard is baked into
  edge construction and the metric becomes a trivial group-by. "Edit" here means
  `is_write(tc)`, so a **shell write** (`sed -i`) between two reads clears the guard
  too — closing a prior blind spot where a re-read after a `sed -i` was falsely flagged.
- `retries` requires same `signature`, same episode, earlier one `status=error`
  (or a detected test-fail). `params_delta ≈ 0` across a chain = pure thrash;
  signature *changing* = the model adapting (escalation, not waste).
- `derives-from`/`anchors-to` are the line-identity backbone (§Line identity, §Cross-session).

## Episodes

The unit users actually reason about ("the auth refactor ate 80k tokens").
Delimited by boundary-class Instruction nodes.

```
detect_episodes(session):
  instrs = instructions(session) ordered by ts
  for instr in instrs:
    if instr.intent in {question} or instr.is_meta: continue   # not a task
    next_boundary = first later instr with
        intent in {feature,fix,refactor} OR is_correction
    end = next_boundary.ts if next_boundary else session.ended_at
    members = nodes with start<=ts<end reachable from instr via
              responds-to* / triggers / reads / produces
    end_reason = correction|next_instruction|session_end
    outcome    = classify_outcome(members, end_reason)
```

**Correction detection** (closes the prior episode as `corrected`). Strongest
signal first:
1. **Graph signal** — the new instruction's tool-calls `edit` a region the prior
   episode just `produced`, within N turns. (Prefer this.)
2. **Lexical signal** — "no", "revert", "that's wrong", "undo", "actually".
3. Triggers an immediate re-edit / retry of just-written code.
Record which fired in `confidence`.

> **REVISED (2026-06-08).** In the as-built classifier ([intent-taxonomy.md](intent-taxonomy.md)),
> a correction is the **`move = correction`** label from the per-message pass — pure LLM judgment,
> not a graph/lexical heuristic. The graph + lexical "priors" above were built then **dropped**
> (redundant with the model). The graph signal (1) — an immediate re-edit of just-`produced` code
> — survives as a **Phase-3 error-attribution** input (blame / recurrence), where behavioral
> re-edit chains are the actual signal, not as a classifier seed.

**Outcome classification:** `corrected` (ended by a correction) · `abandoned`
(unrelated next instruction with errors still open / no successful terminal
edit) · `resolved` (clean: last mutating action succeeded, no open errors).

Episodes are the **denominator** for every waste metric ("3 of 12 reads in this
episode were redundant"). Conceptually a trace slice around one instruction.

## Two core operations

### "Why did you do X?" — backward causality

Backward dynamic slicing on the provenance graph. From any node (usually a
ToolCall or Region), find the triggering Instruction(s).

```
why(X):
  follow = {responds-to, triggers⁻¹, produces⁻¹, edits⁻¹, retries, reads⁻¹}
  BFS from X along `follow`, guarded by max_depth:
    if node is Instruction: record as root, stop that branch
    elif node has no predecessors along follow: record_orphan(node)
    else: expand
  return Resolution(X, roots, orphans)
```

Typed outcomes (**never `null`**):
- `TRACED` — exactly one instruction root. Confidence = product of edge
  confidences along the path.
- `AMBIGUOUS` — ≥2 roots. Return all, ranked by path-confidence × recency.
- `SYSTEM_INDUCED` — root is a hook/system-injected/compaction (`is_meta`) turn,
  not a user instruction.
- `ORPHAN` — frontier exhausted with no instruction. Tag the sub-cause:
  autonomous (model self-directed), broken-chain (data gap), or pre-session
  (file predates the session). **Do not attach a guessed parent.**

Orphan rate is a headline audit metric ("18% of edits had no traceable user
instruction" = autonomy / scope-creep signal). It only exists if the model is
forbidden from papering over gaps — see [trust-discipline](trust-discipline.md).

### "Where did the tokens go?" — weighted aggregation

Token cost lives on assistant `Turn` nodes; push it down attribution edges. Keep
the four components (`in`/`out`/`cache_read`/`cache_create`) separate — a huge
`cache_read` is *good caching*, a different story from huge `output`.

Three lenses from one propagation:
1. **Turn → ToolCall:** split a turn's output across its tool_use blocks;
   apportion shared input/cache by responsibility share.
2. **Episode:** sum cost over member turns.
3. **File:** charge `read_cost` (ingestion) + `gen_cost` (edit/write output) +
   **`resident_cost`** — the big hidden one: a file's bytes ride along in
   `cache_read` every turn until compaction. `resident_cost ≈ file_tokens ×
   turns_resident`. This is what makes "unused context" a *token number*, not just
   a count.
4. **Instruction lineage:** charge each turn to `why(turn)`'s roots, splitting
   AMBIGUOUS by path-confidence.

Charge unattributable tokens (system prompt, tool defs) to a synthetic
`OVERHEAD` bucket so totals reconcile to ground truth.

## Line identity across edits

Line numbers drift; region *identity* must survive. **Use a layered AnchorSet,
resolved by priority** (full rationale in
[ADR-0003](decisions/0003-line-identity-anchoring.md)):

1. **Content-hash anchors (primary)** — normalized hash of the region plus a
   small context fingerprint (2–3 lines above/below). Immune to line-number
   drift; locates the neighborhood even after the region itself changes.
2. **`structuredPatch` threading (within session)** — Edit/Write results ship the
   diff *and* `originalFile`, so the pre→post line mapping is given to us, not
   computed: emit the `derives-from` edge directly from the hunk ranges. (Only fall
   back to a Myers diff if a record predates `structuredPatch` or it's absent.) The
   transcript *tells* us the mapping; no guessing.
3. **Git-blame anchors (cross-session / ground truth)** — anchor regions to
   commits and reconcile via blame / content-hash matching against commit diffs.
   Survives edits made outside any session — but note `userModified` already flags
   tool-channel hand-edits inline, so git is mainly for formatter/`sed`/build
   changes (the channels the tool stream can't see).

`current_span` ([start,end]) is a **projection** of a region onto a file version,
never its identity. Identity = AnchorSet. Anchor at the enclosing symbol
(function/class) when the file parses, falling back to a content-hashed window —
symbol-level regions drift far less and give human-meaningful blame ("the
`authMiddleware` function was rewritten 4 times").

## Cross-session graph

Link per-session graphs two ways:
1. **Shared File nodes** — because File id is `repo_id + relpath`, every
   session's reads/produces converge on the same File node automatically. Cheap,
   always available.
2. **Git commit anchors** — `Commit` nodes (sha, ts, parents, author) +
   `anchors-to` edges. Sessions connect through commits; commits also bound
   sessions via `git_head_start/end`.

**Blame-chain** (trace a final-diff line to its origin): resolve the line to a
Region, walk `derives-from` backward across revisions and sessions; at each step
record session, turn, `why()` instruction, and anchored commit. Newest→oldest;
the last element is the origin. Terminates in `ORPHAN(pre-session)` where history
predates any session — explicit, again. Reconcile session edits with actual
commits by hashing added lines per commit and matching to session-produced
regions; unmatched added lines = human/out-of-band, surface that share too.

## Build vs persist

**Hybrid:** persist a normalized store (SQLite), build the in-memory graph on
demand from it, cache parsed-session artifacts. Rationale and schema in
[ADR-0002](decisions/0002-graph-build-vs-persist.md). In short: one session graph
is hundreds–thousands of nodes (in-memory is trivial and gives rich graph algos
for free); cross-session spans many and re-runs repeatedly (so persist + cache by
file-hash, parse only appended bytes for active sessions).
