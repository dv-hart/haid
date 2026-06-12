"""The user-anchored pass, step 3: group the window's sessions into episodes.

An episode is a collection of one or more WHOLE sessions on a shared component/topic — the
git-free PR proxy and the join point of the whole tool (the unit the why-pass investigates AND
the unit scored for difficulty/cleanliness; plans/agent-analysis.md §1, §5). The session is
atomic: episodes never subdivide one, which is what keeps per-episode token cost cleanly
attributable.

    segment_window(sessions, tagged, backend) -> [Episode]

`sessions` are the loaded Session objects of the window; `tagged` is the [TaggedMessage] from
step 2 (haid.intent) used to give each session its purpose fingerprint. `backend` decides how
the holistic grouping judgment is made: HeuristicBackend (deterministic baseline, no model),
ReplayBackend (saved, CI), or HarnessBackend (the live host-agent path).

This module is the deterministic orchestration around the one grouping call: it summarizes the
sessions (summarize.py), hands them to the backend, validates the returned groups PARTITION the
sessions (every session assigned exactly once — a malformed grouping surfaces, never hides), and
materializes Episode nodes. Stdlib only.
"""

from __future__ import annotations

from . import grouping, summarize
from .model import Episode, EpisodeGroup, SessionSummary, iter_episodes
from .segment import (GroupingBackend, HarnessBackend, HeuristicBackend, PendingSegmentation,
                      ReplayBackend)

__all__ = ["segment_window", "to_json", "render", "Episode", "EpisodeGroup", "SessionSummary",
           "iter_episodes", "summarize", "GroupingBackend", "HeuristicBackend", "ReplayBackend",
           "HarnessBackend", "PendingSegmentation"]


def _validate_partition(groups: list[EpisodeGroup], session_ids: list[str]) -> None:
    """Every window session must be assigned to exactly one episode — no missing, no duplicate,
    no unknown id. Raise loudly otherwise (a bad grouping is a bug, not a silent miscount)."""
    want = set(session_ids)
    seen: set[str] = set()
    for g in groups:
        for sid in g.session_ids:
            if sid not in want:
                raise ValueError(f"grouping references unknown session id {sid!r}")
            if sid in seen:
                raise ValueError(f"session {sid!r} assigned to more than one episode")
            seen.add(sid)
    missing = want - seen
    if missing:
        raise ValueError(f"grouping does not cover every session; missing: {sorted(missing)}")


def segment_window(sessions, tagged, backend: GroupingBackend,
                   overlap_threshold: float = grouping.DEFAULT_OVERLAP) -> list[Episode]:
    """Group a window's sessions into episodes."""
    summaries = summarize.summarize_sessions(sessions, tagged)
    by_id = {s.session_id: s for s in summaries}
    groups = backend.group(summaries)
    _validate_partition(groups, [s.session_id for s in summaries])

    # Episodes ordered by the earliest session they contain (window order).
    def earliest(g: EpisodeGroup) -> int:
        return min(by_id[sid].index for sid in g.session_ids)

    episodes: list[Episode] = []
    for n, g in enumerate(sorted(groups, key=earliest), start=1):
        members = sorted((by_id[sid] for sid in g.session_ids), key=lambda s: s.index)
        firsts = [m.first_ts for m in members if m.first_ts]
        lasts = [m.last_ts for m in members if m.last_ts]
        episodes.append(Episode(
            id=f"ep{n}", title=g.title, session_ids=[m.session_id for m in members],
            first_ts=min(firsts) if firsts else None,
            last_ts=max(lasts) if lasts else None,
            rationale=g.rationale))
    return episodes


def to_json(episodes: list[Episode], label: str = "") -> dict:
    """Machine-readable hand-off to episode-scope metrics + achievement scoring (step 4)."""
    return {
        "schema_version": "1.0",
        "kind": "episodes",
        "window": label,
        "episodes": [
            {"id": e.id, "title": e.title, "session_ids": e.session_ids,
             "n_sessions": e.n_sessions, "first_ts": e.first_ts, "last_ts": e.last_ts,
             "rationale": e.rationale}
            for e in episodes
        ],
    }


def render(episodes: list[Episode], summaries=None, label: str = "") -> str:
    """The eyeball view: each episode, its sessions, and (if `summaries` given) their purposes."""
    by_id = {s.session_id: s for s in (summaries or [])}
    head = f"# episodes — {label}" if label else "# episodes"
    lines = [head, ""]
    for e in episodes:
        span = f"{(e.first_ts or '?')[:10]}…{(e.last_ts or '?')[:10]}"
        lines.append(f"## {e.id}: {e.title}  [{e.n_sessions} session(s), {span}]")
        if e.rationale:
            lines.append(f"   ↳ {e.rationale}")
        for sid in e.session_ids:
            s = by_id.get(sid)
            if s:
                topic = s.purposes[0] if s.purposes else "(no tagged purpose)"
                drift = "  ⚠drift" if s.drift_flag else ""
                lines.append(f"     [{sid}] {s.n_messages} msgs — {topic}{drift}")
            else:
                lines.append(f"     [{sid}]")
        lines.append("")
    return "\n".join(lines).rstrip()
