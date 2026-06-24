"""Live viz wiring — extraction, the episode-source precedence ladder, and self-contained
render. Run: PYTHONPATH=src python -m pytest tests/viz/ -q
"""

from __future__ import annotations

import os
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(_ROOT, "src"))

from haid.session import records as rec
from haid.session.forest import Forest
from haid.viz import assemble, extract, render

CWD = "/proj"


class FakeSession:
    def __init__(self, path, records):
        self.path = path
        self.parse = type("P", (), {"records": records})()
        self.subagents = []
        self.forest = Forest(records)

    def warnings(self):
        return []


def _r(d):
    return rec.from_dict(d)


def read_session(stem, path):
    """A minimal one-read session: user prompt → assistant Read → tool_result."""
    recs = [
        _r({"type": "user", "uuid": f"u_{stem}", "parentUuid": None,
            "timestamp": f"2026-06-0{stem}T10:00:00Z", "cwd": CWD,
            "message": {"role": "user", "content": "look at the file"}}),
        _r({"type": "assistant", "uuid": f"a_{stem}", "parentUuid": f"u_{stem}",
            "timestamp": f"2026-06-0{stem}T10:00:01Z", "cwd": CWD,
            "message": {"role": "assistant", "model": "claude-haiku-4-5",
                        "usage": {"input_tokens": 200, "output_tokens": 60},
                        "content": [{"type": "tool_use", "id": f"c_{stem}", "name": "Read",
                                     "input": {"file_path": path}}]}}),
        _r({"type": "user", "uuid": f"r_{stem}", "parentUuid": f"a_{stem}",
            "timestamp": f"2026-06-0{stem}T10:00:02Z", "cwd": CWD,
            "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": f"c_{stem}",
                 "content": "x" * 400}]}}),
    ]
    return FakeSession(f"/x/{path[-10:]}{stem}abcdef.jsonl".replace("/", "_"), recs)


# --- extraction -----------------------------------------------------------------------
def test_extract_session_spine_and_files():
    s = FakeSession(
        "/x/deadbeef1234.jsonl",
        read_session("1", f"{CWD}/src/app.py").parse.records)
    d = extract.extract_session(s, project_path=CWD)
    assert d["stem"] == "deadbeef"
    kinds = [it["kind"] for it in d["spine"]]
    assert kinds == ["user", "assistant"]
    call = d["spine"][1]["calls"][0]
    assert call["tool"] == "Read" and call["direction"] == "in"
    assert call["file"] == "src/app.py"           # repo-relative display
    assert d["n_files"] == 1 and d["files"][0]["reads"] == 1


# --- assemble: the precedence ladder --------------------------------------------------
def _sd(stem):  # a minimal extracted session dict with a non-empty spine
    return {"stem": stem, "spine": [{"kind": "user", "ts": "2026-06-01T0%s:00:00Z" % stem,
                                     "text": f"prompt {stem}"}], "files": []}


def test_scores_path_is_primary_with_badges():
    sessions = [_sd("1"), _sd("2"), _sd("3")]
    scores = {"window_score": {"value": 1.2},
              "episodes": [
                  {"id": "ep0", "title": "Auth", "session_ids": ["1", "2"],
                   "achievement": 88.2, "value": 6e-7, "difficulty": {"rung": 9.0},
                   "cleanliness": {"percentile": 0.8}},
                  {"id": "ep1", "title": "Docs", "session_ids": ["3"]}]}
    b = assemble.assemble_bundle(sessions, scores_doc=scores, label="W")
    assert b["episode_source"] == "scores"
    assert b["window_score"] == {"value": 1.2}
    ep0 = b["episodes"][0]
    assert ep0["session_stems"] == ["1", "2"]
    assert ep0["score"]["difficulty_rung"] == 9.0
    assert b["episodes"][1]["score"] is None      # unscored episode → no badge


def test_scores_filters_absent_sessions():
    # episode references a session that wasn't extracted → that stem is dropped
    scores = {"episodes": [{"id": "ep0", "title": "T", "session_ids": ["1", "ghost"]}]}
    b = assemble.assemble_bundle([_sd("1")], scores_doc=scores, label="W")
    assert b["episodes"][0]["session_stems"] == ["1"]


def test_grouping_path_when_no_scores():
    grouping = {"episodes": [{"title": "Feature", "session_ids": ["1", "2"]}]}
    b = assemble.assemble_bundle([_sd("1"), _sd("2")], grouping_doc=grouping, label="W")
    assert b["episode_source"] == "grouping"
    assert b["episodes"][0]["title"] == "Feature" and b["episodes"][0]["score"] is None


def test_single_window_fallback():
    b = assemble.assemble_bundle([_sd("1"), _sd("2")], label="W")
    assert b["episode_source"] == "single_window"
    assert len(b["episodes"]) == 1
    assert set(b["episodes"][0]["session_stems"]) == {"1", "2"}


def test_grouping_referencing_no_present_session_falls_back():
    grouping = {"episodes": [{"title": "Gone", "session_ids": ["ghost"]}]}
    b = assemble.assemble_bundle([_sd("1")], grouping_doc=grouping, label="W")
    assert b["episode_source"] == "single_window"


def test_empty_spine_sessions_are_skipped():
    sessions = [_sd("1"), {"stem": "2", "spine": [], "files": []}]
    b = assemble.assemble_bundle(sessions, label="W")
    assert set(b["sessions"]) == {"1"}


# --- render: self-contained inlining --------------------------------------------------
def test_self_contained_html_inlines_everything():
    b = assemble.assemble_bundle([_sd("1")], label="My Window")
    html = render.self_contained_html(b)
    assert 'href="bus.css"' not in html
    assert 'src="data.js"' not in html and 'src="bus.js"' not in html
    assert "window.HAID_DATA" in html
    assert "My Window" in html                    # the bundle is embedded
