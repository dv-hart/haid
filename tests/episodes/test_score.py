"""Per-episode scoring + the window distribution (step 4) — end-to-end, deterministic.

Drives `score_episodes` over real reconstructed diffs with a deterministic comparison backend
(placement correctness itself is validated upstream in tests/scoring; here we pin the
orchestration: diff → cost → metrics → placement → achievement → value, per episode, and the
empty-diff / no-artifact path). Run: PYTHONPATH=src python -m pytest tests/episodes/ -q
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(_ROOT, "src"))

from haid.episodes import score as episode_score
from haid.episodes.model import Episode
from haid.graph.build import build_graph, timeline_toolcalls
from haid.metrics.base import WindowView
from haid.session import records as rec
from haid.session.forest import Forest

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


def edit_session(stem, path, original, old, new):
    recs = [
        _r({"type": "user", "uuid": f"u_{stem}", "parentUuid": None, "timestamp": f"{stem}T10:00:00Z",
            "cwd": CWD, "message": {"role": "user", "content": "edit"}}),
        _r({"type": "assistant", "uuid": f"a_{stem}", "parentUuid": f"u_{stem}",
            "timestamp": f"{stem}T10:00:01Z", "cwd": CWD,
            "message": {"role": "assistant", "model": "claude-haiku-4-5",
                        "usage": {"input_tokens": 200, "output_tokens": 60},
                        "content": [{"type": "tool_use", "id": f"c_{stem}", "name": "Edit",
                                     "input": {"file_path": path, "old_string": old, "new_string": new}}]}}),
        _r({"type": "user", "uuid": f"r_{stem}", "parentUuid": f"a_{stem}", "timestamp": f"{stem}T10:00:02Z",
            "cwd": CWD, "message": {"role": "user",
                                    "content": [{"type": "tool_result", "tool_use_id": f"c_{stem}"}]},
            "toolUseResult": {"filePath": path, "originalFile": original, "oldString": old, "newString": new}}),
        _r({"type": "last-prompt", "leafUuid": f"a_{stem}"}),
    ]
    return FakeSession(f"/x/{stem}.jsonl", recs)


def read_session(stem):
    """Read-only: no writes → no diff → no scored artifact."""
    recs = [
        _r({"type": "user", "uuid": f"u_{stem}", "parentUuid": None, "timestamp": f"{stem}T10:00:00Z",
            "cwd": CWD, "message": {"role": "user", "content": "look"}}),
        _r({"type": "assistant", "uuid": f"a_{stem}", "parentUuid": f"u_{stem}",
            "timestamp": f"{stem}T10:00:01Z", "cwd": CWD,
            "message": {"role": "assistant", "model": "claude-haiku-4-5",
                        "usage": {"input_tokens": 80, "output_tokens": 10},
                        "content": [{"type": "tool_use", "id": f"c_{stem}", "name": "Read",
                                     "input": {"file_path": "/proj/notes.md"}}]}}),
        _r({"type": "user", "uuid": f"r_{stem}", "parentUuid": f"a_{stem}", "timestamp": f"{stem}T10:00:02Z",
            "cwd": CWD, "message": {"role": "user",
                                    "content": [{"type": "tool_result", "tool_use_id": f"c_{stem}"}]},
            "toolUseResult": {"file": {"filePath": "/proj/notes.md", "content": "x" * 50,
                                       "startLine": 1, "numLines": 3, "totalLines": 3}}}),
        _r({"type": "last-prompt", "leafUuid": f"a_{stem}"}),
    ]
    return FakeSession(f"/x/{stem}.jsonl", recs)


def view_of(sessions):
    active, timelines = [], []
    for s in sessions:
        sid = Path(s.path).stem[:8]
        g = build_graph(s.parse.records)
        for tl in s.forest.timelines():
            tcs = timeline_toolcalls(g, tl)
            timelines.append((f"{sid}:{tl.label}", tcs))
            if tl.is_active:
                active.extend((sid, tc) for tc in tcs)
    return WindowView(active_stream=active, timelines=timelines, n_sessions=len(sessions))


from haid.scoring.defects import DefectResult


class FakeBackend:
    """Deterministic difficulty placement: subject beats the lower half (a mid rung)."""
    def compare_batch(self, subject, anchors, axis):
        k = len(anchors) // 2
        return ["subject"] * k + ["anchor"] * (len(anchors) - k)


class FakeDetect:
    """Deterministic cleanliness detection: `severe` severe defects, post-verify."""
    def __init__(self, severe=0):
        self.severe = severe

    def detect(self, diff, changed_lines):
        findings = [{"defect_class": "error_swallowing", "locator": f"e{i}", "note": "x"}
                    for i in range(self.severe)]
        return DefectResult.from_findings(findings, changed_lines)


def _backend_for(axis, subject_id):
    return FakeDetect() if axis == "cleanliness" else FakeBackend()


def test_scores_an_edit_episode_end_to_end():
    s = edit_session("20260601", "/proj/foo.py", "a\nb\nc\n", "b", "B")
    eps = [Episode(id="ep1", title="tweak foo", session_ids=["20260601"])]
    dist = episode_score.score_episodes(view_of([s]), [s], eps, _backend_for, label="w")

    [sc] = dist.scores
    assert sc.has_artifact and not sc.pending
    assert sc.difficulty is not None and sc.cleanliness is not None
    assert sc.achievement is not None and sc.value is not None
    v = sc.value_scalar
    assert v == v and v > 0                                  # finite, positive
    assert "rereads" in sc.metrics                           # episode-scope metrics attached


class DeferringDetect:
    """A cleanliness backend that defers (live file-handoff not yet fulfilled)."""
    def detect(self, diff, changed_lines):
        from haid.scoring.detect import PendingDetection
        raise PendingDetection("out/jobs/ep1_detect.detect.job.json", phase="detect")


def test_deferred_detection_records_pending_and_does_not_score():
    s = edit_session("20260601", "/proj/foo.py", "a\nb\nc\n", "b", "B")
    eps = [Episode(id="ep1", title="tweak foo", session_ids=["20260601"])]

    def backend_for(axis, subject_id):
        return DeferringDetect() if axis == "cleanliness" else FakeBackend()

    dist = episode_score.score_episodes(view_of([s]), [s], eps, backend_for, label="w")
    [sc] = dist.scores
    assert sc.has_artifact and sc.pending                      # manifest recorded
    assert sc.value is None and sc.achievement is None         # not scored while pending
    assert dist.to_json()["pending_placements"] == 1


def test_no_artifact_episode_is_not_scored():
    s = read_session("20260601")
    eps = [Episode(id="ep1", title="just looking", session_ids=["20260601"])]
    dist = episode_score.score_episodes(view_of([s]), [s], eps, _backend_for)
    [sc] = dist.scores
    assert not sc.has_artifact and sc.value is None and sc.achievement is None


def test_distribution_json_and_render():
    s1 = edit_session("20260601", "/proj/foo.py", "a\nb\n", "b", "B")
    s2 = read_session("20260602")
    eps = [Episode(id="ep1", title="edit foo", session_ids=["20260601"]),
           Episode(id="ep2", title="reading", session_ids=["20260602"])]
    dist = episode_score.score_episodes(view_of([s1, s2]), [s1, s2], eps, _backend_for, label="w")

    doc = dist.to_json()
    assert doc["kind"] == "episode_scores" and doc["n_episodes"] == 2
    ep1 = next(e for e in doc["episodes"] if e["id"] == "ep1")
    ep2 = next(e for e in doc["episodes"] if e["id"] == "ep2")
    assert ep1["has_artifact"] and "value" in ep1
    assert ep2["has_artifact"] is False
    text = dist.render()
    assert "ep1" in text and "no code artifact" in text       # ep2 listed as unscored


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
