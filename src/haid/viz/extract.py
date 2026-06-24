"""Extract one session's bus-diagram spine — the per-session input the visualizer consumes.

Promoted from the prototype feeder `scripts/viz_extract.py` into the package: same Tier-1
IO graph + cumulative cost axis, but parameterized off an already-loaded `Session` (the
window layer hands these in) instead of re-discovering a hardcoded project path. The spine
is the ACTIVE timeline only — the same scope the metrics score.

Each tool call carries: tool, file (repo-relative display), file_id, direction
(in/out/both/none), token_weight, the derived-shell flag, and status. Turns carry role +
a short text preview. The right margin's cost axis is the running cumulative
normalized-token tally from scoring/cost.py.
"""

from __future__ import annotations

import re
from pathlib import Path

_ABS = re.compile(r"^(?:/|[A-Za-z]:[\\/]|\\\\)")   # posix root, drive letter, or UNC

from ..graph import build
from ..graph.model import is_read, is_write
from ..scoring import cost as C
from ..session.loader import Session


def session_stem(session: Session) -> str:
    """The 8-char id episodes and the window layer key by (summarize.py / window.py)."""
    return Path(session.path).stem[:8]


def short_file(fid: str, project_path: str | None) -> str:
    """Display name for a file id. The graph already stores repo-relative ids, so those pass
    through unchanged; only genuinely absolute/external paths are shortened (strip the project
    root if it's under it, else basename)."""
    f = fid.replace("\\", "/")
    if not _ABS.match(f):
        return f                                  # already repo-relative — leave it
    if project_path:
        root = project_path.replace("\\", "/").rstrip("/")
        if root and (f == root or f.startswith(root + "/")):
            return f[len(root) + 1:]
        tail = "/" + Path(root).name + "/"
        if tail in f:
            return f.split(tail, 1)[1]
    return f.split("/")[-1]


def _est_tokens(b: int) -> int:
    return b // 4


def call_detail(tc) -> str:
    """A short human-readable description of what a tool call did — the square's content."""
    p = tc.params or {}
    t = tc.tool
    if t == "Bash":
        cmd = (p.get("command") or "").strip().replace("\n", " ")
        m = re.match(r'''cd\s+["']?[^"'&]+["']?\s*&&\s*''', cmd)
        if m:
            cmd = cmd[m.end():]
        return cmd[:80]
    if t in ("Grep", "Glob"):
        return (p.get("pattern") or "")[:60]
    if t == "Read" and tc.read_span:
        return f"lines {tc.read_span[0]}–{tc.read_span[1]}"
    if t == "Agent":
        return (p.get("description") or p.get("subagent_type") or "")[:60]
    if t == "TodoWrite":
        return f"{len(p.get('todos') or [])} item(s)"
    return ""  # Edit/MultiEdit/Write: file shown separately


def _msg_ntok(raw: dict) -> float:
    msg = raw.get("message") or {}
    u = msg.get("usage")
    if not isinstance(u, dict):
        return 0.0
    d = dict(u)
    d["model"] = msg.get("model", "")
    return C.measure([C.Usage.from_dict(d)]).normalized_tokens


def extract_session(session: Session, project_path: str | None = None) -> dict:
    """The compact bus-diagram bundle for one loaded session (active timeline only)."""
    g = build.build_graph(session.parse.records)
    timelines = session.forest.timelines()
    active = next((t for t in timelines if t.is_active), None)
    if active is None:
        return {"session": Path(session.path).name, "stem": session_stem(session),
                "n_toolcalls": 0, "n_files": 0, "total_ntok": 0, "files": [], "spine": []}

    tcs = build.timeline_toolcalls(g, active)
    tc_by_turn: dict[str, list] = {}
    for tc in tcs:
        tc_by_turn.setdefault(tc.turn_id, []).append(tc)

    file_order: list[str] = []

    def color_idx(fid: str) -> int:
        if fid not in file_order:
            file_order.append(fid)
        return file_order.index(fid)

    by_uuid = session.forest.by_uuid
    cum = 0.0
    spine: list[dict] = []
    for uuid in active.node_uuids:
        r = by_uuid.get(uuid)
        if r is None:
            continue
        cum += _msg_ntok(r.raw)
        if r.type == "user" and r.is_user_prompt():
            spine.append({"kind": "user", "ts": r.timestamp,
                          "text": r.text().strip()[:160], "cum_ntok": round(cum)})
        elif r.type == "assistant":
            text = r.text().strip()
            item = {"kind": "assistant", "ts": r.timestamp, "text": text[:160],
                    "has_text": bool(text), "cum_ntok": round(cum), "calls": []}
            for tc in tc_by_turn.get(uuid, []):
                fid = tc.target_file_id
                rd, wr = is_read(tc), is_write(tc)
                direction = ("both" if rd and wr else "in" if rd
                             else "out" if wr else "none")
                item["calls"].append({
                    "tool": tc.tool,
                    "file": short_file(fid, project_path) if fid else None,
                    "file_id": fid,
                    "color": color_idx(fid) if fid else None,
                    "direction": direction,
                    "token_weight": _est_tokens(tc.result_bytes),
                    "derived": bool(tc.derived_read or tc.derived_write),
                    "status": tc.status,
                    "read_span": list(tc.read_span) if tc.read_span else None,
                    "detail": call_detail(tc),
                })
            spine.append(item)

    files: dict[str, dict] = {}
    for it in spine:
        if it["kind"] != "assistant":
            continue
        for c in it["calls"]:
            if not c["file_id"]:
                continue
            f = files.setdefault(c["file_id"], {
                "file": c["file"], "color": c["color"],
                "in_tok": 0, "out_tok": 0, "reads": 0, "writes": 0, "touches": 0})
            f["touches"] += 1
            if c["direction"] in ("in", "both"):
                f["in_tok"] += c["token_weight"]; f["reads"] += 1
            if c["direction"] in ("out", "both"):
                f["out_tok"] += c["token_weight"]; f["writes"] += 1

    return {
        "session": Path(session.path).name,
        "stem": session_stem(session),
        "n_toolcalls": len(tcs),
        "n_files": len(files),
        "total_ntok": round(cum),
        "files": sorted(files.values(), key=lambda f: -(f["in_tok"] + f["out_tok"])),
        "spine": spine,
    }
