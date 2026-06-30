"""The why → score join: fix spans → placed, eligibility-gated CuredBugs for the bug-fix term.

This is the wire from the (coaching) bug-attribution world into the (scoring) achievement world
that the bug-fix reward needs (docs/plans/bugfix-reward.md). Two stages, split by the model
boundary exactly as the rest of the stack:

  collect_candidates(sessions, tagged)  — DETERMINISTIC. Every fix span (why.bug_anchors.fix_spans)
      → its span-relative diff (bridge.span_inputs) + a find-cost proxy. No model.
  resolve_cured(candidates, backend_for, eligible) — places each fix diff on the difficulty ladder
      (the magnitude) and keeps the eligible cures → value.CuredBug.

The find-cost proxy (maintainer's call): the normalized-token cost of the assistant turns in the
span's hunt window (seed → last resolving edit). It sits in BOTH the value denominator (real cost)
and the bug-fix numerator (via CuredBug), so a hard-to-find bug lifts achievement while value reads
as remediation efficiency, not hunt length. Waste-discount (subtracting flagged retries/rereads in
the span) is a Phase-4 refinement — this version credits the raw hunt cost and says so.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ..bridge import span_inputs
from ..why.bug_anchors import fix_spans
from . import cost as _cost
from . import value as _value
from .compare import PendingComparisons
from .placement import place

# The anti-farm gate: reward curing INHERITED / other-thread bugs that STUCK — never a bug you
# just introduced in the same thread (that is self-inflicted rework, already paid for in cost).
_ELIGIBLE_CAUSE = frozenset({"source"})


def is_eligible(note) -> bool:
    """True iff this fix's attribution makes it a creditable cure: `cause_class=source` OR
    `scope=cross_episode`, AND `holding != recurred` (a fix that didn't hold is not value). The
    note is one bug-attribution result (why.prompts.BUG_NOTE_SCHEMA); None / missing ⇒ not
    eligible (cite-or-orphan: no attribution, no credit)."""
    if not isinstance(note, dict):
        return False
    if str(note.get("holding")) == "recurred":
        return False
    return note.get("cause_class") in _ELIGIBLE_CAUSE or note.get("scope") == "cross_episode"


def _stem(path: str) -> str:
    return Path(path).stem[:8]


@dataclass(frozen=True)
class CuredCandidate:
    """The deterministic substrate of one fix span, before model placement / attribution."""
    bug_id: str
    span: object                          # why.bug_anchors.FixSpan
    diff: str
    earned_find_cost: float
    incomplete: bool = False              # the span diff hit a reconstruction caveat (e.g. hunks)
    files: list = field(default_factory=list)


def _hunt_find_cost(session, lo_ts: str | None, hi_ts: str | None) -> float:
    """Normalized-token cost of the assistant turns in [lo_ts, hi_ts] on `session` — the hunt that
    produced the cure. Dominated by cache-reads (the context the search accreted), which IS the
    elusiveness signal. (Raw; waste-discount is Phase 4.)"""
    usages = []
    for r in session.parse.records:
        if r.type != "assistant" or not r.timestamp:
            continue
        if (lo_ts and r.timestamp < lo_ts) or (hi_ts and r.timestamp > hi_ts):
            continue
        msg = r.raw.get("message") or {}
        u = msg.get("usage")
        if isinstance(u, dict):
            d = dict(u)
            d["model"] = msg.get("model", "")
            usages.append(_cost.Usage.from_dict(d))
    return _cost.measure(usages).normalized_tokens


def collect_candidates(sessions, tagged) -> list[CuredCandidate]:
    """Every fix span across `sessions` → its span diff + find-cost proxy. Deterministic, no model.

    Each span is reconstructed against a PER-SESSION sub-view (like episode_inputs), so a span's
    diff is its own delta with no cross-session bleed. A span with an empty diff (the fix's edits
    weren't reconstructable) is dropped."""
    from ..window import build_view

    by_sid = {_stem(s.path): s for s in sessions}
    tags_by_sid: dict[str, list] = {}
    for t in tagged or []:
        tags_by_sid.setdefault(t.session_id, []).append(t)

    out: list[CuredCandidate] = []
    for sid, sess in by_sid.items():
        sess_tags = tags_by_sid.get(sid)
        if not sess_tags:
            continue
        sub = build_view([sess])
        for j, span in enumerate(fix_spans(sess_tags, sub), 1):
            recon = span_inputs(sub, [sess], branch=span.branch,
                                start_ts=span.start_ts, end_ts=span.end_ts)
            diff = recon.diff.strip()
            if not diff:
                continue
            hunt_end = max((e.ts for e in span.edits if e.ts), default=span.end_ts)
            find = _hunt_find_cost(sess, span.start_ts, hunt_end)
            out.append(CuredCandidate(
                bug_id=f"bugfix_{sid}_{j}", span=span, diff=diff, earned_find_cost=find,
                incomplete=bool(recon.incomplete), files=list(span.files)))
    return out


def resolve_cured(candidates, backend_for, *, eligible=None, samples: int = 1):
    """Place each eligible candidate's fix diff on the difficulty ladder → value.CuredBug.

    `backend_for("difficulty", bug_id)` supplies a compare.Backend (same factory score_episodes
    uses). `eligible(bug_id) -> bool` applies the attribution gate (default: all — the caller wires
    the real gate from bug notes via `is_eligible`). A placement that defers under the live
    file-handoff path is collected as a pending manifest, mirroring score_episodes. Returns
    `(cured_bugs, pending_manifest_paths)`."""
    eligible = eligible or (lambda _bug_id: True)
    cured: list = []
    pending: list[str] = []
    for c in candidates:
        if not eligible(c.bug_id):
            continue
        try:
            pl = place(c.diff, "difficulty", backend_for("difficulty", c.bug_id),
                       samples=samples, subject_id=c.bug_id)
        except PendingComparisons as p:
            pending.append(p.manifest_path)
            continue
        cured.append(_value.CuredBug(fix_difficulty=pl, earned_find_cost=c.earned_find_cost,
                                     bug_id=c.bug_id))
    return cured, pending
