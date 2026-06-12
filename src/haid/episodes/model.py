"""Episode nodes — the git-free PR proxy, at SESSION grain.

An **episode = a collection of one or more WHOLE sessions on a shared component or topic**
(grain decision 2026-06-08; plans/agent-analysis.md §1). The **session is atomic** — one
continuous context window, the only boundary at which token cost attributes cleanly — so an
episode never subdivides a session. Hierarchy: session ⊆ episode ⊆ window; a session belongs to
exactly one episode.

This module is pure data: the `SessionSummary` (the rolled-up per-session unit the grouping pass
reads), the `EpisodeGroup` a backend returns, the `Episode` node, and `iter_episodes` — the
slicer that maps an episode back onto its `Session` objects, which is what Step 4 runs the bridge
and the metrics over. Stdlib only; no model.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class SessionSummary:
    """One whole session, rolled up from its message tags + graph. The atomic unit the grouping
    pass clusters. `purposes` is the session's per-message purpose snapshots in order (its topic
    fingerprint); `file_set` is the files it touched (the component cue + cross-session re-read
    signal); `n_new_directives > 1` is a cheap within-session drift proxy (a coaching note, never
    a reason to split — the session stays whole)."""
    session_id: str                 # 8-char stem
    index: int                      # chronological position in the window (0-based)
    first_ts: str | None
    last_ts: str | None
    n_messages: int
    purposes: list[str] = field(default_factory=list)
    file_set: set[str] = field(default_factory=set)
    n_new_directives: int = 0

    @property
    def drift_flag(self) -> bool:
        return self.n_new_directives > 1


@dataclass(frozen=True)
class EpisodeGroup:
    """What a grouping backend returns: a topic title, the session ids that belong together
    (≥1, may be non-contiguous in time — a thread resumed days later), and the rationale."""
    title: str
    session_ids: list[str]
    rationale: str = ""


@dataclass
class Episode:
    """A reconstructed unit of work = a set of whole sessions on a shared component/topic."""
    id: str                          # "ep1", "ep2", … in window order
    title: str
    session_ids: list[str] = field(default_factory=list)
    first_ts: str | None = None      # min over its sessions
    last_ts: str | None = None       # max over its sessions
    rationale: str = ""

    @property
    def n_sessions(self) -> int:
        return len(self.session_ids)


def _stem(path: str) -> str:
    return Path(path).stem[:8]


def iter_episodes(episodes, sessions):
    """Yield (Episode, [Session]) — the loaded sessions that make up each episode, in the
    episode's session order. This is the slicer Step 4 consumes: it runs `bridge.window_inputs`
    and the waste metrics over exactly these sessions (per-episode diff + cost + metrics)."""
    by_id = {_stem(s.path): s for s in sessions}
    for ep in episodes:
        members = [by_id[sid] for sid in ep.session_ids if sid in by_id]
        yield ep, members
