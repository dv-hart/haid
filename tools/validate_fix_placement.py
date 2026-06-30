"""Settle reuse-vs-new-ladder: do FIX-SPAN diffs place coherently on the difficulty ladder?

The bug-fix reward (docs/plans/bugfix-reward.md) reuses the existing difficulty ladder to score a
fix's difficulty — IF placement is coherent against an independent reference order. This harness
runs that check (scoring.coherence): place each fix-span diff on the ladder, compare to a provided
reference rank, and print the Kendall tau-b + inversions verdict. A COHERENT result ⇒ reuse the
ladder; INCOHERENT (inversions / low tau) ⇒ build a dedicated bug-fix ladder.

Input JSON: {"subjects": [{"id": "...", "diff": "<unified diff>", "reference_rank": <number>}, ...]}
The `reference_rank` is the INDEPENDENT difficulty signal (an absolute tiering pass, human labels,
or known ground truth) — this harness consumes it, it does not invent it (cross-method convergence,
docs/difficulty-ladder.md).

Modes:
  python tools/validate_fix_placement.py subjects.json --placed placed.json
      No model: `placed.json` = {"subjects": [{"id","placed_rung","reference_rank"}, ...]} from a
      completed run; print coherence directly. (subjects.json optional here.)

  python tools/validate_fix_placement.py subjects.json --verdicts verdicts.json
      Replay: place via saved pairwise verdicts (ReplayBackend), then print coherence. Deterministic.

  python tools/validate_fix_placement.py subjects.json --emit JOBDIR
      Live: write one placement manifest per subject under JOBDIR (HarnessBackend file-handoff).
      Run the comparison subagents, drop each `<id>.verdicts.json` beside its manifest, then
      re-run with --emit to fold them in and print coherence.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(_ROOT, "src"))

from haid.scoring import coherence as coh
from haid.scoring.compare import HarnessBackend, PendingComparisons, ReplayBackend
from haid.scoring.placement import place


def _load_subjects(path: str) -> list[coh.Subject]:
    rows = json.load(open(path, encoding="utf-8"))["subjects"]
    return [coh.Subject(id=r["id"], diff=r.get("diff", ""),
                        reference_rank=float(r["reference_rank"])) for r in rows]


def _report(report: coh.CoherenceReport) -> int:
    print(report.summary())
    if report.inversions:
        print("  inversions (placed order contradicts the reference):")
        for a, b in report.inversions:
            print(f"    - {a} <-> {b}")
    print("\nVERDICT:", "reuse the difficulty ladder for fix spans." if report.coherent
          else "ladder placement is INCOHERENT for fixes -- build a bug-fix ladder.")
    return 0 if report.coherent else 1


def from_placed(path: str) -> int:
    rows = json.load(open(path, encoding="utf-8"))["subjects"]
    items = [(r["id"], float(r["placed_rung"]), float(r["reference_rank"])) for r in rows]
    return _report(coh.coherence(items))


def from_verdicts(subjects_path: str, verdicts_path: str) -> int:
    subjects = _load_subjects(subjects_path)
    backend = ReplayBackend.from_files(verdicts_path)
    _, report = coh.validate_placements(subjects, backend)
    return _report(report)


def emit_or_fold(subjects_path: str, job_dir: str) -> int:
    """Place each subject via file-handoff; collect any pending manifests, else print coherence."""
    subjects = _load_subjects(subjects_path)
    os.makedirs(job_dir, exist_ok=True)
    pending, placements = [], []
    for s in subjects:
        backend = HarnessBackend(job_dir, job_name=f"fixplace_{s.id}")
        try:
            placements.append(place(s.diff, "difficulty", backend, subject_id=s.id))
        except PendingComparisons as p:
            pending.append(p.manifest_path)
    if pending:
        print(f"{len(pending)} placement manifest(s) pending — run the comparison subagents, "
              "write each <name>.verdicts.json beside its manifest, then re-run with --emit:")
        for m in pending:
            print(f"    - {m}")
        return 2
    items = [(s.id, p.rung, s.reference_rank) for s, p in zip(subjects, placements)]
    return _report(coh.coherence(items))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("subjects", nargs="?", help="subjects JSON (id, diff, reference_rank)")
    ap.add_argument("--placed", help="precomputed placements JSON (no model)")
    ap.add_argument("--verdicts", help="saved pairwise verdicts for ReplayBackend")
    ap.add_argument("--emit", metavar="JOBDIR", help="live placement via file-handoff under JOBDIR")
    args = ap.parse_args(argv)

    if args.placed:
        return from_placed(args.placed)
    if not args.subjects:
        ap.error("need a subjects JSON (or --placed)")
    if args.verdicts:
        return from_verdicts(args.subjects, args.verdicts)
    if args.emit:
        return emit_or_fold(args.subjects, args.emit)
    ap.error("choose a mode: --placed, --verdicts, or --emit")


if __name__ == "__main__":
    raise SystemExit(main())
