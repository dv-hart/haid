"""Combine the measured axes into achievement, then into value = achievement / cost.

This is the final fold of the scoring stack. Every input it consumes is already produced
and validated upstream:

  - volume    (volume.VolumeResult.weighted_loc)        — deterministic surviving-LOC
  - difficulty (placement.PlacementResult, axis=difficulty) — relative ladder placement
  - cleanliness(defects.DefectResult)                       — counted severe-defect density
  - cost      (cost.CostResult.normalized_tokens)        — normalized-token denominator

The agreed model (see docs/scoring-rubric.md "Combining into achievement and value"):

    achievement = LOC**alpha  *  D(difficulty)  *  C(cleanliness)
    value       = achievement / (normalized_tokens / cost_unit)

where, with knobs alpha, top_ratio (=10x), the cleanliness defect-density knobs
(k_defect/loc_floor/exec_floor), and cost_unit (=1e9):

  cost_unit makes `value` human-readable. The denominator is dominated by cache-read
  tokens — every turn re-reads the whole cached context, so a real window accumulates
  1e8..1e10 normalized tokens while achievement is order 10..1000. Dividing the raw ratio
  out per single nTok therefore lands every value at ~1e-7 ("0.0" after rounding). We
  instead report value as **achievement per BILLION normalized tokens** (one "GnTok"),
  which puts it in an order-1..1000 range. cost_unit is a pure linear unit choice — it
  preserves every ranking, percentile, and run-over-run comparison untouched — but it IS
  pinned in combiner_config(), so two users on different units are bucketed apart on the
  benchmark rather than silently mis-ranked (ADR-0005).

and, with the remaining knobs alpha, top_ratio (=10x) and the defect-density knobs above:

  D(difficulty) = exp( lam * (latent - latent_median) )          # convex, Elo/BT-grounded
                  lam chosen so the hardest end is `top_ratio`x the median ("10x engineer").
                  `latent` is the diff's Bradley-Terry score, interpolated from where it
                  placed between the anchors (the anchor `score` field IS the BT latent).

  C(cleanliness) = max(exec_floor, 1 - k_defect * severe / sqrt(max(LOC, loc_floor)))
                  severe = count of verified severe defects in the diff (scoring/defects.py).
                  Penalty-only (tops out at 1.0 for clean work, never a bonus) and bounded by
                  `exec_floor` so cleanliness can sting but never dominate the score. Replaced
                  the pairwise cleanliness ladder, which was non-ordinal (see defects.py).

NOTHING is collapsed: the result keeps every component (volume, latent, D, severe_count, C, cost)
so the diagnosis router can key off *which* term is bad, not just the scalar.

Design decisions (locked with the maintainer, 2026-06-06):
  - alpha < 1: diminishing returns on raw volume.
  - difficulty is convex (BT latent), top/median = 10x. Bottom/median falls out at ~0.1x.
  - cleanliness is a penalty-only multiplier, NOT symmetric with difficulty in an L2 norm.
    The earlier symmetric-norm + cleanliness-bonus idea was dropped on purpose. (History:
    cleanliness was originally a steep p**gamma pairwise-ladder penalty; it was retired for
    counted severe-defect density in 2026-06 — non-ordinal ladder, see defects.py — and is
    now a bounded discount via execution_factor(), not a co-equal squared multiplier.)
  - cost is LINEAR in normalized tokens (an org pays per token); the small-change "fixed
    exploration cost" penalty is acceptable because it lands on VALUE, not achievement.

The combined value is a stable, deterministic function of (diff, usage) given a ladder
version, so it is comparable across users (it feeds the opt-in community benchmark, ADR-0005).
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .anchors import Ladder, load_ladder
from .placement import PlacementResult

if TYPE_CHECKING:                       # annotation-only; defects has no runtime use here
    from .defects import DefectResult

# --- knobs (all overridable per call) --------------------------------------------------
DEFAULT_ALPHA = 0.5         # volume exponent: diminishing returns (sqrt)
DEFAULT_TOP_RATIO = 10.0    # difficulty: hardest-end worth vs median ("10x engineer")
DEFAULT_COST_UNIT = 1e9     # value denominator unit: achievement per BILLION nTok (GnTok)

# --- cleanliness-as-defect-density knobs (the ladder replacement; see scoring/defects.py)
# execution_factor = max(EXEC_FLOOR, 1 - K_DEFECT * severe_count / sqrt(max(changed_lines, LOC_FLOOR)))
# The denominator is sqrt(LOC), NOT LOC: severe-defect COUNT scales sub-linearly with size (a
# 5k-line project does not have 5k severe defects), so dividing by full LOC washes the signal out
# of big projects and forces an absurd k that then floors any small diff with one defect. sqrt gives
# big work only sub-linear tolerance, so the COUNT drives the penalty and a riddled large project
# still bites. Tuned empirically against real diffs (tools/tune_execution_factor.py).
DEFAULT_K_DEFECT = 2.3      # how hard severe defects bite (slope on severe / sqrt(LOC))
DEFAULT_LOC_FLOOR = 50      # small-diff smoothing: one defect in a tiny diff isn't auto-floored
DEFAULT_EXEC_FLOOR = 0.6    # worst-case execution discount (bounded so cleanliness is never dominant)


def combiner_config(*, alpha: float = DEFAULT_ALPHA, top_ratio: float = DEFAULT_TOP_RATIO,
                    k_defect: float = DEFAULT_K_DEFECT, loc_floor: int = DEFAULT_LOC_FLOOR,
                    exec_floor: float = DEFAULT_EXEC_FLOOR,
                    cost_unit: float = DEFAULT_COST_UNIT) -> dict:
    """The combiner knobs that fold the axes into `value`. Single source of truth for the
    combiner-config hash: two users on the same ladders but different knobs (or a different
    `cost_unit`, which rescales the value magnitude everyone is ranked on) are NOT
    comparable, so the benchmark payload pins these (ADR-0005). The cleanliness knobs are the
    defect-density ones (k_defect/loc_floor/exec_floor) — gamma/floor of the retired pairwise
    ladder no longer affect the score."""
    return {"alpha": alpha, "top_ratio": top_ratio, "k_defect": k_defect,
            "loc_floor": loc_floor, "exec_floor": exec_floor, "cost_unit": cost_unit}


@dataclass(frozen=True)
class DifficultyWorth:
    """The convex difficulty multiplier and the latent it was derived from."""
    D: float
    latent: float
    lam: float
    latent_median: float
    top_ratio: float


@dataclass(frozen=True)
class AchievementResult:
    """achievement = volume_term * difficulty_D * cleanliness_C, components preserved."""
    achievement: float
    # volume leg
    volume_loc: float
    alpha: float
    volume_term: float
    # difficulty leg
    difficulty_D: float
    difficulty_latent: float
    lam: float
    top_ratio: float
    # cleanliness leg (counted-defect density; see scoring/defects.py)
    cleanliness_C: float                  # the execution_factor multiplier applied
    floor: float                          # the execution floor used
    cleanliness_mode: str = "defects"
    severe_count: int = 0
    changed_lines: int = 0

    def summary(self) -> str:
        clean = (f"  cleanliness: {self.severe_count} severe / {self.changed_lines} chg LOC "
                 f"-> C={self.cleanliness_C:.3g} (defect-density, floor={self.floor:g})")
        return (f"achievement={self.achievement:.3g}\n"
                f"  volume:      LOC={self.volume_loc:.1f} ^{self.alpha:g} "
                f"-> {self.volume_term:.3g}\n"
                f"  difficulty:  latent={self.difficulty_latent:+.2f} "
                f"-> D={self.difficulty_D:.3g} (top/median={self.top_ratio:g}x)\n"
                + clean)


@dataclass(frozen=True)
class ValueResult:
    """value = achievement / (normalized_tokens / cost_unit) — achievement per `cost_unit`
    normalized tokens (default: per billion, "GnTok"). Achievement decomposition kept
    alongside; the raw normalized_tokens is preserved untouched."""
    value: float
    normalized_tokens: float
    achievement: AchievementResult
    cost_unit: float = DEFAULT_COST_UNIT
    cost_breakdown: dict = field(default_factory=dict)   # optional by_type/by_tier passthrough

    def summary(self) -> str:
        v = "n/a" if self.value != self.value else f"{self.value:.4g}"
        unit = f"{self.normalized_tokens / self.cost_unit:.3g}" if self.cost_unit else "n/a"
        return (f"value={v}  (achievement {self.achievement.achievement:.3g} / "
                f"{unit} GnTok; {self.normalized_tokens:.0f} nTok raw)\n"
                + self.achievement.summary())


# --- difficulty: interpolate a latent from the placement, then map to a convex multiplier
def interpolate_latent(pl: PlacementResult, ladder: Ladder) -> float:
    """Estimate the subject's Bradley-Terry latent from where it placed on the ladder.

    The subject beat some anchors (latent above theirs) and lost to others (latent below).
    Its latent sits between the highest anchor it beat and the lowest it lost to; a tie pins
    it to that anchor. If it beat (or lost to) every anchor, extrapolate one mean gap past
    the top (or bottom) anchor.
    """
    scores = {a.id: a.score for a in ladder.anchors}
    all_scores = [a.score for a in ladder.anchors]
    if not all_scores:
        return float("nan")
    lo_candidates: list[float] = []   # subject >= these
    hi_candidates: list[float] = []   # subject <= these
    for aid, winner in pl.per_anchor:
        s = scores.get(aid)
        if s is None:
            continue
        if winner == "subject":
            lo_candidates.append(s)
        elif winner == "anchor":
            hi_candidates.append(s)
        else:  # tie pins both sides to this anchor
            lo_candidates.append(s)
            hi_candidates.append(s)

    lo = max(lo_candidates) if lo_candidates else None
    hi = min(hi_candidates) if hi_candidates else None
    if lo is not None and hi is not None:
        return (lo + hi) / 2.0
    mean_gap = ((max(all_scores) - min(all_scores)) / (len(all_scores) - 1)
                if len(all_scores) > 1 else 1.0)
    if lo is not None:                 # beat everything -> harder than the hardest anchor
        return max(all_scores) + mean_gap
    if hi is not None:                 # lost to everything -> easier than the easiest anchor
        return min(all_scores) - mean_gap
    return float("nan")                # no comparisons available


def difficulty_worth(pl: PlacementResult, ladder: Ladder | None = None, *,
                     top_ratio: float = DEFAULT_TOP_RATIO) -> DifficultyWorth:
    """Convex difficulty multiplier: exp(lam*(latent - median)), median->1, top->top_ratio."""
    ladder = ladder or load_ladder("difficulty")
    latent = interpolate_latent(pl, ladder)
    scores = [a.score for a in ladder.anchors]
    median = statistics.median(scores)
    top = max(scores)
    lam = math.log(top_ratio) / (top - median) if top > median else 0.0
    D = math.exp(lam * (latent - median)) if latent == latent else float("nan")
    return DifficultyWorth(D=D, latent=latent, lam=lam, latent_median=median,
                           top_ratio=top_ratio)


# --- cleanliness as a counted-defect DENSITY penalty (the ladder replacement) -----------
def execution_factor(severe_count: int, changed_lines: int, *,
                     k: float = DEFAULT_K_DEFECT, loc_floor: int = DEFAULT_LOC_FLOOR,
                     floor: float = DEFAULT_EXEC_FLOOR) -> float:
    """Bounded, orthogonal-to-difficulty execution discount from counted severe defects.

        density = severe_count / sqrt(max(changed_lines, loc_floor))
        factor  = max(floor, 1 - k * density)

    The denominator is sqrt(LOC), not LOC. Severe-defect COUNT scales sub-linearly with
    size, so the size tolerance must too: dividing by full LOC makes the per-defect cost
    inversely proportional to size — a big project washes out (10 defects in 2000 lines would
    score better than 1 defect in 30) and the only way to make it bite is a k so large it
    floors any small diff. sqrt fixes this: COUNT drives the penalty, size buys only
    sub-linear slack, so a lone defect in a big file is gentle while a riddled large project
    still hits the floor. (This deliberately drops strict 2x/2x scale-invariance in favor of
    'more defects always hurt more'.) `loc_floor` keeps one defect in a tiny diff off the
    floor; `floor` bounds the worst case so cleanliness is a discount, never dominant.

    0 severe defects -> 1.0 (no penalty). Only SEVERE defects enter here; minors are
    coaching color (weight 0). See scoring/defects.py for how the counts are produced."""
    if severe_count < 0:
        raise ValueError("severe_count must be >= 0")
    denom = max(int(changed_lines), int(loc_floor))
    if denom <= 0:                      # empty diff: nothing to penalize
        return 1.0
    density = severe_count / math.sqrt(denom)
    return max(floor, 1.0 - k * density)


# --- the fold ---------------------------------------------------------------------------
def achievement(volume, difficulty_pl: PlacementResult, cleanliness: DefectResult, *,
                alpha: float = DEFAULT_ALPHA, top_ratio: float = DEFAULT_TOP_RATIO,
                difficulty_ladder: Ladder | None = None,
                k_defect: float = DEFAULT_K_DEFECT, loc_floor: int = DEFAULT_LOC_FLOOR,
                exec_floor: float = DEFAULT_EXEC_FLOOR) -> AchievementResult:
    """achievement = weighted_loc**alpha * D(difficulty) * C(cleanliness).

    `volume` is a volume.VolumeResult (uses .weighted_loc) or a bare float LOC.
    `cleanliness` is a defects.DefectResult; C = execution_factor over its severe-defect
    density (the counted-defect model that replaced the pairwise cleanliness ladder).
    Difficulty is still a pairwise placement.
    """
    loc = getattr(volume, "weighted_loc", volume)
    loc = max(float(loc), 0.0)
    dw = difficulty_worth(difficulty_pl, difficulty_ladder, top_ratio=top_ratio)
    vol_term = loc ** alpha
    C = execution_factor(cleanliness.severe_count, cleanliness.changed_lines,
                         k=k_defect, loc_floor=loc_floor, floor=exec_floor)
    ach = vol_term * dw.D * C
    return AchievementResult(
        achievement=ach,
        volume_loc=loc, alpha=alpha, volume_term=vol_term,
        difficulty_D=dw.D, difficulty_latent=dw.latent, lam=dw.lam, top_ratio=top_ratio,
        cleanliness_C=C, floor=exec_floor,
        cleanliness_mode="defects", severe_count=cleanliness.severe_count,
        changed_lines=cleanliness.changed_lines,
    )


def value_ratio(achievement: float, normalized_tokens: float, *,
                cost_unit: float = DEFAULT_COST_UNIT) -> float:
    """THE headline ratio — the single definition of `value`, shared by every caller
    (value(), the window roll-up, the benchmark payload, and its plausibility re-check) so
    they cannot drift. value = achievement / (normalized_tokens / cost_unit); nan when there
    is no cost. cost_unit only rescales the denominator into a readable range (see the module
    docstring) — it is linear, so it leaves all rankings/percentiles invariant."""
    ntok = float(normalized_tokens)
    if ntok <= 0:
        return float("nan")
    return achievement / (ntok / cost_unit)


def value(ach: AchievementResult, cost, *, cost_unit: float = DEFAULT_COST_UNIT) -> ValueResult:
    """value = achievement / (normalized_tokens / cost_unit). `cost` is a cost.CostResult or a
    bare float."""
    ntok = getattr(cost, "normalized_tokens", cost)
    ntok = float(ntok)
    v = value_ratio(ach.achievement, ntok, cost_unit=cost_unit)
    breakdown = {}
    if hasattr(cost, "by_type"):
        breakdown = {"by_type": cost.by_type, "by_tier": cost.by_tier}
    return ValueResult(value=v, normalized_tokens=ntok, achievement=ach,
                       cost_unit=cost_unit, cost_breakdown=breakdown)
