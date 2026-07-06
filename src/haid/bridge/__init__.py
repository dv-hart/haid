"""The bridge: window → (diff, usage) — the join between the real-session pipeline and the
scoring stack.

The scorer (volume / difficulty / cleanliness / value) was built and validated against
calibration diffs; the session pipeline (session → graph → metrics) ingests real transcripts.
This package connects them: given an analysis window it produces the two inputs the scorer
needs — a reconstructed unified **diff** and a normalized-token **cost** — so `haid value` runs
on real work.

Design (recorded in the project notes, decided after measuring the gap):
  - **Replay-primary, no git.** The diff is reconstructed from the transcript (see
    `reconstruct`). The bash-write-to-source gap was measured at ~0–1% on real projects; what
    little it misses is detected and FLAGGED, never silently dropped.
  - **Grain-agnostic core.** `window_inputs` slices the whole window; the same engine will slice
    by episode once episodes exist (Phase 2 — episode↔PR alignment is explicitly TBD, not v1).

Stdlib only; no model.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .reconstruct import FileRecon, ReconResult, reconstruct
from .usage import extract_cost

__all__ = ["BridgeResult", "window_inputs", "episode_inputs", "span_inputs", "reconstruct",
           "extract_cost", "FileRecon", "ReconResult"]

_ABS = re.compile(r"^(?:/|[A-Za-z]:[\\/]|\\\\)")   # posix root, drive letter, or UNC


def _is_external(file_id: str) -> bool:
    """A file id that isn't repo-relative — temp files, other repos, /etc — is not part of the
    project work product and must not enter the scored diff. (build.py makes ids repo-relative
    only when the path is under the session cwd; everything else stays absolute.)"""
    return bool(_ABS.match(file_id))


@dataclass
class BridgeResult:
    diff: str                                    # reconstructed unified diff (scorer input)
    cost: object                                 # cost.CostResult (scorer denominator)
    files: list = field(default_factory=list)    # per-file FileRecon (kept for inspection)
    caveats: list = field(default_factory=list)  # honesty surface — no silent gaps

    def summary(self) -> str:
        changed = [f for f in self.files if f.changed]
        incomplete = [f for f in self.files if not f.complete]
        lines = [f"bridge: {len(changed)} changed file(s) reconstructed, "
                 f"{len(incomplete)} flagged incomplete",
                 self.cost.summary()]
        if self.caveats:
            lines.append("caveats:")
            lines.extend(f"  {c}" for c in self.caveats)
        return "\n".join(lines)


def window_inputs(view, sessions) -> BridgeResult:
    """Build the scorer inputs (diff, cost) for a whole analysis window.

    `view` is a metrics.WindowView (its `active_stream` gives the active-branch tool calls in
    order); `sessions` are the loaded Session objects (for token usage + edit content).

    Subagent-authored edits are folded in from the sidecars (input-driven — see
    `subagent_writes`), spliced at the position of their spawning `Agent` call so only
    active-branch subagents reach the diff. Tokens for ALL subagents (any branch) still count on
    the cost side; that asymmetry is intentional.
    """
    from . import subagent_writes as saw
    from ..graph.model import is_write

    tur_by_id = _tur_index(sessions)
    sa = saw.collect(sessions, _is_external)
    sa_by_call = sa.by_call          # bind once (it is a derived view, recomputed per access)
    writes = []
    excluded = sa.external
    for _sid, tc in view.active_stream:
        fid = tc.target_file_id
        if is_write(tc) and fid:
            if _is_external(fid):
                excluded += 1
            else:
                tur = tur_by_id.get(tc.id, {})
                writes.append((fid, tc.tool, tur, tc.write_op, tc.write_content, tc.derived_write))
        # Splice this Agent call's subagent writes in right after it (active-branch only, since
        # an abandoned Agent call never appears in the active stream).
        if tc.id in sa_by_call:
            writes.extend(sa_by_call[tc.id])
    # Subagents we couldn't tie to a spawning call (null meta.toolUseId) — include, don't drop.
    writes.extend(sa.unattributed)

    recon = reconstruct(writes, baselines=_baselines(sessions))
    recon.excluded_external = excluded

    caveats = list(recon.caveats)
    if excluded:
        caveats.append(f"{excluded} write(s) to files outside the project tree "
                       "(temp / other repos) excluded from the diff")
    if sa.n_writes:
        caveats.append(f"{sa.n_writes} file-write(s) authored by {sa.n_agents} subagent(s) "
                       "folded into the diff (reconstructed from tool inputs)")
    if sa.unattributed:
        caveats.append(f"{len(sa.unattributed)} subagent write(s) had no recorded spawning call "
                       "(null meta.toolUseId) — included but not attributable to a branch, so a "
                       "few may belong to an abandoned attempt")

    return BridgeResult(diff=recon.diff, cost=extract_cost(sessions),
                        files=recon.files, caveats=caveats)


def episode_inputs(episode_sessions) -> BridgeResult:
    """Build the scorer inputs (diff, cost) for ONE episode = its subset of whole sessions.

    Because an episode is a set of *whole sessions* (grain decision 2026-06-08), this is just
    `window_inputs` over that subset — no new slicing engine. Two things fall out for free:
      - **episode-relative diff baseline**: `_baselines` takes the earliest captured `originalFile`
        across these sessions only, which is each file's state as it ENTERED the episode (i.e.
        after any earlier episodes touched it), so the diff is the episode's own delta;
      - **clean cost**: `extract_cost` sums these sessions' per-context-window costs — no entangled
        sub-session token split (the whole reason the session is the atomic floor).
    """
    from ..window import build_view
    sub_view = build_view(episode_sessions)
    return window_inputs(sub_view, episode_sessions)


def _write_tuple(tc, tur_by_id: dict) -> tuple:
    """A ToolCall → the (file_id, tool, tur, write_op, write_content, derived) tuple reconstruct
    consumes. Mirrors the per-write shape window_inputs builds from the active stream."""
    return (tc.target_file_id, tc.tool, tur_by_id.get(tc.id, {}),
            tc.write_op, tc.write_content, tc.derived_write)


def _branch_writes(view, branch: str) -> list:
    """The branch's content-modifying writes (repo-relative; externals dropped), chronological."""
    from ..graph.model import is_write
    out = []
    for label, tcs in view.timelines:
        if label != branch:
            continue
        out.extend(tc for tc in tcs
                   if is_write(tc) and tc.target_file_id and not _is_external(tc.target_file_id))
    return sorted(out, key=lambda tc: tc.ts or "")


def span_inputs(view, sessions, *, branch: str, start_ts: str,
                end_ts: str | None = None) -> ReconResult:
    """Reconstruct ONE fix-span's net diff: the delta produced by writes in [start_ts, end_ts) on
    a single `branch` (the "sid:timeline" label from build_view). `end_ts=None` runs to the
    branch's end.

    Baseline = each touched file's state as it ENTERED the span, computed by replaying that
    branch's PRE-span writes — so the diff is the span's OWN delta (the bug present at span entry,
    gone at span exit), correct even for a large file whose in-span edit never captured an
    `originalFile` (where window-entry baselines would otherwise leak pre-span changes in).

    Subagent-authored writes are folded in too (see `subagent_writes`), attributed to this branch
    iff their spawning `Agent` call is on the branch (`saw.on_branch`), then MERGED by timestamp
    with the branch's own main-transcript writes into one stream before the pre/inspan split. This
    matters both ways: an in-span subagent edit reaches the diff, and a PRE-span subagent write
    participates in the span-entry baseline replay just like a main pre-span write — so an in-span
    edit of a file a subagent set up still finds its baseline. (Unattributed subagents — null
    spawning-call id — can't be branch-placed, so `on_branch` omits them and they never enter a
    span; a known limitation, consistent with the branch-scoped footprint/gate consumers.)

    This is the scored substrate for the bug-fix reward (docs/plans/bugfix-reward.md): the fix
    span's diff is what gets placed on the difficulty ladder. **Grain note:** this slices BELOW a
    session, which is sound for a DIFF (replay is line-exact) but NOT for cost — token cost stays
    session-atomic (see `episode_inputs`), so find-cost is attributed separately, never here.
    """
    from . import subagent_writes as saw

    tur_by_id = _tur_index(sessions)
    win_baselines = _baselines(sessions)

    # One timestamp-ordered stream of (ts, reconstruct-tuple): main-transcript branch writes plus
    # this branch's subagent writes. `w.tup is None` (external) is dropped — externals never enter
    # the diff — and `w.ts` is required so the pre/inspan split is well-defined.
    main = [(tc.ts, _write_tuple(tc, tur_by_id)) for tc in _branch_writes(view, branch)]
    sub = saw.collect(sessions, _is_external)
    subw = [(w.ts, w.tup) for w in saw.on_branch(sub, view, branch) if w.tup is not None and w.ts]
    allw = sorted(main + subw, key=lambda x: x[0] or "")

    pre = [t for ts, t in allw if ts and start_ts and ts < start_ts]
    inspan = [t for ts, t in allw
              if ts and start_ts and ts >= start_ts and (end_ts is None or ts < end_ts)]

    # Replay pre-span writes to learn each touched file's state at span entry; only buffer-mode
    # reconstructions carry a trustworthy `final` (hunks mode has no full content), so seed the
    # span baseline from those and fall back to window-entry for the rest.
    pre_recon = reconstruct(pre, baselines=win_baselines)
    span_baselines = {fr.file_id: fr.final for fr in pre_recon.files if fr.mode == "buffer"}

    return reconstruct(inspan, baselines={**win_baselines, **span_baselines})


def _tur_index(sessions) -> dict:
    """tool_use id -> toolUseResult dict, across main + subagent records of every session.

    Pairing key is the tool_use_id inside the result's tool_result block (verified on real
    data — there is no top-level sourceToolUseID)."""
    out: dict[str, dict] = {}
    for s in sessions:
        recs = list(s.parse.records) + [r for sa in s.subagents for r in sa.parse.records]
        for r in recs:
            tur = r.raw.get("toolUseResult")
            if not isinstance(tur, dict):
                continue
            c = r.content
            if not isinstance(c, list):
                continue
            for b in c:
                if isinstance(b, dict) and b.get("type") == "tool_result" and b.get("tool_use_id"):
                    out[b["tool_use_id"]] = tur
                    break
    return out


def _baselines(sessions) -> dict:
    """file_id -> the file's content as it ENTERED the window: the earliest captured
    `originalFile` for that file across all records (any branch, main + subagents), and — as a
    lower-priority source — the content of the earliest FULL, untruncated Read of the file.

    Two reasons a file may lack an `originalFile`: Claude Code omits it on some edits (e.g. large
    files), and subagent edits never carry it at all (sidecars have no enriched result). The Read
    fallback matters most for the "main agent reads, then a subagent edits" pattern: the main
    agent's full Read gives the subagent edit an exact buffer-mode baseline instead of the coarse
    pairs-mode fragment. Files with neither source fall back to hunks/pairs mode in reconstruct().

    The winner per file is the EARLIEST-timestamped candidate (its state as the window opens);
    on an equal timestamp `originalFile` beats a Read, since it is the exact pre-edit content.
    Choosing by timestamp — not by source — matters: a file read at window open and only edited
    later must seed from the Read (window entry), not from the later edit's `originalFile` (a
    mid-window state)."""
    from ..graph.build import _file_id

    # fid -> (ts, priority, content); keep the min (earliest ts, then originalFile over Read).
    cand: dict[str, tuple[str, int, str]] = {}

    def _offer(fid, ts, prio, content):
        if not fid or content is None:
            return
        key = (ts or "", prio)
        cur = cand.get(fid)
        if cur is None or key < (cur[0], cur[1]):
            cand[fid] = (key[0], prio, content)

    for s in sessions:
        cwd = next((r.raw.get("cwd") for r in s.parse.records if r.raw.get("cwd")), None)
        # originalFile — exact pre-edit state (priority 0), main + subagents.
        for r in list(s.parse.records) + [rr for sa in s.subagents for rr in sa.parse.records]:
            tur = r.raw.get("toolUseResult")
            if isinstance(tur, dict) and tur.get("originalFile") is not None:
                path = tur.get("filePath") or (tur.get("file") or {}).get("filePath")
                _offer(_file_id(path, cwd), r.timestamp, 0, tur["originalFile"])
        # full, untruncated Read content — raw file bytes (priority 1), main transcript only.
        for r in s.parse.records:
            tur = r.raw.get("toolUseResult")
            f = tur.get("file") if isinstance(tur, dict) else None
            if not isinstance(f, dict) or f.get("content") is None or f.get("truncatedByTokenCap"):
                continue
            if (f.get("startLine") or 1) != 1:
                continue
            total, num = f.get("totalLines"), f.get("numLines")
            if total is not None and num is not None and num < total:
                continue  # partial read — not a whole-file baseline
            _offer(_file_id(f.get("filePath"), cwd), r.timestamp, 1, f["content"])

    return {fid: content for fid, (_ts, _prio, content) in cand.items()}
