"""Session graph layer (Phase 1, Step 2): records -> L0 spine + L1 action/IO graph.

Deterministic, Tier 1 (+ Tier-2 signatures). Public surface:
  - build.build_graph(records, cwd=...) -> SessionGraph
  - build.timeline_toolcalls(graph, timeline) -> [ToolCall]   (per-timeline metric scope)
  - model.SessionGraph / Turn / ToolCall / File / Region / Edge
"""

from __future__ import annotations

from . import build, model, signature
from .build import build_graph, timeline_toolcalls
from .model import Edge, File, Region, SessionGraph, ToolCall, Turn

__all__ = [
    "build", "model", "signature",
    "build_graph", "timeline_toolcalls",
    "Edge", "File", "Region", "SessionGraph", "ToolCall", "Turn",
]
