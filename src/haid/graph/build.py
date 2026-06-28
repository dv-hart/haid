"""Build the L0 spine + L1 action/IO graph from parsed records — all Tier 1.

Construction order (docs/session-graph-design.md):
  L0  Turn nodes for every threaded record + responds-to edges (parentUuid).
  L1  ToolCall nodes from assistant tool_use blocks, paired to results via
      sourceToolUseID; reads/produces/edits edges to File/Region nodes, with line ranges
      read STRAIGHT OFF the result's structuredPatch — no diff engine. Signatures (Tier 2)
      computed per call.

Works on any record list, so the loader builds one graph for the main transcript and one
per subagent. Per-timeline metric scoping (Step 3+) uses forest.timelines() to walk
ToolCalls along a single root->leaf path. Stdlib only; no model.
"""

from __future__ import annotations

import hashlib

from ..session import records as rec
from . import bash_read, bash_write, signature as sig
from .model import Edge, File, Region, SessionGraph, ToolCall, Turn, is_read

_READ = {"Read"}
_EDIT = {"Edit", "MultiEdit"}
_WRITE = {"Write"}


def _norm(p: str) -> str:
    return p.replace("\\", "/")


def _file_id(path: str | None, cwd: str | None) -> str | None:
    if not path:
        return None
    np = _norm(path)
    if cwd:
        nc = _norm(cwd).rstrip("/") + "/"
        if np.startswith(nc):
            return np[len(nc):]
    return np


def _anchor(lines: list[str]) -> str:
    return hashlib.sha256("\n".join(lines).encode("utf-8", "replace")).hexdigest()[:16]


def _result_bytes(tool: str, tur: dict) -> int:
    if tool in _READ:
        f = tur.get("file") or {}
        return len(f.get("content") or "")
    if tool == "Bash":
        return len(tur.get("stdout") or "") + len(tur.get("stderr") or "")
    if tool in _EDIT:
        return len(tur.get("newString") or "")
    if tool in _WRITE:
        return len(tur.get("content") or "")
    return len(str(tur.get("content") or ""))


def _block_text(b: dict) -> str:
    """Text of a tool_result content block (a plain string OR a list of text blocks)."""
    c = b.get("content")
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return "\n".join(x.get("text") or "" for x in c
                         if isinstance(x, dict) and x.get("type") == "text")
    return ""


def _result_index(records: list[rec.Record]) -> dict[str, dict]:
    """tool_use id -> {tur, is_error, error_text} from result-bearing user records.

    Pairing key is the `tool_use_id` INSIDE the tool_result content block (verified on
    real data — there is NO top-level `sourceToolUseID`; `sourceToolAssistantUUID` only
    points at the assistant turn). Falls back to `sourceToolUseID` for other versions.

    FAILURES are surfaced by `is_error` on the tool_result block (Bash included) and the
    error results carry NO `toolUseResult` dict — so index on the block, not on the dict,
    or every failed call is dropped and mis-marked 'unknown'. For the same reason the
    error TEXT lives in the block's content, not in `toolUseResult`; we keep it so the
    retry metric can tell a same-error loop from a later attempt failing differently."""
    out: dict[str, dict] = {}
    for r in records:
        tur = r.raw.get("toolUseResult")
        tur = tur if isinstance(tur, dict) else {}
        tid, is_error, has_block, err_text = None, False, False, ""
        c = r.content
        if isinstance(c, list):
            for b in c:
                if isinstance(b, dict) and b.get("type") == "tool_result":
                    has_block = True
                    tid = tid or b.get("tool_use_id")
                    if b.get("is_error"):
                        is_error = True
                        err_text = err_text or _block_text(b)
        tid = tid or r.raw.get("sourceToolUseID")
        if tid and (has_block or tur):
            out[tid] = {"tur": tur, "is_error": is_error,
                        "error_text": err_text or None}
    return out


def _read_span(tur: dict) -> tuple[int, int] | None:
    f = tur.get("file")
    if isinstance(f, dict) and "startLine" in f:
        return (f.get("startLine") or 0, f.get("numLines") or 0)
    return None


def build_graph(records: list[rec.Record], session_id: str | None = None,
                cwd: str | None = None) -> SessionGraph:
    if cwd is None:
        for r in records:
            if r.raw.get("cwd"):
                cwd = r.raw["cwd"]
                break
    g = SessionGraph(session_id=session_id, cwd=cwd)

    # L0: turns + responds-to
    for r in records:
        if not r.is_threaded():
            continue
        g.turns[r.uuid] = Turn(
            id=r.uuid, role=r.type, parent_uuid=r.parent_uuid, ts=r.timestamp,
            is_tool_result=r.has_tool_result(), agent_id=r.agent_id,
        )
    for t in g.turns.values():
        if t.parent_uuid and t.parent_uuid in g.turns:
            g.edges.append(Edge(src=t.id, dst=t.parent_uuid, type="responds-to", ts=t.ts))

    # L1: tool calls + I/O edges
    results = _result_index(records)
    seen_call_ids: set[str] = set()
    for r in records:
        if r.type != "assistant" or not isinstance(r.content, list):
            continue
        for b in r.content:
            if not (isinstance(b, dict) and b.get("type") == "tool_use"):
                continue
            cid = b.get("id")
            if not cid:
                continue
            seen_call_ids.add(cid)
            tool = b.get("name", "~unknown")
            params = b.get("input") or {}
            res = results.get(cid)
            tur = res["tur"] if res else {}
            path = (tur.get("filePath")
                    or (tur.get("file") or {}).get("filePath")
                    or params.get("file_path"))
            fid = _file_id(path, cwd)
            rspan = _read_span(tur)
            read_span_abs = (rspan[0], rspan[0] + rspan[1]) if rspan else None
            derived_read = False
            derived_write = False
            write_op = None
            write_content = None
            # A Bash command can read or write a single file with no filePath in its
            # result — recover the file (+ line range / op) from the command string.
            if tool == "Bash" and not fid:
                command = params.get("command", "")
                intent = bash_read.parse_bash_read(command, tur.get("stdout"))
                if intent:
                    bfid = _file_id(intent[0], cwd)
                    if bfid:
                        fid, read_span_abs, derived_read, path = (
                            bfid, intent[1], True, path or intent[0])
                else:
                    wintent = bash_write.parse_bash_write(command)
                    hintent = wintent or bash_write.parse_heredoc_write(command)
                    if hintent:
                        bfid = _file_id(hintent[0], cwd)
                        if bfid:
                            # heredoc form carries recovered content (3-tuple); others don't.
                            content = hintent[2] if len(hintent) > 2 else None
                            fid, derived_write, write_op, write_content, path = (
                                bfid, True, hintent[1], content, path or hintent[0])
            if fid and fid not in g.files:
                g.files[fid] = File(id=fid, path=path)
            status = ("error" if (res and res["is_error"])
                      else "ok" if res else "unknown")
            tc = ToolCall(
                id=cid, tool=tool, turn_id=r.uuid, ts=r.timestamp, params=params,
                status=status, error_text=(res or {}).get("error_text"),
                result_bytes=_result_bytes(tool, tur),
                signature=sig.signature(tool, params, fid, rspan),
                target_file_id=fid,
                read_span=read_span_abs,
                derived_read=derived_read,
                derived_write=derived_write,
                write_op=write_op,
                write_content=write_content,
            )
            g.toolcalls[cid] = tc
            _io_edges(g, tc, tur, fid)

    g.unpaired_results = sum(1 for tid in results if tid not in seen_call_ids)
    return g


def timeline_toolcalls(g: SessionGraph, timeline) -> list[ToolCall]:
    """ToolCalls along one timeline (root->leaf), in order. This is the per-timeline
    scope every Step-3 waste metric must run within — never the whole graph at once, or
    repeats across abandoned branches become phantom findings."""
    by_turn: dict[str, list[ToolCall]] = {}
    for tc in g.toolcalls.values():
        by_turn.setdefault(tc.turn_id, []).append(tc)
    out: list[ToolCall] = []
    for uuid in timeline.node_uuids:
        out.extend(by_turn.get(uuid, []))
    return out


def _io_edges(g: SessionGraph, tc: ToolCall, tur: dict, fid: str | None) -> None:
    if is_read(tc) and fid:
        # Native Read edges keep the (startLine, numLines) result span; Bash-derived
        # reads have no result span, so fall back to the parsed absolute read_span.
        span = _read_span(tur) or tc.read_span
        g.edges.append(Edge(src=tc.id, dst=fid, type="reads", ts=tc.ts,
                            attrs={"span": span, "bytes": tc.result_bytes}))
        return
    if tc.derived_write and fid:
        # Shell write (sed -i / redirection / tee / cp / mv). No structuredPatch and no
        # recoverable content, so we emit a file-level edge (no Region) and stop.
        etype = "edits" if tc.write_op == "edit" else "produces"
        g.edges.append(Edge(src=tc.id, dst=fid, type=etype, ts=tc.ts,
                            attrs={"op": tc.write_op, "via": "shell"}))
        return
    if tc.tool in _WRITE and fid:
        g.edges.append(Edge(src=tc.id, dst=fid, type="produces", ts=tc.ts,
                            attrs={"op": "write"}))
    if tc.tool in _EDIT and fid:
        g.edges.append(Edge(src=tc.id, dst=fid, type="edits", ts=tc.ts))
    # Region materialization from structuredPatch (Edit + Write both ship it).
    for hunk in (tur.get("structuredPatch") or []):
        if not isinstance(hunk, dict) or not fid:
            continue
        lines = hunk.get("lines") or []
        anchor = _anchor(lines)
        rid = f"{fid}:{anchor}"
        start = hunk.get("newStart") or 0
        span = (start, start + (hunk.get("newLines") or 0))
        if rid not in g.regions:
            g.regions[rid] = Region(id=rid, file_id=fid, anchor_hash=anchor, current_span=span)
        g.edges.append(Edge(src=tc.id, dst=rid,
                            type="edits" if tc.tool in _EDIT else "produces",
                            ts=tc.ts, attrs={"span": span}))
