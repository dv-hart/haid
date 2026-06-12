"""The why-pass — per-anchor investigation agents over the metrics substrate.

Step 5 of the agent/why-pass (plans/agent-analysis.md §2-§4): the metrics JSON's ranked
instances seed tool-using analysis agents that explain WHY each flagged pattern happened
(with a mandatory audit of the anchor itself), returning evidence-grounded notes +
non-exclusive flags + a hedged remedy. Concept validated live 2026-06-09 on c7-connector
and boxBot before this module was written; the prompts in prompts.py are the validated
wording.

    doc = metrics json_out.build(...)            # the cheap, deterministic substrate
    anchors = select_anchors(doc, top=6)         # triage budgets the expensive layer
    notes = investigate_window(doc, anchors, backend,
                               transcript_dir=..., project_path=...)
"""

from __future__ import annotations

from .anchors import (DEFAULT_MIN_TOKENS, DEFAULT_PER_METRIC_CAP, DEFAULT_TOP,
                      WhyAnchor, select_anchors)
from .investigate import (HarnessBackend, Note, PendingInvestigations, ReplayBackend,
                          WhyBackend, validate_note)
from .prompts import FLAGS, NOTE_SCHEMA, RECOMMENDED_MODEL, build_anchor_prompt

__all__ = [
    "WhyAnchor", "select_anchors", "DEFAULT_TOP", "DEFAULT_PER_METRIC_CAP",
    "DEFAULT_MIN_TOKENS", "WhyBackend", "ReplayBackend", "HarnessBackend",
    "PendingInvestigations", "Note", "validate_note", "FLAGS", "NOTE_SCHEMA",
    "RECOMMENDED_MODEL", "build_anchor_prompt", "investigate_window", "to_json",
    "render",
]


def investigate_window(doc: dict, anchors: list[WhyAnchor], backend: WhyBackend, *,
                       transcript_dir: str, project_path: str) -> list[tuple[WhyAnchor, Note]]:
    """Run the investigation backend over selected anchors → [(anchor, validated note)]."""
    all_sids = [s["id"] for s in doc.get("window", {}).get("sessions", [])]
    notes = backend.investigate_batch(anchors, transcript_dir=transcript_dir,
                                      project_path=project_path,
                                      all_session_ids=all_sids)
    return list(zip(anchors, notes))


def to_json(results: list[tuple[WhyAnchor, Note]], *, label: str = "") -> dict:
    return {"kind": "why_notes", "label": label,
            "notes": [{"anchor_id": a.id, "metric": a.metric, "detail": a.detail,
                       "token_weight": a.token_weight, **n} for a, n in results]}


def render(results: list[tuple[WhyAnchor, Note]], *, label: str = "") -> str:
    """Human-readable notes (the report compositor consumes to_json instead)."""
    lines = [f"# why-pass notes — {label}", ""]
    for a, n in results:
        lines.append(f"## {a.id} (~{a.token_weight} tok) — {a.detail}")
        lines.append(f"   audit: {n['anchor_audit']}")
        lines.append(f"   why:   {n['note']}")
        if n["flags"]:
            lines.append(f"   flags: {', '.join(n['flags'])}")
        lines.append(f"   remedy: {n['remedy']}")
        avoid = n["estimated_avoidable_tokens"]
        if avoid is not None:
            lines.append(f"   avoidable: ~{avoid} tok ({n['avoidable_basis']})")
        lines.append(f"   confidence: {n['confidence']}")
        lines.append("")
    return "\n".join(lines)
