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

import os
import re
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


# A HAID self-audit session is a Claude Code session that RAN HAID on the project — running the
# haid-report skill, or invoking the `haid` CLI. It is meta-work, not project work: it produces
# ~no project achievement yet costs real tokens (the whole pipeline fans out many agents), so
# leaving it in the window dilutes the project's value score and — since the run happens inside
# the project's own transcript dir — makes the project perturb its own measurement every run.
# Detection is deterministic from the transcript (no model): the skill-attribution envelope
# field, or a Bash tool call invoking a haid subcommand.
_HAID_CLI = re.compile(
    r"""(?:^|[\s;&|()'"])(?:python\s+-m\s+haid|haid)\s+"""
    r"(?:metrics|tag|episodes|score|why|report|bridge|value|viz|benchmark|submit|rank|"
    r"volume|cost|place)\b")


def _is_self_audit(session: Session) -> bool:
    for r in session.parse.records:
        raw = getattr(r, "raw", None) or {}
        if "haid-report" in (raw.get("attributionSkill") or ""):
            return True
        msg = raw.get("message") or {}
        content = msg.get("content") if isinstance(msg, dict) else None
        if isinstance(content, list):
            for b in content:
                if (isinstance(b, dict) and b.get("type") == "tool_use"
                        and b.get("name") == "Bash"
                        and _HAID_CLI.search((b.get("input") or {}).get("command", "") or "")):
                    return True
    return False


def partition_self_audit(sessions: list[Session]) -> tuple[list[Session], list[Session]]:
    """Split sessions into (project work, HAID self-audit). Deterministic, model-free."""
    kept, excluded = [], []
    for s in sessions:
        (excluded if _is_self_audit(s) else kept).append(s)
    return kept, excluded


def _exclusion_note(excluded: list[Session]) -> str:
    return (f"{len(excluded)} HAID self-audit session(s) excluded from the window "
            f"(meta-analysis, not project work): {', '.join(_sid(s) for s in excluded)}")


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
    # The transcript dir is named after the ABSOLUTE cwd (discover.encode_project_path), so a
    # relative `--project` like "." or "src" must be resolved first — otherwise we encode the
    # literal "." and look under ~/.claude/projects/. and silently find zero sessions.
    project_path = os.path.abspath(os.path.expanduser(project_path))
    now = now or datetime.now()
    since = (now - timedelta(days=days)).strftime("%Y-%m-%d")
    files = discover.find_sessions(project_path, projects_root=projects_root, since=since)
    discovered = [load_session(str(fp)) for fp in files]
    # Drop HAID self-audit sessions so the project isn't scored on the cost of measuring itself
    # (and so the live analysis session can't perturb its own window). Surfaced as a note —
    # never silent (trust-discipline.md §5).
    sessions, excluded = partition_self_audit(discovered)
    label = f"{project_path} — last {days}d ({len(sessions)} sessions)"
    view = build_view(sessions, label=label)
    if excluded:
        view.notes.append(_exclusion_note(excluded))
    return view, sessions


def from_files(paths: list[str], label: str = "explicit session list") -> tuple[WindowView, list[Session]]:
    """Window from an explicit list of transcript paths (overrides the project/timeframe)."""
    sessions = [load_session(p) for p in paths]
    return build_view(sessions, label=f"{label} ({len(sessions)} sessions)"), sessions
