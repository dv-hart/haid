# Phase 1 build plan (MVP)

> A concrete, sequenced build layered on the spec in [mvp.md](mvp.md). Where this
> doc and `mvp.md` differ, the differences are **deliberate amendments** recorded in
> §0; `mvp.md` keeps the canonical metric/pipeline definitions.

Phase 1 implements the **signature-scanning analysis pass** end to end: deterministic
(Tier 1 + Tier 2), no model in the loop. That reasoning-free quality is the whole
point — it is the cheapest way to test the core risk: **are the diagnoses
trustworthy?** Pipeline: **PARSE → GRAPH → METRICS → (inspection view).**

**Naming (corrected 2026-06-06):** the Phase-1 deliverable is **not** "the report." It is
`haid metrics` — the deterministic measured **substrate**: it computes the four waste metrics
at both `session` and `window` **scope** (window scope is where the cross-session signals —
the re-establishment tax, cross-session rework — live) with per-scope baseline placement, and
emits a plain inspection view (for DoD validation) plus a
machine-readable JSON hand-off for the **later-phase subagent passes** (intent-tagging in
Phase 2, error-attribution / `why()` in Phase 3) that answer *why*. The user-facing
**report and visualization ARE the final product** — the report is the Phase-5 packaged
`/haid:report`, the visualization is Phase 1.5 — and they *compose* this substrate with the
why-analysis and the value score. "report" is reserved for that final thing.

**Visualization is the *next* deliverable after this build, as Phase 1.5 (MVP)** —
not Phase 4.5 as originally planned ([visualization.md](../docs/visualization.md),
[roadmap.md](roadmap.md)). It was moved up because its dependencies are already met: the
multi-session window and repo-relative file nodes (the cross-session column) are built
here in Phase 1, and it renders the very metrics this build produces (bus width = metric
token-weight). Only git commit-anchors wait for Phase 4. This Phase-1 build ends at the
`haid metrics` inspection view (+ JSON hand-off) — the diagram is the immediately-following
step, fed by the same graph and metrics.

## 0. Amendments to the original MVP scope (decided 2026-06-06, user-driven)

1. **Multi-session by default, not single-session.** The single-session framing in
   `mvp.md` was about making the trustworthiness test cheap, not capping the product.
   Coaching value is cumulative ("how am I doing across the sessions that went into
   this PR?"). The graph already supports this for free: `File` ids are
   `repo_id + relpath`, so every session's reads/produces converge on the same File
   node. Phase 1 ingests **N sessions for a project → one combined graph → metrics
   with per-session AND aggregate views.** Default behavior of "how am I doing?" =
   recent sessions for the current project (cwd), not just the active one.

2. **Draw the cross-session line at the CHEAP half.** *In:* multi-session ingestion,
   shared File-node aggregation, per-session + aggregate metrics. *Deferred to Phase
   4 (and it should be — opinion, flagged):* true git/**PR-based grouping** (needs git
   reconciliation, [ADR-0004](../docs/decisions/0004-git-session-tagging.md)) and
   **cross-session line lineage / blame-chains** (needs git-blame anchors). For Phase
   1, "the sessions in a PR" = a project + time window or an explicit session list;
   **line-level rework stays a within-session metric, aggregated across sessions by
   count.** This limit is stated loudly in the report (no-silent-caps), never faked.

3. **Persistence = SQLite-backed parse cache + in-memory graph.** This is ADR-0002's
   hybrid, minimally. SQLite is **zero user friction** — `sqlite3` is in the Python
   stdlib (no install, no server, just a file under the project or `~/.haid`). The
   user flow stays `pip install haid` → add skill → "how am I doing?". Multi-session
   makes the cache *earn its keep*: parse only new/appended bytes, never re-parse 20
   sessions every run. The graph itself is built in-memory per run (trivial at this
   scale).

4. **Branching / rewind / resume is a Phase-1 correctness gate (not just dedup).**
   See §0.5 — grounded in a scan of all 26 real transcripts on the machine.

## 0.5 Branching, rewinds, and resume (empirically grounded 2026-06-06)

A session transcript is a **forest**, not a line. Verified against all 26 local
transcripts. Four distinct on-disk shapes that MUST NOT be conflated:

| Shape | On-disk signature | Rewind? |
|---|---|---|
| **Structural fork** | one assistant turn, ≥2 children: parallel `tool_use`→multiple `tool_result`, async-subagent attach, or final reply past `leafUuid` | No — normal graph |
| **Rewind / abandoned branch** | an **off-active-path `user` *text* prompt** + its descendants | **Yes** |
| **Resume (new trunk)** | a 2nd `parentUuid:null` root in the same file; often a *"Continue from where you left off."* prompt | No |
| **Interrupt** | `"[Request interrupted by user]"` user record | Branch stops |

Verified facts (HAID's 10 transcripts + 55 boxBot transcripts in WSL at
`~/.claude/projects/-home-jhart-software-boxBot`, the richer branched corpus):
- **Dedup key = record `uuid`.** No uuid collisions exist; the only "extra" records
  are uuid-less metadata (`ai-title`, `last-prompt`, `mode`, `queue-operation`,
  `custom-title`). Dedup is trivial and exact.
- **~20% of sessions branch** (11 of 55 boxBot files have ≥1 rewind).
- **Rewinds manifest two ways:** (a) **sibling fork** — one parent, ≥2 children whose
  user text is the *same or edited* prompt (edit-and-resubmit); one child active, the
  twin abandoned. (b) **off-path chain** — an abandoned prompt + its descendants not on
  the active path.
- **Active branch = latest `last-prompt.leafUuid` walked via `parentUuid` to a root**
  — BUT it is **absent/dangling in 18% of files (10 of 55 boxBot)**, so a **fallback is
  mandatory**: the latest-`timestamp` main-thread leaf, walked to its root.
- **Synthetic-content records are a false-positive trap.** Content wrapped in
  `<command-name>` / `<command-message>` / `<command-args>` / `<local-command-stdout>` /
  `<local-command-caveat>` (slash-/local-commands, e.g. a `/login` cluster sharing one
  timestamp), `<bash-input>` / `<bash-stdout>` / `<bash-stderr>` (ctrl-B bash mode), and
  `<task-notification>` (injected background-task notices) sit off the active path and
  *look* like user prompts. They are NOT instructions — the classifier MUST exclude them
  (and tool_results and `[Request interrupted by user]`). Filtering these dropped the
  boxBot rewind count from a naive 11 files/21 to a true **8 files/12**.
- **Known metadata types (uuid-less, outside the tree):** `ai-title`, `custom-title`,
  `last-prompt`, `mode`, `permission-mode`, `queue-operation`, `pr-link` (carries
  `prNumber`/`prUrl` — useful for Phase-4 PR grouping), `agent-name`, and
  `file-history-snapshot` (CC's rewind-CHECKPOINT file state — 918 in boxBot). Register
  all of them or the drift report fires on every session.
- A single file can hold **multiple disconnected roots** (`parentUuid:null` more than
  once) from in-file resume — RARE (0 of 55 boxBot; seen once, in a git-*worktree*
  project dir). Cross-file resume (a root whose `parentUuid` points into another file)
  wasn't seen but must be tolerated.

**Required handling:**
1. **Parse to a forest** keyed by `uuid`; build parent/child maps; identify all roots
   (null or external `parentUuid`) and the active leaf (leafUuid + timestamp fallback).
2. **Classify each branch point** as structural vs semantic — a structural fork
   (parallel tool results, subagent attach) is NOT a rewind. Mistaking the two
   manufactures phantom timelines.
3. **Enumerate timelines** = the actual root→leaf paths the model experienced: the
   active timeline + each abandoned rewind branch + each resume trunk.
4. **Count roots/instructions once.** Branches are alternate *continuations of one
   instruction*, not new roots — otherwise `why()` triple-credits a shared plan
   prefix.
5. **SCOPE EVERY METRIC WITHIN A TIMELINE, never across the flattened record set.**
   This is the correctness crux: two reads on *different* branches are not a redundant
   re-read (the model never had the first in context on the second branch). Flattening
   by timestamp manufactures false positives — exactly the DoD failure mode.
6. **Two signals the forest gives for free** (label them distinctly, never merge into
   the four core metrics): **abandoned-branch cost** (tokens spent on rewound-away
   branches — cost counts all branches, achievement is measured only on the surviving
   active branch's end state) and **rewind re-establishment cost** (re-reading files
   to rebuild context after a rewind — real, but NOT an in-context redundant re-read).

## Module layout (new subpackages under `src/haid/`)

```
src/haid/
  session/          # PARSE  ✅ all built 2026-06-06
    records.py      # typed record view + content classification + version-aware validation
    parse.py        # tolerant JSONL reader (partial trailing line OK) + drift report
    forest.py       # forest model: dedup by uuid, roots, active leaf (leafUuid + ts
                    #   fallback), structural-vs-rewind classification, timelines
    subagents.py    # stitch TOP-LEVEL subagents/agent-*.jsonl via meta.toolUseId
    overflow.py     # follow toolUseResult.persistedOutputPath (+ sidecar fallback)
    cache.py        # SQLite parse cache, keyed by (path, content-hash); ~/.haid/cache.db
    loader.py       # load_session(): main + subagents + overflow + forest (Step-2 input)
    discover.py     # find sessions for a project (cwd -> encoded transcript dir)
  graph/            # GRAPH (L0 spine + L1 action/IO, all Tier 1)
    model.py        # Session/Turn/ToolCall/File/Region nodes; edge types
    build.py        # L0 timestamp spine -> L1 pairing + reads/produces/edits
    signature.py    # normalized signatures (Bash/Read/Edit) -- Tier 2
    regions.py      # AnchorSet (content-hash + structuredPatch threading), within-session
    derived.py      # re-reads / retries / churns-with edges (carve-outs baked in)
    bashstatus.py   # Bash success/failure inference rule (Tier 2; gates retries)
  metrics/          # METRICS (Tier 2)
    base.py         # MetricResult: instances + rate + token_weight + carve_out + skipped
    rereads.py  retries.py  retouched.py  unused_context.py
  metrics/
    view.py         # METRICS OUTPUT: pure-measurement inspection view (Markdown) — metric +
                    #   baseline placement + ranked traceable instances + no-silent-caps footer.
                    #   NO remedy/"this suggests…" lines (the why/fix is Phase 2/3).
    json_out.py     # machine-readable hand-off to the Phase 2/3 subagent passes (flagged
                    #   files/regions/timelines to investigate) — the real point of JSON here.
                    #   CONTRACT: docs/metrics-output-schema.md (versioned).
```
CLI gains: `haid metrics [--session PATH|UUID]... [--project PATH] [--since DATE] [--json]`.
No args = recent sessions for cwd's project.

## Build sequence

Ordered to hit a real, eyeballable metric on real data as early as possible.

### Step 1 — Parse + validate (`session/`) ✅ COMPLETE 2026-06-06
Shipped: `records.py`, `parse.py`, `forest.py`, `subagents.py`, `overflow.py`, `cache.py`,
`loader.py`, `discover.py`; `tests/session/` (20 fixture tests). **50 tests pass.**

**Corpus-validated** over 65 real transcripts (10 HAID + 55 boxBot):
- **Parse/forest:** 0 schema drift (after registering 4 metadata types), active-leaf
  fallback fires on the 10 dangling-leaf files, **12 boxBot rewinds (7 sibling-fork, 5
  off-path-chain), zero false positives** — all genuine user instructions. The naive
  ad-hoc count (11 files / 21) dropped once command/bash-mode/`<task-notification>` noise
  was filtered.
- **Subagents:** TOP-LEVEL glob only — `rglob` had pulled in 2581+ nested workflow-agent
  files from one session. Every subagent that records a `toolUseId` links to its parent
  Agent call (HAID 4/4; boxBot 10/10 of those with an id); the other 27 boxBot subagents
  have `meta.toolUseId: null` (version/spawn-path drift) — parsed and surfaced as an
  attribution caveat, never silently dropped.
- **Overflow:** follow `toolUseResult.persistedOutputPath` (absolute); sidecar
  `tool-results/<basename>` fallback recovers it when the absolute path doesn't resolve
  (moved tree / cross-context). Genuinely-missing files are reported.
- **Cache:** SQLite by content-hash; partial-tail (active) sessions skipped.

Deferred (noted, not silently): incremental "parse only appended bytes" for active
sessions; cross-file/resume subagent linkage (the null-id and resumed-sibling cases);
nested sub-subagent stitching (open-questions V3, not yet observed).
- Line-by-line JSONL reader; tolerate a partial trailing line (active session).
- Typed records branching on `version`; **validate each record and loudly flag
  unknown shapes — never silently drop** (port the Rust crate's schema-drift idea).
- Pair tool calls -> results via the **`tool_result` block's `tool_use_id`** on the
  result-bearing `user` record (verified 100% / 7509 results; **no top-level
  `sourceToolUseID` exists** — earlier docs were wrong); `sourceToolAssistantUUID` -> the
  calling *turn* only. There is **no top-level `tool_result` record type** (see
  [data-format](../docs/claude-code-data-format.md)).
- Resolve overflow: `tool-results/<shortid>.txt`, Bash `persistedOutputPath`; respect
  `truncatedByTokenCap`.
- Discover + stitch `subagents/agent-*.jsonl` via `meta.json.toolUseId` <-> parent
  `Agent` call; handle async subagents (launch-receipt result).
- **Forest, not line (§0.5):** build the `uuid`-keyed parent/child maps; identify all
  roots (null/external `parentUuid`); resolve the active leaf from the latest
  `last-prompt.leafUuid` with the **timestamp-fallback** for when it's dangling.
  `branches.py` classifies each branch point (structural vs semantic-rewind) and
  enumerates the timelines (active + abandoned + resume trunks).
- **Multi-session:** `discover.py` maps a project path to its encoded transcript dir
  (`C--Users-...`); `dedup.py` dedups records across files by `uuid`; `cache.py`
  stores parsed artifacts in SQLite keyed by `(path, file_hash)`, re-parsing only
  appended bytes.
- **Validate by running parse over all of the maintainer's own sessions** — any
  unhandled shape surfaces here, loudly.

### Step 2 — L0 spine + L1 I/O graph (`graph/`, all Tier 1) ✅ COMPLETE 2026-06-06
Shipped: `graph/model.py` (Turn/ToolCall/File/Region/Edge/SessionGraph), `graph/build.py`
(`build_graph` + `timeline_toolcalls`), `graph/signature.py`; `tests/graph/test_build.py`
(7 tests). **57 tests pass.** Built as specified:
- Nodes: Turn, ToolCall, File (`repo-relative` id -> shared across sessions), Region
  (lazy, materialized from `structuredPatch` hunks).
- Edges: `responds-to`, `reads`, `produces`, `edits`. Edit/Write line ranges come
  **straight off the result's `structuredPatch` — no diff engine.**
- `signature` per call (Bash normalized cmd / Read `(file,range)` / Edit `(file,old_hash)`).
- `timeline_toolcalls(graph, timeline)` gives the per-timeline scope Step 3 metrics run in.

**Corpus-validated** (65 transcripts): HAID 862 toolcalls / 251 regions / 0 unpaired;
boxBot 6977 / 1262 / 0. **KEY SCHEMA FIX surfaced here:** call↔result pairing is via the
`tool_result` block's **`tool_use_id`**, NOT a top-level `sourceToolUseID` (which does not
exist — 0/7509 results). The earlier docs/memory were wrong; corrected across the repo.
Deferred to Step 3: Bash success/failure is Tier-2 (`status="unknown"` for Bash today).

### Steps 3+4 — Derived signals + the four metrics ✅ COMPLETE 2026-06-06
Built as `src/haid/metrics/` (base, rereads, retries, retouched, unused_context, baseline,
+ json_out, view — Step 5) + `src/haid/window.py` + `tests/metrics/`. **82 total tests pass.**
Derived edges are
**not materialized** — each metric computes directly over the window's tool calls.
**Bash-failure rule RESOLVED:** `status=="error"` via the tool_result `is_error` flag
(open-questions V6) — no heuristic needed.

**UNIT = the analysis WINDOW, not one session** (`haid.window`; user decision 2026-06-06).
A window = a project's sessions over a timeframe (default **30 days**, configurable; history
retention verified ≥38 days, no 30-day cap). The window is also the **baseline** unit.

**Two orthogonal axes: metric × scope** (the elegant model; see
[metrics-output-schema.md](../docs/metrics-output-schema.md)). Scope is **never** baked into
a metric name — there is no `cross_session_rereads` metric; it is `rereads @ scope:window`.

- **metric** ∈ `{rereads, retries, retouched, unused_context}` — *what kind* of waste (four).
- **scope** ∈ `{session, window}` — *over what unit* the detector runs (extensible to `pr`
  with git, `all_time` later). Every metric is reported at **both** scopes, each with its own
  rate AND baseline placement.

**The cross-session signals are just `metric @ window`:** `rereads @ window` is the
**re-establishment tax** (a file rediscovered across N sessions, never edited — "pin it in
CLAUDE.md / memory / a skill", a remedy [scoring-rubric.md](../docs/scoring-rubric.md) already
names); `retouched @ window` is cross-session rework. Cheap — needs only `File.id` + session
count, **no git/line-lineage** — so it is Phase 1. **These are headline signals for the
cross-session visualizer** (a file with a fat recurring bus across the session stack).

**One rule per metric; scope is only the memory length.** There is *no* per-scope rule — e.g.
rereads is always "read tokens covering content already read, no edit since"; `session` resets
that memory each session, `window` keeps it across the whole window, so cross-session re-reads
fall out for free (no second rule). A wider scope simply *sees more* (the cross-session repeats
a session forgets), so a window rate is **not** the arithmetic sum of session rates and isn't
directly comparable — hence each `(metric, scope)` keeps **its own baseline**. The only
non-rule constraint: memory accumulates along the **active path** (`active_stream`), so
abandoned rewind branches never manufacture phantom re-reads.

**Benchmarkable token-RATES, not verdicts** (the key reframe): each metric reports
`wasted tokens / total tokens of that kind` (e.g. rewrite tokens / authored tokens), and
`metrics.baseline.position()` places it against a population distribution — same
placement-against-reference idea as difficulty/cleanliness, applied to behavior. This
**dissolves the false-positive problem**: normal iteration sits in the baseline, so only an
*above-baseline* rate is notable. Granularity fixed to match: re-reads now **range-level**;
re_touched counts only the **overlapping rewritten-line** tokens.

**Bootstrap baseline** shipped as package data (`data/metric_baselines.json`, built by
`scripts/build_metric_baselines.py`): **per-scope** distributions (`{metric: {session, window}}`),
a single-author **labeled placeholder** until the community benchmark (ADR-0005). Current
local rebuild = 6 windows / 27 sessions (a larger 81-session corpus lives on another machine;
re-run the script there to ship a stronger placeholder). Medians, **session → window**:
re_reads 0% → 15.3%, retries 0% → 0%, re_touched 2.8% → 8.5%, unused 52% → 52%.

The session→window jump for re_reads (0% → 15%) and re_touched (2.8% → 8.5%) is the model
working: the *same rule* with a longer memory surfaces cross-session rediscovery and
compounding rework that per-session can't see — which is exactly why each `(metric, scope)`
keeps its own baseline (placing a window rate against a session population would read p100
when it's really ~p50). unused is flat (~52%) — reading-without-editing is *normal* at any
scope; flagging it as waste would be a huge false positive.

Remaining v1 refinements (noted, not silent): `resident_cost` for unused_context;
larger/real baseline via the benchmark; cross-session line lineage (Phase 4).

**Built for `haid metrics`** (✅ 2026-06-07): the **`session` and `window` scope** of every
metric with per-scope rates + baseline placement (incl. the `rereads @ window` / `retries @
window` cross-session views), and **per-scope baselines** in `metric_baselines.json`
(`{metric: {session, window}}`, rebuilt by `scripts/build_metric_baselines.py`). A `(metric,
scope)` with no population sample reports `baseline: null` (flagged in `caps.baseline.missing`),
never faked. Remaining: a larger/real baseline via the benchmark, and the waste→value nTok
reconciliation (deferred — see metrics-output-schema.md caps).

### Step 5 — Metrics output (`haid metrics`) ✅ BUILT 2026-06-07
`src/haid/metrics/json_out.py` (the JSON contract) + `view.py` (Markdown, rendered from the
same dict) + the `haid metrics` CLI; validated on real sessions (82 tests at the time; the
full suite is now **163** with the scoring stack + the bridge).
The measured **substrate**, not the final report (see Naming, top). Pure measurement —
**no remedy/"this suggests…" lines**; inferring *why* and *what to fix* is the job of the
Phase 2/3 subagent passes this output feeds.
- **Objective measurement only**, plainly stated: "`auth.ts` read 4× with no edit between,
  ~6k tokens," each with its **baseline placement** ("p82, above normal") and traceable ids.
  **Aggregate window + per-session sections** (per-session is where a later pass correlates
  outcome with process).
- **Ranked, not dumped:** the volume metrics are correct-but-noisy, so rank instances by
  token weight and lead the headline with above-baseline metrics (a below-baseline metric
  reads as *not* a problem).
- **No silent caps footer:** malformed/unknown records, un-stitched subagents, top-N
  truncation, deduped resume overlaps, the single-author baseline caveat, and the
  within-session-only line-lineage limit — all listed.
- **Markdown** = the maintainer's eyeball/DoD-validation view. **JSON** = the
  machine-readable hand-off to the Phase 2/3 subagent passes (the flagged files / regions /
  timelines to investigate). The JSON is the point, not a nice-to-have. **The JSON contract
  is spec'd in [metrics-output-schema.md](../docs/metrics-output-schema.md)** (versioned;
  pointers-not-dumps; dual granularity; refs carry `tool_use_id`+`turn_id`+`file_id`+span).

### Step 6 — Validate against Definition of Done
Run on the maintainer's own sessions end to end. **Manually confirm flagged waste is
recognizably real (low false-positive rate). If the metrics cry wolf, fix that before
declaring Phase 1 done** — a tool that misdiagnoses is worse than nothing.

## Decisions to settle in situ (resolve against real data, not in the abstract)

These are the still-open questions from [open-questions.md](open-questions.md) that
land inside the build:

- **V6 Bash-failure rule** (Step 3/5) — ✅ RESOLVED: a call failed iff its `tool_result`
  block has `is_error: true` (uniform across tools incl. Bash; no stderr/narration
  heuristic needed). Implemented in `graph/build.py` (`status`).
- **Region-identity granularity** (Step 3/5) — sidestepped for v1: re_touched is
  **content-based** (matches the agent's own produced lines), so it doesn't depend on
  region identity. The symbol-vs-window question remains open for richer region work later.
- **V4 resumed-session dedup + branching** (Step 1) — RESOLVED empirically (§0.5):
  dedup by `uuid`; parse as a forest; scope metrics within a timeline. Remaining
  in-situ check: the `leafUuid` timestamp-fallback and structural-vs-rewind classifier
  on more real branched sessions (only 4 in the local corpus).
- **Attachment modeling** (Step 1/2) — first-class nodes vs folded into the turn that
  carries them. Resolve when it first matters for a metric.

## Test fixtures (also closes a Phase-0 checkbox)
- **Branched-corpus source:** the 55 boxBot transcripts at
  `~/.claude/projects/-home-jhart-software-boxBot` (WSL) — 11 with rewinds, 10 with a
  dangling `leafUuid`. Use these to validate the forest model, the leafUuid fallback,
  and the command-noise filter. Specific cases worth pinning: `1d4c7019` (sibling-fork
  rewind + a `/login` command-noise chain), and any of the 10 dangling-leaf files.
- A small **anonymized** real session -> `tests/fixtures/` for parser tests.
- A session **with subagents** (V3) and, if available, a **compacted** one (V1).
- Deterministic graph/metric tests run model-free, like the existing scoring tests.

## Definition of done
- Runs over the maintainer's own real sessions (single and multi) end to end.
- Flagged waste is **recognizably real** on manual review (low false-positive rate).
- The report never hides a cap, a skip, or the within-session line-lineage limit.
