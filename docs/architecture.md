# Architecture

HAID is a pipeline that turns raw Claude Code transcripts into a graph, runs two
orthogonal analysis passes over that graph, and renders a hedged report.

> **Status (2026-06-07):** the deterministic spine of this pipeline is built and
> tested — **parse → graph → signature-scanning metrics**, computed over an **analysis
> window** (a project's sessions over a timeframe; the MVP stand-in for the episode unit
> described below, which lands in Phase 2). **`haid metrics`** — the pure-measurement
> **substrate** of pass (3b): metrics + baseline placement + traceable instances — is now built
> (inspection view + JSON hand-off). Also built: the placement scorer (difficulty + cleanliness),
> the volume/cost/value combiner, and the transcript→diff **bridge** (`src/haid/bridge/`), so the
> full scoring stack now runs on real sessions. **Phase 2 has begun (2026-06-08):** the
> user-anchored pass's first step — the per-message **classifier** (`src/haid/intent/` + `haid
> tag`: move × work-type + purpose snapshot, the manifest/backend pattern, walking all branches) —
> is built. Next is **episode segmentation** (step 3) and the diagnosis router. Note the
> `(4) REPORT` node below is the *final product*
> that **composes both passes** (and the value score) with hedged interpretation — it is not
> the Phase-1 step; the user-anchored pass (3a), episodes, and that composed report are later.
> See [roadmap.md](../plans/roadmap.md) and [phase1-build.md](../plans/archive/phase1-build.md).

```
  ~/.claude/projects/<proj>/*.jsonl ──┐
  + subagents/*.jsonl                  │   (1) PARSE
  + tool-results/*.txt                 ├──────────────► typed records
  + git history (optional)             │
                                       │
  typed records ─────────────────────────► (2) BUILD GRAPH ──► session graph
                                                                (nodes + edges)
                                                                     │
                       ┌─────────────────────────────────────────────┤
                       │                                             │
              (3a) USER-ANCHORED PASS                    (3b) SIGNATURE-SCANNING PASS
              (catches misalignment)                     (catches silent inefficiency)
                       │                                             │
                       └──────────────────► (4) REPORT ◄────────────┘
                                          objective metrics first,
                                          hedged interpretation on top
```

## The components

### 1. Parse

Read the JSONL transcripts (and stitch in subagent transcripts and overflow
tool-result files) into typed records. The on-disk format is documented and
**verified against real files** in
[claude-code-data-format.md](claude-code-data-format.md). Key correctness
concerns: tool results are carried on `user` records (not a top-level
`tool_result` type in current versions), large outputs overflow to
`tool-results/*.txt`, and subagents live in separate files that must be stitched.

Borrow the schema-drift-validator idea from the Rust `claude-code-transcripts`
crate: the format drifts across Claude Code versions, so the parser should
validate and loudly flag unknown record shapes rather than silently dropping
them.

### 2. Build the graph

One graph is the spine of the whole tool. Tool-call is the primary analysis
grain. Nodes: session, turn, tool-call, file, region (line-span), instruction,
episode. Edges: responds-to, reads, produces/edits, triggers, retries, re-reads,
churns-with, derives-from. Full taxonomy and the two core operations ("why did
you do X?" and "where did the tokens go?") are in
[session-graph-design.md](session-graph-design.md).

### 3. Two orthogonal passes

The hard problem: **task difficulty is genuinely hard to judge, so "waste" can't
be inferred from cost alone.** The design splits the work into two independent
passes that catch different failure classes.

#### 3a. User-anchored pass — catches *misalignment*

Work backwards from the end result, anchored on user messages. The key insight:
**corrections are ground truth.** Every "no, I meant…", "that's wrong", "stop, go
back" is a human-labeled failure point you can *read* rather than infer. So the
classifier's high-value job is not a sprawling ontology but reliably catching a
few high-signal message types — instruction, correction, clarification, re-prompt
(the user had to say it twice).

This yields the natural unit of analysis: the **task episode** — the span from an
instruction to its resolution (the next instruction, or a correction). Episodes
give a denominator for "what did this ask cost" and keep backwards traces
tractable by scoping them within an episode.

#### 3b. Signature-scanning pass — catches *silent inefficiency*

User-anchoring misses the most expensive failures that produce no correction: the
agent quietly burns 40k tokens spelunking, then succeeds, so nobody complains. A
second independent pass scans for objective, reasoning-free waste signatures:

- Redundant re-reads of the same files.
- Retry loops (the same failing command/test run repeatedly before the approach
  changes).
- Context spent on files that are never used.
- Re-touching the same lines — a strong signal a mistake was made and reworked.

The two passes are orthogonal and you want both: **one finds where the agent did
the wrong thing, the other finds where it did the right thing wastefully.**

### 4. Report

The **final product** — it composes both passes (and the value score) and is where the
**clearly-hedged** interpretation lives, never masquerading as fact. See
[trust-discipline.md](trust-discipline.md). It is distinct from the Phase-1 **`haid metrics`**
substrate (pass 3b), which is *pure measurement* — metric + baseline placement + traceable
instances, no remedy/"this suggests…" lines. The why-and-fix interpretation enters here,
fed by the metrics substrate plus the user-anchored pass (3a, Phase 2) and error
attribution (Phase 3). "report" names this final thing (the Phase-5 `/haid:report`), not the
measurement step.

## Working backwards from end state

For a code-review window, start at the artifact: diff from the start of the
window to the end. Score the result on complexity, elegance, cleanliness, etc.;
attribute changes back to the sessions that produced them; trace backwards from
there.

> **That window diff is now built — and it's reconstructed from the transcript, not git
> (`src/haid/bridge/`, 2026-06-07).** The decision to go replay-only was made after measuring
> the bash-write-to-source gap at ~0–1% on real projects (git's marginal coverage didn't justify
> its complexity + attribution noise; it would bundle non-agent edits). Heredoc writes are
> recovered; the rare unrecoverable shell write is flagged, not dropped. Git stays an optional
> blame/verifier layer (Phase 4), never the diff source. See the Phase-5 Bridge note in the
> [roadmap](../plans/roadmap.md).

For most turns and steps the analysis stays cheap — token counts and file reads
only. The tool spends its expensive, deeper (LLM) attention **selectively**: when
a *mistake* is detected, it digs in to attribute the source — which session
introduced the problem, and why.

A caveat the design takes seriously: snapshot quality scores (elegance,
cleanliness) are a **hedged top layer, not the spine.** They are the same
LLM-confabulation risk as any inferred judgment, and — as the canonical example
shows — they structurally cannot see *relational* failures, where code is bad
only relative to what it should have been.

## Cheap-by-default, expensive-on-signal

A core efficiency principle: the bulk of the analysis is deterministic graph
queries over token counts and file I/O (cheap, reasoning-free, trustworthy). LLM
judgment is reserved for the small set of places a deterministic signal has
already flagged something — attributing a detected mistake to its source,
classifying a borderline message, summarizing a confirmed pattern. This keeps
both cost and confabulation risk low.

## Data sources and their division of labor

| Source | Good for | Blind to |
|--------|----------|----------|
| **Tool-call stream** (in-session JSONL) | the *why* inside a session: edits with adjacent reasoning, token attribution, exact diffs (`structuredPatch` + `originalFile`), `userModified` flagging tool-channel hand-edits, and **transcribed Bash file IO** parsed into reads/writes (`bash_read.py`/`bash_write.py` — incl. an agent's own `sed -i`/`>`, detected but content not recovered) | changes made *outside* a transcribed tool call (formatters-on-save, build steps, a `sed -i` the user runs in their own shell) |
| **Git** (across sessions) | the *what* across sessions: ground-truth on-disk state (incl. the formatter/`sed`/build changes above), stable cross-session anchors (SHAs, blame) | the reasoning; only sees committed state |
| **Compaction boundaries** | detecting "agent acted on a compacted/lost instruction" by diffing the `isCompactSummary` summary vs the retained pre-compaction turns; magnitude from `compactMetadata.pre/postTokens` | — (pre-compaction turns are retained on disk; see data-format doc) |

Tool stream and git are **complementary**: tool stream for the why inside a
session, git for the what across them. Details and known gaps in
[claude-code-data-format.md](claude-code-data-format.md).
