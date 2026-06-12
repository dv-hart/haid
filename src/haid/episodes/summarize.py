"""Roll up a window's sessions into the per-session summaries the grouping pass clusters.

The session is the atomic unit (grain decision 2026-06-08), so episode formation works over
`SessionSummary` objects, not individual messages. Each summary folds two cheap, deterministic
inputs: the session's **message purpose snapshots** (its topic fingerprint, from the already-built
intent classifier) and the **files it touched** (the component cue — and the same file id scheme
that makes repeated cross-session re-reads a detectable signal). No model is called here; the one
model judgment in this pass is the grouping itself (segment.py). Stdlib only.
"""

from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path

from ..graph.build import build_graph
from .model import SessionSummary

_ABS = re.compile(r"^(?:/|[A-Za-z]:[\\/]|\\\\)")   # posix root, drive letter, or UNC


def _is_external(file_id: str) -> bool:
    """A non-repo-relative id (temp file, other repo, /etc) — excluded so it can't link two
    unrelated sessions into one episode. Mirrors the bridge's external test."""
    return bool(_ABS.match(file_id))


def _session_files(session) -> set[str]:
    g = build_graph(session.parse.records)
    return {tc.target_file_id for tc in g.toolcalls.values()
            if tc.target_file_id and not _is_external(tc.target_file_id)}


def _ts_bounds(session) -> tuple[str | None, str | None]:
    ts = [r.timestamp for r in session.parse.records if r.timestamp]
    return (min(ts), max(ts)) if ts else (None, None)


def summarize_sessions(sessions, tagged) -> list[SessionSummary]:
    """One SessionSummary per loaded session, in window-chronological order.

    `tagged` is the [TaggedMessage] from `haid tag` (intent); messages are grouped to their
    session and ordered by their window index."""
    by_sess: dict[str, list] = defaultdict(list)
    for t in sorted(tagged, key=lambda x: x.index):
        by_sess[t.session_id].append(t)

    def first_ts(s):
        return _ts_bounds(s)[0] or ""

    out: list[SessionSummary] = []
    for idx, s in enumerate(sorted(sessions, key=first_ts)):
        sid = Path(s.path).stem[:8]
        msgs = by_sess.get(sid, [])
        lo, hi = _ts_bounds(s)
        out.append(SessionSummary(
            session_id=sid, index=idx, first_ts=lo, last_ts=hi,
            n_messages=len(msgs),
            purposes=[m.purpose for m in msgs],
            file_set=_session_files(s),
            n_new_directives=sum(1 for m in msgs if m.move == "new_directive"),
        ))
    return out
