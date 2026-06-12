"""Volume — deterministic weighted surviving-LOC of a session diff (no model).

The size term of achievement = f(volume, difficulty, cleanliness). Kept ORTHOGONAL to
difficulty (difficulty's pairwise oracle is size-decoupled; we measure size separately so
combining them doesn't double-count).

What it measures: the NET artifact in the final diff — added lines that survived to the
end, weighted by file kind (hand logic > config > tests > docs; generated/lockfiles ~0).
We do NOT try to discount within-session churn (written-then-deleted lines): rewrites are
paid for on the COST side via token counts, so volume stays a clean property of the final
artifact.

No Halstead (dropped — language-specific tokenization, low marginal value). Structural
counts are light regex heuristics, hedged.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..diffio import FileDiff, parse_diff
from ..filekind import KIND_WEIGHT, file_kind

# light, language-agnostic "a function/def was added" heuristic (hedged, not a parser)
_FUNC_RE = re.compile(
    r"\b(?:def|fn|func|function)\b"            # py / rust / go / js
    r"|=>\s*\{"                                # js/ts arrow body
    r"|\b\w[\w:<>]*\s+\w+\s*\([^)]*\)\s*\{",  # c/java/go-style signature
)


@dataclass(frozen=True)
class VolumeResult:
    weighted_loc: float                 # the volume score (Σ added × kind weight)
    raw_added: int                      # total added lines (all kinds)
    raw_removed: int                    # total removed lines (informational)
    by_kind: dict = field(default_factory=dict)        # kind -> {added, weighted}
    files_changed: int = 0
    functions_added: int = 0            # heuristic
    tests_touched: int = 0              # files classified as tests with changes

    def summary(self) -> str:
        parts = [f"{k}:{v['added']}(*{KIND_WEIGHT[k]:g}={v['weighted']:.0f})"
                 for k, v in sorted(self.by_kind.items())]
        return (f"weighted_loc={self.weighted_loc:.1f}  raw +{self.raw_added}/"
                f"-{self.raw_removed}  files={self.files_changed}  "
                f"funcs~{self.functions_added}  tests={self.tests_touched}\n  "
                + "  ".join(parts))


def _count_functions(fd: FileDiff) -> int:
    return sum(1 for line in fd.added if _FUNC_RE.search(line))


def measure(diff_text: str) -> VolumeResult:
    files = parse_diff(diff_text)
    by_kind: dict[str, dict[str, float]] = {}
    raw_added = raw_removed = funcs = tests = 0
    for fd in files:
        kind = file_kind(fd.path)
        slot = by_kind.setdefault(kind, {"added": 0, "weighted": 0.0})
        slot["added"] += fd.n_added
        slot["weighted"] += fd.n_added * KIND_WEIGHT[kind]
        raw_added += fd.n_added
        raw_removed += fd.n_removed
        if kind == "test":
            tests += 1
        else:
            funcs += _count_functions(fd)
    weighted = sum(s["weighted"] for s in by_kind.values())
    return VolumeResult(
        weighted_loc=weighted,
        raw_added=raw_added,
        raw_removed=raw_removed,
        by_kind=by_kind,
        files_changed=len(files),
        functions_added=funcs,
        tests_touched=tests,
    )


def measure_file(path: str) -> VolumeResult:
    return measure(open(path, encoding="utf-8").read())
