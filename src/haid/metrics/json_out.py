"""Build the `haid metrics` JSON hand-off — the Phase-1 → Phase-2/3 contract.

Implements docs/metrics-output-schema.md exactly: one rule per metric, run at `session` and
`window` scope; a `measurements` (metric × scope × unit) table; a flat, scope-tagged
`instances` list of pure pointers; and a `caps` block (no silent caps). Pure measurement —
no remedy/interpretation. Stdlib only.
"""

from __future__ import annotations

from pathlib import Path

from . import baseline, run_sessions, run_window, METRIC_NAMES, SCOPES

SCHEMA_VERSION = "1.0"

# The one detection rule per metric (applied at every scope; scope only sets memory length).
RULES = {
    "rereads": "read tokens covering content already read, with no edit since (and not the "
               "harness-required Read before an edit); scope = how far back 'already' reaches",
    "retries": "tokens of the 2nd+ attempt of a signature that already failed THE SAME WAY "
               "(matching error output), no success/change between",
    "retouched": "tokens rewriting a line the agent itself produced earlier within the memory "
                 "window",
    "unused_context": "tokens of a large read of a file never edited within the memory window",
}


def _sid(path: str) -> str:
    return Path(path).stem[:8]


def _baseline_block(metric: str, scope: str, token_rate: float) -> dict:
    pos = baseline.position(metric, scope, token_rate)
    if pos is None:
        return {"percentile": None, "note": f"no {scope}-scope baseline yet"}
    return {"percentile": pos["percentile"], "median": pos["median"], "n": pos["n"],
            "band": baseline.band(pos["percentile"]), "source": pos["source"]}


def _measure_row(metric: str, scope: str, unit_id: str, m) -> dict:
    return {"metric": metric, "scope": scope, "unit_id": unit_id,
            "count": m.count, "denominator": m.denominator,
            "token_weight": m.token_weight, "total_tokens": m.total_tokens,
            "rate": round(m.rate, 4), "token_rate": round(m.token_rate, 4),
            "baseline": _baseline_block(metric, scope, m.token_rate)}


def _norm_refs(refs: dict, call_index: dict) -> dict:
    """Map a metric's raw refs to the schema refs: file_id, session_ids, calls[], line_span, …"""
    out: dict = {}
    if refs.get("file"):
        out["file_id"] = refs["file"]
    call_ids = [refs["call"]] if refs.get("call") else list(refs.get("calls", []))
    calls, sids = [], set()
    for cid in call_ids:
        info = call_index.get(cid, {})
        sid = info.get("session_id")
        if sid:
            sids.add(sid)
        calls.append({"tool_use_id": cid, "turn_id": info.get("turn_id"), "session_id": sid})
    if calls:
        out["calls"] = calls
    if sids:
        out["session_ids"] = sorted(sids)
        if len(sids) > 1:
            out["n_sessions"] = len(sids)
    if refs.get("span"):
        out["line_span"] = refs["span"]
    if refs.get("sample_lines"):
        out["sample_lines"] = refs["sample_lines"]
    if refs.get("signature"):
        out["signature"] = refs["signature"]
    return out


def _instances(metric: str, scope: str, m, call_index: dict) -> list:
    """Ranked, scope-tagged instances for one (metric, scope)."""
    ranked = sorted(m.instances, key=lambda i: i.token_weight, reverse=True)
    out = []
    for rank, inst in enumerate(ranked, 1):
        sid = inst.timeline if inst.timeline not in ("window", "") else None
        out.append({
            "id": f"{metric}/{scope}/{rank}",
            "metric": metric, "scope": scope,
            "session_id": sid, "timeline": inst.timeline,
            "detail": inst.detail, "token_weight": inst.token_weight,
            "refs": _norm_refs(inst.refs, call_index),
        })
    return out


def _call_index(view) -> dict:
    """tool_use_id -> {turn_id, session_id} from the stream's ToolCall objects."""
    idx = {}
    for sid, tc in view.active_stream:
        idx[tc.id] = {"turn_id": tc.turn_id, "session_id": sid}
    for label, tcs in view.timelines:
        sid = label.split(":", 1)[0]
        for tc in tcs:
            idx.setdefault(tc.id, {"turn_id": tc.turn_id, "session_id": sid})
    return idx


def build(view, sessions=None, *, project_path=None, days=None,
          haid_version="0.1.0", generated_at="") -> dict:
    """Assemble the full metrics JSON document from a WindowView (+ optional Session list)."""
    win = run_window(view)
    per_sess = run_sessions(view)
    call_index = _call_index(view)

    # --- window provenance ---
    sess_meta = []
    if sessions:
        tl_by_sid: dict[str, list] = {}
        for label, _ in view.timelines:
            sid = label.split(":", 1)[0]
            tl_by_sid.setdefault(sid, []).append(label)
        for s in sessions:
            sid = _sid(s.path)
            ts = [r.timestamp for r in s.parse.records if r.timestamp]
            sess_meta.append({"id": sid, "path": s.path,
                              "first_ts": min(ts) if ts else None,
                              "timelines": tl_by_sid.get(sid, [])})

    window = {"label": view.label, "project_path": project_path, "days": days,
              "n_sessions": view.n_sessions, "sessions": sess_meta}

    # --- metric_defs (one rule each; carve_out/notes/denom from the result) ---
    metric_defs = {}
    for name in METRIC_NAMES:
        m = win[name]
        metric_defs[name] = {"token_denom_label": m.token_denom_label,
                             "rule": RULES.get(name, ""),
                             "carve_out": m.carve_out, "notes": m.notes}

    # --- measurements (metric × scope × unit) + instances (per scope) ---
    measurements, instances = [], []
    for name in METRIC_NAMES:
        measurements.append(_measure_row(name, "window", "window", win[name]))
        instances += _instances(name, "window", win[name], call_index)
    for sid, ms in per_sess.items():
        for name in METRIC_NAMES:
            measurements.append(_measure_row(name, "session", sid, ms[name]))
            instances += _instances(name, "session", ms[name], call_index)

    # --- caps (no silent caps) ---
    have, missing = [], []
    for name in METRIC_NAMES:
        for scope in SCOPES:
            (have if baseline.position(name, scope, 0.0) else missing).append(f"{name}@{scope}")
    caps = {
        "notes": list(view.notes),
        "baseline": {"have": have, "missing": missing,
                     "source": "single-author bootstrap (placeholder until community benchmark)"},
        "limits": [
            "Line lineage is within-session only (cross-session lineage is Phase 4).",
            "Token weights are per-artifact byte/4 counts (right granularity for same-kind "
            "ratios); cost.py's normalized tokens are per-message and enter at the waste→value "
            "reconciliation, not here.",
            "Window scope sees cross-session repeats a session forgets — placed against a "
            "window-scope baseline, not comparable to session rates.",
        ],
        "instances_truncated": False,
    }

    return {"schema_version": SCHEMA_VERSION, "kind": "metrics",
            "haid_version": haid_version, "generated_at": generated_at,
            "window": window, "scopes": list(SCOPES),
            "metric_defs": metric_defs, "measurements": measurements,
            "instances": instances, "caps": caps}
