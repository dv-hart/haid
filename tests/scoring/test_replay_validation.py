"""Replay validation: prove the runtime scorer reproduces the calibration result.

No model is involved — ReplayBackend answers from saved verdicts, so this pins the
product placement code (haid.scoring.placement + compare + anchors) to the validated
experiment. If these pass, only the live model backend is unproven.

  1. Difficulty headline: place all 46 holdouts via the saved Haiku placements →
     Spearman(rung, Opus full-sort score) must reproduce 0.866 (calibration/ladder.py).
  2. Anchor self-placement (both axes): place each anchor against the rest via the dense
     all-pairs verdicts → placement rung must be monotonic with the locked ladder rung.

Reads calibration artifacts from out/ (dev fixtures, gitignored). Run either way:
  pytest tests/scoring/ -q       (from repo root, with src on path)
  PYTHONPATH=src python tests/scoring/test_replay_validation.py
"""

from __future__ import annotations

import json
import os
import sys

# allow `python tests/scoring/test_replay_validation.py` from repo root
_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(_ROOT, "src"))   # haid package
sys.path.insert(0, _ROOT)                          # calibration package (dev-only)

from calibration.bt_h5 import spearman          # dev-only: validating against calibration
from haid.scoring import placement
from haid.scoring.anchors import load_ladder
from haid.scoring.compare import ReplayBackend

BLINDED = "out/blinded"
DIFFICULTY_HEADLINE = 0.866


def _diff(unit_id: str) -> str:
    return open(os.path.join(BLINDED, f"{unit_id}.diff"), encoding="utf-8").read()


def test_difficulty_haiku_placement_reproduces_0866():
    backend = ReplayBackend.from_files("out/haiku_placements.json")
    holdouts = json.load(open("out/ladder_anchors.json", encoding="utf-8"))["holdouts"]
    rungs, opus = [], []
    for h in holdouts:
        res = placement.place(_diff(h["id"]), "difficulty", backend, subject_id=h["id"])
        rungs.append(res.rung)
        opus.append(h["score"])
    rho = spearman(rungs, opus)
    print(f"[difficulty] Haiku-placement vs Opus full-sort: rho={rho:+.3f} "
          f"(n={len(rungs)}, baseline {DIFFICULTY_HEADLINE})")
    assert abs(rho - DIFFICULTY_HEADLINE) < 0.005, rho


def _self_placement_rho(axis: str, dense_verdicts: str) -> float:
    backend = ReplayBackend.from_files(dense_verdicts)
    ladder = load_ladder(axis)
    placed, true = [], []
    for a in ladder.anchors:
        res = placement.place(a.diff, axis, backend, subject_id=a.id)
        placed.append(res.rung)
        true.append(a.rung)
    rho = spearman(placed, true)
    print(f"[{axis}] anchor self-placement vs locked rung: rho={rho:+.3f} "
          f"(n={len(placed)})")
    return rho


def test_difficulty_anchor_self_placement_monotonic():
    rho = _self_placement_rho("difficulty", "out/anchor_dense_verdicts.json")
    assert rho > 0.95, rho


def test_cleanliness_anchor_self_placement_monotonic():
    rho = _self_placement_rho("cleanliness", "out/cleanliness_anchor_dense_verdicts.json")
    assert rho > 0.95, rho


if __name__ == "__main__":
    test_difficulty_haiku_placement_reproduces_0866()
    test_difficulty_anchor_self_placement_monotonic()
    test_cleanliness_anchor_self_placement_monotonic()
    print("\nALL REPLAY VALIDATIONS PASSED")
