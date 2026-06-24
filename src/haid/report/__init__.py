"""Report layer — what + why + scores -> deterministic breakdowns, a composed coaching
report (opus-tier agent, manifest pattern), and the benchmark submission payload.

  treatments.py — the symptom+why -> remedy catalog (package data, cited, versioned)
  compose.py    — deterministic findings/digest + the composition model boundary
  benchmark.py  — the ADR-0005 v1 summary-only submission payload (the leaderboard row)
  submit.py     — opt-in: open the validated PR that adds your row to the community board
  rank.py       — read-only: where your row lands against the community distribution
"""

from . import benchmark, rank, submit
from .compose import (COMPOSITION_SCHEMA, ComposeBackend, Finding, HarnessBackend,
                      PendingComposition, RECOMMENDED_MODEL, ReplayBackend,
                      build_findings, digest_json, render_community, render_digest,
                      render_report, validate_composition)
from .treatments import (Catalog, MATURITIES, SYMPTOM_KEYS, Treatment, load_catalog)

__all__ = [
    "Catalog", "Treatment", "load_catalog", "SYMPTOM_KEYS", "MATURITIES",
    "Finding", "build_findings", "digest_json", "render_digest", "render_report",
    "render_community",
    "ComposeBackend", "ReplayBackend", "HarnessBackend", "PendingComposition",
    "COMPOSITION_SCHEMA", "RECOMMENDED_MODEL", "validate_composition",
    "benchmark", "submit", "rank",
]
