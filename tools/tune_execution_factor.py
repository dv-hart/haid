"""Tune the cleanliness execution_factor knobs (K_DEFECT, LOC_FLOOR, EXEC_FLOOR).

Two modes:

  python tools/tune_execution_factor.py
      Synthetic sweep: print the execution_factor for a grid of (severe_count, changed_lines)
      archetypes under several candidate parameter sets, so you can pick knobs against intent.

  python tools/tune_execution_factor.py <counts.json>
      Empirical: <counts.json> is {"episodes": [{"id","severe","minor","other","changed_lines",
      "old_percentile"?}, ...]} produced by running the defect detector over real diffs. Prints
      the resulting factor per episode + the distribution, for each candidate parameter set.

The point of the synthetic mode is to anchor the knobs to stated intent BEFORE we trust the
detector; the empirical mode checks the chosen knobs give a sane spread on real work.
"""

from __future__ import annotations

import json
import os
import statistics
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(_ROOT, "src"))

from haid.scoring import value

# Candidate parameter sets to compare. (label, k, loc_floor, floor)
CANDIDATES = [
    ("gentle  k=4 ", 4.0, 40, 0.6),
    ("default k=8 ", 8.0, 40, 0.6),
    ("steep   k=12", 12.0, 40, 0.6),
    ("steep   k=16", 16.0, 40, 0.6),
    ("k=8 floor.4 ", 8.0, 40, 0.4),
]

# Archetype scenarios with the intended factor band (what a senior would expect).
# (label, severe_count, changed_lines, intent)
SCENARIOS = [
    ("clean, typical diff",          0, 150, "= 1.0 (no penalty)"),
    ("1 lapse, large diff (300)",    1, 300, "mild nick (~0.95+)"),
    ("1 lapse, typical diff (150)",  1, 150, "small (~0.9)"),
    ("1 lapse, small diff (25)",     1, 25,  "small, NOT floored (LOC_FLOOR)"),
    ("2 defects, medium (200)",      2, 200, "noticeable (~0.85)"),
    ("3 defects, medium (150)",      3, 150, "clear discount"),
    ("riddled: 1/20 lines (200)",   10, 200, "near floor"),
    ("all slop: 1/10 lines (100)",  10, 100, "at floor"),
]


def _fmt(f: float) -> str:
    return f"{f:5.3f}"


def synthetic() -> None:
    print("=== synthetic anchor-intent sweep ===\n")
    header = f"{'scenario':<32}" + "".join(f"{lab:>13}" for lab, *_ in CANDIDATES)
    print(header)
    print("-" * len(header))
    for slab, sev, loc, intent in SCENARIOS:
        row = f"{slab:<32}"
        for _, k, lf, fl in CANDIDATES:
            row += f"{_fmt(value.execution_factor(sev, loc, k=k, loc_floor=lf, floor=fl)):>13}"
        print(row)
        print(f"{'  intent: ' + intent:<32}")
    print()


def empirical(path: str) -> None:
    data = json.load(open(path, encoding="utf-8"))
    eps = data["episodes"]
    print(f"=== empirical: {len(eps)} episodes from {path} ===\n")
    for _, k, lf, fl in CANDIDATES:
        factors = []
        print(f"--- k={k} loc_floor={lf} floor={fl} ---")
        print(f"  {'id':<6}{'severe':>7}{'minor':>6}{'chgLOC':>8}{'factor':>8}"
              f"{'oldPct':>8}")
        for e in eps:
            f = value.execution_factor(e["severe"], e["changed_lines"],
                                       k=k, loc_floor=lf, floor=fl)
            factors.append(f)
            op = e.get("old_percentile")
            ops = f"{op:.2f}" if isinstance(op, (int, float)) else "  - "
            print(f"  {e['id']:<6}{e['severe']:>7}{e.get('minor',0):>6}"
                  f"{e['changed_lines']:>8}{f:>8.3f}{ops:>8}")
        print(f"  factor: min={min(factors):.3f} median={statistics.median(factors):.3f} "
              f"max={max(factors):.3f} spread={max(factors)-min(factors):.3f}\n")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        empirical(sys.argv[1])
    else:
        synthetic()
