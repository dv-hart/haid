"""Render the `haid metrics` inspection view (Markdown) from the JSON document.

Derived from the SAME dict that `json_out.build` produces, so the two can never drift. This
is the maintainer's eyeball / DoD-validation surface — **pure measurement**, no remedy or
"this suggests…" lines (the why/fix is the Phase-2/3 job; see metrics-output-schema.md).
Stdlib only.
"""

from __future__ import annotations


def _pct(token_rate: float) -> str:
    p = token_rate * 100
    if 0 < p < 0.1:               # keep tiny-but-nonzero rates legible (don't show "0.0%")
        return f"{p:.2g}%"
    return f"{round(p, 1)}%"


def _placement(bl: dict) -> str:
    if not bl or bl.get("percentile") is None:
        return "no baseline"
    return f"p{bl['percentile']} vs ~{_pct(bl['median'])} median ({bl['band']})"


def _verdict(r: dict) -> str:
    """Hedged one-liner for a measurement row; a 0% rate is 'none flagged', never anomalous."""
    if r["count"] == 0 or r["token_rate"] <= 0:
        return "0% — none flagged"
    return f"{_pct(r['token_rate'])} — {_placement(r['baseline'])}"


def _is_anomalous(r: dict) -> bool:
    return r["count"] > 0 and (r["baseline"].get("percentile") or 0) >= 75


def _rows(doc, scope):
    return [r for r in doc["measurements"] if r["scope"] == scope]


def render(doc: dict, top_n: int = 10) -> str:
    w = doc["window"]
    out: list[str] = []
    out.append(f"# HAID metrics — {w.get('label') or 'analysis window'}")
    out.append(f"_Generated {doc.get('generated_at') or '—'} · {w.get('n_sessions', 0)} "
               f"sessions · deterministic, model-free · the measured substrate (not the report)_")
    out.append("")

    win_rows = {r["metric"]: r for r in _rows(doc, "window")}
    defs = doc["metric_defs"]

    # --- headline: window-scope metrics, most anomalous first -----------------------------
    out.append("## Window headline (vs baseline)")
    ordered = sorted(win_rows.values(),
                     key=lambda r: (not _is_anomalous(r),
                                    -(r["baseline"].get("percentile") or 0)))
    for r in ordered:
        flag = "⚠ " if _is_anomalous(r) else "  "
        out.append(f"- {flag}**{r['metric']}** {_verdict(r)}")
    out.append("")

    # --- per-metric detail (window scope, top-N ranked instances) -------------------------
    out.append("## By metric (window scope)")
    for name, r in win_rows.items():
        insts = [i for i in doc["instances"] if i["metric"] == name and i["scope"] == "window"]
        insts.sort(key=lambda i: i["token_weight"], reverse=True)
        out.append(f"### {name} — {_verdict(r)}")
        out.append(f"_{defs[name]['rule']}_")
        if insts:
            for n, i in enumerate(insts[:top_n], 1):
                where = f" [{i['session_id']}]" if i.get("session_id") else ""
                out.append(f"  {n}. {i['detail']} (~{i['token_weight']} tok){where}")
            if len(insts) > top_n:
                out.append(f"  …showing top {top_n} of {len(insts)} (ranked by tokens)")
        else:
            out.append("  (none flagged)")
        out.append(f"  Denominator: {r['denominator']} · {r['total_tokens']} {defs[name]['token_denom_label']}")
        out.append(f"  Carve-out: {defs[name]['carve_out']}")
        out.append("")

    # --- per-session table (session scope) -----------------------------------------------
    sess_rows = _rows(doc, "session")
    if sess_rows:
        metrics_order = list(defs.keys())
        by_unit: dict = {}
        for r in sess_rows:
            by_unit.setdefault(r["unit_id"], {})[r["metric"]] = r
        out.append("## Per session (session scope)")
        out.append("| session | " + " | ".join(metrics_order) + " |")
        out.append("|" + "---|" * (len(metrics_order) + 1))
        for unit in sorted(by_unit):
            cells = []
            for name in metrics_order:
                r = by_unit[unit].get(name)
                if not r:
                    cells.append("—")
                    continue
                p = r["baseline"].get("percentile")
                cells.append(_pct(r["token_rate"]) + (f" (p{p})" if p is not None else ""))
            out.append(f"| {unit} | " + " | ".join(cells) + " |")
        out.append("")

    # --- caps -----------------------------------------------------------------------------
    caps = doc["caps"]
    out.append("## Limits & caps (nothing hidden)")
    out.append(f"- Baseline: {caps['baseline']['source']}.")
    if caps["baseline"].get("missing"):
        out.append(f"- No baseline yet for: {', '.join(caps['baseline']['missing'])} "
                   "(rate shown, placement omitted).")
    for note in caps.get("notes", []):
        out.append(f"- {note}")
    for lim in caps.get("limits", []):
        out.append(f"- {lim}")
    out.append("")
    return "\n".join(out)
