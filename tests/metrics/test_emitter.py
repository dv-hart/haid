"""The metrics emitter — JSON contract (json_out) + Markdown view, deterministic.

Asserts the schema shape, the metric × scope table, scope-tagged instances with resolved
refs, and that the Markdown renders. Run: PYTHONPATH=src python -m pytest tests/metrics/ -q
"""

from __future__ import annotations

import os
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(_ROOT, "src"))

from haid.session import records as rec
from haid.session.forest import Forest
from haid.graph.build import build_graph, timeline_toolcalls
from haid.metrics.base import WindowView
from haid.metrics import json_out, view, METRIC_NAMES

CWD = "/proj"


def view_of(*session_recs):
    active, timelines = [], []
    for i, recs in enumerate(session_recs):
        g = build_graph(recs)
        for tl in Forest(recs).timelines():
            tcs = timeline_toolcalls(g, tl)
            timelines.append((f"s{i}:{tl.label}", tcs))
            if tl.is_active:
                active.extend((f"s{i}", tc) for tc in tcs)
    return WindowView(active_stream=active, timelines=timelines, n_sessions=len(session_recs),
                      label=f"test ({len(session_recs)} sessions)")


def asst(uuid, parent, blocks, ts="1"):
    return rec.from_dict({"type": "assistant", "uuid": uuid, "parentUuid": parent, "timestamp": ts,
                          "cwd": CWD, "message": {"role": "assistant", "content": blocks}})


def tu(cid, name, inp):
    return {"type": "tool_use", "id": cid, "name": name, "input": inp}


def res(uuid, parent, cid, tur=None):
    raw = {"type": "user", "uuid": uuid, "parentUuid": parent, "timestamp": "2", "cwd": CWD,
           "message": {"role": "user",
                       "content": [{"type": "tool_result", "tool_use_id": cid, "is_error": False}]}}
    if tur is not None:
        raw["toolUseResult"] = tur
    return rec.from_dict(raw)


def read_tur(path, nbytes=4000):
    return {"file": {"filePath": path, "content": "x" * nbytes, "startLine": 1,
                     "numLines": 10, "totalLines": 10}}


def _two_session_view():
    # Session A reads a.py; session B reads the same a.py again (cross-session re-read).
    sess_a = [
        asst("a1", None, [tu("c1", "Read", {"file_path": "/proj/a.py"})]),
        res("r1", "a1", "c1", read_tur("/proj/a.py")),
        rec.from_dict({"type": "last-prompt", "leafUuid": "r1"}),
    ]
    sess_b = [
        asst("b1", None, [tu("c9", "Read", {"file_path": "/proj/a.py"})]),
        res("rb", "b1", "c9", read_tur("/proj/a.py")),
        rec.from_dict({"type": "last-prompt", "leafUuid": "rb"}),
    ]
    return view_of(sess_a, sess_b)


def test_doc_shape():
    doc = json_out.build(_two_session_view(), generated_at="2026-06-07T00:00:00")
    for key in ("schema_version", "kind", "scopes", "metric_defs", "measurements",
                "instances", "caps"):
        assert key in doc
    assert doc["kind"] == "metrics"
    assert doc["scopes"] == ["session", "window"]
    assert set(doc["metric_defs"]) == set(METRIC_NAMES)
    assert all(doc["metric_defs"][m]["rule"] for m in METRIC_NAMES)


def test_measurements_have_both_scopes():
    doc = json_out.build(_two_session_view(), generated_at="t")
    win = [r for r in doc["measurements"] if r["scope"] == "window"]
    sess = [r for r in doc["measurements"] if r["scope"] == "session"]
    assert {r["metric"] for r in win} == set(METRIC_NAMES)        # one window row per metric
    assert len(sess) == 2 * len(METRIC_NAMES)                     # two sessions × metrics
    for r in win:
        assert set(r) >= {"metric", "scope", "unit_id", "rate", "token_rate", "baseline"}


def test_cross_session_reread_is_window_instance_only():
    doc = json_out.build(_two_session_view(), generated_at="t")
    rr = [i for i in doc["instances"] if i["metric"] == "rereads"]
    assert any(i["scope"] == "window" for i in rr)                # window catches it
    assert not any(i["scope"] == "session" for i in rr)          # no session sees it alone
    inst = next(i for i in rr if i["scope"] == "window")
    assert inst["id"].startswith("rereads/window/")
    assert inst["refs"]["file_id"].endswith("a.py")
    assert "tool_use_id" in inst["refs"]["calls"][0]


def test_markdown_renders():
    doc = json_out.build(_two_session_view(), generated_at="t")
    md = view.render(doc, top_n=5)
    assert "# HAID metrics" in md
    assert "## Window headline" in md
    assert "## Per session" in md
    assert "## Limits & caps" in md


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
