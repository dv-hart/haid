# Originality ladder — the production scorer

> **⛔ DROPPED FROM THE SCORING MODEL (2026-06-05).** This axis was calibrated
> successfully (record below) but is **not used** in `achievement = f(volume, difficulty,
> cleanliness)`. Why: it **saturates** (the corpus tops out at "mid" — genuine novelty is
> vanishingly rare in real merged work, 0 "high" anchors), it is the **least
> difficulty-distinct** axis (ρ=+0.68 vs difficulty, versus cleanliness's +0.03), and it
> has **no resolution in the recombination space where ~all SE lives** (it scores
> masterful composition and lazy gluing identically as "low"). Its one legitimate job — a
> *reinvention discount* for difficult-but-derivative reimplementation — overlaps with
> cleanliness and is delivered as a coaching signal instead. Full rationale:
> scoring-rubric.md "Axis decision". **This doc is retained as the calibration record**
> (the playbook worked; the axis just didn't earn its place). Artifacts kept under
> `out/originality_*.json`.

> **Status: originality mechanism calibrated (2026-06-05)** following
> [axis-calibration-playbook.md](axis-calibration-playbook.md). Haiku-on-ladder
> reproduces the dense anchor order at ρ=0.78 (stable across 3 averaged samples).
> **The score is RELATIVE** — a diff's placement on the ladder — not an absolute level.
> The low/mid/high scale below is the **convergence cross-check** and an optional coarse
> *rendering*, never the score (same discipline as
> [difficulty-ladder.md](difficulty-ladder.md)).

## What this axis measures

ORIGINALITY = how much genuinely novel problem-solving a change required, vs.
reassembling patterns a library or standard idiom already provides. A change that solves
a problem **lacking an off-the-shelf solution** is HIGH; reimplementing what a
library/stdlib already does well is LOW **even if it was hard to write**; a genuinely
novel approach is HIGH **even if it is small**. Size and raw difficulty are explicitly
ignored — this is the axis that gives the *originality discount* in the combined score.

## How a session diff gets an originality score

1. Blind the diff (strip identifiers; reassemble code-files-first) — see
   [blind.py](../calibration/blind.py).
2. **Place it on the ladder**: Haiku does ~1 pairwise comparison vs. each anchor rung
   (anchors are fixed → **prompt-cached**) → the diff's **rung = how many anchors it is
   judged MORE original than**. Average 2–3 placement samples to cut variance.
3. **That rung (relative position) IS the originality score**, compared against the
   reference corpus / past sessions / teammates — not an absolute number.

## The locked anchor ladder (dense all-pairs order)

Order from the **dense all-pairs** comparison of 11 candidate anchors (110 verdicts,
counterbalanced both directions, **97.2% consistent, 7.3% position bias** — the 4
direction-flips are all local near-ties in the low/derivative cluster, none reorder the
ladder globally). Candidates were drawn from a rough k=2 sort of all 55 units, then
re-sorted densely — which corrected several transitivity artifacts of the rough sort
(e.g. U01 the flock-guard rose rough-rung 5 → dense 9; U21 the Go concurrency leak-fix
fell from rough-top → dense 7, since idiomatic channel-shutdown is less novel than a
bespoke type-system trick). The low/mid level column is the **independent
classification** pass; the order is **monotonic** with it (no violations) — the
cross-method convergence we trust in lieu of human labels.

| rung | anchor | reference change (anonymized class) | level |
|---|---|---|---|
| 0 | U54 | React/TSX: delete duplicated upload/create CTA buttons from cards | **low** |
| 1 | U33 | sparkline numeric-sanitization helper (`Number`/`isFinite`/`Math.max`) + test | **low** |
| 2 | U19 | React MCP-server enable/disable toggle (status state + i18n wiring) | **low** |
| 3 | U05 | thread `&AccessToken` through a GitHub client; split one App into two creds | **low** |
| 4 | U14 | k8s image-load: drop `removeExistingImage`, add in-use-overwrite integration test | **low** |
| 5 | U30 | pnpm `minimumReleaseAge`-from-workspace.yaml behavioral test + version gating | **low** |
| 6 | U51 | workflow-result hidden-context marker injected into the session store (idempotent) | **low** |
| 7 | U21 | readahead goroutine-leak fix: done-channel shutdown + reader `Close` on cancel | **low** |
| 8 | U44 | multi-tenant active-project cache: `(user,session)→(user,None)→global` fallback tier | **mid** |
| 9 | U01 | flock RAII guard: `PhantomData` lifetime-enforced LIFO nesting + revertible SH→EX upgrade | **mid** |
| 10 | U32 | terminal HiDPI: derive a clamped integer device-pixel-ratio for mouse→cell mapping | **mid** |

## Orthogonality (gates)

- **⊥ LOC (marginal):** ρ(originality, churn) = **+0.33** (full-55 rough sort), +0.27 on
  the dense anchors. Not the clean ≈0 that difficulty's dense oracle hit (−0.05). A
  low-moderate residual size correlation — partly rough-sort noise, partly a genuine
  confound (bigger changes do tend to carry more novel problem-solving). **Remedy if it
  matters downstream:** harden the prompt's "IGNORE size" caution and re-place. Documented,
  not hidden.
- **Distinct from difficulty (pass):** ρ(originality, difficulty) = **+0.68** (n=55) —
  moderate, well below ~0.9, so it is a genuinely distinct axis. Confirmed by content:
  U05 (high difficulty-prior) sits at the *bottom* of originality; the hard-but-derivative
  reimplementations lose. (On the 11 anchors alone the value is 0.85, inflated by small-n
  and by anchors that happen to span both axes; the n=55 figure is the reliable one.)

## Placement validation

Haiku placed all 44 holdouts against the 11 anchors, ×3 samples (majority-voted):
**ρ(Haiku rung, rough-sort originality score) = 0.784**, identical to the single-sample
0.782. The 3-sample agreement shows **Haiku variance is not the bottleneck** — the gap
below difficulty's 0.87 is attributable to (a) the holdout ground-truth being the rougher
k=2 sort (no dense holdout scores exist) and (b) the corpus's compressed originality range
(see ceiling below), which leaves less spread to resolve.

## Honesty / known limits

- **Corpus ceiling = "mid".** The independent classification assigned **no "high"
  labels**: this calibration corpus (personal projects + everyday OSS PRs) contains no
  genuinely novel-algorithm / research-tier originality. The top rungs (U32, U01, U44) are
  *problem-specific design assembled from known idioms*, not new algorithms. Mirrors the
  difficulty ladder's T4 ceiling — appropriate for HAID's users, but means the high end of
  the scale is under-anchored.
- **Mid-range resolution is coarse** — 8 of 11 anchors sit in the "low" band; the
  derivative end is dense and the flips cluster there. Mitigations: more mid/high rungs if
  a richer corpus is harvested, and 2–3 placement samples averaged (already applied).
- **Relative, not absolute** — the rung comes from comparison to the anchor ladder; no
  absolute originality number is ever asked of the model.
- This scores one component of the **what**. It feeds the combined score as the
  originality discount: `achievement = f(volume[LOC], difficulty, cleanliness,
  originality)`; reported as a **relative** comparison, not a standalone number.

## Artifacts

- `out/originality_verdicts.json` — rough k=2 sort (110 verdicts, 55 units, 95.4% consistent).
- `out/originality_anchor_dense_verdicts.json` — dense all-pairs (110 verdicts, 11 anchors).
- `out/originality_tiers.json` — independent low/mid/high classification.
- `out/originality_anchors.json` — **the locked ladder** (11 anchors w/ dense rung+score+level, 44 holdouts w/ rough score).
- `out/originality_haiku_placements_agg.json` — 3-sample majority-voted placements.
