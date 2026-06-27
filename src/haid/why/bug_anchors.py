"""Bug-fix anchor source — the user-anchored seed for bug-source attribution.

The waste-metric anchors (anchors.py) point the why-pass at *inefficiency*. This module
points it at *defects*: every bug fix in the window earns an investigation that traces the
bug back to whoever introduced it — the agent, the user, or an external/inherited source
(docs/detectors.md "Recurrence / bug attribution"; trust-discipline.md cite-or-orphan).

The unit is a **fix span**, NOT an episode (a feature episode can contain feature work AND a
bugfix — the fix is its own unit, and the most coachable case is a fix that traces back to the
episode's own earlier feature work). A fix span is seeded by a tagged message that is either:
  - work_type=implementation with impl_kind=bugfix, or
  - a `correction` move on implementation/investigation work (the user said "that's wrong"),
and runs until the next user message on the same branch. The agent edits inside that span are
the resolving edits; their file footprint + token cost is the deterministic substrate the
why-agent then traces from (the architecture decision: deterministic footprint → agent trace).

This module is pure measurement — it never decides blame (that is the why-agent's job, behind
the model boundary). Stdlib only; no model.
"""

from __future__ import annotations

from .anchors import WhyAnchor
from ..graph.model import is_write

DEFAULT_BUG_TOP = 4          # bug anchors have their OWN budget so waste can't crowd them out
_FIX_WORK_TYPES = ("implementation", "investigation")


def _is_fix_seed(t) -> bool:
    """A tagged message that opens a fix span (a bug fix, or a correction closing a defect)."""
    if t.work_type == "implementation" and t.impl_kind == "bugfix":
        return True
    return t.move == "correction" and t.work_type in _FIX_WORK_TYPES


def _writes_by_branch(view) -> dict[str, list]:
    """Map 'sid:timeline' -> its write tool calls (with a ts), in chronological order."""
    out: dict[str, list] = {}
    if view is None:
        return out
    for label, tcs in view.timelines:
        writes = [tc for tc in tcs if is_write(tc) and tc.ts]
        if writes:
            out[label] = sorted(writes, key=lambda tc: tc.ts)
    return out


def _next_ts_on_branch(tagged, sid: str, timeline: str, after_ts) -> str | None:
    """Timestamp of the next user message on the same branch (the fix span's far edge)."""
    later = [t.ts for t in tagged
             if t.session_id == sid and t.timeline == timeline and t.ts and after_ts
             and t.ts > after_ts]
    return min(later) if later else None


def _span_edits(writes, start_ts, end_ts) -> list:
    """Write calls in [start_ts, end_ts) — the edits that resolved this fix."""
    out = []
    for tc in writes:
        if start_ts and tc.ts >= start_ts and (end_ts is None or tc.ts < end_ts):
            out.append(tc)
    return out


def _detail(t, files: list[str], n_edits: int) -> str:
    kind = f":{t.impl_kind}" if t.impl_kind else ""
    where = (" → " + ", ".join(files[:3]) + (" …" if len(files) > 3 else "")) if files else \
            " (no resolving edit located in span)"
    return f'fix: "{t.purpose}" ({t.move} × {t.work_type}{kind}){where}'


def select_bug_anchors(tagged, view, *, top: int = DEFAULT_BUG_TOP) -> list[WhyAnchor]:
    """Pick the window's bug-fix spans worth a bug-attribution agent, ranked by fix cost.

    `tagged` is the message-tag output (haid tag); `view` is the WindowView (for the edit
    footprint). Deterministic: ranked by resolving-edit token weight, then chronological for
    ties; cut to `top`. A fix with no locatable edit still qualifies (the why-agent can find
    the resolving change itself) but sorts last.
    """
    writes_by_branch = _writes_by_branch(view)
    seeds = [t for t in (tagged or []) if _is_fix_seed(t)]

    rows: list[tuple[int, str, WhyAnchor]] = []
    for t in seeds:
        branch = f"{t.session_id}:{t.timeline}"
        writes = writes_by_branch.get(branch, [])
        end_ts = _next_ts_on_branch(tagged, t.session_id, t.timeline, t.ts)
        edits = _span_edits(writes, t.ts, end_ts)
        files = sorted({tc.target_file_id for tc in edits if tc.target_file_id})
        weight = sum(tc.result_bytes for tc in edits) // 4
        rows.append((weight, t.ts or "", WhyAnchor(
            id="",                          # assigned after ranking
            metric="bugfix", scope="window",
            detail=_detail(t, files, len(edits)),
            token_weight=weight,
            file_id=files[0] if files else None,
            session_ids=[t.session_id],
            refs={"fix_uuid": t.uuid, "fix_ts": t.ts, "fix_purpose": t.purpose,
                  "fix_text_preview": (t.text[:200] if t.text else ""),
                  "move": t.move, "work_type": t.work_type, "impl_kind": t.impl_kind,
                  "fix_files": files, "n_edits": len(edits)},
        )))

    rows.sort(key=lambda r: (-r[0], r[1]))
    out = []
    for rank, (_w, _ts, a) in enumerate(rows[:top], 1):
        out.append(WhyAnchor(id=f"bugfix/window/{rank}", metric=a.metric, scope=a.scope,
                             detail=a.detail, token_weight=a.token_weight, file_id=a.file_id,
                             session_ids=a.session_ids, refs=a.refs))
    return out
