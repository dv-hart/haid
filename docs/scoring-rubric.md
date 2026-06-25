# Scoring rubric — achievement vs. cost

> **Status: calibration validated for the difficulty axis (2026-06-04).** The
> approach below was **substantially revised by the calibration pilots** — the
> achievement score is now **RELATIVE, not absolute**, with **no senior-engineer-hours
> grounding** and **no uncertainty bands** (all three proved unreliable; the pilot and
> full experiment log are archived with the calibration harness on the
> `archive/experiments` branch). The validated mechanism
> and how to extend it to the cleanliness axis:
> **[difficulty-ladder.md](difficulty-ladder.md)** + **[axis-calibration-playbook.md](axis-calibration-playbook.md)**.
> **Originality was dropped as a scoring axis (2026-06-05)** — see the Axis decision note below.
> This doc is the high-level rubric; those are the ground truth. Read
> [trust-discipline.md](trust-discipline.md) alongside it.

## The core equation

```
value  =  achievement  ÷  cost
```

- **Achievement** is a property of the **final artifact** (the window diff), judged
  as if a human handed it to you cold. It has *nothing* to do with how many tokens
  it took. This decoupling is the whole design.
- **Cost** is the transcript: tokens × model tier, time, turns, compaction.

A flawless session can still be low-value: lots of planning + a request producing a
small amount of unoriginal code at huge token cost is a *bad ratio*, and the tool
should say so.

## Relative, anchored to a reference corpus (revised)

> **Revised.** The original aim was an *absolute* "how much was achieved" in
> senior-engineer-hours. The pilots showed absolute SEH grounding is unreliable (there
> is no clean external label for code difficulty — stars measure value, review-effort
> measures process; both falsified), so we score **relatively**: a diff's position
> against a fixed **reference ladder** of real code changes. LLMs are bad at
> vacuum-estimation but reliable at **relative placement**, which is all the mechanism
> uses. The score is comparative — you vs. the reference corpus, you vs. your past
> sessions, and (opt-in) you vs. the **community benchmark**, a default-off,
> self-reported public leaderboard that uploads only a signed score summary, never logs
> ([ADR-0005](decisions/0005-community-benchmark.md)). Team/cross-user comparison breaks
> the local-only default, so it is always explicit opt-in. See §Calibration.

## Achievement = f(Volume, Difficulty, Cleanliness) (report all, never collapse)

> The concrete, built combiner is **[§ Combining into achievement and value](#combining-into-achievement-and-value--built-2026-06-06)**
> below — `achievement = LOC**α · D(difficulty) · C(cleanliness)`. This section is the
> conceptual backdrop (why the axes stay separate).

The examples we care about are points on a plane; 100 hrs of boilerplate ≠ 100 hrs
of research, so we keep the axes separate and also give a combined estimate.

```
 difficulty
 (expertise) ▲
  research   │   • lock-free queue            • novel ranking algo
  /PhD       │     (low vol, high diff)
  senior     │            • text-placement library (high vol, high diff)
  mid        │   • typical CRUD service
  junior     │   • config/glue      • worksheet reimplementing quicksort by hand
             └─────────────────────────────────────────────▶ volume
                                                      (durable artifact)
```

### Volume — deterministic
- *Surviving* artifact in the final diff (not churned-away lines), weighted by kind:
  hand-written logic > config > generated/boilerplate.
- Structural counts (functions, modules, tests).
- **Halstead volume** as a grounded measure.

### Difficulty / expertise — relative placement (validated)
Difficulty is judged by **placing the diff against a reference ladder** of real code
changes (LLM pairwise placement, the validated mechanism — [difficulty-ladder.md](difficulty-ladder.md)),
**deliberately kept orthogonal to size** (the pairwise oracle decoupled from churn,
ρ≈−0.05; we measure volume separately so combining them must not double-count). The
prompt forces the judge to *ignore size and surface sophistication* and ask "what
fraction of working engineers could produce THIS correctly." (The earlier
Halstead/cyclomatic "deterministic backbone" idea is **not** what we built — it
correlates with size, which is exactly what difficulty must avoid; it could return
later only as a contamination-immune sanity floor.)

- **Cleanliness / parsimony** is its **own axis**, on the same relative-placement
  mechanism (one reference ladder, per the playbook), verified ⊥ size *and* ⊥ difficulty
  so the combined score doesn't double-count ([cleanliness-ladder.md](cleanliness-ladder.md)).
  "Reimplementing a solved problem by hand" — effortful reinvention of what a library
  provides — is a **negative coaching signal** surfaced as a targeted reinvention discount
  (no longer a dedicated axis; see the Axis decision below).

### Axis decision — originality dropped (2026-06-05)

The combined achievement is **`f(volume[LOC], difficulty, cleanliness)`** — three terms,
not four. Originality was calibrated (the originality-ladder record is archived on the
`archive/experiments` branch) and then **dropped as a co-equal axis** because it doesn't
earn its place:

- **It saturates.** The independent classification of real merged code tops out at "mid"
  originality (zero "high" anchors) — genuine novelty is vanishingly rare, so the axis is
  a flat floor across the whole range where real work lives.
- **It is the least distinct axis** (ρ=+0.68 vs difficulty, versus cleanliness's +0.03):
  most of what it measures, difficulty already captures.
- **It has no resolution where engineering happens.** Almost all SE is *recombination of
  existing tools*; originality scores masterful composition and lazy gluing identically as
  "low," so it can't grade the thing that actually varies.
- **Its one legitimate job — a *reinvention discount*** (flag difficult-but-derivative
  reimplementation that difficulty would over-credit) — overlaps with cleanliness and is
  better delivered as the coaching signal above than as a fourth score.

### Output
- A **relative position** per axis (placement on the reference ladder), **not** an
  absolute SEH number and **not** an uncertainty band.
- Optionally rendered as a coarse human-readable **tier label** (junior … expert), but
  the tier is a *rendering*, never the load-bearing score (absolute tiering leaked
  size — it's a cross-check, not the score).

## Calibration — validated approach (dense anchors + placement)

> The full validated recipe is [axis-calibration-playbook.md](axis-calibration-playbook.md);
> the worked difficulty example is [difficulty-ladder.md](difficulty-ladder.md). The
> earlier plan (mined PR review-signals as ground truth, expert raters, absolute SEH)
> was **falsified/dropped** — see the pilot report on the `archive/experiments` branch. In
> brief:

- **Reference corpus = real code changes spanning the range HAID's users produce** —
  small/personal-project commits *and* OSS PRs (beginner → expert). Units are blinded
  (identity stripped, code-files-first) so the judge can't recognize a project.
- **No mined external label.** There is no clean auto-minable ground truth for code
  difficulty (stars = value; review-effort = process; both falsified). Ground truth
  comes instead from **cross-method convergence**: a dense pairwise ordering and an
  independent coarse classification that *agree*.
- **Dense anchors, not a sparse sort of many.** A sparse pairwise sort mis-ranks the
  dense middle (transitivity through easy opponents). Instead: densely compare a
  *small* anchor set **all-pairs** → a bulletproof reference ordering (Bradley-Terry).
- **Score in production by placement:** compare the diff against each anchor (cheap
  model, anchors prompt-cached) → its relative position. Validated: a cheap model
  (Haiku) reproduces the expensive Opus ordering at ρ≈0.87.
- **Per axis, one ladder.** Difficulty and cleanliness are done (each passed the
  **orthogonality gates** — ⊥ size, ⊥ each other — that decide whether an axis earns its
  place). Originality was calibrated the same way but **failed to earn its place** and was
  dropped (see the Axis decision above); the gates did their job.

## Cost side — built (2026-06-06): normalized tokens, never dollars

> Built in [src/haid/scoring/cost.py](../src/haid/scoring/cost.py) (CLI: `haid cost --usage PATH`;
> tests `tests/scoring/test_cost.py`). **Decision: cost is reported in tokens, NOT currency.**
> Different users pay different rates and subscription users pay nothing per token, so a dollar
> figure is unstable and often meaningless. Instead cost is a **normalized token count** — every
> token converted to a common unit by *dimensionless relative weights* (the ratios between token
> kinds and model tiers), which are fixed by Anthropic's pricing *structure* and identical for
> everyone regardless of their actual rate. The unit ("normalized token", nTok) is one Haiku
> *input* token-equivalent.

- **Token-TYPE weights** (uniform across every tier — using them is "adjust for relative cost,"
  not "assume a rate"): `input 1× · output 5× · cache-write 1.25× (5m) / 2× (1h) · cache-read 0.1×`.
- **Model-TIER weights** — the single pricing-derived assumption (user-approved, fully overridable):
  `Haiku 1 · Sonnet 3 · Opus 15` (list-price input ratios; verify as pricing drifts). `tier × type`
  reproduces the full cross-tier price ratio (an Opus output token = 15×5 = 75 nTok), so the scalar
  is internally consistent — but it is a relative *effort* figure, not a bill.
- **Transparency:** the raw unweighted total and the per-type / per-tier breakdown are ALWAYS
  reported alongside the scalar — nothing hides behind one number. A huge `cache_read` (good
  caching) stays visibly distinct from huge `output`.
- **Process costs reported separately, never folded into the token total:** turns / tool-calls,
  wall-clock, and **compaction events** (both a real cost and a context-overflow smell).
- *Upstream dependency:* `cost.measure` takes a list of per-message `Usage` records; the
  **bridge** ([src/haid/bridge/](../src/haid/bridge/)) now pulls them from real sessions, so
  `haid value --project/--session` runs cost on real sessions (it still accepts a supplied usage
  JSON for explicit inputs).

## Combining into achievement and value — BUILT (2026-06-06)

> Built in [src/haid/scoring/value.py](../src/haid/scoring/value.py) (CLI: `haid value --diff PATH
> --usage PATH`, and — via the bridge — `haid value --project/--session` to score real sessions
> directly; tests `tests/scoring/test_value.py`, 14 deterministic tests). This is the final
> fold; every input it consumes is already produced and validated upstream (volume, the two
> placements, cost).

The locked combiner (knob defaults in parentheses):

```
achievement = LOC**alpha  *  D(difficulty)  *  C(cleanliness)
value       = achievement / (normalized_tokens / cost_unit)

  cost_unit (1e9)  value is reported as achievement per BILLION normalized tokens ("GnTok").
                   The denominator is dominated by cache-read (every turn re-reads the whole
                   cached context), so a real window runs 1e8..1e10 nTok while achievement is
                   order 10..1000 — divided per single nTok, every value collapses to ~1e-7
                   ("0.0" after rounding). The GnTok unit lands value in an order-1..1000
                   range. It is a pure LINEAR unit choice: rankings, percentiles, and
                   run-over-run comparisons are all invariant — but it is pinned in
                   combiner_config (and the benchmark hash), so users on different units are
                   bucketed apart rather than mis-ranked.
  alpha (0.5)      volume exponent — diminishing returns on raw surviving-LOC
  D(difficulty)    = exp( lam * (latent - latent_median) )
                     lam set so the hardest end is top_ratio x the median; top_ratio = 10
                     ("10x engineer"). Bottom/median falls out at ~0.1x automatically.
                     `latent` is the diff's Bradley-Terry score, interpolated from where it
                     placed between the anchors (the anchor `score` field IS the BT latent).
  C(cleanliness)   = floor + (1 - floor) * p_clean**gamma     gamma = 2, floor = 0.001
                     penalty-only (tops out at 1.0, never a bonus), steep, with an anti-spam
                     floor so LOC-padding can never out-pull LOC**alpha.
```

**Design decisions, locked with the maintainer (so they read as decisions, not drift):**

- **Volume is sub-linear (`alpha ≈ 0.5`).** Doubling lines is worth `2**alpha ≈ 1.4x`, not 2x —
  100→200 LOC is not the same marginal effort as a 10-line change. *(This was a genuine fork:
  strict linearity — "2x lines = 2x score" — is mathematically incompatible with "a small
  excellent change is worth a lot relative to a big one." We chose the latter.)*
- **Difficulty is convex, driven by the BT latent, not the rung.** Median→top = 10x; the rank
  flattens the tails, the latent (Elo/Bradley-Terry log-odds) keeps the "a pro is ~10x the median,
  not 2x" spread. One knob (`top_ratio`) with a clean interpretation.
- **Cleanliness is a STEEP, penalty-only multiplier — a *major* axis, ~co-equal to difficulty
  over real functional code (≈8x span, p 0.35→1.0), plus a 0.001 tail for slop.** This is a
  deliberate reversal of two earlier sketches: (a) the symmetric `√(diff² + clean²)` norm — dropped
  because once difficulty went convex/wide the norm geometry collapses the cleanliness leg; and
  (b) a cleanliness *bonus* (C>1) — dropped because a bonus partially reopens the LOC-spam door.
  "Top cleanliness is worth a lot" is expressed as *everything below top is heavily penalized*,
  never as fictional credit. Worked: 8 pristine lines beat 16 lines of `cost_calc` +
  `cost_calc_enhanced` (p≈0.35 → C≈0.12) by ~6x — the bigger, messier diff *loses*.
- **Cost is LINEAR in normalized tokens** (an org pays per token, not per `log(token)`). The
  fixed-exploration-cost penalty on small changes (few lines for many tokens) is acceptable because
  it lands on *value* (efficiency), not on *achievement* — the small clean change keeps its full
  achievement credit; value honestly reports it was expensive per line.

**Never collapse:** `value.py` returns the scalar *with* every component preserved
(volume / latent / D / p_clean / C / cost), so the diagnosis router keys off *which* term is bad.

**Scoring grain = the EPISODE, not per-file and not a blended window (decided 2026-06-07;
episode = a group of whole SESSIONS, grain locked 2026-06-08).**
The ladders were built at **coherent-change grain** (anchors U37…U50 are PR-sized changes), and
difficulty is **⊥ LOC**. So the unit we place must be a coherent unit of work — the **episode**
(the git-free PR proxy; see [agent-analysis.md §1](../plans/agent-analysis.md)). **An episode is a
collection of one or more whole sessions on a shared component/topic; the session is atomic and is
never subdivided** (one session = one context window = the only boundary at which token cost
attributes cleanly). Per-*file* is wrong (a file isn't a unit of work; no ladder analogue). A
**blended whole-window diff is wrong too** — it hits the **95/5 problem**: a project's weeks of
scaffolding drag the placement down and bury the critical 5%. Instead, score **per episode** and
report the **distribution** across the window (scaffolding episodes place T0–T1; the critical 5%
place T3–T4; because difficulty ⊥ LOC, the small hard episode isn't penalized for size). This is
also what credits **earned rework**: iteration on a high-difficulty episode is rewarded in
achievement, not just flagged as waste (rework × low-difficulty = the real smell). **Dependency:**
the per-episode diff = the existing window→diff/usage bridge run over the episode's **session
subset** (`bridge.window_inputs` already takes a session list — no new slicing engine, and cost =
the sum of the sessions' clean per-context-window costs). **Open:** `cleanliness` may want the
*final-artifact* grain (end-state quality) rather than per-episode — decide separately.

**Relative vs. absolute — and the benchmark.** The *per-axis placements* are relative (a diff's
position against a fixed reference ladder; SEH absolute grounding was falsified, see Calibration).
But the *combined* `achievement`/`value` is a **stable, deterministic function of (diff, usage)
for a given ladder version**, so it **is comparable across users** — which is exactly what the
opt-in community benchmark ([ADR-0005](decisions/0005-community-benchmark.md)) needs. "Relative
placement" is the per-axis *mechanism*; it does not mean the headline score is meaningless in
absolute terms or that magnitude can't be reported.

## Honesty guardrails (non-negotiable)

- **Snapshot achievement cannot see relational badness.** The canonical case's
  contorted code scores as "reasonable difficulty" in a snapshot. So the achievement
  score **never stands alone** — it is combined with the process passes (co-churn,
  cleanup, re-touch). Achievement scores the *what*; the waste passes correct it with
  the *how*.
- **Correctness gates achievement.** Broken/abandoned code is discounted;
  tests-passing / survival-to-final-diff is the multiplier. No achievement credit for
  code that didn't survive or doesn't run.
- **The soft layer stays hedged.** Elegance/specialization judgments are
  LLM-judged and clearly labeled as estimates, never the load-bearing score. The
  highest-value coaching (below) is built on the deterministic spine and doesn't
  need them.

## Diagnosis: the ratio routes to a recommendation

The score isn't just a number — *which* part of the ratio is bad selects the
coaching message. Notably, all of these are detectable on the **deterministic
spine**:

| Signature | Coaching message |
|-----------|------------------|
| high cost + low/unoriginal output | **over-powered: use a smaller model / template this boilerplate** |
| high re-reads + cross-session rediscovery | **add a skill / CLAUDE.md / memory so you stop rediscovering** |
| huge tokens + huge cache + compaction + thin output | **context bloat: start a fresh session** |
| repeated planning across sessions, thin impl | **write ONE planning doc; stop re-planning every session** |
| high correction rate | **misalignment: invest in clearer upfront specs** |
| high re-touch + co-churn | **wrong contract: your specs/tests are driving bad code** |
| high volume, low difficulty, reimplements a library | **solved problem: use an existing library** (reinvention discount) |

## Headline output (illustrative)
Relative, comparative — never an absolute SEH number:
> "Expert-tier specialized concurrency work (top ~10% difficulty of your sessions), at
> 280k Opus tokens / 2.1h — strong leverage."
>
> "Boilerplate (bottom-quartile difficulty), 400k Opus tokens — use Haiku and a template."

## Open refinements (for a focused session) ⟳
- ~~**Build the relative difficulty scorer** (placement vs the locked anchors → relative
  position); re-validate placement on held-out units.~~ — **done (2026-06-05):** built in
  `src/haid/scoring/` (placement.py + compare.py + anchors.py). Replay-validated — the
  runtime path reproduces the calibration result exactly (difficulty Haiku-placement
  ρ=0.866; anchor self-placement ρ=1.000 difficulty / 0.984 cleanliness;
  `tests/scoring/test_replay_validation.py`). Live model judgment is delegated to host-agent
  subagents (compare.HarnessBackend), never an in-process API call.
- ~~Calibrate the originality + cleanliness axes~~ — **done: cleanliness calibrated
  ([cleanliness-ladder.md](cleanliness-ladder.md)); originality calibrated then dropped**
  (Axis decision above). No further axes planned.
- ~~**Volume** measure (surviving LOC by kind, deterministic) — kept separate from
  difficulty.~~ — **done (2026-06-05):** `src/haid/scoring/volume.py` (weighted surviving
  LOC by file kind + structural counts; no Halstead). Confirmed ⊥ difficulty
  (Spearman=+0.32 over the 55 units). Rewrites are captured on the cost side (tokens), so
  volume stays a clean property of the final artifact.
- ~~Cost side (tokens × type/tier weights, components kept separate).~~ — **done
  (2026-06-06):** `src/haid/scoring/cost.py` — normalized tokens (no dollars), relative
  type + tier weights (both configurable), process costs separate. See the Cost side section above.
- ~~**Combine** the axes + cost into the value verdict.~~ — **done (2026-06-06):**
  [src/haid/scoring/value.py](../src/haid/scoring/value.py), see [§ Combining into achievement
  and value](#combining-into-achievement-and-value--built-2026-06-06). `achievement = LOC**α ·
  D(difficulty) · C(cleanliness)`; `value = achievement / normalized_tokens`. 14 deterministic
  tests; validated end-to-end via the replay + harness CLI paths.
- **Rendering / comparison surface** still open: self-over-time vs corpus position vs the opt-in
  **community benchmark** ([ADR-0005](decisions/0005-community-benchmark.md),
  [plans/community-benchmark.md](../plans/community-benchmark.md)). The combined value is a stable
  absolute scalar per ladder version, so all three are supported by the same number.
- **Diagnosis router** (the signature→coaching table above) and the **skill glue** that drives
  the live comparison subagents remain — see roadmap. *(The window→diff/usage extractor that feeds
  real sessions into the scorer is **built**: [src/haid/bridge/](../src/haid/bridge/), replay-only
  from the transcript, no git; `haid value --project/--session` runs the full stack. The
  **episode-grain** refinement — per-episode diffs — waits on Phase-2 episodes.)*
