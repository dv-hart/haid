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

from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from ..bridge import span_inputs
from ..graph.model import is_write
from ..metrics.retouched import _nontrivial   # single source of truth for a "trackable" line
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
    self_inflicted: bool = False          # the fixed lines were (mostly) authored earlier THIS episode
    files: list = field(default_factory=list)


# --- the deterministic eligibility gate (Step 4; docs/plans/bugfix-reward.md) ---------------
# A cure scores unless the lines it fixed were authored earlier IN THE SAME EPISODE (self-
# inflicted rework, already paid for in cost). This is content-lineage, model-free: it reuses
# retouched's `_nontrivial` notion of a trackable line and the episode's own tool-call stream,
# so it applies to EVERY cure — not just the top-N the (expensive) bug-attribution agent reaches.

def _authored_text(tc) -> str:
    """The new file content a write tool-call produced (Edit/MultiEdit/Write + heredoc bash)."""
    if tc.tool == "MultiEdit":
        return "\n".join(e.get("new_string", "") for e in (tc.params.get("edits") or []))
    if tc.tool in ("Edit", "Write"):
        return tc.params.get("new_string") or tc.params.get("content") or ""
    return tc.write_content or ""         # derived (heredoc) shell write; None otherwise


def _produced_before(episode_stream, cutoff_ts) -> dict:
    """{file_id: set(nontrivial lines)} the agent authored in this episode STRICTLY BEFORE the
    fix seed — the lines that, if the fix rewrites them, mark it self-inflicted."""
    produced: dict[str, set] = defaultdict(set)
    for _sid, tc in episode_stream:
        if not is_write(tc) or not tc.target_file_id or not tc.ts:
            continue
        if cutoff_ts and tc.ts >= cutoff_ts:
            continue
        produced[tc.target_file_id] |= _nontrivial(_authored_text(tc))
    return produced


def _removed_lines(diff: str) -> dict:
    """{file_id: set(nontrivial removed lines)} from a reconstructed span diff (the buggy lines
    the fix takes out). Works for buffer- and hunks-mode diffs and Write overwrites alike, since
    it reads the '-' lines, not a per-edit old_string."""
    per: dict[str, list] = {}
    cur = None
    for ln in diff.splitlines():
        if ln.startswith("diff --git"):
            cur = ln.split(" b/", 1)[1] if " b/" in ln else ln.split()[-1]
            per.setdefault(cur, [])
        elif ln.startswith(("---", "+++")) or cur is None:
            continue
        elif ln.startswith("-"):
            per[cur].append(ln[1:])
    return {fid: _nontrivial("\n".join(lines)) for fid, lines in per.items()}


def is_self_inflicted(diff: str, episode_stream, cutoff_ts) -> bool:
    """True iff a MAJORITY of the fix's (trackable) removed lines were authored earlier in this
    episode. No trackable removed lines (e.g. a tiny/short-line fix) → False (default: scored),
    matching retouched's <8-char carve-out."""
    produced = _produced_before(episode_stream, cutoff_ts)
    total = matched = 0
    for fid, lines in _removed_lines(diff).items():
        pf = produced.get(fid, set())
        for ln in lines:
            total += 1
            matched += ln in pf
    return total > 0 and matched * 2 > total


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
    """Every fix span across `sessions` (= ONE episode's members) → its span diff, find-cost proxy,
    and the deterministic self-inflicted flag. Deterministic, no model.

    `sessions` is treated as one episode: a single episode-wide view supplies both the gate's
    authorship stream (all sessions, so a bug written in an earlier session of the episode counts
    as self-inflicted) and the per-span reconstruction (the branch label pins each span's diff to
    its own session). A span with an empty diff (the fix wasn't reconstructable) is dropped."""
    from ..window import build_view

    by_sid = {_stem(s.path): s for s in sessions}
    ep_view = build_view(sessions)
    ep_stream = ep_view.active_stream          # the episode's chronological write/read stream

    out: list[CuredCandidate] = []
    for span in fix_spans(tagged, ep_view):
        sess = by_sid.get(span.session_id)
        if sess is None:
            continue
        recon = span_inputs(ep_view, sessions, branch=span.branch,
                            start_ts=span.start_ts, end_ts=span.end_ts)
        diff = recon.diff.strip()
        if not diff:
            continue
        hunt_end = max((e.ts for e in span.edits if e.ts), default=span.end_ts)
        find = _hunt_find_cost(sess, span.start_ts, hunt_end)
        selfinf = is_self_inflicted(diff, ep_stream, span.start_ts)
        out.append(CuredCandidate(
            bug_id=f"fix_{span.seed_uuid[:12]}",   # canonical id (seed-uuid) — shared with tracing
            span=span, diff=diff, earned_find_cost=find, incomplete=bool(recon.incomplete),
            self_inflicted=selfinf, files=list(span.files)))
    return out


def resolve_cured(candidates, backend_for, *, eligible=None, samples: int = 1):
    """Place each eligible candidate's fix diff on the difficulty ladder → value.CuredBug.

    The **deterministic self-inflicted gate always applies** — a candidate whose fixed lines were
    (majority) authored earlier in this episode is skipped, no model needed (Step 4). `eligible(
    bug_id) -> bool` is an OPTIONAL further refinement (e.g. a `holding=recurred` downgrade wired
    from the top-N tracer); omitted ⇒ no extra filter. `backend_for("difficulty", bug_id)` supplies
    a compare.Backend. A placement that defers under the live file-handoff path is collected as a
    pending manifest. Returns `(cured_bugs, pending_manifest_paths)`."""
    cured: list = []
    pending: list[str] = []
    for c in candidates:
        if c.self_inflicted:                     # the deterministic gate: skip same-episode rework
            continue
        if eligible is not None and not eligible(c.bug_id):   # optional refinement (e.g. recurred)
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
