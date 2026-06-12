"""Tolerant line-by-line JSONL parsing of a transcript file.

Requirements (from plans/mvp.md §1, plans/phase1-build.md Step 1):
  - Read line-by-line; tolerate a partial trailing line (an actively-written session).
  - Validate each record and LOUDLY flag unknown shapes — never silently drop. Drift in
    this undocumented format is expected, so we count and report it instead of crashing.
  - Keep uuid-less metadata records (titles/mode/last-prompt/queue) — the forest layer
    needs last-prompt for the active-leaf pointer.

Returns a ParseResult carrying the records plus a drift report (unknown types, lines that
failed to parse, partial trailing line). Stdlib only.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field

from . import records as rec


@dataclass
class ParseResult:
    path: str
    records: list[rec.Record] = field(default_factory=list)
    n_lines: int = 0
    n_parse_errors: int = 0          # lines that were non-empty but not valid JSON
    had_partial_tail: bool = False    # last line failed to parse → likely mid-write
    unknown_types: Counter = field(default_factory=Counter)  # type -> count for drift

    @property
    def threaded(self) -> list[rec.Record]:
        return [r for r in self.records if r.is_threaded()]

    def warnings(self) -> list[str]:
        w: list[str] = []
        if self.unknown_types:
            shapes = ", ".join(f"{t}×{n}" for t, n in self.unknown_types.most_common())
            w.append(f"{sum(self.unknown_types.values())} record(s) of unknown type "
                     f"({shapes}) — schema drift, surfaced not dropped")
        if self.n_parse_errors and not self.had_partial_tail:
            w.append(f"{self.n_parse_errors} line(s) failed to parse as JSON")
        if self.had_partial_tail:
            w.append("trailing line was incomplete (session likely still being written)")
        return w


def parse_file(path: str) -> ParseResult:
    """Parse one `<session-uuid>.jsonl` into records, tolerant of a partial last line."""
    res = ParseResult(path=str(path))
    with open(path, encoding="utf-8") as fh:
        lines = fh.readlines()
    for i, line in enumerate(lines):
        s = line.strip()
        if not s:
            continue
        res.n_lines += 1
        try:
            obj = json.loads(s)
        except json.JSONDecodeError:
            res.n_parse_errors += 1
            # A failure on the very last line is the expected active-session case.
            if i == len(lines) - 1 and not line.endswith("\n"):
                res.had_partial_tail = True
            continue
        if not isinstance(obj, dict):
            res.n_parse_errors += 1
            continue
        if not rec.is_known_shape(obj):
            res.unknown_types[obj.get("type", "~missing")] += 1
            # Still keep it — unknown shapes are reported, never dropped.
        res.records.append(rec.from_dict(obj))
    return res
