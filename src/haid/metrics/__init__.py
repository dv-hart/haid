"""The four Tier-2 waste metrics (Phase 1, Step 3) — ONE rule each, run at any scope.

  - rereads          (rereads.py)
  - retries          (retries.py)   — uses the resolved is_error failure signal
  - retouched        (retouched.py)
  - unused_context   (unused_context.py)  — softest; heavily hedged

Each metric is a single rule (`<module>._core(stream)`) over a stream of (sid, ToolCall).
**Scope is only the memory window** (see docs/metrics-output-schema.md), realized by *which*
stream you feed the same core:
  - `run_window(view)`   → core over the whole active stream (memory persists across sessions,
                           surfacing cross-session signals like the re-establishment tax).
  - `run_sessions(view)` → core per session (memory resets each session).

Deterministic, model-free. Stdlib only.
"""

from __future__ import annotations

from . import baseline, rereads, retouched, retries, unused_context
from .base import Instance, MetricResult, WindowView, est_tokens, iter_sessions, SCOPES

__all__ = ["baseline", "rereads", "retries", "retouched", "unused_context",
           "Instance", "MetricResult", "WindowView", "est_tokens", "SCOPES",
           "run_window", "run_sessions", "run_episodes", "run_all"]

_CORES = {
    "rereads": rereads._core,
    "retries": retries._core,
    "retouched": retouched._core,
    "unused_context": unused_context._core,
}

METRIC_NAMES = tuple(_CORES)


def run_window(view: WindowView) -> dict:
    """{name: MetricResult} — each metric's rule over the whole active stream (`window` scope)."""
    return {name: core(view.active_stream, "window") for name, core in _CORES.items()}


def run_sessions(view: WindowView) -> dict:
    """{sid: {name: MetricResult}} — each metric's rule per session (`session` scope)."""
    return {sid: {name: core(sub, sid) for name, core in _CORES.items()}
            for sid, sub in iter_sessions(view.active_stream)}


def run_episodes(view: WindowView, episodes) -> dict:
    """{episode_id: {name: MetricResult}} — each metric's rule per EPISODE (`episode` scope).

    An episode is a group of whole sessions, so episode scope = the same core run over the
    sub-stream of the episode's sessions (memory persists across them, so a file re-read across
    the episode's sessions surfaces as the episode's re-establishment tax). Mirrors `run_sessions`,
    grouping by episode instead of by session. `episodes` is a list of episodes.Episode objects
    (each exposing `.id` and `.session_ids`); every episode gets an entry even if it issued no
    tool calls (a planning-only episode → empty-stream metrics, not a missing key)."""
    sid_to_ep = {sid: ep.id for ep in episodes for sid in ep.session_ids}
    grouped: dict[str, list] = {ep.id: [] for ep in episodes}
    for sid, tc in view.active_stream:
        ep_id = sid_to_ep.get(sid)
        if ep_id is not None:
            grouped[ep_id].append((sid, tc))
    return {ep_id: {name: core(sub, ep_id) for name, core in _CORES.items()}
            for ep_id, sub in grouped.items()}


# Backward-compatible alias: the old single-scope entry point is now window scope.
run_all = run_window
