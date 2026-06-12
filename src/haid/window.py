"""The analysis window: the multi-session unit HAID actually assesses.

A single transcript is rarely the meaningful unit — coaching value is cumulative across
the sessions that went into a body of work ("how am I doing across this PR / this month").
So metrics, rates, and the baseline are all computed over a *window* of sessions, not one
file. The default window is "this project, last N days" (N=30); a custom timeframe or an
explicit session list overrides it. Git/PR-bounded windows arrive with Phase-4 git
reconciliation.

This module composes the lower layers (session -> forest -> graph) into the `WindowView`
the metrics consume. Stdlib only; no model.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from .graph.build import build_graph, timeline_toolcalls
from .metrics.base import WindowView
from .session import discover
from .session.loader import Session, load_session


def _first_ts(s: Session) -> str:
    ts = [r.timestamp for r in s.parse.records if r.timestamp]
    return min(ts) if ts else ""


def _sid(s: Session) -> str:
    return Path(s.path).stem[:8]


def build_view(sessions: list[Session], label: str = "") -> WindowView:
    """Assemble a WindowView from already-loaded sessions, chronologically ordered."""
    sessions = sorted(sessions, key=_first_ts)
    active_stream: list = []
    timelines: list = []
    notes: list = []
    for s in sessions:
        g = build_graph(s.parse.records)
        sid = _sid(s)
        for tl in s.forest.timelines():
            tcs = timeline_toolcalls(g, tl)
            timelines.append((f"{sid}:{tl.label}", tcs))
            if tl.is_active:
                active_stream.extend((sid, tc) for tc in tcs)
        for w in s.warnings():
            notes.append(f"{sid}: {w}")
    return WindowView(active_stream=active_stream, timelines=timelines,
                      n_sessions=len(sessions), label=label, notes=notes)


def for_project(project_path: str, days: int = 30, projects_root=None,
                now: datetime | None = None) -> tuple[WindowView, list[Session]]:
    """Window = sessions for `project_path` within the last `days` days (default 30).

    Returns (view, sessions). History retention is generous (verified >38 days on real
    data), so 30 days is a safe default, not a retention limit."""
    now = now or datetime.now()
    since = (now - timedelta(days=days)).strftime("%Y-%m-%d")
    files = discover.find_sessions(project_path, projects_root=projects_root, since=since)
    sessions = [load_session(str(fp)) for fp in files]
    label = f"{project_path} — last {days}d ({len(sessions)} sessions)"
    return build_view(sessions, label=label), sessions


def from_files(paths: list[str], label: str = "explicit session list") -> tuple[WindowView, list[Session]]:
    """Window from an explicit list of transcript paths (overrides the project/timeframe)."""
    sessions = [load_session(p) for p in paths]
    return build_view(sessions, label=f"{label} ({len(sessions)} sessions)"), sessions
