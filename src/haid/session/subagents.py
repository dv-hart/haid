"""Discover and stitch subagent sidecar transcripts.

When the main agent fires an `Agent` tool, the subagent's turns are written to a sidecar:
  <session-uuid>/subagents/agent-<agentId>.jsonl   (its own conversation tree)
  <session-uuid>/subagents/agent-<agentId>.meta.json = {agentType, description, toolUseId}

`meta.toolUseId` links back to the parent `Agent` tool_use block (id == toolUseId) in the
main transcript — verified exact on real data. Subagent records carry their own `agentId`
and `isSidechain:true`, and form an independent tree (own parentUuid root), so each is
modeled as its own Forest scope, attached to the parent session under the spawning call.

Robust to the variants seen on disk: a `subagents/` dir may also contain a nested
`workflows/` tree holding THOUSANDS of workflow-orchestration agent files (e.g. 2581 in
one real session). Those are NOT session subagents — we match only TOP-LEVEL
`subagents/agent-*.jsonl`, never recurse. Nested sub-subagents (a subagent spawning its
own) are not yet observed; deferred (open-questions V3). Stdlib only.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .parse import ParseResult, parse_file


@dataclass
class Subagent:
    agent_id: str                 # from filename agent-<id>.jsonl (== record agentId)
    meta: dict                    # {agentType, description, toolUseId}
    parse: ParseResult
    jsonl_path: str

    @property
    def parent_tool_use_id(self) -> str | None:
        return self.meta.get("toolUseId")

    @property
    def agent_type(self) -> str | None:
        return self.meta.get("agentType")


def sidecar_dir(session_path: str | Path) -> Path:
    """The `<session-uuid>/` sidecar directory next to a `<session-uuid>.jsonl`."""
    p = Path(session_path)
    return p.with_suffix("")  # strip .jsonl -> dir of the same stem


def discover_subagents(session_path: str | Path) -> list[Subagent]:
    """Find, parse, and meta-link all subagent sidecars for a session (recursive, to
    catch nested subagents/workflows trees)."""
    base = sidecar_dir(session_path) / "subagents"
    if not base.is_dir():
        return []
    out: list[Subagent] = []
    for jsonl in sorted(base.glob("agent-*.jsonl")):  # top-level only — see module docstring
        meta_path = jsonl.with_suffix(".meta.json")
        meta = {}
        if meta_path.is_file():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                meta = {}
        agent_id = jsonl.stem[len("agent-"):]
        out.append(Subagent(
            agent_id=agent_id,
            meta=meta,
            parse=parse_file(str(jsonl)),
            jsonl_path=str(jsonl),
        ))
    return out
