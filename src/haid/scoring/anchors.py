"""Load the locked anchor ladders + their reference diff texts.

Each axis has a fixed ladder of reference diffs whose relative order we trust (the dense
all-pairs calibration result). A session diff is scored by placing it against these.
The ladders + their diff texts are shipped as package data (src/haid/data/), so scoring
needs no access to the calibration `out/` tree at runtime.

  difficulty  → data/difficulty_anchors.json   (rung 0 easiest .. 8 hardest)
  cleanliness → data/cleanliness_anchors.json  (rung 0 least clean .. 10 cleanest)

The anchor diffs are fixed, so the live backend can prompt-cache them.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache
from importlib.resources import files

AXES = ("difficulty", "cleanliness")


@dataclass(frozen=True)
class Anchor:
    id: str
    rung: int
    score: float
    diff: str                       # reference diff text (already blinded + code-first)
    extra: dict = field(default_factory=dict)   # axis-specific (level, churn, kind)


@dataclass(frozen=True)
class Ladder:
    axis: str
    orientation: str
    method: str
    anchors: tuple[Anchor, ...]     # ordered by rung, ascending

    @property
    def n_rungs(self) -> int:
        return len(self.anchors)

    def ascending_means(self) -> str:
        return self.orientation


def _data_dir():
    return files("haid") / "data"


def _read_diff(anchor_id: str) -> str:
    return (_data_dir() / "anchor_diffs" / f"{anchor_id}.diff").read_text(encoding="utf-8")


@lru_cache(maxsize=None)
def load_ladder(axis: str) -> Ladder:
    if axis not in AXES:
        raise ValueError(f"unknown axis {axis!r}; expected one of {AXES}")
    raw = json.loads((_data_dir() / f"{axis}_anchors.json").read_text(encoding="utf-8"))
    anchors = []
    for a in raw["anchors"]:
        extra = {k: v for k, v in a.items() if k not in ("id", "rung", "score")}
        anchors.append(Anchor(id=a["id"], rung=a["rung"], score=a["score"],
                              diff=_read_diff(a["id"]), extra=extra))
    anchors.sort(key=lambda x: x.rung)
    return Ladder(axis=axis,
                  orientation=raw.get("orientation", ""),
                  method=raw.get("method", ""),
                  anchors=tuple(anchors))
