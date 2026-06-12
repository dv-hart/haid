"""L0/L1 graph build — hand-built fixtures, deterministic, model-free.

Covers: tool-call pairing via tool_result.tool_use_id, reads/produces/edits edges, Region
materialization from structuredPatch (no diff engine), signatures, status/result_bytes,
unpaired-result counting, responds-to spine, repo-relative File ids, and per-timeline
ToolCall scoping (a call on an abandoned rewind branch is excluded from the active timeline).

Run: PYTHONPATH=src python -m pytest tests/graph/ -q
"""

from __future__ import annotations

import os
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(_ROOT, "src"))

from haid.session import records as rec
from haid.session.forest import Forest
from haid.graph.build import build_graph, timeline_toolcalls

CWD = "/proj"


def asst(uuid, parent, ts, blocks):
    return rec.from_dict({"type": "assistant", "uuid": uuid, "parentUuid": parent,
                          "timestamp": ts, "cwd": CWD,
                          "message": {"role": "assistant", "content": blocks}})


def tool_use(cid, name, inp):
    return {"type": "tool_use", "id": cid, "name": name, "input": inp}


def result(uuid, parent, cid, tur, is_error=False, ts=None):
    # Real shape: the pairing id lives in the tool_result block's `tool_use_id`.
    return rec.from_dict({"type": "user", "uuid": uuid, "parentUuid": parent, "timestamp": ts,
                          "toolUseResult": tur, "cwd": CWD,
                          "message": {"role": "user",
                                      "content": [{"type": "tool_result", "tool_use_id": cid,
                                                   "is_error": is_error}]}})


def user(uuid, parent, ts, text):
    return rec.from_dict({"type": "user", "uuid": uuid, "parentUuid": parent, "timestamp": ts,
                          "cwd": CWD, "message": {"role": "user", "content": text}})


def test_read_edge_and_signature():
    recs = [
        user("u1", None, "t0", "read it"),
        asst("a1", "u1", "t1", [tool_use("c1", "Read", {"file_path": "/proj/a.py"})]),
        result("r1", "a1", "c1",
               {"file": {"filePath": "/proj/a.py", "content": "x" * 50,
                         "startLine": 1, "numLines": 10, "totalLines": 10}}),
    ]
    g = build_graph(recs)
    assert "a.py" in g.files                      # repo-relative id (cwd stripped)
    tc = g.toolcalls["c1"]
    assert tc.tool == "Read" and tc.status == "ok" and tc.result_bytes == 50
    assert tc.signature == ("Read", "a.py", 1, 10)
    reads = g.edges_of("reads")
    assert len(reads) == 1 and reads[0].dst == "a.py" and reads[0].attrs["span"] == (1, 10)


def test_edit_materializes_region():
    sp = [{"oldStart": 1, "oldLines": 1, "newStart": 1, "newLines": 2, "lines": ["-x", "+y", "+z"]}]
    recs = [
        user("u1", None, "t0", "edit it"),
        asst("a1", "u1", "t1", [tool_use("c1", "Edit",
              {"file_path": "/proj/a.py", "old_string": "x", "new_string": "y"})]),
        result("r1", "a1", "c1",
               {"filePath": "/proj/a.py", "oldString": "x", "newString": "y\nz",
                "structuredPatch": sp, "originalFile": "x"}),
    ]
    g = build_graph(recs)
    assert len(g.regions) == 1
    region = next(iter(g.regions.values()))
    assert region.file_id == "a.py" and region.current_span == (1, 3)
    edits = g.edges_of("edits")
    assert any(e.dst == region.id and e.attrs["span"] == (1, 3) for e in edits)
    assert g.toolcalls["c1"].signature[0] == "Edit"


def test_write_produces():
    sp = [{"oldStart": 0, "oldLines": 0, "newStart": 1, "newLines": 1, "lines": ["+hello"]}]
    recs = [
        asst("a1", None, "t1", [tool_use("c1", "Write",
              {"file_path": "/proj/new.py", "content": "hello"})]),
        result("r1", "a1", "c1",
               {"filePath": "/proj/new.py", "content": "hello", "structuredPatch": sp}),
    ]
    g = build_graph(recs)
    assert any(e.type == "produces" and e.dst == "new.py" for e in g.edges)
    assert len(g.regions) == 1


def test_bash_no_file_edge_and_error_status():
    recs = [
        asst("a1", None, "t1", [tool_use("c1", "Bash", {"command": "ls  -la"})]),
        result("r1", "a1", "c1", {"stdout": "out", "stderr": "boom", "interrupted": False},
               is_error=True),
    ]
    g = build_graph(recs)
    tc = g.toolcalls["c1"]
    assert tc.status == "error"
    assert tc.signature == ("Bash", "ls -la")     # whitespace normalized
    assert not any(e.type in ("reads", "edits", "produces") for e in g.edges)


def test_bash_read_parsed_to_file_and_span():
    # A `sed -n` shell read: result carries ONLY stdout (no filePath) — the real Bash
    # result shape — yet it must register as a read with a file id, span, and reads edge.
    recs = [
        asst("a1", None, "t1", [tool_use("c1", "Bash",
              {"command": "sed -n '1,3p' /proj/a.py"})]),
        result("r1", "a1", "c1",
               {"stdout": "l1\nl2\nl3\n", "stderr": "", "interrupted": False}),
    ]
    g = build_graph(recs)
    tc = g.toolcalls["c1"]
    assert tc.tool == "Bash" and tc.derived_read is True
    assert tc.target_file_id == "a.py" and tc.read_span == (1, 4)
    assert tc.signature == ("Bash", "sed -n '1,3p' /proj/a.py")   # command sig unchanged
    reads = g.edges_of("reads")
    assert len(reads) == 1 and reads[0].dst == "a.py" and reads[0].attrs["span"] == (1, 4)


def test_bash_write_parsed_to_file_and_edge():
    # `sed -i` mutates a file with no structuredPatch and no filePath — must register as
    # a write (edits edge, derived_write, op) so read accounting knows the file changed.
    recs = [
        asst("a1", None, "t1", [tool_use("c1", "Bash",
              {"command": "sed -i 's/a/b/' /proj/a.py"})]),
        result("r1", "a1", "c1", {"stdout": "", "stderr": "", "interrupted": False}),
    ]
    g = build_graph(recs)
    tc = g.toolcalls["c1"]
    assert tc.tool == "Bash" and tc.derived_write is True and tc.write_op == "edit"
    assert tc.target_file_id == "a.py"
    assert tc.signature == ("Bash", "sed -i 's/a/b/' /proj/a.py")   # command sig unchanged
    edits = g.edges_of("edits")
    assert len(edits) == 1 and edits[0].dst == "a.py" and edits[0].attrs["op"] == "edit"


def test_bash_redirect_write_produces_edge():
    recs = [
        asst("a1", None, "t1", [tool_use("c1", "Bash",
              {"command": "python gen.py > /proj/out.json"})]),
        result("r1", "a1", "c1", {"stdout": "", "stderr": "", "interrupted": False}),
    ]
    g = build_graph(recs)
    tc = g.toolcalls["c1"]
    assert tc.derived_write is True and tc.write_op == "overwrite"
    assert any(e.type == "produces" and e.dst == "out.json" for e in g.edges)


def test_bash_grep_is_not_a_read():
    recs = [
        asst("a1", None, "t1", [tool_use("c1", "Bash",
              {"command": "grep -n foo /proj/a.py"})]),
        result("r1", "a1", "c1", {"stdout": "12:foo\n", "stderr": "", "interrupted": False}),
    ]
    g = build_graph(recs)
    tc = g.toolcalls["c1"]
    assert tc.derived_read is False and tc.target_file_id is None
    assert not any(e.type == "reads" for e in g.edges)


def test_unpaired_result_counted():
    recs = [
        asst("a1", None, "t1", [tool_use("c1", "Read", {"file_path": "/proj/a.py"})]),
        result("r1", "a1", "c1", {"file": {"filePath": "/proj/a.py", "content": "x"}}),
        # a stray result with no matching tool_use
        result("r2", "a1", "GHOST", {"file": {"filePath": "/proj/b.py", "content": "y"}}),
    ]
    g = build_graph(recs)
    assert g.unpaired_results == 1


def test_responds_to_spine():
    recs = [user("u1", None, "t0", "hi"), asst("a1", "u1", "t1", [])]
    g = build_graph(recs)
    assert any(e.type == "responds-to" and e.src == "a1" and e.dst == "u1" for e in g.edges)


def test_timeline_scoping_excludes_abandoned_branch():
    # p0 -> A (abandoned, has a Read) ; p0 -> B (active, has a Read). leafUuid = active.
    recs = [
        asst("p0", None, "t0", []),
        user("A", "p0", "t1", "do step 1"),
        asst("Aa", "A", "t2", [tool_use("cA", "Read", {"file_path": "/proj/a.py"})]),
        result("rA", "Aa", "cA", {"file": {"filePath": "/proj/a.py", "content": "x"}}),
        user("B", "p0", "t3", "do step 2"),
        asst("Bb", "B", "t4", [tool_use("cB", "Read", {"file_path": "/proj/a.py"})]),
        result("rB", "Bb", "cB", {"file": {"filePath": "/proj/a.py", "content": "x"}}),
        rec.from_dict({"type": "last-prompt", "leafUuid": "Bb"}),
    ]
    g = build_graph(recs)
    f = Forest(recs)
    active = next(t for t in f.timelines() if t.is_active)
    ids = [tc.id for tc in timeline_toolcalls(g, active)]
    assert ids == ["cB"]          # the abandoned-branch Read (cA) is NOT in the active timeline


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
