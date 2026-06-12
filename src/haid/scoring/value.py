"""Combine the measured axes into achievement, then into value = achievement / cost.

This is the final fold of the scoring stack. Every input it consumes is already produced
and validated upstream:

  - volume    (volume.VolumeResult.weighted_loc)        — deterministic surviving-LOC
  - difficulty (placement.PlacementResult, axis=difficulty) — relative ladder placement
  - cleanliness(placement.PlacementResult, axis=cleanliness)— relative ladder placement
  - cost      (cost.CostResult.normalized_tokens)        — normalized-token denominator

The agreed model (see docs/scoring-rubric.md "Combining into achievement and value"):

    achievement = LOC**alpha  *  D(difficulty)  *  C(cleanliness)
    value       = achievement / normalized_tokens

where, with knobs alpha, top_ratio (=10x), gamma (=2), floor (=0.001):

  D(difficulty) = exp( lam * (latent - latent_median) )          # convex, Elo/BT-grounded
                  lam chosen so the hardest end is `top_ratio`x the median ("10x engineer").
                  `latent` is the diff's Bradley-Terry score, interpolated from where it
                  placed between the anchors (the anchor `score` field IS the BT latent).

  C(cleanliness) = floor + (1 - floor) * p_clean ** gamma        # penalty-only, anti-spam
                  p_clean = the cleanliness placement percentile (0=least clean .. 1=most).
                  Penalty-only (tops out at 1.0, never a bonus) so it cannot be gamed upward;
                  the `floor` guarantees LOC-spam can never out-pull LOC**alpha (a maximally
                  sloppy diff is multiplied by ~0.001).

NOTHING is collapsed: the result keeps every component (volume, latent, D, p_clean, C, cost)
so the diagnosis router can key off *which* term is bad, not just the scalar.

Design decisions (locked with the maintainer, 2026-06-06):
  - alpha < 1: diminishing returns on raw volume.
  - difficulty is convex (BT latent), top/median = 10x. Bottom/median falls out at ~0.1x.
  - cleanliness is a STEEP, penalty-only multiplier (a major axis, ~co-equal to difficulty
    over real functional code), NOT the minor modifier first sketched, and NOT symmetric
    with difficulty in an L2 norm. The earlier symmetric-norm + cleanliness-bonus idea was
    dropped on purpose.
  - cost is LINEAR in normalized tokens (an org pays per token); the small-change "fixed
    exploration cost" penalty is acceptable because it lands on VALUE, not achievement.

The combined value is a stable, deterministic function of (diff, usage) given a ladder
version, so it is comparable across users (it feeds the opt-in community benchmark, ADR-0005).
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field

from .anchors import Ladder, load_ladder
from .placement import PlacementResult

# --- knobs (all overridable per call) --------------------------------------------------
DEFAULT_ALPHA = 0.5         # volume exponent: diminishing returns (sqrt)
DEFAULT_TOP_RATIO = 10.0    # difficulty: hardest-end worth vs median ("10x engineer")
DEFAULT_GAMMA = 2.0         # cleanliness penalty steepness
DEFAULT_FLOOR = 0.001       # cleanliness floor: anti-LOC-spam guard


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
    # cleanliness leg
    cleanliness_pct: float
    cleanliness_C: float
    gamma: float
    floor: float

    def summary(self) -> str:
        return (f"achievement={self.achievement:.3g}\n"
                f"  volume:      LOC={self.volume_loc:.1f} ^{self.alpha:g} "
                f"-> {self.volume_term:.3g}\n"
                f"  difficulty:  latent={self.difficulty_latent:+.2f} "
                f"-> D={self.difficulty_D:.3g} (top/median={self.top_ratio:g}x)\n"
                f"  cleanliness: p={self.cleanliness_pct:.2f} "
                f"-> C={self.cleanliness_C:.3g} (gamma={self.gamma:g}, floor={self.floor:g})")


@dataclass(frozen=True)
class ValueResult:
    """value = achievement / normalized_tokens. Achievement decomposition kept alongside."""
    value: float
    normalized_tokens: float
    achievement: AchievementResult
    cost_breakdown: dict = field(default_factory=dict)   # optional by_type/by_tier passthrough

    def summary(self) -> str:
        v = "n/a" if self.value != self.value else f"{self.value:.4g}"
        return (f"value={v}  (achievement {self.achievement.achievement:.3g} / "
                f"{self.normalized_tokens:.0f} nTok)\n" + self.achievement.summary())


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


# --- cleanliness: steep, penalty-only multiplier with an anti-spam floor ----------------
def cleanliness_factor(pl: PlacementResult, *, gamma: float = DEFAULT_GAMMA,
                       floor: float = DEFAULT_FLOOR) -> float:
    """C = floor + (1-floor) * p**gamma. p=1 (pristine) -> 1.0; p=0 (slop) -> floor."""
    p = pl.percentile
    if p != p:  # nan
        return float("nan")
    p = min(max(p, 0.0), 1.0)
    return floor + (1.0 - floor) * (p ** gamma)


# --- the fold ---------------------------------------------------------------------------
def achievement(volume, difficulty_pl: PlacementResult, cleanliness_pl: PlacementResult, *,
                alpha: float = DEFAULT_ALPHA, top_ratio: float = DEFAULT_TOP_RATIO,
                gamma: float = DEFAULT_GAMMA, floor: float = DEFAULT_FLOOR,
                difficulty_ladder: Ladder | None = None) -> AchievementResult:
    """achievement = weighted_loc**alpha * D(difficulty) * C(cleanliness).

    `volume` is a volume.VolumeResult (uses .weighted_loc) or a bare float LOC.
    """
    loc = getattr(volume, "weighted_loc", volume)
    loc = max(float(loc), 0.0)
    dw = difficulty_worth(difficulty_pl, difficulty_ladder, top_ratio=top_ratio)
    C = cleanliness_factor(cleanliness_pl, gamma=gamma, floor=floor)
    vol_term = loc ** alpha
    ach = vol_term * dw.D * C
    return AchievementResult(
        achievement=ach,
        volume_loc=loc, alpha=alpha, volume_term=vol_term,
        difficulty_D=dw.D, difficulty_latent=dw.latent, lam=dw.lam, top_ratio=top_ratio,
        cleanliness_pct=cleanliness_pl.percentile, cleanliness_C=C, gamma=gamma, floor=floor,
    )


def value(ach: AchievementResult, cost) -> ValueResult:
    """value = achievement / normalized_tokens. `cost` is a cost.CostResult or a bare float."""
    ntok = getattr(cost, "normalized_tokens", cost)
    ntok = float(ntok)
    v = ach.achievement / ntok if ntok > 0 else float("nan")
    breakdown = {}
    if hasattr(cost, "by_type"):
        breakdown = {"by_type": cost.by_type, "by_tier": cost.by_tier}
    return ValueResult(value=v, normalized_tokens=ntok, achievement=ach,
                       cost_breakdown=breakdown)


def score(diff: str, difficulty_backend, cleanliness_backend, cost_result, *,
          samples: int = 1, alpha: float = DEFAULT_ALPHA,
          top_ratio: float = DEFAULT_TOP_RATIO, gamma: float = DEFAULT_GAMMA,
          floor: float = DEFAULT_FLOOR) -> ValueResult:
    """End-to-end convenience: measure volume, place both axes, fold into value.

    Backends are supplied per axis (they may be the same object). Cost is passed in already
    measured (cost.measure(...)), since usage extraction is upstream. Placement may raise
    compare.PendingComparisons under the live HarnessBackend file-handoff path.
    """
    from . import volume as _volume
    from .placement import place

    vol = _volume.measure(diff)
    dpl = place(diff, "difficulty", difficulty_backend, samples=samples)
    cpl = place(diff, "cleanliness", cleanliness_backend, samples=samples)
    ach = achievement(vol, dpl, cpl, alpha=alpha, top_ratio=top_ratio,
                      gamma=gamma, floor=floor)
    return value(ach, cost_result)
