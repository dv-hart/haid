"""Assemble the `window.HAID_DATA` bundle the visualizer consumes.

Promoted from the prototype `scripts/viz_assemble.py`, with its two worst hacks removed:
the hardcoded session-stem list and the file-overlap `group_episodes()` stand-in. Episodes
now come from the REAL pipeline output, best-available first:

  1. scores.json   — the normal full-pipeline case: real grouping + titles + per-episode
                     achievement/value/rung + the window_score. This is what the viz uses
                     in practice.
  2. grouping.json — `haid episodes` ran but not `haid score`: real grouping + titles, no
                     score badges.
  3. single window — the true last resort (neither artifact present): one flat episode over
                     every session, so the command never hard-fails.

The session spines come from extract.extract_session; metrics.json (optional) supplies the
window metric headline and the per-file flag overlay.
"""

from __future__ import annotations


def _session_title(d: dict) -> str:
    for it in d.get("spine", []):
        if it.get("kind") == "user" and it.get("text"):
            return it["text"][:80]
    return d.get("stem", "")


def _first_ts(d: dict) -> str:
    for it in d.get("spine", []):
        if it.get("ts"):
            return it["ts"]
    return ""


def _episode_score(e: dict) -> dict | None:
    """The compact per-episode score badge, when scoring ran for this episode."""
    if e.get("achievement") is None and e.get("value") is None:
        return None
    return {
        "achievement": e.get("achievement"),
        "value": e.get("value"),
        "difficulty_rung": (e.get("difficulty") or {}).get("rung"),
        "cleanliness_pct": (e.get("cleanliness") or {}).get("percentile"),
        "normalized_tokens": e.get("normalized_tokens"),
        "has_artifact": e.get("has_artifact", True),
    }


def _episodes_from_scores(scores_doc: dict, present: set[str]) -> list[dict]:
    out = []
    for e in scores_doc.get("episodes", []):
        stems = [s for s in e.get("session_ids", []) if s in present]
        if not stems:
            continue
        out.append({"id": e.get("id"), "title": e.get("title", "") or e.get("id"),
                    "session_stems": stems, "score": _episode_score(e)})
    return out


def _episodes_from_grouping(grouping_doc: dict, present: set[str]) -> list[dict]:
    out = []
    for i, g in enumerate(grouping_doc.get("episodes", [])):
        stems = [s for s in g.get("session_ids", []) if s in present]
        if not stems:
            continue
        out.append({"id": g.get("id") or f"ep{i}",
                    "title": g.get("title", "") or f"Episode {i + 1}",
                    "session_stems": stems, "score": None})
    return out


def _single_window_episode(present_ordered: list[str]) -> list[dict]:
    if not present_ordered:
        return []
    return [{"id": "win", "title": "Whole window (ungrouped)",
             "session_stems": present_ordered, "score": None}]


def _metrics_overlay(metrics_doc: dict | None) -> tuple[list, dict]:
    """Window metric headline + per-file flag map (which metrics flagged each file)."""
    if not metrics_doc:
        return [], {}
    headline = [
        {k: m.get(k) for k in ("metric", "scope", "rate", "token_rate",
                               "token_weight", "baseline")}
        for m in metrics_doc.get("measurements", []) if m.get("scope") == "window"
    ]
    flags: dict[str, dict] = {}
    for inst in metrics_doc.get("instances", []):
        fid = (inst.get("refs") or {}).get("file_id")
        if not fid:
            continue
        rec = flags.setdefault(fid, {"metrics": {}, "weight": 0})
        rec["metrics"][inst["metric"]] = max(
            rec["metrics"].get(inst["metric"], 0), inst.get("token_weight", 0))
        rec["weight"] += inst.get("token_weight", 0)
    return headline, flags


def assemble_bundle(session_dicts: list[dict], *, scores_doc: dict | None = None,
                    grouping_doc: dict | None = None, metrics_doc: dict | None = None,
                    label: str = "") -> dict:
    """Build the `window.HAID_DATA` bundle. `session_dicts` come from extract_session."""
    sessions = {}
    for d in session_dicts:
        if not d.get("spine"):
            continue                       # nothing to draw — skip (caller may note it)
        d = dict(d)
        d["title"] = _session_title(d)
        d["first_ts"] = _first_ts(d)
        sessions[d["stem"]] = d

    present = set(sessions)
    present_ordered = sorted(present, key=lambda s: _first_ts(sessions[s]))

    # episode precedence: real scores > real grouping > single-window fallback
    source = "single_window"
    if scores_doc and scores_doc.get("episodes"):
        episodes = _episodes_from_scores(scores_doc, present)
        source = "scores"
    elif grouping_doc and grouping_doc.get("episodes"):
        episodes = _episodes_from_grouping(grouping_doc, present)
        source = "grouping"
    else:
        episodes = _single_window_episode(present_ordered)
    if not episodes:                       # grouping referenced no extracted session
        episodes = _single_window_episode(present_ordered)
        source = "single_window"

    headline, flags = _metrics_overlay(metrics_doc)
    return {
        "generated_for": "haid viz — live window",
        "window_label": label,
        "episode_source": source,
        "window_score": (scores_doc or {}).get("window_score"),
        "headline": headline,
        "flags": flags,
        "episodes": episodes,
        "sessions": sessions,
    }
