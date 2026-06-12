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

__all__ = ["BridgeResult", "window_inputs", "episode_inputs", "reconstruct", "extract_cost",
           "FileRecon", "ReconResult"]

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
    """
    from ..graph.model import is_write

    tur_by_id = _tur_index(sessions)
    writes = []
    excluded = 0
    for _sid, tc in view.active_stream:
        if not is_write(tc):
            continue
        fid = tc.target_file_id
        if not fid:
            continue
        if _is_external(fid):
            excluded += 1
            continue
        tur = tur_by_id.get(tc.id, {})
        writes.append((fid, tc.tool, tur, tc.write_op, tc.write_content, tc.derived_write))

    recon = reconstruct(writes, baselines=_baselines(sessions))
    recon.excluded_external = excluded

    caveats = list(recon.caveats)
    if excluded:
        caveats.append(f"{excluded} write(s) to files outside the project tree "
                       "(temp / other repos) excluded from the diff")
    subagent_writes = _subagent_write_count(sessions)
    if subagent_writes:
        caveats.append(f"{subagent_writes} subagent file-write call(s) are not yet folded into "
                       "the diff (subagent edit stitching is deferred)")

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
    `originalFile` for that file across all records (any branch, main + subagents).

    Claude Code omits originalFile on some edits (e.g. large files), so the first edit we see
    in the active stream may lack it even though an earlier touch captured it. Sourcing the
    earliest one window-wide gives buffer-mode reconstruction a correct seed; files that never
    captured it fall back to hunks mode in reconstruct()."""
    from ..graph.build import _file_id

    by_first_ts = sorted(sessions, key=lambda s: min(
        (r.timestamp for r in s.parse.records if r.timestamp), default=""))
    out: dict[str, str] = {}
    for s in by_first_ts:
        cwd = next((r.raw.get("cwd") for r in s.parse.records if r.raw.get("cwd")), None)
        for r in list(s.parse.records) + [rr for sa in s.subagents for rr in sa.parse.records]:
            tur = r.raw.get("toolUseResult")
            if not isinstance(tur, dict) or tur.get("originalFile") is None:
                continue
            path = tur.get("filePath") or (tur.get("file") or {}).get("filePath")
            fid = _file_id(path, cwd)
            if fid and fid not in out:
                out[fid] = tur["originalFile"]
    return out


def _subagent_write_count(sessions) -> int:
    from ..graph.model import is_write
    from ..graph.build import build_graph
    n = 0
    for s in sessions:
        for sa in s.subagents:
            g = build_graph(sa.parse.records)
            n += sum(1 for tc in g.toolcalls.values()
                     if is_write(tc) and tc.target_file_id and not _is_external(tc.target_file_id))
    return n
