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

from dataclasses import dataclass, field

from .anchors import WhyAnchor
from ..graph.model import is_write

DEFAULT_BUG_TOP = 4          # bug anchors have their OWN budget so waste can't crowd them out
_FIX_WORK_TYPES = ("implementation", "investigation")


@dataclass(frozen=True)
class FixSpan:
    """One fix span: a bug-fix/correction seed message + its resolving edits on a branch.

    The shared primitive both consumers build on: `select_bug_anchors` ranks + caps this list for
    the (expensive) coaching why-pass, while the SCORED bug-fix reward (scoring/bugfix.py) credits
    EVERY eligible span — no cap (a benchmarked axis must not silently truncate)."""
    seed_uuid: str
    session_id: str
    timeline: str
    start_ts: str | None
    end_ts: str | None
    move: str
    work_type: str
    impl_kind: str | None
    purpose: str
    text: str
    edits: tuple = ()
    files: list = field(default_factory=list)
    fix_token_weight: int = 0          # resolving-edit cost (Σ result_bytes // 4)

    @property
    def branch(self) -> str:
        return f"{self.session_id}:{self.timeline}"

    @property
    def n_edits(self) -> int:
        return len(self.edits)


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


def _authored_bytes(tc) -> int:
    """Bytes AUTHORED by a write, read from its tool_use INPUT params.

    Subagent write ToolCalls carry no `toolUseResult`, so their `result_bytes` is structurally
    0 — ranking on it would sink every delegated fix to last and drop it from the coaching top-N
    (the exact bug this fold exists to fix). We instead measure the content the subagent wrote,
    mirroring how the main path's `_result_bytes` sizes a write (Edit=len(newString),
    Write=len(content)) so subagent and main edits are directly comparable in the weight."""
    p = tc.params or {}
    if tc.tool == "MultiEdit":
        return sum(len(e.get("new_string") or "") for e in (p.get("edits") or []))
    if tc.tool == "Edit":
        return len(p.get("new_string") or "")
    if tc.tool == "Write":
        return len(p.get("content") or "")
    return len(tc.write_content or "")          # derived/heredoc bash write


def _sub_edits_on_branch(sessions, view, branch, start_ts, end_ts) -> list:
    """Subagent-authored write ToolCalls on `branch` whose ts is in [start_ts, end_ts).

    A fix done by a spawned subagent lives on `Session.subagents`, not on `view.timelines`,
    so `_writes_by_branch` misses it and the span's footprint reads empty — which sinks the
    span in `select_bug_anchors`' cost ranking and drops it from the coaching top-N. We fold
    those writes back in via the shared foundation. Lazy import: keep `why` off `bridge`'s
    import path (no cycle). `is_external=lambda _f: False` matches `_writes_by_branch`, which
    does NOT filter external paths — the two footprint sources must count identically."""
    from ..bridge.subagent_writes import collect, on_branch
    sub = collect(sessions, lambda _f: False)
    subs = on_branch(sub, view, branch)
    return [w.tc for w in subs
            if w.tc is not None and w.ts
            and start_ts and w.ts >= start_ts and (end_ts is None or w.ts < end_ts)]


def fix_spans(tagged, view, sessions=None) -> list[FixSpan]:
    """EVERY fix span in the window (seeded by impl_kind=bugfix or a correction move) with its
    resolving-edit footprint. No ranking, no cap — the scored reward credits all eligible cures;
    `select_bug_anchors` ranks + caps THIS list for the coaching budget. Deterministic.

    When `sessions` is given, subagent-authored writes on the span's branch (in-span by ts) are
    merged into the footprint, so a fix delegated to a spawned agent is not scored as empty. With
    `sessions=None` the footprint is main-transcript-only — identical to the historical behavior."""
    writes_by_branch = _writes_by_branch(view)
    out: list[FixSpan] = []
    for t in (tagged or []):
        if not _is_fix_seed(t):
            continue
        branch = f"{t.session_id}:{t.timeline}"
        writes = writes_by_branch.get(branch, [])
        end_ts = _next_ts_on_branch(tagged, t.session_id, t.timeline, t.ts)
        edits = _span_edits(writes, t.ts, end_ts)
        # Main writes are sized by result_bytes; subagent writes have result_bytes 0 (no enriched
        # result) so they're sized by their authored INPUT bytes — kept separate ONLY so each side
        # uses its own byte source, then folded into one // 4 weight and one chronological footprint.
        sub_edits = (_sub_edits_on_branch(sessions, view, branch, t.ts, end_ts)
                     if sessions is not None else [])
        weight = (sum(tc.result_bytes for tc in edits)
                  + sum(_authored_bytes(tc) for tc in sub_edits)) // 4
        edits = sorted(edits + sub_edits, key=lambda tc: tc.ts or "")
        files = sorted({tc.target_file_id for tc in edits if tc.target_file_id})
        out.append(FixSpan(
            seed_uuid=t.uuid, session_id=t.session_id, timeline=t.timeline,
            start_ts=t.ts, end_ts=end_ts, move=t.move, work_type=t.work_type,
            impl_kind=t.impl_kind, purpose=t.purpose, text=t.text or "",
            edits=tuple(edits), files=files, fix_token_weight=weight))
    return out


def _detail(span: FixSpan) -> str:
    kind = f":{span.impl_kind}" if span.impl_kind else ""
    where = (" → " + ", ".join(span.files[:3]) + (" …" if len(span.files) > 3 else "")
             ) if span.files else " (no resolving edit located in span)"
    return f'fix: "{span.purpose}" ({span.move} × {span.work_type}{kind}){where}'


def select_bug_anchors(tagged, view, *, top: int = DEFAULT_BUG_TOP, sessions=None) -> list[WhyAnchor]:
    """Pick the window's bug-fix spans worth a bug-attribution agent, ranked by fix cost.

    `tagged` is the message-tag output (haid tag); `view` is the WindowView (for the edit
    footprint). Deterministic: ranked by resolving-edit token weight, then chronological for
    ties; cut to `top`. A fix with no locatable edit still qualifies (the why-agent can find
    the resolving change itself) but sorts last. Pass `sessions` to fold subagent-authored
    fixes into each span's footprint (else the delegated fix ranks as empty and gets dropped).
    """
    ranked = sorted(fix_spans(tagged, view, sessions),
                    key=lambda s: (-s.fix_token_weight, s.start_ts or ""))
    out = []
    for rank, s in enumerate(ranked[:top], 1):
        out.append(WhyAnchor(
            id=f"bugfix/window/{rank}", metric="bugfix", scope="window",
            detail=_detail(s), token_weight=s.fix_token_weight,
            file_id=s.files[0] if s.files else None, session_ids=[s.session_id],
            refs={"fix_uuid": s.seed_uuid, "fix_ts": s.start_ts, "fix_purpose": s.purpose,
                  "fix_text_preview": (s.text[:200] if s.text else ""),
                  "move": s.move, "work_type": s.work_type, "impl_kind": s.impl_kind,
                  "fix_files": s.files, "n_edits": s.n_edits,
                  "fix_timeline": s.timeline, "fix_end_ts": s.end_ts}))
    return out
