"""Value combiner: fold volume + difficulty + cleanliness into achievement, then value.

All deterministic (no model): we synthesize PlacementResults directly to exercise the math
agreed with the maintainer (docs/scoring-rubric.md "Combining into achievement and value"):

  achievement = LOC**alpha * D(difficulty) * C(cleanliness)   ;  value = achievement / nTok
  D = exp(lam*(latent-median)), top/median = 10x   ;   C = execution_factor over defect density

Run: PYTHONPATH=src python tests/scoring/test_value.py   (or pytest tests/scoring/)
"""

from __future__ import annotations

import math
import os
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(_ROOT, "src"))

from haid.scoring import value
from haid.scoring.anchors import load_ladder
from haid.scoring.defects import DefectResult
from haid.scoring.placement import PlacementResult


def _clean_defects(changed_lines: int = 200) -> DefectResult:
    return DefectResult.from_findings([], changed_lines)


def _defects(severe: int, changed_lines: int = 200) -> DefectResult:
    findings = [{"defect_class": "error_swallowing", "locator": f"except {i}: pass",
                 "note": "x"} for i in range(severe)]
    return DefectResult.from_findings(findings, changed_lines)


def _diff_placement(beats: int, ties_top: bool = False) -> PlacementResult:
    """Difficulty placement: subject beats the `beats` easiest anchors, loses to the rest.

    If `ties_top`, the single anchor just above the beaten block is a tie (pins the latent
    to that anchor's score) rather than a loss.
    """
    ladder = load_ladder("difficulty")
    anchors = list(ladder.anchors)  # ascending by rung/score
    per = []
    for i, a in enumerate(anchors):
        if i < beats:
            per.append((a.id, "subject"))
        elif ties_top and i == beats:
            per.append((a.id, "tie"))
        else:
            per.append((a.id, "anchor"))
    return PlacementResult(axis="difficulty", rung=float(beats), seen=len(anchors),
                           n_rungs=len(anchors), samples=1, per_anchor=per)


# ---------------------------------------------------------------- difficulty worth
def test_difficulty_median_is_one():
    """A diff that ties the median anchor scores D ~= 1."""
    # 9 anchors, median = rung 4. Beat 0..3, tie rung 4, lose 5..8 -> latent == median.
    dw = value.difficulty_worth(_diff_placement(beats=4, ties_top=True))
    assert abs(dw.D - 1.0) < 1e-6


def test_difficulty_top_is_ten_x():
    """Tying the hardest anchor scores ~10x the median (the locked top_ratio)."""
    ladder = load_ladder("difficulty")
    n = len(ladder.anchors)
    dw = value.difficulty_worth(_diff_placement(beats=n - 1, ties_top=True))
    assert abs(dw.D - 10.0) < 0.2


def test_difficulty_bottom_falls_out_at_tenth():
    """Tying the easiest anchor scores ~1/10x the median (symmetry of the exp curve)."""
    dw = value.difficulty_worth(_diff_placement(beats=0, ties_top=True))
    assert abs(dw.D - 0.1) < 0.02


def test_difficulty_beating_all_extrapolates_above_ten():
    dw = value.difficulty_worth(_diff_placement(beats=load_ladder("difficulty").n_rungs))
    assert dw.D > 10.0


# ---------------------------------------------------------------- achievement / anti-spam
def _mid_difficulty():
    return _diff_placement(beats=4, ties_top=True)   # D ~= 1


def test_achievement_components_preserved():
    ach = value.achievement(8.0, _mid_difficulty(), _clean_defects())
    assert ach.volume_loc == 8.0
    assert abs(ach.volume_term - math.sqrt(8.0)) < 1e-9
    assert abs(ach.difficulty_D - 1.0) < 1e-6
    assert ach.cleanliness_mode == "defects"
    assert ach.cleanliness_C == 1.0                    # 0 severe defects -> no penalty
    assert abs(ach.achievement - math.sqrt(8.0)) < 1e-6


def test_dirty_work_is_penalized_vs_same_volume_clean():
    """At equal volume + difficulty, severe defects strictly reduce achievement (bounded
    by the execution floor — cleanliness stings but is not annihilating like the old axis)."""
    clean = value.achievement(50.0, _mid_difficulty(), _defects(0, 200))
    dirty = value.achievement(50.0, _mid_difficulty(), _defects(4, 200))
    assert dirty.achievement < clean.achievement
    assert dirty.cleanliness_C >= value.DEFAULT_EXEC_FLOOR - 1e-9   # bounded, never below floor


def test_difficulty_dominates_volume_at_the_top():
    """A small elite-difficulty change can outscore a large trivial one (convex difficulty)."""
    elite_small = value.achievement(
        10.0, _diff_placement(beats=8, ties_top=True), _clean_defects())
    trivial_big = value.achievement(
        500.0, _diff_placement(beats=0, ties_top=True), _clean_defects())
    assert elite_small.achievement > trivial_big.achievement


# ---------------------------------------------------------------- bug-fix reward term
def _cured(beats: int, find_cost: float, bug_id: str = "b") -> value.CuredBug:
    """An eligible cured bug: fix-span placed at `beats` on the difficulty ladder, with a
    waste-discounted find cost (eligibility is assumed applied upstream)."""
    return value.CuredBug(fix_difficulty=_diff_placement(beats, ties_top=True),
                          earned_find_cost=find_cost, bug_id=bug_id)


def test_no_cured_bugs_leaves_achievement_untouched():
    """Backward compat: the default (no cured bugs) is exactly the old volume*D*C."""
    base = value.achievement(50.0, _mid_difficulty(), _clean_defects())
    assert base.bugfix_term == 0.0
    assert base.n_cured_bugs == 0
    assert abs(base.achievement - base.volume_term * base.difficulty_D * base.cleanliness_C) < 1e-9


def test_curing_an_inherited_bug_increases_achievement():
    """An eligible cured bug adds a strictly positive additive term."""
    base = value.achievement(50.0, _mid_difficulty(), _clean_defects())
    withbug = value.achievement(50.0, _mid_difficulty(), _clean_defects(),
                                cured_bugs=[_cured(beats=4, find_cost=2e6)])
    assert withbug.n_cured_bugs == 1
    assert withbug.bugfix_term > 0.0
    assert withbug.achievement > base.achievement
    assert abs(withbug.achievement - (base.achievement + withbug.bugfix_term)) < 1e-9


def test_harder_to_find_bug_is_worth_more():
    """Elusiveness pays: same fix difficulty, more earned find-cost -> bigger term (gamma>0)."""
    easy = value.bugfix_term([_cured(beats=4, find_cost=1e6)])
    hard = value.bugfix_term([_cured(beats=4, find_cost=9e6)])
    assert hard.term > easy.term


def test_harder_to_fix_bug_is_worth_more():
    """Same find-cost, higher fix-difficulty placement -> bigger term (D leg)."""
    trivial = value.bugfix_term([_cured(beats=1, find_cost=3e6)])
    expert = value.bugfix_term([_cured(beats=8, find_cost=3e6)])
    assert expert.term > trivial.term


def test_bugfix_count_is_concave_anti_farm():
    """beta<1: two cured bugs are worth LESS than twice one (no linear mass-fix farming)."""
    one = value.bugfix_term([_cured(beats=4, find_cost=3e6, bug_id="a")])
    two = value.bugfix_term([_cured(beats=4, find_cost=3e6, bug_id="a"),
                             _cured(beats=4, find_cost=3e6, bug_id="b")])
    assert two.term > one.term                       # more is still more
    assert two.term < 2.0 * one.term                 # but sub-linear (concave)


def test_bugfix_zero_find_cost_contributes_nothing():
    """A bug with no attributable (earned) find-cost adds nothing — elusiveness is the driver."""
    r = value.bugfix_term([_cured(beats=4, find_cost=0.0)])
    assert r.term == 0.0


def test_bugfix_skips_nan_placement():
    """A fix-span with no usable comparisons (nan latent -> nan D) is skipped, not crashed."""
    empty_pl = PlacementResult(axis="difficulty", rung=0.0, seen=0, n_rungs=9, samples=1,
                               per_anchor=[])
    r = value.bugfix_term([value.CuredBug(fix_difficulty=empty_pl, earned_find_cost=5e6)])
    assert r.n_bugs == 0
    assert r.term == 0.0


def test_bugfix_knobs_pinned_in_combiner_config():
    cfg = value.combiner_config()
    for k in ("bugfix_gain", "find_unit", "find_gamma", "bugfix_beta"):
        assert k in cfg


# ---------------------------------------------------------------- value = achievement / cost
def test_value_divides_by_normalized_tokens_in_gntok_units():
    """value = achievement per cost_unit nTok (default 1e9), NOT per single nTok — otherwise
    a real window (1e9 nTok) lands every value at ~1e-7 and rounds to 0.0."""
    ach = value.achievement(100.0, _mid_difficulty(), _clean_defects())
    vr = value.value(ach, 50_000.0)
    assert abs(vr.value - ach.achievement / (50_000.0 / value.DEFAULT_COST_UNIT)) < 1e-9
    assert vr.normalized_tokens == 50_000.0       # raw cost preserved untouched
    assert vr.cost_unit == value.DEFAULT_COST_UNIT


def test_value_in_a_readable_range_for_a_real_window():
    """A realistic window (achievement ~169 over ~2.1e9 nTok) yields ~80, never 0.0."""
    ach = value.achievement(150.0, _diff_placement(beats=8, ties_top=True),
                            _clean_defects())
    vr = value.value(ach, 2.13e9)
    assert 1.0 < vr.value < 1e4                    # order-1..1000, not ~1e-7


def test_cost_unit_is_a_linear_rescale_only():
    """Changing cost_unit scales value by exactly that factor — rankings are invariant."""
    ach = value.achievement(100.0, _mid_difficulty(), _clean_defects())
    per_tok = value.value(ach, 1e9, cost_unit=1.0).value
    per_gtok = value.value(ach, 1e9, cost_unit=1e9).value
    assert abs(per_gtok / per_tok - 1e9) < 1e-3


def test_cost_unit_is_pinned_in_combiner_config():
    assert value.combiner_config()["cost_unit"] == value.DEFAULT_COST_UNIT


def test_value_linear_in_cost():
    """Cost is LINEAR: 5x the tokens for the same work => 1/5 the value (it bites)."""
    ach = value.achievement(100.0, _mid_difficulty(), _clean_defects())
    cheap = value.value(ach, 50_000.0).value
    pricey = value.value(ach, 250_000.0).value
    assert abs(cheap / pricey - 5.0) < 1e-9


def test_value_handles_zero_cost():
    ach = value.achievement(100.0, _mid_difficulty(), _clean_defects())
    vr = value.value(ach, 0.0)
    assert vr.value != vr.value   # nan, not a crash
    assert value.value_ratio(100.0, 0.0) != value.value_ratio(100.0, 0.0)   # helper: nan too


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn) and fn.__code__.co_argcount == 0:
            fn()
            print(f"ok  {name}")
    print("\nALL VALUE TESTS PASSED")
