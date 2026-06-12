"""Resolve overflowed (persisted) tool outputs.

When a tool's output is too large it is truncated in the transcript and the full text is
written to a sidecar file. The result record points at it explicitly:
  toolUseResult.persistedOutputPath = "<abs path>/tool-results/<id>.txt"
and the content carries a "<persisted-output>\\nOutput too large (NN KB). Full output saved
to: ..." marker. Filenames vary (`toolu_<id>.txt` vs short random ids) — we always follow
the explicit path, so the naming difference is irrelevant.

Resolution is LAZY: the four MVP metrics need token weights and file targets, not the full
overflow text, so we record availability + load on demand. Reads also flag
`truncatedByTokenCap`. Stdlib only.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from . import records as rec


@dataclass
class Overflow:
    tool_use_id: str | None
    persisted_path: str | None
    available: bool          # the persisted file exists on disk

    def load(self) -> str | None:
        if not (self.persisted_path and self.available):
            return None
        try:
            return Path(self.persisted_path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None


def _tool_use_result(r: rec.Record) -> dict:
    tur = r.raw.get("toolUseResult")
    return tur if isinstance(tur, dict) else {}


def _tool_use_id(r: rec.Record) -> str | None:
    """The paired call's id: the tool_result block's `tool_use_id` (no top-level
    `sourceToolUseID` exists in real data); fall back to it for other versions."""
    c = r.content
    if isinstance(c, list):
        for b in c:
            if isinstance(b, dict) and b.get("type") == "tool_result" and b.get("tool_use_id"):
                return b["tool_use_id"]
    return r.raw.get("sourceToolUseID")


def overflow_of(r: rec.Record, tool_results_dir: str | Path | None = None) -> Overflow | None:
    """Return an Overflow handle if this record's tool result was persisted, else None.

    `persistedOutputPath` is an ABSOLUTE path from the machine that wrote the session, so
    it can fail to resolve if the tree was moved or is read cross-context (e.g. a WSL path
    read from Windows). When `tool_results_dir` is given, fall back to its `<basename>`."""
    path = _tool_use_result(r).get("persistedOutputPath")
    if not path:
        return None
    resolved = path
    if not Path(path).is_file() and tool_results_dir:
        alt = Path(tool_results_dir) / Path(path).name
        if alt.is_file():
            resolved = str(alt)
    return Overflow(
        tool_use_id=_tool_use_id(r),
        persisted_path=resolved,
        available=Path(resolved).is_file(),
    )


def was_truncated(r: rec.Record) -> bool:
    """True if a Read result was capped by the token limit (full content not inline)."""
    tur = _tool_use_result(r)
    f = tur.get("file")
    if isinstance(f, dict) and f.get("truncatedByTokenCap"):
        return True
    return bool(tur.get("persistedOutputPath"))
