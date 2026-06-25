"""Value combiner: fold volume + difficulty + cleanliness into achievement, then value.

All deterministic (no model): we synthesize PlacementResults directly to exercise the math
agreed with the maintainer (docs/scoring-rubric.md "Combining into achievement and value"):

  achievement = LOC**alpha * D(difficulty) * C(cleanliness)   ;  value = achievement / nTok
  D = exp(lam*(latent-median)), top/median = 10x   ;   C = floor + (1-floor)*p**gamma

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
from haid.scoring.placement import PlacementResult


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


def _clean_placement(pct: float, n: int = 11) -> PlacementResult:
    """Cleanliness placement at a given percentile (rung/seen)."""
    return PlacementResult(axis="cleanliness", rung=pct * n, seen=n,
                           n_rungs=n, samples=1, per_anchor=[])


# ---------------------------------------------------------------- cleanliness factor
def test_cleanliness_pristine_is_one_slop_is_floor():
    assert value.cleanliness_factor(_clean_placement(1.0)) == 1.0
    assert abs(value.cleanliness_factor(_clean_placement(0.0)) - value.DEFAULT_FLOOR) < 1e-9


def test_cleanliness_is_steep_and_monotonic():
    cs = [value.cleanliness_factor(_clean_placement(p)) for p in (0.0, 0.25, 0.5, 0.75, 1.0)]
    assert cs == sorted(cs)                     # monotonic increasing
    # gamma=2 -> midpoint is well below linear (steep penalty)
    assert value.cleanliness_factor(_clean_placement(0.5)) < 0.3


def test_cleanliness_duplication_neighbourhood():
    """The cost_calc / cost_calc_enhanced duplication pattern (~p=0.35) lands near 0.12."""
    c = value.cleanliness_factor(_clean_placement(0.35))
    assert 0.10 < c < 0.16


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
    ach = value.achievement(8.0, _mid_difficulty(), _clean_placement(1.0))
    assert ach.volume_loc == 8.0
    assert abs(ach.volume_term - math.sqrt(8.0)) < 1e-9
    assert abs(ach.difficulty_D - 1.0) < 1e-6
    assert ach.cleanliness_C == 1.0
    assert abs(ach.achievement - math.sqrt(8.0)) < 1e-6


def test_loc_spam_cannot_win():
    """3 clean lines must beat the same 3 lines + 7 lines of unused slop (10 LOC, p~0)."""
    clean = value.achievement(3.0, _mid_difficulty(), _clean_placement(1.0))
    slop = value.achievement(10.0, _mid_difficulty(), _clean_placement(0.0))
    assert clean.achievement > slop.achievement
    assert slop.achievement < 0.05            # slop is crushed by the floor


def test_pristine_beats_duplication_about_six_x():
    """8 pristine lines vs 16 lines of cost_calc + cost_calc_enhanced (~p=0.35): ~6x, and
    the bigger/messier diff LOSES despite double the LOC."""
    pristine = value.achievement(8.0, _mid_difficulty(), _clean_placement(1.0))
    dup = value.achievement(16.0, _mid_difficulty(), _clean_placement(0.35))
    ratio = pristine.achievement / dup.achievement
    assert 5.0 < ratio < 6.5
    assert pristine.achievement > dup.achievement      # more LOC did not help


def test_difficulty_dominates_volume_at_the_top():
    """A small elite-difficulty change can outscore a large trivial one (convex difficulty)."""
    elite_small = value.achievement(
        10.0, _diff_placement(beats=8, ties_top=True), _clean_placement(0.8))
    trivial_big = value.achievement(
        500.0, _diff_placement(beats=0, ties_top=True), _clean_placement(0.8))
    assert elite_small.achievement > trivial_big.achievement


# ---------------------------------------------------------------- value = achievement / cost
def test_value_divides_by_normalized_tokens_in_gntok_units():
    """value = achievement per cost_unit nTok (default 1e9), NOT per single nTok — otherwise
    a real window (1e9 nTok) lands every value at ~1e-7 and rounds to 0.0."""
    ach = value.achievement(100.0, _mid_difficulty(), _clean_placement(1.0))
    vr = value.value(ach, 50_000.0)
    assert abs(vr.value - ach.achievement / (50_000.0 / value.DEFAULT_COST_UNIT)) < 1e-9
    assert vr.normalized_tokens == 50_000.0       # raw cost preserved untouched
    assert vr.cost_unit == value.DEFAULT_COST_UNIT


def test_value_in_a_readable_range_for_a_real_window():
    """A realistic window (achievement ~169 over ~2.1e9 nTok) yields ~80, never 0.0."""
    ach = value.achievement(150.0, _diff_placement(beats=8, ties_top=True),
                            _clean_placement(0.9))
    vr = value.value(ach, 2.13e9)
    assert 1.0 < vr.value < 1e4                    # order-1..1000, not ~1e-7


def test_cost_unit_is_a_linear_rescale_only():
    """Changing cost_unit scales value by exactly that factor — rankings are invariant."""
    ach = value.achievement(100.0, _mid_difficulty(), _clean_placement(1.0))
    per_tok = value.value(ach, 1e9, cost_unit=1.0).value
    per_gtok = value.value(ach, 1e9, cost_unit=1e9).value
    assert abs(per_gtok / per_tok - 1e9) < 1e-3


def test_cost_unit_is_pinned_in_combiner_config():
    assert value.combiner_config()["cost_unit"] == value.DEFAULT_COST_UNIT


def test_value_linear_in_cost():
    """Cost is LINEAR: 5x the tokens for the same work => 1/5 the value (it bites)."""
    ach = value.achievement(100.0, _mid_difficulty(), _clean_placement(1.0))
    cheap = value.value(ach, 50_000.0).value
    pricey = value.value(ach, 250_000.0).value
    assert abs(cheap / pricey - 5.0) < 1e-9


def test_value_handles_zero_cost():
    ach = value.achievement(100.0, _mid_difficulty(), _clean_placement(1.0))
    vr = value.value(ach, 0.0)
    assert vr.value != vr.value   # nan, not a crash
    assert value.value_ratio(100.0, 0.0) != value.value_ratio(100.0, 0.0)   # helper: nan too


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn) and fn.__code__.co_argcount == 0:
            fn()
            print(f"ok  {name}")
    print("\nALL VALUE TESTS PASSED")
