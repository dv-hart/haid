"""Episode grouping (the why-pass, step 3) — SESSION grain, model-free where possible.

An episode is a collection of whole sessions on a shared component/topic; the session is atomic.
Tested without any model:
  - SUMMARIZE: each session rolls up to its purposes + touched-file set + drift proxy.
  - HEURISTIC backend: consecutive sessions sharing files group; unrelated sessions don't;
    a standalone session is its own episode.
  - REPLAY backend: folds a saved grouping; a non-partition (missing/duplicate/unknown) raises.
  - HARNESS backend: writes the single grouping manifest + raises PendingSegmentation; an
    injected runner is honored (and may group non-adjacent sessions).
  - SLICER: iter_episodes maps an episode back to its Session objects.

Run: PYTHONPATH=src python -m pytest tests/episodes/ -q
"""

from __future__ import annotations

import json
import os
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(_ROOT, "src"))

from haid import episodes
from haid.episodes import summarize
from haid.episodes.segment import (HarnessBackend, HeuristicBackend, PendingSegmentation,
                                    ReplayBackend)
from haid.intent import TaggedMessage
from haid.session import records as rec
from haid.session.forest import Forest


# --- builders ---------------------------------------------------------------------------
CWD = "/proj"


def asst_tu(uuid, parent, ts, cid, name, inp):
    return rec.from_dict({"type": "assistant", "uuid": uuid, "parentUuid": parent, "timestamp": ts,
                          "cwd": CWD, "message": {"role": "assistant",
                          "content": [{"type": "tool_use", "id": cid, "name": name, "input": inp}]}})


def user(uuid, parent, ts, text):
    return rec.from_dict({"type": "user", "uuid": uuid, "parentUuid": parent, "timestamp": ts,
                          "cwd": CWD, "message": {"role": "user", "content": text}})


def last_prompt(leaf):
    return rec.from_dict({"type": "last-prompt", "leafUuid": leaf})


class FakeSession:
    """Minimal stand-in: grouping needs path + parse.records + forest."""
    def __init__(self, path, records):
        self.path = path
        self.parse = type("P", (), {"records": records})()
        self.forest = Forest(records)


def session(stem, ts, files, n_user=1):
    """A session whose tool calls touch `files`, at time `ts` (YYYY-MM-DD)."""
    recs = [user("u_" + stem, None, f"{ts}T10:00:00Z", "do work")]
    for i, f in enumerate(files):
        recs.append(asst_tu(f"a_{stem}_{i}", "u_" + stem, f"{ts}T10:0{i+1}:00Z",
                            f"c_{stem}_{i}", "Write", {"file_path": f}))
    recs.append(last_prompt(recs[0].uuid))
    return FakeSession(f"/x/{stem}.jsonl", recs)


def tagged_for(stem, purpose, *, move="new_directive"):
    return TaggedMessage(uuid="u_" + stem, session_id=stem, timeline="active", ts=None,
                         index=0, text=purpose, move=move, work_type="implementation",
                         purpose=purpose)


# --- summarize --------------------------------------------------------------------------
def test_summarize_rolls_up_files_and_purposes():
    s = session("aaaaaaaa", "2026-06-01", ["src/auth.py", "src/auth_test.py"])
    tagged = [tagged_for("aaaaaaaa", "Build auth")]
    [summ] = summarize.summarize_sessions([s], tagged)
    assert summ.session_id == "aaaaaaaa"
    assert summ.file_set == {"src/auth.py", "src/auth_test.py"}
    assert summ.purposes == ["Build auth"]
    assert summ.index == 0


def test_summarize_excludes_external_files():
    s = session("aaaaaaaa", "2026-06-01", ["src/auth.py", "/tmp/scratch.txt"])
    [summ] = summarize.summarize_sessions([s], [tagged_for("aaaaaaaa", "p")])
    assert summ.file_set == {"src/auth.py"}            # absolute /tmp path dropped


# --- heuristic backend ------------------------------------------------------------------
def _three_sessions():
    # two auth sessions (shared file) then an unrelated billing session
    s1 = session("aaaaaaaa", "2026-06-01", ["src/auth.py"])
    s2 = session("bbbbbbbb", "2026-06-02", ["src/auth.py", "src/auth_test.py"])
    s3 = session("cccccccc", "2026-06-03", ["src/billing.py"])
    tagged = [tagged_for("aaaaaaaa", "Build auth"), tagged_for("bbbbbbbb", "Test auth"),
              tagged_for("cccccccc", "Build billing")]
    return [s1, s2, s3], tagged


def test_heuristic_groups_shared_files_splits_unrelated():
    sessions, tagged = _three_sessions()
    eps = episodes.segment_window(sessions, tagged, HeuristicBackend())
    assert [e.session_ids for e in eps] == [["aaaaaaaa", "bbbbbbbb"], ["cccccccc"]]
    assert eps[0].n_sessions == 2 and eps[1].n_sessions == 1


def test_heuristic_standalone_session_is_its_own_episode():
    s = session("aaaaaaaa", "2026-06-01", ["src/a.py"])
    eps = episodes.segment_window([s], [tagged_for("aaaaaaaa", "solo")], HeuristicBackend())
    assert len(eps) == 1 and eps[0].session_ids == ["aaaaaaaa"]


def test_episode_ts_bounds_span_member_sessions():
    sessions, tagged = _three_sessions()
    eps = episodes.segment_window(sessions, tagged, HeuristicBackend())
    assert eps[0].first_ts.startswith("2026-06-01") and eps[0].last_ts.startswith("2026-06-02")


# --- replay backend + partition validation ----------------------------------------------
def test_replay_folds_saved_grouping_incl_non_adjacent():
    sessions, tagged = _three_sessions()
    # group the two auth sessions even though billing sits between them by id order
    backend = ReplayBackend.from_rows([
        {"title": "auth", "session_ids": ["aaaaaaaa", "bbbbbbbb"], "rationale": "shared auth.py"},
        {"title": "billing", "session_ids": ["cccccccc"], "rationale": "standalone"},
    ])
    eps = episodes.segment_window(sessions, tagged, backend)
    assert [e.title for e in eps] == ["auth", "billing"]


def test_partition_missing_session_raises():
    sessions, tagged = _three_sessions()
    backend = ReplayBackend.from_rows([
        {"title": "auth", "session_ids": ["aaaaaaaa", "bbbbbbbb"]},  # cccccccc missing
    ])
    try:
        episodes.segment_window(sessions, tagged, backend)
        assert False, "expected ValueError for the uncovered session"
    except ValueError:
        pass


def test_partition_duplicate_session_raises():
    sessions, tagged = _three_sessions()
    backend = ReplayBackend.from_rows([
        {"title": "a", "session_ids": ["aaaaaaaa", "bbbbbbbb"]},
        {"title": "b", "session_ids": ["bbbbbbbb", "cccccccc"]},     # bbbbbbbb twice
    ])
    try:
        episodes.segment_window(sessions, tagged, backend)
        assert False, "expected ValueError for the duplicated session"
    except ValueError:
        pass


def test_partition_unknown_session_raises():
    sessions, tagged = _three_sessions()
    backend = ReplayBackend.from_rows([
        {"title": "a", "session_ids": ["aaaaaaaa", "bbbbbbbb", "cccccccc", "zzzzzzzz"]},
    ])
    try:
        episodes.segment_window(sessions, tagged, backend)
        assert False, "expected ValueError for the unknown session id"
    except ValueError:
        pass


# --- harness backend --------------------------------------------------------------------
def test_harness_writes_manifest_and_raises(tmp_path):
    sessions, tagged = _three_sessions()
    job_dir = str(tmp_path / "jobs")
    try:
        episodes.segment_window(sessions, tagged, HarnessBackend(job_dir=job_dir))
        assert False, "expected PendingSegmentation"
    except PendingSegmentation as p:
        manifest = json.load(open(p.manifest_path, encoding="utf-8"))
    assert manifest["task"] == "group_sessions_into_episodes"
    assert manifest["schema"]["required"] == ["episodes"]
    assert "SESSION is atomic" in manifest["prompt"]
    assert "NEVER split a session" in manifest["prompt"]


def test_harness_runner_can_group_non_adjacent():
    sessions, tagged = _three_sessions()

    def runner(manifest):
        return {"episodes": [
            {"title": "auth", "session_ids": ["aaaaaaaa", "cccccccc"], "rationale": "r"},
            {"title": "mid", "session_ids": ["bbbbbbbb"], "rationale": "r"}]}
    eps = episodes.segment_window(sessions, tagged, HarnessBackend(job_dir="/unused", runner=runner))
    assert eps[0].session_ids == ["aaaaaaaa", "cccccccc"]      # non-contiguous group, honored


def test_harness_reads_back_grouping(tmp_path):
    sessions, tagged = _three_sessions()
    job_dir = tmp_path / "jobs"
    job_dir.mkdir()
    (job_dir / "episodes.grouping.json").write_text(json.dumps({"episodes": [
        {"title": "all", "session_ids": ["aaaaaaaa", "bbbbbbbb", "cccccccc"], "rationale": "r"},
    ]}), encoding="utf-8")
    eps = episodes.segment_window(sessions, tagged, HarnessBackend(job_dir=str(job_dir)))
    assert len(eps) == 1 and eps[0].n_sessions == 3


# --- slicer + json/render ---------------------------------------------------------------
def test_iter_episodes_maps_back_to_sessions():
    sessions, tagged = _three_sessions()
    eps = episodes.segment_window(sessions, tagged, HeuristicBackend())
    sliced = list(episodes.iter_episodes(eps, sessions))
    assert [len(members) for _, members in sliced] == [2, 1]
    assert sliced[0][1][0].path.endswith("aaaaaaaa.jsonl")


def test_to_json_and_render_shapes():
    sessions, tagged = _three_sessions()
    eps = episodes.segment_window(sessions, tagged, HeuristicBackend())
    doc = episodes.to_json(eps, label="w")
    assert doc["kind"] == "episodes" and len(doc["episodes"]) == 2
    assert doc["episodes"][0]["session_ids"] == ["aaaaaaaa", "bbbbbbbb"]
    summaries = summarize.summarize_sessions(sessions, tagged)
    text = episodes.render(eps, summaries=summaries, label="w")
    assert "ep1" in text and "Build auth" in text and "Build billing" in text


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
