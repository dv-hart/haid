"""Normalized tool-call signatures — the Tier-2 key that powers retry/redundancy queries.

A signature turns "did the model do the same thing twice?" into an O(group-by) instead of
O(n²) scan (docs/session-graph-design.md §ToolCall). The design's normalization rules:
  - Bash  -> normalized command (collapse whitespace; strip a trailing volatile token-ish
            tail is left to Step 3 if needed). Same command => same signature => retry.
  - Read  -> (file_id, start_line, num_lines) — exact range from the result.
  - Edit  -> (file_id, hash(old_string)) — same target text => same edit attempt.
  - Write -> (file_id,) — writing the same file.
  - other -> (tool, file_id_or_param_hash)

Signature *changing* across a chain = adaptation (not waste); identical = thrash. Stdlib only.
"""

from __future__ import annotations

import hashlib
import re

_WS = re.compile(r"\s+")


def _h(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8", "replace")).hexdigest()[:16]


def normalize_command(cmd: str) -> str:
    return _WS.sub(" ", (cmd or "").strip())


def signature(tool: str, params: dict, file_id: str | None,
              read_span: tuple[int, int] | None = None) -> tuple:
    """Compute a tool call's signature. `read_span` = (start_line, num_lines) from the
    Read result when available (more precise than the offset/limit request)."""
    p = params or {}
    if tool == "Bash":
        return ("Bash", normalize_command(p.get("command", "")))
    if tool == "Read":
        if read_span is not None:
            return ("Read", file_id, read_span[0], read_span[1])
        return ("Read", file_id, p.get("offset"), p.get("limit"))
    if tool in ("Edit", "MultiEdit"):
        return ("Edit", file_id, _h(p.get("old_string", "")))
    if tool == "Write":
        return ("Write", file_id)
    if tool in ("Grep", "Glob"):
        return (tool, p.get("pattern"), file_id or p.get("path"))
    # Fallback: tool + a stable hash of its inputs.
    return (tool, file_id or _h(repr(sorted(p.items()))))
