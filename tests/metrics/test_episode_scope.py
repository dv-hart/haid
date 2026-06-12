"""Episode-scope metrics — `run_episodes` groups the active stream by episode (step 4).

Mirrors run_sessions: same cores, grouped by episode (a set of whole sessions) instead of by
session. Every episode gets an entry, including one with no sessions in the stream.
Run: PYTHONPATH=src python -m pytest tests/metrics/ -q
"""

from __future__ import annotations

import os
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(_ROOT, "src"))

from haid import metrics
from haid.graph.build import build_graph, timeline_toolcalls
from haid.metrics.base import WindowView
from haid.session import records as rec
from haid.session.forest import Forest

CWD = "/proj"


class Ep:
    """Minimal stand-in for episodes.model.Episode (run_episodes only reads .id/.session_ids)."""
    def __init__(self, id, session_ids):
        self.id = id
        self.session_ids = session_ids


def asst(uuid, parent, ts, blocks):
    return rec.from_dict({"type": "assistant", "uuid": uuid, "parentUuid": parent, "timestamp": ts,
                          "cwd": CWD, "message": {"role": "assistant", "content": blocks}})


def tu(cid, name, inp):
    return {"type": "tool_use", "id": cid, "name": name, "input": inp}


def res(uuid, parent, cid, tur, ts):
    return rec.from_dict({"type": "user", "uuid": uuid, "parentUuid": parent, "timestamp": ts,
                          "cwd": CWD, "message": {"role": "user",
                          "content": [{"type": "tool_result", "tool_use_id": cid}]},
                          "toolUseResult": tur})


def user(uuid, parent, ts, text):
    return rec.from_dict({"type": "user", "uuid": uuid, "parentUuid": parent, "timestamp": ts,
                          "cwd": CWD, "message": {"role": "user", "content": text}})


def last_prompt(leaf):
    return rec.from_dict({"type": "last-prompt", "leafUuid": leaf})


def read_tur(path):
    return {"file": {"filePath": path, "content": "x" * 400, "startLine": 1,
                     "numLines": 10, "totalLines": 10}}


def reread_session(stem):
    """A session that reads foo.py twice with no edit between → a reread."""
    return [
        user(f"u_{stem}", None, "1", "look"),
        asst(f"a1_{stem}", f"u_{stem}", "2", [tu(f"c1_{stem}", "Read", {"file_path": "/proj/foo.py"})]),
        res(f"r1_{stem}", f"a1_{stem}", f"c1_{stem}", read_tur("/proj/foo.py"), "3"),
        asst(f"a2_{stem}", f"r1_{stem}", "4", [tu(f"c2_{stem}", "Read", {"file_path": "/proj/foo.py"})]),
        res(f"r2_{stem}", f"a2_{stem}", f"c2_{stem}", read_tur("/proj/foo.py"), "5"),
        last_prompt(f"a2_{stem}"),
    ]


def clean_session(stem):
    """A session that reads bar.py once → no reread."""
    return [
        user(f"u_{stem}", None, "1", "look"),
        asst(f"a1_{stem}", f"u_{stem}", "2", [tu(f"c1_{stem}", "Read", {"file_path": "/proj/bar.py"})]),
        res(f"r1_{stem}", f"a1_{stem}", f"c1_{stem}", read_tur("/proj/bar.py"), "3"),
        last_prompt(f"a1_{stem}"),
    ]


def view_of(named_sessions):
    active, timelines = [], []
    for sid, recs in named_sessions:
        g = build_graph(recs)
        for tl in Forest(recs).timelines():
            tcs = timeline_toolcalls(g, tl)
            timelines.append((f"{sid}:{tl.label}", tcs))
            if tl.is_active:
                active.extend((sid, tc) for tc in tcs)
    return WindowView(active_stream=active, timelines=timelines, n_sessions=len(named_sessions))


def test_run_episodes_groups_by_episode_and_covers_empty():
    view = view_of([("s0", reread_session("s0")), ("s1", clean_session("s1"))])
    episodes = [Ep("ep1", ["s0"]), Ep("ep2", ["s1"]), Ep("ep3", ["s2"])]  # ep3 has no stream
    res_ = metrics.run_episodes(view, episodes)

    assert set(res_) == {"ep1", "ep2", "ep3"}              # every episode present
    assert res_["ep1"]["rereads"].count >= 1               # the reread lives in s0 → ep1
    assert res_["ep2"]["rereads"].count == 0               # s1 had none
    assert res_["ep3"]["rereads"].count == 0               # empty episode, not a missing key


def test_episode_grouping_partitions_the_stream():
    view = view_of([("s0", reread_session("s0")), ("s1", clean_session("s1"))])
    # one episode holding both sessions == the window for these sessions
    [whole] = list(metrics.run_episodes(view, [type("E", (), {"id": "ep", "session_ids": ["s0", "s1"]})()]).values())
    win = metrics.run_window(view)
    for name in metrics.METRIC_NAMES:
        assert whole[name].denominator == win[name].denominator


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
