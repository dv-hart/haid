"""Load a complete session: main transcript + subagents + overflow handles + forest.

This is the public output of Step 1 — the object Step 2 (the L0/L1 graph) consumes. It
composes the focused modules:
  - parse/cache : tolerant parse of the main `<uuid>.jsonl` (cached by content hash)
  - forest      : the branch model (roots, active branch, rewinds, timelines)
  - subagents   : stitched sidecar agents, linked by meta.toolUseId -> parent Agent call
  - overflow    : lazy handles for persisted (too-large) tool outputs

Stdlib only; no model.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from . import cache, overflow
from .forest import Forest
from .parse import ParseResult
from .subagents import Subagent, discover_subagents


@dataclass
class Session:
    path: str
    parse: ParseResult
    forest: Forest
    subagents: list[Subagent] = field(default_factory=list)
    cache_hit: bool = False

    @property
    def subagent_forests(self) -> dict[str, Forest]:
        """Per-subagent forest, keyed by agentId (each is its own tree scope)."""
        return {sa.agent_id: Forest(sa.parse.records) for sa in self.subagents}

    def overflow_handles(self) -> list[overflow.Overflow]:
        """Lazy handles for every persisted (overflowed) tool result, main + subagents.

        Passes the session's own `tool-results/` dir as a portability fallback for absolute
        persistedOutputPaths that don't resolve in the current context."""
        tr_dir = Path(self.path).with_suffix("") / "tool-results"
        out = []
        for r in self.parse.records:
            o = overflow.overflow_of(r, tool_results_dir=tr_dir)
            if o:
                out.append(o)
        for sa in self.subagents:
            for r in sa.parse.records:
                o = overflow.overflow_of(r, tool_results_dir=tr_dir)
                if o:
                    out.append(o)
        return out

    def warnings(self) -> list[str]:
        """No-silent-caps surface: parse drift + any unresolved overflow / subagent gaps."""
        w = list(self.parse.warnings())
        missing = [o for o in self.overflow_handles() if not o.available]
        if missing:
            w.append(f"{len(missing)} persisted tool-output file(s) referenced but missing on disk")
        # Subagent attribution gaps (two distinct cases, both surfaced).
        main_tool_use_ids = {
            b.get("id")
            for r in self.parse.records
            for b in (r.content if isinstance(r.content, list) else [])
            if isinstance(b, dict) and b.get("type") == "tool_use"
        }
        no_id = [sa for sa in self.subagents if not sa.parent_tool_use_id]
        unmatched = [sa for sa in self.subagents
                     if sa.parent_tool_use_id and sa.parent_tool_use_id not in main_tool_use_ids]
        if no_id:
            w.append(f"{len(no_id)} subagent(s) with no recorded toolUseId — parsed, but "
                     "can't be tied to a specific spawning call (meta.toolUseId null)")
        if unmatched:
            w.append(f"{len(unmatched)} subagent(s) whose toolUseId has no parent Agent call "
                     "in this transcript (may live in a resumed sibling session)")
        return w

    def summary(self) -> dict:
        s = dict(self.forest.summary())
        s["subagents"] = len(self.subagents)
        s["overflow_persisted"] = len(self.overflow_handles())
        s["cache_hit"] = self.cache_hit
        return s


def load_session(path: str | Path, db_path: str | Path | None = None,
                 use_cache: bool = True) -> Session:
    parse_res, hit = cache.load_or_parse(path, db_path=db_path, use_cache=use_cache)
    return Session(
        path=str(path),
        parse=parse_res,
        forest=Forest(parse_res.records),
        subagents=discover_subagents(path),
        cache_hit=hit,
    )
