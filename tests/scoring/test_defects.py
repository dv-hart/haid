"""Cleanliness-as-defect-density: the counted-defect taxonomy + the execution_factor math.

Deterministic (no model): we hand the math raw counts and assert the properties the
maintainer specified — chiefly SUB-LINEAR size tolerance (more defects always hurt more;
strict 2x/2x invariance was deliberately dropped so big riddled projects still bite) and
SEVERITY-BY-LOOKUP (the judge never sets severity).

Run: PYTHONPATH=src python tests/scoring/test_defects.py   (or pytest tests/scoring/)
"""

from __future__ import annotations

import os
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(_ROOT, "src"))

from haid.scoring import defects, value
from haid.scoring.defects import DefectResult


# ----------------------------------------------------------------- taxonomy / severity
def test_severity_is_a_lookup_not_a_judgment():
    # severe and minor resolve from the table; unknown -> 'other' (weight 0)
    assert defects.severity_of("reinvents_primitive") == defects.SEVERE
    assert defects.severity_of("verbosity") == defects.MINOR
    assert defects.severity_of("something_a_judge_made_up") == defects.OTHER
    assert defects.severity_of("other") == defects.OTHER


def test_schema_enum_is_the_closed_taxonomy_plus_other():
    enum = defects.DEFECT_SCHEMA["properties"]["findings"]["items"]["properties"][
        "defect_class"]["enum"]
    assert set(enum) == set(defects.DEFECT_CLASSES) | {defects.OTHER}
    # no severity field is solicited from the judge — we derive it
    item_props = defects.DEFECT_SCHEMA["properties"]["findings"]["items"]["properties"]
    assert "severity" not in item_props
    assert set(item_props) == {"defect_class", "locator", "note"}


def test_prompt_lists_every_class_and_forbids_unanchored_findings():
    p = defects.build_defect_prompt("--- some diff ---")
    for cls in defects.DEFECT_CLASSES:
        assert cls in p
    assert "locator" in p and "verbatim" in p


# ----------------------------------------------------------------- counting from findings
def test_from_findings_counts_by_lookup_and_ignores_judge_severity():
    findings = [
        {"defect_class": "reinvents_primitive", "locator": "datetime.strptime hand-rolled",
         "note": "manual ISO parse", "severity": "minor"},     # judge 'severity' is IGNORED
        {"defect_class": "dead_code", "locator": "# old_impl()", "note": "commented block"},
        {"defect_class": "verbosity", "locator": "for x in...", "note": "could be a comp"},
        {"defect_class": "other", "locator": "weird thing", "note": "novel"},
    ]
    r = DefectResult.from_findings(findings, changed_lines=120)
    assert r.severe_count == 2           # reinvents_primitive + dead_code, NOT the bogus 'minor'
    assert r.minor_count == 1            # verbosity
    assert r.other_count == 1            # other never counts as severe
    assert r.changed_lines == 120
    assert r.by_class()["reinvents_primitive"] == 1


def test_empty_is_clean():
    r = DefectResult.from_findings([], changed_lines=80)
    assert r.severe_count == 0
    assert value.execution_factor(r.severe_count, r.changed_lines) == 1.0


# ----------------------------------------------------------------- execution_factor math
def test_clean_diff_is_no_penalty():
    assert value.execution_factor(0, 200) == 1.0


def test_sublinear_size_tolerance_more_defects_always_hurt_more():
    """We DROPPED strict 2x/2x invariance on purpose: with a sqrt(LOC) denominator, doubling
    both the size and the defect count must cost MORE, not the same — because severe-defect
    count scales sub-linearly with size, so a 2x-bigger diff with 2x the defects is dirtier."""
    a = value.execution_factor(1, 100, k=1.0)
    b = value.execution_factor(2, 200, k=1.0)
    c = value.execution_factor(4, 400, k=1.0)   # explicit low k keeps all three off the floor
    assert a > b > c                      # each doubling bites harder, not equal


def test_count_drives_penalty_size_only_softens():
    """A lone defect in a big file is gentle; many defects in the SAME big file bite hard —
    the property linear/LOC got backwards (there, many-in-big scored better than one-in-small)."""
    lone_big = value.execution_factor(1, 2000)
    many_big = value.execution_factor(10, 2000)
    lone_small = value.execution_factor(1, 30)
    assert lone_big > 0.9                 # one defect in 2000 lines barely registers
    assert many_big <= value.DEFAULT_EXEC_FLOOR + 1e-9   # ten defects floor even in a big file
    assert many_big < lone_small          # many-in-big now correctly worse than one-in-small


def test_monotonic_decreasing_in_severe_count():
    fs = [value.execution_factor(n, 200) for n in range(0, 6)]
    assert fs == sorted(fs, reverse=True)
    assert fs[0] == 1.0


def test_floor_clamps_the_worst_case():
    # a diff that is essentially all slop saturates at the floor, never below
    f = value.execution_factor(severe_count=50, changed_lines=50,
                               floor=value.DEFAULT_EXEC_FLOOR)
    assert f == value.DEFAULT_EXEC_FLOOR


def test_loc_floor_smooths_tiny_diffs():
    """One severe defect in a 5-line diff is NOT auto-floored — the LOC_FLOOR denominator
    treats it as if the diff were LOC_FLOOR lines, so a lone lapse costs the same modest
    amount whether the diff is 5 lines or LOC_FLOOR lines."""
    tiny = value.execution_factor(1, 5)
    at_floor_size = value.execution_factor(1, value.DEFAULT_LOC_FLOOR)
    assert abs(tiny - at_floor_size) < 1e-12
    assert tiny > value.DEFAULT_EXEC_FLOOR        # a single defect doesn't bottom out the score


def test_density_orders_as_expected():
    """Denser slop is penalized harder than the same defects spread over more code."""
    dense = value.execution_factor(3, 60)
    sparse = value.execution_factor(3, 600)
    assert dense < sparse <= 1.0


def test_k_controls_bite():
    base = value.execution_factor(1, 400, k=1.5)
    harder = value.execution_factor(1, 400, k=2.5)
    assert harder < base <= 1.0


# ----------------------------------------------------------------- verify pass
def _result_with_two_severe_one_minor():
    findings = [
        {"defect_class": "dead_code", "locator": "# old()", "note": "commented block"},
        {"defect_class": "verbosity", "locator": "for x...", "note": "could be comp"},
        {"defect_class": "error_swallowing", "locator": "except: pass", "note": "swallows"},
    ]
    return DefectResult.from_findings(findings, changed_lines=200)


def test_severe_findings_excludes_minors():
    r = _result_with_two_severe_one_minor()
    sev = r.severe_findings()
    assert [f["defect_class"] for f in sev] == ["dead_code", "error_swallowing"]


def test_verify_drops_refuted_severe_and_rebuilds_counts():
    r = _result_with_two_severe_one_minor()
    # refute the first severe (dead_code), confirm the second (error_swallowing)
    verdicts = [{"verdict": "refuted", "reason": "intentional"},
                {"verdict": "confirmed", "reason": "real"}]
    out = defects.apply_verify(r, verdicts)
    assert out.severe_count == 1                       # one survived
    assert out.minor_count == 1                        # minor untouched
    assert [f["defect_class"] for f in out.severe_findings()] == ["error_swallowing"]


def test_verify_all_refuted_yields_clean():
    r = _result_with_two_severe_one_minor()
    verdicts = [{"verdict": "refuted", "reason": "x"}, {"verdict": "refuted", "reason": "y"}]
    out = defects.apply_verify(r, verdicts)
    assert out.severe_count == 0
    assert value.execution_factor(out.severe_count, out.changed_lines) == 1.0


def test_verify_requires_one_verdict_per_severe():
    r = _result_with_two_severe_one_minor()
    try:
        defects.apply_verify(r, [{"verdict": "confirmed", "reason": "x"}])  # only 1, need 2
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_verify_prompt_defaults_to_refute():
    p = defects.build_verify_prompt(
        {"defect_class": "dead_code", "locator": "# old()", "note": "n"}, "--- diff ---")
    assert "refute" in p.lower() and "dead_code" in p


def test_verify_schema_is_reasoning_first():
    """`reason` before `verdict` so it conditions the verdict (not a post-hoc rationalization);
    prompt forbids out-of-tool prose. See compare.VERDICT_SCHEMA for the why."""
    props = list(defects.VERIFY_SCHEMA["properties"])
    assert props.index("reason") < props.index("verdict")
    assert defects.VERIFY_SCHEMA["required"] == ["reason", "verdict"]
    p = defects.build_verify_prompt(
        {"defect_class": "dead_code", "locator": "# old()", "note": "n"}, "--- diff ---")
    assert "NO analysis or prose" in p and "plus reason" not in p


# ----------------------------------------------------------------- achievement via defects
def test_achievement_uses_execution_factor_for_defectresult():
    """A DefectResult flows through achievement() as the execution_factor penalty leg."""
    from haid.scoring.placement import PlacementResult
    # mid difficulty (ties the median anchor) so D ~= 1, isolating the cleanliness leg
    from haid.scoring.anchors import load_ladder
    anchors = list(load_ladder("difficulty").anchors)
    per = [(a.id, "subject" if i < 4 else "tie" if i == 4 else "anchor")
           for i, a in enumerate(anchors)]
    dpl = PlacementResult(axis="difficulty", rung=4.0, seen=len(anchors),
                          n_rungs=len(anchors), samples=1, per_anchor=per)
    clean = DefectResult.from_findings([], changed_lines=200)               # no defects -> C=1
    dirty = DefectResult.from_findings(
        [{"defect_class": "error_swallowing", "locator": "except: pass", "note": "x"},
         {"defect_class": "dead_code", "locator": "#", "note": "y"},
         {"defect_class": "reinvents_primitive", "locator": "z", "note": "w"}],
        changed_lines=200)
    a_clean = value.achievement(100.0, dpl, clean)
    a_dirty = value.achievement(100.0, dpl, dirty)
    assert a_clean.cleanliness_mode == "defects"
    assert abs(a_clean.cleanliness_C - 1.0) < 1e-9                          # clean = no penalty
    assert a_dirty.severe_count == 3
    assert a_dirty.cleanliness_C < a_clean.cleanliness_C                    # 3 severe bites
    assert a_dirty.achievement < a_clean.achievement


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn) and fn.__code__.co_argcount == 0:
            fn()
            print(f"ok  {name}")
    print("\nALL DEFECT TESTS PASSED")
