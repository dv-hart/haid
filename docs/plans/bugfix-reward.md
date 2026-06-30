# Bug-fix reward — crediting the cure of inherited bugs

> **Status: design + Phase-0 combiner term (this doc).** Folds a positive *bug-fix* term
> into achievement so remediation episodes stop scoring as low-value cleanup. Decided with the
> maintainer over the 2026-06-29 scaling review.

## The problem

HAID scores an episode's **net artifact** (`achievement = LOC^α · D(difficulty) · C(cleanliness)`)
over its **cost** (`value = achievement / nTok`). For inspection/remediation work this is
structurally backwards:

- The cure of a bug is a deletion + a tiny fix → near-zero volume → near-zero achievement.
- The win is **counterfactual** (a severe bug averted) and has **no positive term** — cleanliness
  is penalty-only (`C ≤ 1.0`, [scoring/value.py](../../src/haid/scoring/value.py)).
- The find cost (often the whole episode) sits in the **denominator**, so the harder the hunt the
  *worse* `value` looks.

Worst case, the ledger is one-directional: a severe defect can only ever *subtract* (when it sits
in a diff, via `C`); its removal is invisible (volume counts added lines only). Curing a bug
introduced in episode A but fixed in a separate episode B scores ~nothing for B — and B's hunt
*hurts* its value. That actively discourages the most responsible behavior.

## What already exists (the substrate)

The research found most of the machinery is built — as **coaching**, never wired to the score:

- **Cure detection** — `why/bug_anchors.select_bug_anchors` finds **fix spans** seeded by
  `impl_kind=bugfix` or a `correction` move ([why/bug_anchors.py](../../src/haid/why/bug_anchors.py)).
  Tag-anchored, so there is **no censored-density problem** — a bug exists because the human framed
  one. Better than the diff-baseline detection we first imagined; that approach is dropped.
- **Anti-farm attribution** — the bug why-pass labels every fix `cause_class ∈ {agent,user,source,
  undetermined}`, `scope ∈ {same_episode,cross_episode,unknown}`, `holding ∈ {held,recurred,unknown}`
  ([why/prompts.py](../../src/haid/why/prompts.py)).
- **Difficulty placement** — `scoring/placement.place()` against the locked, **size-orthogonal**
  ladder (ρ-vs-LOC = −0.05), which already contains senior bug-fix anchors at rungs 6–7
  ([docs/difficulty-ladder.md](../difficulty-ladder.md)).
- **Cost** — already in the value denominator; per-span fix cost is measured.

Gaps to build: a span-grain fix **diff**, a **why → score join**, the **combiner term**, and the
**benchmark re-bucket**.

## The model change

Per episode, fold an additive bug-fix term into the existing achievement fold
([scoring/value.py](../../src/haid/scoring/value.py) `achievement()`):

```
achievement = LOC^α · D(difficulty) · C(cleanliness)  +  bugfix_term
value       = achievement / (normalized_tokens / cost_unit)        # denominator UNCHANGED
```

with

```
worth(bug)   = D(fix_difficulty)  ·  (earned_find_cost / find_unit)^γ
bugfix_term  = gain · ( Σ_eligible worth(bug) )^β
```

- **`D(fix_difficulty)`** — the fix-span diff placed on the **existing difficulty ladder** and run
  through the **existing** `difficulty_worth()` convex map. No new ladder unless validation
  (below) shows incoherent placement.
- **`earned_find_cost`** — investigation nTok attributable to locating this bug, **minus the waste
  the detectors already flag** (retries/rereads/unused-context in the span) so flailing is not
  credited as elusiveness. Computed upstream; the combiner trusts the number.
- **`γ` (find-cost exponent)** — why the find cost belongs in the numerator too: it sits in the
  denominator, so without a matching numerator term a *hard-to-find, easy-to-fix, critical* bug
  scores near zero. Putting it in the numerator means **`achievement_total` rewards elusiveness**
  (hard finds add more), while in the **`value` ratio** the find cost partially cancels, so `value`
  measures remediation *efficiency*, not hunt length. `γ=1` ≈ neutral in value; `γ>1` net-rewards
  elusiveness.
- **`β < 1`** — concavity over the count of cured bugs (mirrors `α`), so an "oops-all-bugs" cleanup
  can't farm linearly. Replaces an *unobservable* (defect density) with an *observable* (count)
  under a concave transform — answering the density worry without needing to estimate density.
- **`gain`, `find_unit`** — scale knobs so the term lands comparable to the volume term.

### Eligibility gate (anti-farm)

Count a cured bug only when **`(cause_class=source` OR `scope=cross_episode)` AND
`holding≠recurred`**. You are rewarded for clearing *inherited / other-thread* debt that *stuck* —
never for fixing a bug you just introduced in the same thread. The residual hole (plant in an
unsubmitted window, harvest in a submitted one) is bounded by `β`, the verify pass, and the real
token cost of staging a believable hunt — *not worth it*, not *impossible*.

## Reuse-vs-new-ladder: the validation gate

Everything downstream is identical whether we reuse the difficulty ladder or build a bug-fix one.
**Decision gate = a coherence check** (the same test that retired the cleanliness ladder —
[scoring/defects.py](../../src/haid/scoring/defects.py) header): place a set of known-ordered fix
spans and count ordering inversions.

- The known limit is **context, not size**: a fix's difficulty often lives in code the diff doesn't
  show (a one-line race fix is near-blank to a diff-only judge). **First mitigation, keep the
  ladder:** enrich the placement *subject* with the bug context the why-pass already gathers
  (symptom, the traced introducing edit, the surrounding function).
- Build a dedicated bug-fix ladder **only if** enriched placement stays incoherent.

## The architectural cost to accept

This is the **first wire from the why-pass (coaching) into scoring**. The two stacks are
deliberately separate; folding the reward in couples the score to a sonnet tool-using verdict.
Mitigations: an adversarial **verify** pass on the attribution (mirroring cleanliness), and a new
benchmark bucket. The new combiner knobs change `combiner_config()` → a fresh comparability bucket
(ADR-0005), which is correct, not a regression.

## Tuning knobs (all start UNTUNED — calibrate after Phase 0)

| knob | role | start |
|---|---|---|
| `bugfix_gain` | overall term scale | 1.0 |
| `find_unit` | nTok per elusiveness unit | 1e6 |
| `find_gamma` (γ) | find-cost exponent (1=value-neutral; >1 rewards elusiveness) | 1.0 |
| `bugfix_beta` (β) | concavity over cured-bug count | 0.5 |

Open tuning question deferred by design: **impact severity** ("critical") is currently only
*proxied* by find-cost. If the proxy proves too weak, add a severity multiplier (closed-table
lookup, [defects.py](../../src/haid/scoring/defects.py) discipline) as a future knob.

## Build order

0. ✅ **Combiner term** — pure math in [scoring/value.py](../../src/haid/scoring/value.py)
   (`CuredBug`, `bugfix_term`, fold into `achievement()`, 4 knobs into `combiner_config`),
   unit-tested, no model. Local benchmark pin refreshed (intentional re-bucket).
1. ✅ **Span-grain fix diff** — `span_inputs` in [bridge/__init__.py](../../src/haid/bridge/__init__.py):
   span-entry baseline via pre-span replay, correct even when the in-span edit captured no
   `originalFile`. Tested.
2. ✅ **Coherence harness** — [scoring/coherence.py](../../src/haid/scoring/coherence.py)
   (Kendall tau-b + inversion veto) and [tools/validate_fix_placement.py](../../tools/validate_fix_placement.py).
   Tested. **Still needs a LIVE run on real fix spans + an independent reference order to actually
   decide reuse-vs-new-ladder** — the harness is built; the calibration run is outstanding.
3. ✅ **why → score join** — [why/bug_anchors.py](../../src/haid/why/bug_anchors.py) `fix_spans`
   (shared, uncapped) + [scoring/bugfix.py](../../src/haid/scoring/bugfix.py)
   (`collect_candidates` → `resolve_cured`, `is_eligible` gate) wired into
   [episodes/score.py](../../src/haid/episodes/score.py) `score_episodes(tagged=…, cured_eligible=…)`.
   `earned_find_cost` = normalized tokens of the assistant turns in the hunt window (seed → last
   resolving edit); waste-discount deferred to Phase 4. Tested; end-to-end run below.
4. ⬜ **Verify pass + benchmark bucket** — adversarial re-check of attribution; pin knobs.

## End-to-end run (boxBot b1117557, 2026-06-30)

Full chain on the real "boxbot has not been responding" session, one real model placement:
- fix span `+296/-38` over 9 files (hunks-mode); **find-cost 73.0M nTok** (the real hunt).
- difficulty placement (live agent, 9 blind comparisons): **rung 6.0/9** (p67, T3-senior) — beat
  the trivial/mid anchors, lost to the SQLite crash-safety state machine, keyring fail-safe, Arc-CoW.
- `bugfix_term = +12` at the untuned defaults → **the magnitude is sane** (comparable to a whole
  episode's achievement, order 10), confirming `find_unit=1e6` puts the term in range (the scale I
  was least sure of). On this substantial fix the cure lifts achievement/value ~1.7×; for the
  target case (a tiny fix after a long hunt) it dominates more.
- Caveats: heuristic tag (not the real tag pass), default-eligible (attribution gate not exercised),
  hunks-mode diff, untuned knobs — all expected pre-Phase-4/tuning.

## Honesty / known limits

- Score now depends on an agentic pass (less deterministic than the LOC/diff legs) — verify pass
  mitigates, never fully removes.
- `top=4` fix-span budget is a coaching cap; the **scored** path must score *all* eligible spans (no
  silent truncation on a benchmarked axis).
- Find-cost is a noisy proxy for bug worth; severity stays a deferred knob.

## Real-data validation (boxBot, 5 fix/investigation sessions, 2026-06-30)

- `span_inputs` is correct on real transcripts: per-session, **Σ span added-lines == whole-session
  added-lines** for all 5 sessions (no leakage / double-attribution).
- **78% of changed files (28/36) reconstruct in hunks-mode** — Claude Code omits `originalFile` on
  larger files; the sole incompleteness cause. Two consequences:
  - **Validates difficulty+find-cost over baseline-severity:** a "bug present at baseline → gone at
    final" cure-detector is impossible for most files (no full baseline). Our diff-only magnitude
    path is robust to this; a baseline-inspection path would not have been.
  - **Step-3 risk:** hunks-mode "overlapping re-edits may double-count" — carry the hunks caveat
    onto a fix-span's difficulty placement rather than trusting the diff silently.
