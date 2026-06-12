# Agent-analysis phase — the "why" pass that feeds the report

> **Status: design (2026-06-07); steps 2 + 3 BUILT at session grain (2026-06-08).** Consolidates the design for the
> model-in-the-loop analysis that turns the deterministic **metrics substrate**
> ([metrics-output-schema.md](../docs/metrics-output-schema.md)) into the user-facing **report**.
> Spans roadmap **Phase 2** (episodes + the user-anchored pass) and **Phase 3** (error
> attribution); both feed the Phase-5 `/haid:report` compositor. Validated against real sessions
> during Phase-1 DoD (boxBot; see the worked case below). **The per-message classifier (step 2)
> is built** — `src/haid/intent/` + `haid tag`, manifest/backend pattern, walks all branches, no
> deterministic priors (see [intent-taxonomy.md](../docs/intent-taxonomy.md)). **Episode
> segmentation (step 3) is BUILT at SESSION grain** — `src/haid/episodes/` + `haid episodes`:
> roll each session up to a `SessionSummary` (purposes + touched-file set), then one holistic
> grouping pass clusters whole sessions into episodes by shared component/topic (the **session is
> atomic, never subdivided**; grain decision 2026-06-08, see §1). Manifest/backend pattern
> (Heuristic baseline / Replay / Harness); grouping validated to partition the sessions. Step 4
> (episode-scope metrics + achievement) follows and is simpler — cost attributes cleanly because
> we never cut below a session.

## Runtime pipeline — what we're building toward

This is the **run order** of the final `/haid:report` product. It is **not** the build order:
the roadmap builds the cheap deterministic core first to de-risk trust; at runtime the passes
run in dependency order. The two axes are orthogonal — adding episode scope and this run order
does **not** reorder the roadmap phases.

```
0. parse → graph                      deterministic foundation (built)
1. metrics: session + window scope    deterministic, cheap, STANDALONE (built; DoD-validated)
2. tag user messages                  model — move×work-type + purpose snapshot (BUILT: src/haid/intent + `haid tag`)
3. segment episodes                   model — group whole SESSIONS by component/topic (§1) (BUILT: src/haid/episodes + `haid episodes`)
4. episode-scope metrics + achievement  BUILT: metrics.run_episodes + bridge.episode_inputs +
                                        episodes/score.py + `haid score` → WindowDistribution (§5)
5. why-pass / anchor analysis         model — gated on anchors from steps 1 AND 4 (§2–§4, "The shape")
6. visualization + reports/charts     compose session / episode / window views + scores + why-notes
```

**Scope availability mirrors this.** `session` + `window` are computable at **step 1**
(deterministic, no model) — so a "no-model fast path" still yields them, and they're what Phase-1
DoD validated. **`episode` scope only exists from step 4** (after segmentation) — it is an
*enrichment*, never a dependency of the cheap core. Step 5's anchors come from *both* the
deterministic metrics (1) and the episode metrics + achievement (4): a high-rework **episode**,
a re-establishment-tax **file** (window), a low-value/high-difficulty **episode**.

## The shape (detail of step 5)

The metrics layer answers **where / how much** (deterministic, cheap, trustworthy). This phase
answers **why / what to do** (model-in-the-loop, selective, hedged). It is **anchor-driven and
two-stage**:

```
  metrics JSON anchors ──► per-anchor ANALYSIS agents ──► brief notes + flags ──► REPORT compositor
  (rework / reread /        (read transcript via the      (evidence-grounded,      (contextualizes with
   thrash / drift)           pointers; correlate           non-exclusive flags;     the metrics + the
                             corrections; search project)   no rigid verdict)        difficulty distribution)
```

The expensive attention goes **only** where a deterministic signal already flagged something
(cheap-by-default, expensive-on-signal). Corrections are ground truth throughout.

## 1. Episodes — the git-free PR proxy

**An episode = a coherent unit of work spanning one or more whole SESSIONS on a shared component
or topic.** It is the join point of the whole tool: the unit the why-pass investigates **and**
the unit scored for difficulty/cleanliness (§5). It mirrors a PR **without depending on actual
PRs** (many projects have no/giant/messy PRs; git is Phase 4 anyway).

**The session is atomic — an episode never subdivides one (grain decided 2026-06-08,
user-driven).** A session is one continuous context window, so it is the *only* boundary at which
token cost attributes cleanly: split a session that fixed two unrelated bugs and the shared
cache/resident context makes per-fragment cost a brittle guess — a confident wrong number, which
the project forbids. The **95/5** concern §5 raises is about a **project's** scaffolding over
weeks, resolved by grouping sessions into episodes; it never required going below a session. So
the hierarchy is **session ⊆ episode ⊆ window**, and a session belongs to exactly one episode.

**Detection — roll up per message, then group whole sessions:**
1. **Per-message tagging** (cheap) — move × work-type + a one-sentence purpose snapshot
   ([intent-taxonomy.md](../docs/intent-taxonomy.md)). **BUILT** (`src/haid/intent`, `haid tag`):
   pure LLM judgment over a bounded per-branch context, no deterministic priors.
2. **Per-session rollup + drift signal** — the message tags roll up to each session's
   **purpose/topic summary**. Within-session topic drift ("you context-switched mid-session") is
   a *coaching signal*, **not** an episode boundary — the session stays whole.
3. **One grouping pass** clusters the chronologically-ordered sessions into episodes by **shared
   component/topic**, from two deterministic cues: **file-set overlap** (sessions touching the
   same files/areas belong together — and repeated cross-session re-reads of the same context are
   themselves a key signal, the re-establishment tax) and **topic continuity** of the per-session
   summaries; an idle gap between sessions is weak corroborating evidence.

**Rules that make it PR-like without git:**
- **Component/topic is the spine.** Group by *what's worked on* across sessions.
- **An episode spans ≥1 *whole* sessions; a session is never split.** episode ≠ on-disk PR.
- **Cross-session repeated re-reads are a key signal** (the re-establishment tax) *and* a
  positive grouping cue.
- **Grouping is auditable** — each trace to a signal (shared files, topic continuity, idle gap);
  inspectable, not an opaque LLM guess.
- **Git reconciles, never gates** — when present (Phase 4), commits/PRs confirm the grouping and
  attach ground-truth diffs; episodes work without it.

**Worked case (boxBot, May 13–14):** the memory-staleness bug work and the conversation/trigger
redesign happened in *one continuous session* with an overnight gap and a topic pivot. That pivot
is reported as a **within-session drift signal**; the session remains **one atomic unit**.
Episodes form by grouping that session with other sessions that worked the same components.

> **Build status (2026-06-08):** `src/haid/episodes/` is built at **session grain** —
> `summarize.py` rolls each session up to a `SessionSummary` (purposes + touched-file set + a
> within-session drift proxy); `grouping.py` codifies the cluster prompt + schema; `segment.py`
> has the Heuristic baseline (runs of sessions linked by file overlap), Replay, and Harness
> backends; `__init__.segment_window` validates the grouping **partitions the sessions** and
> emits `Episode` nodes (each holding `session_ids`); `iter_episodes` slices back to `Session`
> objects for Step 4. `src/haid/intent/` is unchanged — it feeds the per-session purpose
> fingerprint. (An earlier message-grain cut was discarded in this rework.)

## 2. The anchor-driven why-pass

Each metrics-JSON anchor (a `retouched`/`rereads` instance, a thrash session, later a drift
point) seeds a per-anchor **analysis agent**. It does NOT re-detect; it explains. It needs
**three inputs** (richer than "read the timeline"):
- the **transcript**, located via the anchor's `refs` (file_id, calls[].tool_use_id/turn_id,
  session_id, span);
- the **project working tree** — to find missed context ("the agent guessed and missed
  `doc_5` describing xyz"), extending behavioral-contradiction from "read a doc then did the
  opposite" to "**didn't** read a doc that existed";
- **cross-session search** over the window — for recurrence (§4).

**Classifier context discipline:** to label/understand a message, feed **prior user messages +
the agent's *final text responses*** — **not** thinking blocks or tool calls. Cheaper and
sufficient; keeps the pass bounded and trustworthy.

## 3. Per-anchor output — a brief note + non-exclusive flags (NOT a rigid verdict)

A fixed verdict enum was rejected: the categories overlap (a file can be *both* an
architectural hotspot *and* earned iteration), and forcing one label miscategorizes exactly the
nuanced cases that matter. Instead each anchor yields:
- a **brief, evidence-grounded note** (what happened, with citations into the transcript), and
- a set of **non-exclusive observable flags** — facts, not verdicts:
  `correction_preceded` · `recurred_across_sessions` · `co_churns_with_tests` ·
  `central_file_many_sessions` · `high_difficulty_episode` · `no_user_trigger` (self-thrash).

The **report compositor** (§6) weighs the flags + note to contextualize — e.g. "high rework,
but core/experimental and high-difficulty → earned iteration, consider earlier planning" vs.
"high rework, low-difficulty, no user trigger → self-thrash."

## 4. Cross-session recurrence detection

The highest-value error-attribution signal (Phase 3) is **cross-session**: a symptom fixed in
Sₙ and **re-reported by the user in Sₘ** — a fix that didn't hold. The within-session
Correction/Re-prompt axes miss this. So when an analysis agent tags a bugfix, it **searches the
window backward** for a prior fix of the same symptom and asks *why it didn't hold / what was
missed*. Exemplar: boxBot's calendar-down memory (fixed 5/13, recurred the morning of 5/14) —
**CORRECTION (2026-06-09, live why-pass investigation):** the recurrence did NOT hold up. The
5/12 OAuth production-mode fix held; the 5/14 failures were different defects (a dropped-recipient
bug and a sticky mute). Kept as the exemplar of the *mechanism* — note that the investigation
agent correctly REFUTED the seeded recurrence, which is the trust discipline working.
See [detectors.md → Recurrence](../docs/detectors.md).

## 5. Episode-grain scoring — credit earned rework, don't bury the 5%

The difficulty/cleanliness ladders were built at **coherent-change grain** (the anchors U37…U50
are PR-sized changes), and difficulty is **orthogonal to LOC** by design (ρ vs size = −0.05; see
[difficulty-ladder.md](../docs/difficulty-ladder.md)). Therefore:
- **Score at the episode grain, not per-file and not whole-window-blended.** A file isn't a unit
  of work (no ladder analogue). A blended 30-day diff hits the **95/5 problem** — 95% scaffolding
  drags the placement down and **buries the critical 5%**.
- **The window is a *distribution* of episode placements**, not one number. Scaffolding episodes
  correctly place T0–T1; the critical 5% place T3–T4. Because difficulty ⊥ LOC, the small hard
  episode isn't penalized for size nor the big easy one rewarded.
- **This credits earned rework** (R3): rework on a high-difficulty *episode* (a group of sessions
  redesigning a hard component) is rewarded in achievement, not just flagged as waste. Rework ×
  **low**-difficulty is the real smell; rework × **high**-difficulty is earned.
- **Dependency:** this needs episodes (§1) **and** an *episode-scoped* diff. Because an episode is
  a set of **whole sessions** (grain decision 2026-06-08), this is **not** a new slicing engine —
  it is the existing bridge run over the episode's **session subset**: `bridge.window_inputs`
  already takes a session list, so `episode_inputs` is the same call on those sessions. The bridge
  is **built (2026-06-07, `src/haid/bridge/`) and replay-only — NO git** (the bash-write-to-source
  gap was ~0–1%; episode↔PR(git) alignment is TBD). **Cost likewise attributes cleanly**: an
  episode's cost = the sum of its sessions' costs, each a self-contained context window — no
  entangled sub-session token split.
- **Open:** `cleanliness` may want the *final-artifact* grain (end-state quality) rather than
  per-episode — decide separately (see [scoring-rubric.md](../docs/scoring-rubric.md)).

## 6. The report compositor

The final stage contextualizes the analysis notes + flags with the metrics and the difficulty
distribution into the user-facing report. It is **separate from the metrics substrate** (which
stays pure measurement). Both this and the per-anchor agents are **prompt-tuned** — the prompts
are the product:
- analysis agent: "this is a heavily-iterated core file — read the episode, check for a
  preceding correction and for cross-session recurrence, search the project for missed context."
- compositor: weave the notes into a hedged narrative, credit earned high-difficulty work, lead
  with the highest-value/highest-leverage findings, attach concrete remedies.

**Remedies (validated, pattern-specific):** re-establishment tax → a CLAUDE.md line / skill
("use deploy like (this); commit before deploy" — a real boxBot callout); recurring defect → fix
at the right layer; hotspot → consider splitting; self-thrash → a planning doc; misalignment →
upfront plan/clarify.

## 7. Trust discipline (non-negotiable)
- **Don't cry wolf on the hardest work.** Earned iteration on experimental/high-difficulty code
  must be returnable as *not waste*, with evidence — that's the session that did the hardest work.
- **Corrections are ground truth**; anchor causal claims on them, hedge where absent.
- **Distinguish waste from legitimate** — iteration, exploration, and architectural centrality
  aren't waste. Every claim cites transcript/project evidence ([trust-discipline.md](../docs/trust-discipline.md)).
- **Weight by absolute tokens, not just rate** — a tiny-denominator session shouldn't dominate;
  the why-pass focuses on the whole body of work.

## Open questions
- **Session-grouping quality** on real windows: how aggressively to merge sessions into one
  episode (file-overlap threshold; topic-continuity judgment; how much an idle gap between
  sessions should weigh). The grouping pass is unbuilt at session grain — once reworked, its live
  output needs an eyeball pass on a real window, same validation debt as the classifier.
- **Single-session episodes** are fine and common (a self-contained PR in one sitting); the
  grouping pass must not force-merge unrelated sessions just to make bigger episodes.
- **Within-session drift** reporting: a session that pivots topics mid-stream stays one atomic
  unit but earns a drift *signal* — how loudly to surface it, and whether it ever argues the
  session was mis-scoped (a coaching note, never a re-split).
- Threads vs. episodes: a *thread* (topic/epic) may contain several episodes — keep threads as
  an optional coarser holistic read, or drop them.
- Cleanliness grain (per-episode vs final-artifact), per §5.
- How the compositor renders the episode difficulty distribution without overwhelming the user.
