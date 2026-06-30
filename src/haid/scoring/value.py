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

# --- bug-fix reward knobs (the cured-inherited-bug term; see docs/plans/bugfix-reward.md) ---
# achievement gets an ADDITIVE term for curing inherited bugs, so remediation episodes stop
# scoring as low-value cleanup. Per eligible cured bug:
#     worth = D(fix_difficulty) * (earned_find_cost / find_unit)^gamma
#     bugfix_term = gain * (Σ worth)^beta
# The find-cost is in BOTH the value denominator (real cost) AND here (numerator): on
# achievement_total a hard-to-find bug is worth more (elusiveness rewarded), while in the value
# ratio it partially cancels, so value reads as remediation EFFICIENCY rather than hunt length.
# ALL FOUR START UNTUNED — placeholders to calibrate after the join lands (build order Phase 3).
DEFAULT_BUGFIX_GAIN = 1.0   # overall term scale
DEFAULT_FIND_UNIT = 1e6     # nTok per "elusiveness unit" (earned find-cost scale)
DEFAULT_FIND_GAMMA = 1.0    # find-cost exponent: 1=value-neutral, >1 net-rewards elusiveness
DEFAULT_BUGFIX_BETA = 0.5   # concavity over cured-bug COUNT (mirrors alpha; caps mass-fix farming)

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
                    cost_unit: float = DEFAULT_COST_UNIT,
                    bugfix_gain: float = DEFAULT_BUGFIX_GAIN,
                    find_unit: float = DEFAULT_FIND_UNIT,
                    find_gamma: float = DEFAULT_FIND_GAMMA,
                    bugfix_beta: float = DEFAULT_BUGFIX_BETA) -> dict:
    """The combiner knobs that fold the axes into `value`. Single source of truth for the
    combiner-config hash: two users on the same ladders but different knobs (or a different
    `cost_unit`, which rescales the value magnitude everyone is ranked on) are NOT
    comparable, so the benchmark payload pins these (ADR-0005). The cleanliness knobs are the
    defect-density ones (k_defect/loc_floor/exec_floor) — gamma/floor of the retired pairwise
    ladder no longer affect the score. The bug-fix knobs (bugfix_gain/find_unit/find_gamma/
    bugfix_beta) pin the cured-inherited-bug term; adding them re-buckets the benchmark, which
    is correct (a different achievement definition is not comparable to the old one)."""
    return {"alpha": alpha, "top_ratio": top_ratio, "k_defect": k_defect,
            "loc_floor": loc_floor, "exec_floor": exec_floor, "cost_unit": cost_unit,
            "bugfix_gain": bugfix_gain, "find_unit": find_unit,
            "find_gamma": find_gamma, "bugfix_beta": bugfix_beta}


@dataclass(frozen=True)
class DifficultyWorth:
    """The convex difficulty multiplier and the latent it was derived from."""
    D: float
    latent: float
    lam: float
    latent_median: float
    top_ratio: float


@dataclass(frozen=True)
class CuredBug:
    """One eligible cured-inherited-bug feeding the additive bug-fix achievement term.

    `fix_difficulty` is the fix-span diff's placement on the difficulty ladder (reused, not a
    new ladder); `earned_find_cost` is the waste-discounted nTok attributable to LOCATING the
    bug (the eligibility gate + waste discount are applied upstream — this struct is already
    eligible). See docs/plans/bugfix-reward.md."""
    fix_difficulty: PlacementResult
    earned_find_cost: float
    bug_id: str = ""                      # the fix-span anchor id (provenance)


@dataclass(frozen=True)
class BugfixResult:
    """The additive bug-fix term and its decomposition (every cured bug's worth kept)."""
    term: float
    raw_sum: float                        # Σ worth, pre-gain, pre-beta
    n_bugs: int
    per_bug: tuple = ()                   # ((bug_id, D, elusiveness, worth), ...)
    gain: float = DEFAULT_BUGFIX_GAIN
    beta: float = DEFAULT_BUGFIX_BETA


@dataclass(frozen=True)
class AchievementResult:
    """achievement = volume_term * difficulty_D * cleanliness_C + bugfix_term, components kept."""
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
    # bug-fix leg (cured inherited bugs; ADDITIVE, not a multiplier; see bugfix-reward.md)
    bugfix_term: float = 0.0
    n_cured_bugs: int = 0

    def summary(self) -> str:
        clean = (f"  cleanliness: {self.severe_count} severe / {self.changed_lines} chg LOC "
                 f"-> C={self.cleanliness_C:.3g} (defect-density, floor={self.floor:g})")
        bug = (f"\n  bugfix:      {self.n_cured_bugs} cured -> +{self.bugfix_term:.3g}"
               if self.bugfix_term else "")
        return (f"achievement={self.achievement:.3g}\n"
                f"  volume:      LOC={self.volume_loc:.1f} ^{self.alpha:g} "
                f"-> {self.volume_term:.3g}\n"
                f"  difficulty:  latent={self.difficulty_latent:+.2f} "
                f"-> D={self.difficulty_D:.3g} (top/median={self.top_ratio:g}x)\n"
                + clean + bug)


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


# --- bug-fix reward: an ADDITIVE term for curing inherited bugs (the achievement gap) -----
def bugfix_term(cured, *, gain: float = DEFAULT_BUGFIX_GAIN, find_unit: float = DEFAULT_FIND_UNIT,
                gamma: float = DEFAULT_FIND_GAMMA, beta: float = DEFAULT_BUGFIX_BETA,
                top_ratio: float = DEFAULT_TOP_RATIO,
                difficulty_ladder: Ladder | None = None) -> BugfixResult:
    """The additive bug-fix achievement term over a list of (already-eligible) CuredBugs.

        worth(bug)  = D(fix_difficulty) * (max(earned_find_cost, 0) / find_unit)^gamma
        term        = gain * (Σ worth)^beta

    `D` reuses difficulty_worth() over the fix-span placement (NO new ladder). The find-cost
    enters here AND the value denominator on purpose: it lifts achievement for elusive bugs
    while partially cancelling in the value ratio, so value reads as remediation efficiency,
    not hunt length (docs/plans/bugfix-reward.md). `beta < 1` makes the COUNT concave so an
    'oops-all-bugs' cleanup can't farm linearly. Eligibility + waste-discount are upstream;
    an empty list yields a zero term (and leaves achievement untouched)."""
    per = []
    raw = 0.0
    for b in cured:
        D = difficulty_worth(b.fix_difficulty, difficulty_ladder, top_ratio=top_ratio).D
        if D != D:                                  # nan placement (no comparisons) -> skip
            continue
        elusiveness = (max(float(b.earned_find_cost), 0.0) / find_unit) ** gamma
        w = D * elusiveness
        raw += w
        per.append((b.bug_id, D, elusiveness, w))
    term = gain * (raw ** beta) if raw > 0 else 0.0
    return BugfixResult(term=term, raw_sum=raw, n_bugs=len(per), per_bug=tuple(per),
                        gain=gain, beta=beta)


# --- the fold ---------------------------------------------------------------------------
def achievement(volume, difficulty_pl: PlacementResult, cleanliness: DefectResult, *,
                alpha: float = DEFAULT_ALPHA, top_ratio: float = DEFAULT_TOP_RATIO,
                difficulty_ladder: Ladder | None = None,
                k_defect: float = DEFAULT_K_DEFECT, loc_floor: int = DEFAULT_LOC_FLOOR,
                exec_floor: float = DEFAULT_EXEC_FLOOR,
                cured_bugs=None, bugfix_gain: float = DEFAULT_BUGFIX_GAIN,
                find_unit: float = DEFAULT_FIND_UNIT, find_gamma: float = DEFAULT_FIND_GAMMA,
                bugfix_beta: float = DEFAULT_BUGFIX_BETA) -> AchievementResult:
    """achievement = weighted_loc**alpha * D(difficulty) * C(cleanliness) + bugfix_term.

    `volume` is a volume.VolumeResult (uses .weighted_loc) or a bare float LOC.
    `cleanliness` is a defects.DefectResult; C = execution_factor over its severe-defect
    density (the counted-defect model that replaced the pairwise cleanliness ladder).
    Difficulty is still a pairwise placement. `cured_bugs` is an optional list of CuredBug
    (already eligibility-gated upstream); it adds the ADDITIVE bug-fix term and defaults to
    none, so existing callers are unchanged.
    """
    loc = getattr(volume, "weighted_loc", volume)
    loc = max(float(loc), 0.0)
    dw = difficulty_worth(difficulty_pl, difficulty_ladder, top_ratio=top_ratio)
    vol_term = loc ** alpha
    C = execution_factor(cleanliness.severe_count, cleanliness.changed_lines,
                         k=k_defect, loc_floor=loc_floor, floor=exec_floor)
    bug = bugfix_term(cured_bugs or [], gain=bugfix_gain, find_unit=find_unit,
                      gamma=find_gamma, beta=bugfix_beta, top_ratio=top_ratio,
                      difficulty_ladder=difficulty_ladder)
    ach = vol_term * dw.D * C + bug.term
    return AchievementResult(
        achievement=ach,
        volume_loc=loc, alpha=alpha, volume_term=vol_term,
        difficulty_D=dw.D, difficulty_latent=dw.latent, lam=dw.lam, top_ratio=top_ratio,
        cleanliness_C=C, floor=exec_floor,
        cleanliness_mode="defects", severe_count=cleanliness.severe_count,
        changed_lines=cleanliness.changed_lines,
        bugfix_term=bug.term, n_cured_bugs=bug.n_bugs,
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
