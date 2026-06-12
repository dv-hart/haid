"""Session parsing layer (Phase 1, Step 1): JSONL -> typed records -> forest model.

Deterministic and model-free. Public surface:
  - loader.load_session(path) -> Session          (main + subagents + overflow + forest)
  - parse.parse_file(path) -> ParseResult         (tolerant line reader + drift report)
  - forest.Forest(records)                         (roots, active branch, rewinds, timelines)
  - subagents.discover_subagents(path)             (stitched sidecar agents)
  - overflow.overflow_of(record)                   (lazy persisted-output handles)
  - cache.load_or_parse(path)                      (SQLite parse cache, content-hash keyed)
  - discover.find_sessions(project_path)           (locate a project's transcripts)
  - records.Record                                 (typed view + content classification)
"""

from __future__ import annotations

from . import cache, discover, forest, loader, overflow, parse, records, subagents
from .forest import Forest, Rewind, Timeline
from .loader import Session, load_session
from .overflow import Overflow
from .parse import ParseResult, parse_file
from .records import Record
from .subagents import Subagent

__all__ = [
    "cache", "discover", "forest", "loader", "overflow", "parse", "records", "subagents",
    "Forest", "Rewind", "Timeline", "Session", "load_session", "Overflow",
    "ParseResult", "parse_file", "Record", "Subagent",
]
