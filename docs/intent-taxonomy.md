# Intent taxonomy & the purpose timeline

> **Status: BUILT (2026-06-08) — `src/haid/intent/` + `haid tag`.** The per-message classifier
> is implemented as deterministic orchestration around a single model call, behind the same
> manifest/backend boundary as the scorer: `ReplayBackend` (saved labels, no model — CI) and
> `HarnessBackend` (the live host-agent path; a dynamic workflow is an *optional* runner, never a
> hard dependency). Three things changed from the original sketch below and are now the source of
> truth — the rest of this doc still describes the intent:
> 1. **It walks EVERY branch, not just the active path.** A rewound stretch of work (do step A →
>    rewind → do step B) is real work that cost tokens, so it gets labels and an episode. Messages
>    are deduped by `uuid` (the shared planning prefix is one message) and each message's context
>    is built from *its own branch* (a step-B message must not see step-A context it never had).
>    The waste metrics still scope **within** one timeline — different consumer, different rule:
>    metrics compute redundancy (a cross-branch repeat would be a phantom re-read), the classifier
>    only labels.
> 2. **No deterministic priors.** A lexical/graph "priors seed the classifier" layer was built,
>    then dropped on review — see [Deterministic priors](#deterministic-priors-were-dropped) below.
> 3. **Output is `{move, work_type, purpose}`.** A `reason` audit field was dropped; `purpose` is
>    the load-bearing timeline entry. Category wording (esp. correction vs refinement) is still a
>    hypothesis until the live pass validates it on real labels.

This is the heart of the **user-anchored pass**. We classify each user message and
emit a one-sentence purpose snapshot. The sequence of snapshots becomes the
session's comprehension scaffold and the basis for drift detection — without ever
trying to read the model's cache/attention.

## Why classify at all

You can't fit a multi-hundred-thousand-token session into any agent's head. So we
don't. We do a **cheap per-message pass** that produces a compact, structured
timeline; the expensive analysis (step 3) then reads that small timeline and
spends its attention only on the hotspots it points to. Classification is the
entry point to that triage.

**Method: judge each message by what precedes it.** A message's move is its relationship
to the turn just before it, so corrections — ground truth for misalignment — are read
against the prior agent action, not in hindsight.

**Orchestration (R1, 2026-06-24): one agent per session branch.** The cheap pass batches by
*branch*: one haiku agent reads a whole branch transcript once and labels every user message
on it in order, instead of one agent per message each re-embedding its own context. This is
what makes the pass cheap and the manifest relayable — per-message jobs re-embedded
overlapping context and grew quadratically (an ~800KB manifest too large to fan out); the
per-branch transcript is shown once, so cost is linear and the agent count is ~one per
session. The branch walk and uuid dedup are unchanged (every rewound branch still gets its
own job; a shared prefix is labeled once, shown as context elsewhere), so abandoned work is
still labeled and never bleeds across branches. **Trade-off, stated honestly:** a one-shot
branch agent *can* see messages after *n* when labeling *n*, so causality is preserved by
**instruction** ("no hindsight"), not by construction as the old per-message bounded context
enforced it. Whether that lookahead measurably shifts labels (esp. correction-vs-refinement)
is the open validation question this orchestration trades for the cost win.

## Two orthogonal axes (do not collapse them)

A single message is a *pair*, e.g. **(Correction × Implementation)** = "no, use
middleware not a decorator" — which carries far more signal than any one bucket.
Critically, **Correction lives on its own axis** so it is never filed next to
"question."

### Axis A — conversational move (relationship to the prior turn)
| Move | Meaning | Why it matters |
|------|---------|----------------|
| **New directive** | opens a new task/thread | episode + thread boundary |
| **Correction** | the agent did the wrong/unwanted thing; redo | **ground truth for misalignment** |
| **Re-prompt** | same ask restated because it didn't land | weaker correction (user had to repeat) |
| **Refinement** | "also…", "now add…" — builds on work that was *fine* | explicitly **not** a correction; same thread |
| **Approval / no-op** | "yes go ahead", "looks good", "thanks" | coverage; keeps these out of the work buckets |

### Axis B — work type (what is being asked)
| Type | Meaning |
|------|---------|
| **Question** | info only, no artifact expected |
| **Planning/design** | produce a decision/plan, not code |
| **Implementation** | produce/change artifacts (absorbs generic "request" and "bug fix"; optional sub-tags: feature / bugfix / refactor / chore) |
| **Investigation/debug** | find out *why*; may or may not end in a fix |
| **Meta/ops** | about the session itself (run, commit, configure), not the codebase |

### Deterministic priors were dropped
The original plan was to seed the classifier with Tier-1/2 facts (a message right after the
agent stops re-targeting a just-edited file ⇒ Correction; lexical "no"/"revert"/"actually"
⇒ Correction). **This was built, validated on real data, then dropped** (2026-06-08, after a
design review). The reasoning:
- **Lexical and re-prompt priors are redundant with the model** — it reads the same text, so
  regexing "no, that's wrong" tells it nothing it can't already see.
- **The one out-of-band signal is the graph "immediate re-edit"** (the agent's first action
  after a message re-touches code it just wrote — invisible to the text-only context). But it
  is *weak* evidence for the **move** axis (intent lives in the user's words) and *strong*
  evidence for **error attribution**. So it moves to **Phase 3** (recurrence / blame), where
  behavioral re-edit chains are the actual signal, rather than being bolted onto step 2 with a
  regex apparatus and an override metric.

Validation that drove this: on a real 30-day HAID window the priors fired on ~45% of messages
even after tightening — too noisy to *focus* attention, and adding nothing the model lacked.
The classifier is therefore pure LLM judgment over the bounded context, with no heuristic seed.

## The purpose snapshot

Alongside the two axis labels, each user message gets **one sentence: the current
objective as of this message** — e.g. *"Building the Aubaine wine-club member-status
query."* It's the *declared* purpose, derived from what the user actually asked
(trustworthy anchor). Agent turns inherit the active purpose; they don't get their
own snapshot (keeps it cheap and bounded — even big sessions have only
tens-to-low-hundreds of user messages).

## The purpose timeline → threads & drift (at wrap-up)

The snapshots form a timeline that totals maybe a few hundred words for a whole
session — small enough that the **wrap-up agent reads the entire list at once** and
narrates the threads holistically:

> "You ran three threads: auth (msgs 1–9), billing (10–22), then a tangent bug
> (23–25)."

**Read the whole timeline holistically — do not do pairwise similarity.** Embedding
thresholds false-positive on rewording and false-negative on slow drift; reading
the compact list is cheaper and more robust.

### Drift vs. legitimate decomposition
The move axis disambiguates, so we don't cry "drift!" on healthy work:
- **Refinement** move + purpose that is a *sub-goal* of the prior ⇒ same thread
  (auth → tests-for-auth → docs-for-auth is decomposition, not drift).
- **New-directive** move + purpose *lateral* to the prior ⇒ drift candidate.

### Closing the loop with cost
Drift detection from the timeline tells us **where** the thread changed; the
deterministic token data tells us **what it cost** (carried/resident tokens across
the boundary, and tokens-per-thread once threads are identified). So we report a
**cost-quantified, hedged pattern, never a "wasted cache" verdict** — see
[detectors.md → Purpose drift](detectors.md). We never claim a cached file was
unused (we can't see activations); we report carried cost + observable evidence and
let the user judge.

## Bonus: this feeds episode grouping (but does not split sessions)
The per-message tags roll up to **each session's purpose/topic summary** and a **within-session
drift signal** — and those feed episode formation. **Episodes are groups of whole SESSIONS on a
shared component/topic; the session is atomic and is never subdivided** (grain decision
2026-06-08 — one session is one context window, the only clean cost boundary; see
[../plans/agent-analysis.md §1](../plans/agent-analysis.md)). So a purpose change *within* a
session is reported as **drift coaching**, not an episode boundary. The two axes and the purpose
timeline are still the same L2 artifact (see
[session-graph-design.md](session-graph-design.md#episodes)). *Build note:* the first cut of
`src/haid/episodes/` segmented this message timeline directly; it is being **reworked to session
grain** — this message classifier (`src/haid/intent/`) is unchanged.

This doc owns the **per-message classifier + purpose timeline** (the *inputs*). How those are
**assembled into episodes** (the git-free PR proxy — topic-first grouping, boundary rules,
spanning sessions, and episodes as the difficulty-scoring grain) lives in
[../plans/agent-analysis.md §1](../plans/agent-analysis.md). Key carry-over: **corrections,
re-prompts, and refinements are NOT episode boundaries** — they are iteration inside the unit;
only a new *lateral* directive starts a new episode.

## Open refinements (for a focused session) ⟳
- Final wording of each category; resolve any residual overlap (correction vs refinement is
  the one to stress-test) — validate on the **live** pass against real labeled messages.
- The classifier prompt + few-shot examples (none yet). Context budget is **as-built** but
  tunable: each agent reply is head+tail-truncated to 400 chars and the skeleton keeps the last
  40 turns ([messages.py](../src/haid/intent/messages.py) `_AGENT_REPLY_CAP` / `_SKELETON_ENTRIES`).
- Whether purpose snapshots carry an explicit thread-id / hierarchy, or threads are
  inferred at wrap-up only.
- Multi-instruction user messages (one turn, several asks) — segment into multiple
  Instruction nodes?
