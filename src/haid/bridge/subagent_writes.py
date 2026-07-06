"""Extract a subagent's file writes — input-driven — for the reconstructed diff and its consumers.

The main transcript stores Claude Code's *enriched* tool result (`toolUseResult` with
`originalFile` + `structuredPatch`), and `reconstruct` replays from that. Subagent sidecars
do NOT: they store the raw API message shape, so an Edit/Write result is just a human string
("The file … has been updated") with no `originalFile` and no `structuredPatch` (measured
0/194 subagent transcripts on real data). Everything reconstruction needs is instead in the
**tool_use input**, which sidecars keep in full:

  - Edit       → input.old_string / new_string / replace_all
  - MultiEdit  → input.edits[] (each old_string / new_string / replace_all)
  - Write      → input.content
  - Bash       → heredoc content recovered by the graph builder (same as the main path)

So we synthesize a `toolUseResult`-shaped dict from the input and hand the same write tuple the
engine already consumes. A subagent edit of a pre-existing file has no baseline in the sidecar,
so it reconstructs in `reconstruct`'s `pairs` mode (fragment from old/new strings) — UNLESS the
main agent read or edited the file first, in which case `_baselines` seeds an exact buffer-mode
replay.

Attribution: each subagent links to its spawning `Agent` tool_use via `meta.toolUseId`. Callers
place a subagent's writes at that call's position / branch, so a subagent under an
abandoned/rewound `Agent` call is naturally excluded from the diff (its tokens still count on the
cost side — that asymmetry is intentional). Subagents with a null `toolUseId` (rare on current
data: 0/194) can't be branch-placed, so they surface separately to be included-with-a-flag rather
than silently dropped.

This module is the single source of subagent-write extraction shared by every consumer:
  - the window diff (`window_inputs`)         — via `.by_call` / `.unattributed` (order-spliced),
  - the fix-span diff (`span_inputs`)          — via `on_branch(...)` (timestamp-merged),
  - the fix-span footprint (`why.bug_anchors`) — via `on_branch(...)` `.tc`,
  - the self-inflicted gate (`scoring.bugfix`) — via `on_branch(...)` `.tc` authorship.
Each `SubWrite` therefore carries both the raw `ToolCall` (footprint / authorship) and the
synthesized reconstruct `tup` (diff), plus its `ts` and spawning-call link. Stdlib only; no model.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..graph.build import build_graph, timeline_toolcalls
from ..graph.model import is_write
from ..session.forest import Forest


@dataclass
class SubWrite:
    """One subagent write, with everything its consumers need.

    `tup` is the `(file_id, tool, tur_like, write_op, write_content, derived)` tuple `reconstruct`
    consumes; it is None for an external write (excluded from the diff but still surfaced/counted).
    `tc` is the raw ToolCall (target_file_id / params / ts) for footprint + authorship consumers."""
    parent_call_id: str | None    # spawning Agent tool_use id (meta.toolUseId); None if unrecorded
    ts: str | None
    tc: object                    # graph.model.ToolCall
    agent_id: str | None
    tup: tuple | None = None      # reconstruct tuple; None iff external
    external: bool = False


@dataclass
class SubagentWrites:
    """All subagent writes across the window, plus the derived views the window path expects."""
    writes: list = field(default_factory=list)   # list[SubWrite], in per-subagent active order

    @property
    def external(self) -> int:
        return sum(1 for w in self.writes if w.external)

    @property
    def n_writes(self) -> int:
        """Folded (non-external) writes — the count reported in the window caveat."""
        return sum(1 for w in self.writes if not w.external)

    @property
    def n_agents(self) -> int:
        return len({w.agent_id for w in self.writes if not w.external})

    @property
    def by_call(self) -> dict:
        """{parent Agent call id -> [reconstruct tuple]} for attributed, non-external writes.
        The window path splices these in at the spawning call's position in the active stream."""
        out: dict = {}
        for w in self.writes:
            if w.external or w.tup is None or w.parent_call_id is None:
                continue
            out.setdefault(w.parent_call_id, []).append(w.tup)
        return out

    @property
    def unattributed(self) -> list:
        """Reconstruct tuples with no recorded spawning call (null meta.toolUseId) — included
        with a flag rather than dropped."""
        return [w.tup for w in self.writes
                if not w.external and w.tup is not None and w.parent_call_id is None]


def _tur_like(tc) -> dict:
    """A `toolUseResult`-shaped dict synthesized from a subagent write's tool_use input, so
    `reconstruct` can consume it exactly like a native result. No `originalFile`/`structuredPatch`
    (the sidecar has neither) — buffer mode still fires if `_baselines` seeds a baseline, else
    the engine falls to pairs mode."""
    p = tc.params or {}
    if tc.tool == "MultiEdit":
        return {"edits": [{"old_string": e.get("old_string", ""),
                           "new_string": e.get("new_string", ""),
                           "replace_all": e.get("replace_all", False)}
                          for e in (p.get("edits") or [])]}
    if tc.tool == "Edit":
        return {"edits": [{"old_string": p.get("old_string", ""),
                           "new_string": p.get("new_string", ""),
                           "replace_all": p.get("replace_all", False)}]}
    if tc.tool == "Write":
        return {"content": p.get("content")}
    return {}


def _ordered_subwrites(sa, is_external) -> list:
    """One subagent's content-modifying writes as SubWrite records, in active-timeline order.

    Reuses the same graph + forest + per-timeline scoping as the main path, so subagent
    branching/rewinds are handled identically."""
    g = build_graph(sa.parse.records)
    forest = Forest(sa.parse.records)
    active = next((tl for tl in forest.timelines() if tl.is_active), None)
    tcs = timeline_toolcalls(g, active) if active else list(g.toolcalls.values())
    out = []
    for tc in tcs:
        if not is_write(tc) or not tc.target_file_id:
            continue
        ext = is_external(tc.target_file_id)
        tup = None if ext else (tc.target_file_id, tc.tool, _tur_like(tc),
                                tc.write_op, tc.write_content, tc.derived_write)
        out.append(SubWrite(parent_call_id=sa.parent_tool_use_id, ts=tc.ts, tc=tc,
                            agent_id=sa.agent_id, tup=tup, external=ext))
    return out


def collect(sessions, is_external) -> SubagentWrites:
    """Gather every session's subagents' writes as SubWrite records.

    `is_external(file_id)` decides whether a path is outside the project tree (passed in to
    avoid importing the bridge package back into itself)."""
    res = SubagentWrites()
    for s in sessions:
        for sa in s.subagents:
            res.writes.extend(_ordered_subwrites(sa, is_external))
    return res


# --- branch attribution (shared by span diff, span footprint, self-inflicted gate) ----------

def branch_call_ids(view, branch: str) -> set:
    """Every tool-call id on one `sid:timeline` branch of the view — the ids a subagent's
    `parent_call_id` must be among for the subagent to belong to that branch."""
    return {tc.id for label, tcs in view.timelines if label == branch for tc in tcs}


def on_branch(sub: SubagentWrites, view, branch: str) -> list:
    """The SubWrite records whose spawning Agent call is on `branch`, in timestamp order.

    A subagent runs strictly between its Agent call's request and result, so its write
    timestamps fall inside that branch's time flow — callers merge these by `ts` with the
    branch's own writes."""
    ids = branch_call_ids(view, branch)
    on = [w for w in sub.writes if w.parent_call_id in ids]
    return sorted(on, key=lambda w: w.ts or "")
