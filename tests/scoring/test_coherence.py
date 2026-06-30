"""Coherence check — placed-vs-reference ordering agreement (the reuse-vs-new-ladder gate).

Pure math (Kendall tau-b + inversion count), plus a wiring test of validate_placements over a
stub backend. Run: PYTHONPATH=src python -m pytest tests/scoring/test_coherence.py -q
"""

from __future__ import annotations

import math
import os
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(_ROOT, "src"))

from haid.scoring.coherence import Subject, coherence, validate_placements
from haid.scoring.compare import Backend


# ---------------------------------------------------------------- coherence() math
def _items(pairs):
    """pairs = [(placed, reference)] -> [(id, placed, reference)] with synthetic ids."""
    return [(f"s{i}", p, r) for i, (p, r) in enumerate(pairs)]


def test_perfect_agreement_is_coherent():
    rep = coherence(_items([(0, 0), (1, 1), (2, 2), (3, 3)]))
    assert rep.discordant == 0
    assert abs(rep.tau_b - 1.0) < 1e-9
    assert rep.coherent


def test_perfect_inversion_is_incoherent():
    rep = coherence(_items([(3, 0), (2, 1), (1, 2), (0, 3)]))
    assert rep.concordant == 0
    assert rep.discordant == 6                       # all 4-choose-2 pairs inverted
    assert abs(rep.tau_b + 1.0) < 1e-9
    assert not rep.coherent


def test_one_swap_lowers_tau_and_vetoes_coherence():
    # placed order swaps the top two relative to reference -> a single strict inversion.
    rep = coherence(_items([(0, 0), (1, 1), (3, 2), (2, 3)]))
    assert rep.discordant == 1
    assert 0.0 < rep.tau_b < 1.0
    assert not rep.coherent                          # one hard inversion vetoes, even at high tau
    assert rep.inversions == (("s2", "s3"),)


def test_reference_ties_use_tau_b_denominator():
    # two subjects tied on reference but ordered on placed -> a tied_reference pair, not an inversion.
    rep = coherence(_items([(0, 0), (1, 1), (2, 1)]))
    assert rep.discordant == 0
    assert rep.tied_reference == 1
    # tau_b denominator shrinks one factor by the tie; still positive, still no inversion.
    assert rep.tau_b > 0 and rep.coherent


def test_high_tau_but_an_inversion_is_not_coherent():
    # many concordant pairs, one strict contradiction: tau clears the floor but the veto holds.
    pairs = [(i, i) for i in range(10)]
    pairs[0], pairs[1] = (1, 0), (0, 1)              # swap the bottom two
    rep = coherence(_items(pairs))
    assert rep.discordant == 1
    assert rep.tau_b > 0.7                            # tau alone would pass
    assert not rep.coherent                           # but the inversion vetoes


def test_empty_and_singleton_are_nan_not_crash():
    assert coherence([]).tau_b != coherence([]).tau_b          # nan
    assert not coherence([]).coherent
    assert coherence(_items([(1, 1)])).tau_b != coherence(_items([(1, 1)])).tau_b


# ---------------------------------------------------------------- validate_placements wiring
class _StubBackend(Backend):
    """A deterministic backend: subject `id` beats its first `beats[id]` anchors, loses the rest."""

    def __init__(self, beats: dict):
        self.beats = beats

    def compare_batch(self, subject, anchors, axis):
        k = self.beats[subject.id]
        return ["subject" if i < k else "anchor" for i in range(len(anchors))]


def test_validate_placements_reuse_when_placements_track_reference():
    # reference ranks 0<1<2 and the ladder beats-counts agree (2<5<8) -> coherent -> reuse ladder.
    subs = [Subject("easy", "d", 0), Subject("mid", "d", 1), Subject("hard", "d", 2)]
    backend = _StubBackend({"easy": 2, "mid": 5, "hard": 8})
    placements, rep = validate_placements(subs, backend)
    assert [round(p.rung) for p in placements] == [2, 5, 8]
    assert rep.coherent


def test_validate_placements_flags_a_ladder_that_inverts_fixes():
    # the "hard" fix (reference 2) places LOWEST on the ladder (beats only 1) — a real inversion,
    # the signal that the ladder can't order fix difficulty and a bug-fix ladder is warranted.
    subs = [Subject("easy", "d", 0), Subject("mid", "d", 1), Subject("hard", "d", 2)]
    backend = _StubBackend({"easy": 6, "mid": 4, "hard": 1})
    _, rep = validate_placements(subs, backend)
    assert rep.discordant >= 1
    assert not rep.coherent


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
