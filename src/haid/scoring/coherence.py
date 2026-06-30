"""Coherence of a set of placements against an independent reference order.

The reuse-vs-new-ladder gate (docs/plans/bugfix-reward.md): before trusting the existing
difficulty ladder to rank FIX SPANS, we check that its placements AGREE with an independent
difficulty signal — the same cross-method-convergence discipline that validated the difficulty
ladder and RETIRED the cleanliness one (its placement was non-monotonic: 58 ordering inversions
across 11 episodes, 0/11 coherent — scoring/defects.py header). A category error a better ladder
can't fix shows up here as inversions; a ladder that genuinely orders the quantity does not.

Pure math, no model. Given `(placed_value, reference_rank)` per subject, count concordant vs
discordant (inverted) pairs → Kendall tau-b (ties-aware) + a `coherent` verdict. The model
placements that feed it come from scoring.placement via a backend (see validate_placements).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field


def _sign(x: float) -> int:
    return (x > 0) - (x < 0)


@dataclass(frozen=True)
class CoherenceReport:
    """Concordance of placed-vs-reference orderings over all subject pairs."""
    n: int                          # subjects compared
    concordant: int                 # pairs whose placed order matches the reference order
    discordant: int                 # INVERSIONS — pairs whose placed order contradicts reference
    tied_reference: int             # pairs tied on the reference only (placed strictly ordered)
    tied_placed: int                # pairs tied on the placed value only (reference strictly ordered)
    tau_b: float                    # Kendall tau-b in [-1, 1]; nan when no strictly-ordered pairs
    coherent: bool                  # tau_b >= tau_floor AND no hard inversions among strict pairs
    tau_floor: float
    inversions: tuple = ()          # ((id_i, id_j), ...) the discordant pairs, for inspection

    def summary(self) -> str:
        tau = "n/a" if self.tau_b != self.tau_b else f"{self.tau_b:+.3f}"
        verdict = "COHERENT" if self.coherent else "INCOHERENT"
        return (f"{verdict}: tau_b={tau} over {self.n} subjects "
                f"({self.concordant} concordant / {self.discordant} inverted; "
                f"ties ref={self.tied_reference} placed={self.tied_placed})")


def coherence(items, *, tau_floor: float = 0.7) -> CoherenceReport:
    """Coherence of `items` = [(id, placed_value, reference_rank), ...].

    A pair (i, j) is CONCORDANT when the placed order agrees with the reference order,
    DISCORDANT (an inversion) when it contradicts, and tied when either side is equal. Kendall
    tau-b accounts for ties on each side separately:

        tau_b = (C - D) / sqrt( (C + D + T_placed) * (C + D + T_reference) )

    `coherent` requires tau_b >= `tau_floor` AND zero hard inversions among pairs that BOTH
    orderings rank strictly (a single strict-vs-strict contradiction is the category-error
    signature, so it vetoes coherence regardless of tau)."""
    rows = list(items)
    n = len(rows)
    C = D = T_ref = T_plc = 0
    inv: list[tuple[str, str]] = []
    for a in range(n):
        ida, pa, ra = rows[a]
        for b in range(a + 1, n):
            idb, pb, rb = rows[b]
            sr, sp = _sign(ra - rb), _sign(pa - pb)
            if sr == 0 and sp == 0:
                continue                       # tied on both — carries no ordering information
            if sr == 0:
                T_ref += 1
            elif sp == 0:
                T_plc += 1
            elif sr == sp:
                C += 1
            else:
                D += 1
                inv.append((ida, idb))
    strict = C + D
    denom = math.sqrt((strict + T_plc) * (strict + T_ref)) if strict else 0.0
    tau_b = (C - D) / denom if denom else float("nan")
    coherent = bool(strict) and D == 0 and tau_b >= tau_floor
    return CoherenceReport(n=n, concordant=C, discordant=D, tied_reference=T_ref,
                           tied_placed=T_plc, tau_b=tau_b, coherent=coherent,
                           tau_floor=tau_floor, inversions=tuple(inv))


@dataclass(frozen=True)
class Subject:
    """One thing to place + its independent reference rank (the cross-method signal)."""
    id: str
    diff: str
    reference_rank: float


def validate_placements(subjects, backend, *, axis: str = "difficulty", samples: int = 1,
                        tau_floor: float = 0.7):
    """Place every subject's diff on `axis`'s ladder, then check coherence vs its reference rank.

    Returns `(placements, report)` where `placements` is one PlacementResult per subject (same
    order) and `report` is the CoherenceReport. `backend` is any scoring.compare.Backend
    (ReplayBackend for a saved run, HarnessBackend for live). This is the reuse-vs-new-ladder
    decision: a COHERENT report ⇒ reuse the difficulty ladder for fix spans; an INCOHERENT one
    (inversions / low tau) ⇒ the ladder can't order fix difficulty and a dedicated bug-fix ladder
    is warranted (docs/plans/bugfix-reward.md)."""
    from .placement import place

    placements = [place(s.diff, axis, backend, samples=samples, subject_id=s.id)
                  for s in subjects]
    items = [(s.id, p.rung, s.reference_rank) for s, p in zip(subjects, placements)]
    return placements, coherence(items, tau_floor=tau_floor)
