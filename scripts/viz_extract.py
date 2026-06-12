"""Extract a single session's bus-diagram input for the visualizer prototype.

Produces a compact JSON the HTML wireframes consume. This is a PROTOTYPE feeder, not
production: it renders the same Tier-1 IO graph + the cost.py cost axis the real
visualizer will use, so the samples are grounded in real numbers rather than mock data.

Usage:
    python scripts/viz_extract.py <session-stem-prefix> [out.json]

The spine is the ACTIVE timeline only (one root->leaf path) — the scope the metrics use.
Each tool call carries: tool, file_id, direction (in/out/both/none), token_weight, the
derived-shell flag, and status. Turns carry role + a short text preview. The right-margin
cost axis is the running cumulative normalized-token tally from scoring/cost.py.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from haid.session import discover, loader
from haid.graph import build
from haid.graph.model import is_read, is_write
from haid.scoring import cost as C

PROJECT = r"C:\Users\jhart\Documents\software\HAID"


def short_file(fid: str) -> str:
    """Repo-relative tail for display; absolute/external paths shortened to basename."""
    f = fid.replace("\\", "/")
    if "/HAID/" in f:
        return f.split("/HAID/", 1)[1]
    return f.split("/")[-1] if "/" in f else f


def est_tokens(b: int) -> int:
    return b // 4


def call_detail(tc) -> str:
    """A short human-readable description of what a tool call did — the 'necessary content'
    for its square. Pulled from the raw tool_use input."""
    p = tc.params or {}
    t = tc.tool
    if t == "Bash":
        cmd = (p.get("command") or "").strip().replace("\n", " ")
        # strip leading `cd "<path>" && ` boilerplate — the meaningful command follows
        m = re.match(r'''cd\s+["']?[^"'&]+["']?\s*&&\s*''', cmd)
        if m:
            cmd = cmd[m.end():]
        return cmd[:80]
    if t in ("Grep",):
        return (p.get("pattern") or "")[:60]
    if t in ("Glob",):
        return (p.get("pattern") or "")[:60]
    if t == "Read" and tc.read_span:
        return f"lines {tc.read_span[0]}–{tc.read_span[1]}"
    if t == "Agent":
        return (p.get("description") or p.get("subagent_type") or "")[:60]
    if t in ("TodoWrite",):
        todos = p.get("todos") or []
        return f"{len(todos)} item(s)"
    if t in ("Edit", "MultiEdit", "Write"):
        return ""  # file is shown separately
    return ""


def msg_ntok(raw: dict) -> float:
    msg = raw.get("message") or {}
    u = msg.get("usage")
    if not isinstance(u, dict):
        return 0.0
    d = dict(u)
    d["model"] = msg.get("model", "")
    return C.measure([C.Usage.from_dict(d)]).normalized_tokens


def main():
    prefix = sys.argv[1] if len(sys.argv) > 1 else "bccbf167"
    paths = discover.find_sessions(PROJECT)
    match = [p for p in paths if p.name.startswith(prefix)]
    if not match:
        print("no session matching", prefix, file=sys.stderr)
        sys.exit(1)
    path = match[0]
    sess = loader.load_session(path)
    g = build.build_graph(sess.parse.records)
    timelines = sess.forest.timelines()
    active = next(t for t in timelines if t.is_active)
    active_set = set(active.node_uuids)

    # tool calls in timeline order (the scope the metrics use)
    tcs = build.timeline_toolcalls(g, active)
    tc_by_turn: dict[str, list] = {}
    for tc in tcs:
        tc_by_turn.setdefault(tc.turn_id, []).append(tc)

    # file color assignment: by first-touch order
    file_order: list[str] = []

    def color_idx(fid: str) -> int:
        if fid not in file_order:
            file_order.append(fid)
        return file_order.index(fid)

    # walk the active path records in order, emit spine items + cumulative cost
    by_uuid = sess.forest.by_uuid
    cum = 0.0
    spine = []
    for uuid in active.node_uuids:
        r = by_uuid.get(uuid)
        if r is None:
            continue
        cum += msg_ntok(r.raw)
        if r.type == "user" and r.is_user_prompt():
            spine.append({
                "kind": "user",
                "ts": r.timestamp,
                "text": r.text().strip()[:160],
                "cum_ntok": round(cum),
            })
        elif r.type == "assistant":
            calls = tc_by_turn.get(uuid, [])
            text = r.text().strip()
            item = {
                "kind": "assistant",
                "ts": r.timestamp,
                "text": text[:160],
                "has_text": bool(text),
                "cum_ntok": round(cum),
                "calls": [],
            }
            for tc in calls:
                fid = tc.target_file_id
                rd, wr = is_read(tc), is_write(tc)
                direction = (
                    "both" if rd and wr else
                    "in" if rd else
                    "out" if wr else
                    "none"
                )
                item["calls"].append({
                    "tool": tc.tool,
                    "file": short_file(fid) if fid else None,
                    "file_id": fid,
                    "color": color_idx(fid) if fid else None,
                    "direction": direction,
                    "token_weight": est_tokens(tc.result_bytes),
                    "derived": bool(tc.derived_read or tc.derived_write),
                    "status": tc.status,
                    "read_span": list(tc.read_span) if tc.read_span else None,
                    "detail": call_detail(tc),
                })
            spine.append(item)

    # per-file aggregate (the bus): total in / out token weight, touch count
    files = {}
    for it in spine:
        if it["kind"] != "assistant":
            continue
        for c in it["calls"]:
            if not c["file_id"]:
                continue
            f = files.setdefault(c["file_id"], {
                "file": c["file"], "color": c["color"],
                "in_tok": 0, "out_tok": 0, "reads": 0, "writes": 0, "touches": 0,
            })
            f["touches"] += 1
            if c["direction"] in ("in", "both"):
                f["in_tok"] += c["token_weight"]; f["reads"] += 1
            if c["direction"] in ("out", "both"):
                f["out_tok"] += c["token_weight"]; f["writes"] += 1

    out = {
        "session": path.name,
        "stem": path.name[:8],
        "n_toolcalls": len(tcs),
        "n_files": len(files),
        "total_ntok": round(cum),
        "files": sorted(files.values(), key=lambda f: -(f["in_tok"] + f["out_tok"])),
        "spine": spine,
    }
    dest = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("out") / f"viz_{path.name[:8]}.json"
    dest.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"wrote {dest}  ({len(spine)} spine items, {len(files)} files, {round(cum)} nTok)")


if __name__ == "__main__":
    main()
