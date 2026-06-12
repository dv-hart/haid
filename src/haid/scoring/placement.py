"""Place a session diff on an axis's reference ladder → its relative position.

The runtime difficulty/cleanliness score. Promotes the validated placement logic from
calibration/ladder.py into a reusable product function: compare the diff against each
anchor, and the diff's RUNG = how many anchors it is judged MORE <axis> than. That rung
(a relative position, never an absolute SEH number) IS the score; a coarse tier label may
be rendered for readability but never carries the value.

The model judgment is delegated to the injected `backend` (ReplayBackend for validation,
HarnessBackend for the live host-agent path). This module is pure orchestration + math.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .anchors import load_ladder
from .compare import Backend, CompareItem

# Optional coarse difficulty tier rendering (docs/difficulty-ladder.md). NOT the score —
# tiering leaked size; this is a readability label only. Keyed by rung of the 9-rung ladder.
_DIFFICULTY_TIER = {0: "T0 trivial", 1: "T1 junior", 2: "T2 mid", 3: "T2 mid",
                    4: "T2 mid", 5: "T2 mid", 6: "T3 senior", 7: "T3 senior",
                    8: "T4 expert", 9: "T4 expert"}


@dataclass(frozen=True)
class PlacementResult:
    axis: str
    rung: float                  # anchors beaten (averaged over samples); the score
    seen: int                    # anchors actually compared (excludes self if subject is one)
    n_rungs: int                 # anchors in the ladder
    samples: int
    per_anchor: list = field(default_factory=list)   # [(anchor_id, winner)] last sample
    subject_id: str | None = None

    @property
    def percentile(self) -> float:
        """Relative position in [0, 1] — rung normalized by anchors compared."""
        return self.rung / self.seen if self.seen else float("nan")

    def tier_label(self) -> str | None:
        """Coarse, non-load-bearing rendering (difficulty only)."""
        if self.axis != "difficulty":
            return None
        return _DIFFICULTY_TIER.get(round(self.rung))


def place(diff: str, axis: str, backend: Backend, *, samples: int = 1,
          subject_id: str | None = None) -> PlacementResult:
    """Score `diff` on `axis` by placement against the locked ladder.

    `samples` > 1 averages repeated placements (a live-model variance mitigation; replay
    is deterministic). If the subject is itself a ladder anchor (`subject_id` matches), it
    is excluded from its own comparison set.
    """
    if samples < 1:
        raise ValueError("samples must be >= 1")
    ladder = load_ladder(axis)
    anchors = [CompareItem(diff=a.diff, id=a.id) for a in ladder.anchors
               if a.id != subject_id]
    subject = CompareItem(diff=diff, id=subject_id)

    total_beats = 0.0
    last_winners: list[str] = []
    for _ in range(samples):
        winners = backend.compare_batch(subject, anchors, axis)
        total_beats += sum(1 for w in winners if w == "subject")
        last_winners = winners

    per_anchor = [(a.id, w) for a, w in zip(anchors, last_winners)]
    return PlacementResult(
        axis=axis,
        rung=total_beats / samples,
        seen=len(anchors),
        n_rungs=ladder.n_rungs,
        samples=samples,
        per_anchor=per_anchor,
        subject_id=subject_id,
    )
