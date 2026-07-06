"""Subagent edit stitching — subagent-authored writes are folded into the reconstructed diff.

The load-bearing facts (verified on real data, see bridge/subagent_writes.py):
  - subagent sidecars carry NO enriched `toolUseResult` (no `originalFile`, no `structuredPatch`),
    only the tool_use INPUT — so subagent writes reconstruct input-driven;
  - a subagent edit whose file the main agent read/edited first reconstructs in exact buffer
    mode; with no baseline anywhere it falls to `pairs` mode (fragment from old/new strings);
  - a subagent under an ABANDONED (off-active-path) Agent call is excluded from the diff, but its
    TOKENS still count (cost = all branches; diff = active end-state — the intentional asymmetry).

Run: PYTHONPATH=src python -m pytest tests/bridge/ -q
"""

from __future__ import annotations

import os
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(_ROOT, "src"))

from haid.bridge import window_inputs
from haid.session import records as rec
from haid.session.forest import Forest
from haid.window import build_view

CWD = "/proj"
DAY = "20260601"


def _r(d):
    return rec.from_dict(d)


class _Parse:
    def __init__(self, records):
        self.records = records

    def warnings(self):
        return []


class FakeSubagent:
    """Enough of session.subagents.Subagent for the bridge (records + parent link)."""
    def __init__(self, agent_id, tool_use_id, records):
        self.agent_id = agent_id
        self.meta = {"toolUseId": tool_use_id}
        self.parse = _Parse(records)
        self.jsonl_path = f"/x/{agent_id}.jsonl"

    @property
    def parent_tool_use_id(self):
        return self.meta.get("toolUseId")


class FakeSession:
    def __init__(self, path, records, subagents=None):
        self.path = path
        self.parse = _Parse(records)
        self.subagents = subagents or []
        self.forest = Forest(records)

    def warnings(self):
        return []


# --- subagent sidecar records (RAW api shape: no toolUseResult) --------------------------

def _sub_call(i, tool, inp, hh, parent, agent_id):
    aid, rid, cid = f"{agent_id}a{i}", f"{agent_id}r{i}", f"{agent_id}c{i}"
    recs = [
        _r({"type": "assistant", "uuid": aid, "parentUuid": parent, "isSidechain": True,
            "agentId": agent_id, "timestamp": f"{DAY}T{hh}:00Z", "cwd": CWD,
            "message": {"role": "assistant", "model": "claude-sonnet-5",
                        "usage": {"input_tokens": 50, "output_tokens": 10},
                        "content": [{"type": "tool_use", "id": cid, "name": tool, "input": inp}]}}),
        # result is a plain string — exactly what real sidecars store (no toolUseResult).
        _r({"type": "user", "uuid": rid, "parentUuid": aid, "isSidechain": True,
            "agentId": agent_id, "timestamp": f"{DAY}T{hh}:01Z", "cwd": CWD,
            "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": cid,
                 "content": "The file has been updated successfully."}]}}),
    ]
    return recs, aid


def make_subagent(agent_id, tool_use_id, calls):
    """calls = [(tool, input_dict, "HH:MM")]. Linear sidecar tree."""
    root = f"{agent_id}root"
    recs = [_r({"type": "user", "uuid": root, "parentUuid": None, "isSidechain": True,
                "agentId": agent_id, "timestamp": f"{DAY}T09:30:00Z", "cwd": CWD,
                "message": {"role": "user", "content": "do the work"}})]
    parent = root
    for i, (tool, inp, hh) in enumerate(calls):
        pair, parent = _sub_call(i, tool, inp, hh, parent, agent_id)
        recs.extend(pair)
    return FakeSubagent(agent_id, tool_use_id, recs)


# --- main transcript records ------------------------------------------------------------

def _read_pair(uuid, parent, path, content, hh):
    """A main-transcript full Read of `path` (enriched result, as the main transcript stores)."""
    aid, rid, cid = f"{uuid}a", f"{uuid}r", f"{uuid}c"
    nlines = content.count("\n")
    recs = [
        _r({"type": "assistant", "uuid": aid, "parentUuid": parent, "timestamp": f"{DAY}T{hh}:00Z",
            "cwd": CWD, "message": {"role": "assistant", "model": "claude-opus-4-8",
                                    "usage": {"input_tokens": 80, "output_tokens": 5},
                                    "content": [{"type": "tool_use", "id": cid, "name": "Read",
                                                 "input": {"file_path": path}}]}}),
        _r({"type": "user", "uuid": rid, "parentUuid": aid, "timestamp": f"{DAY}T{hh}:01Z",
            "cwd": CWD, "message": {"role": "user",
                                    "content": [{"type": "tool_result", "tool_use_id": cid}]},
            "toolUseResult": {"file": {"filePath": path, "content": content, "startLine": 1,
                                       "numLines": nlines, "totalLines": nlines,
                                       "truncatedByTokenCap": False}}}),
    ]
    return recs, aid


def _agent_call(uuid, parent, call_id, hh):
    aid, rid = f"{uuid}a", f"{uuid}r"
    recs = [
        _r({"type": "assistant", "uuid": aid, "parentUuid": parent, "timestamp": f"{DAY}T{hh}:00Z",
            "cwd": CWD, "message": {"role": "assistant", "model": "claude-opus-4-8",
                                    "usage": {"input_tokens": 200, "output_tokens": 40},
                                    "content": [{"type": "tool_use", "id": call_id, "name": "Agent",
                                                 "input": {"description": "impl",
                                                           "subagent_type": "general-purpose"}}]}}),
        _r({"type": "user", "uuid": rid, "parentUuid": aid, "timestamp": f"{DAY}T{hh}:02Z",
            "cwd": CWD, "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": call_id, "content": "done"}]}}),
    ]
    return recs, aid


def _main_edit(uuid, parent, path, old, new, original, hh):
    aid, rid, cid = f"{uuid}a", f"{uuid}r", f"{uuid}c"
    recs = [
        _r({"type": "assistant", "uuid": aid, "parentUuid": parent, "timestamp": f"{DAY}T{hh}:00Z",
            "cwd": CWD, "message": {"role": "assistant", "model": "claude-opus-4-8",
                                    "usage": {"input_tokens": 90, "output_tokens": 20},
                                    "content": [{"type": "tool_use", "id": cid, "name": "Edit",
                                                 "input": {"file_path": path, "old_string": old,
                                                           "new_string": new}}]}}),
        _r({"type": "user", "uuid": rid, "parentUuid": aid, "timestamp": f"{DAY}T{hh}:01Z",
            "cwd": CWD, "message": {"role": "user",
                                    "content": [{"type": "tool_result", "tool_use_id": cid}]},
            "toolUseResult": {"filePath": path, "oldString": old, "newString": new,
                              "originalFile": original}}),
    ]
    return recs, aid


# ----------------------------------------------------------------------------------------
def test_subagent_write_creates_file_in_diff():
    """A subagent that Writes a new file — its content lands in the diff, no baseline needed."""
    sub = make_subagent("suba", "CALL1", [
        ("Write", {"file_path": "/proj/new.py", "content": "def f():\n    return 1\n"}, "10:00")])
    main = _agent_call("g", "u0", "CALL1", "09:50")[0]
    main = [_r({"type": "user", "uuid": "u0", "parentUuid": None, "timestamp": f"{DAY}T09:00:00Z",
                "cwd": CWD, "message": {"role": "user", "content": "go"}})] + main
    main.append(_r({"type": "last-prompt", "leafUuid": "gr"}))
    s = FakeSession(f"/x/{DAY}.jsonl", main, subagents=[sub])

    res = window_inputs(build_view([s]), [s])
    assert "diff --git a/new.py b/new.py" in res.diff
    assert "+def f():" in res.diff and "+    return 1" in res.diff
    assert any("authored by 1 subagent" in c for c in res.caveats)
    # new-file create is clean — no incompleteness flag
    assert not [f for f in res.files if f.file_id == "new.py" and not f.complete]


def test_subagent_edit_uses_main_read_as_baseline():
    """Main agent Reads a file (full), subagent Edits it → exact BUFFER mode, not a fragment."""
    read, a_read = _read_pair("rd", "u0", "/proj/foo.py", "alpha\nbeta\ngamma\n", "09:10")
    agent, a_ag = _agent_call("g", a_read, "CALL1", "09:50")
    main = [_r({"type": "user", "uuid": "u0", "parentUuid": None, "timestamp": f"{DAY}T09:00:00Z",
                "cwd": CWD, "message": {"role": "user", "content": "go"}})]
    main += read + agent
    main.append(_r({"type": "last-prompt", "leafUuid": "gr"}))
    sub = make_subagent("suba", "CALL1", [
        ("Edit", {"file_path": "/proj/foo.py", "old_string": "beta", "new_string": "BETA"}, "10:00")])
    s = FakeSession(f"/x/{DAY}.jsonl", main, subagents=[sub])

    res = window_inputs(build_view([s]), [s])
    assert "-beta" in res.diff and "+BETA" in res.diff
    assert " alpha" in res.diff or "alpha" in res.diff       # context line survives (buffer mode)
    foo = next(f for f in res.files if f.file_id == "foo.py")
    assert foo.mode == "buffer" and foo.complete             # exact reconstruction, no flag


def test_subagent_edit_without_baseline_uses_pairs_mode():
    """No baseline anywhere for a pre-existing file → pairs mode + honest flag."""
    agent, a_ag = _agent_call("g", "u0", "CALL1", "09:50")
    main = [_r({"type": "user", "uuid": "u0", "parentUuid": None, "timestamp": f"{DAY}T09:00:00Z",
                "cwd": CWD, "message": {"role": "user", "content": "go"}})] + agent
    main.append(_r({"type": "last-prompt", "leafUuid": "gr"}))
    sub = make_subagent("suba", "CALL1", [
        ("Edit", {"file_path": "/proj/bar.py", "old_string": "x = 1", "new_string": "x = 2"}, "10:00")])
    s = FakeSession(f"/x/{DAY}.jsonl", main, subagents=[sub])

    res = window_inputs(build_view([s]), [s])
    bar = next(f for f in res.files if f.file_id == "bar.py")
    assert bar.mode == "pairs" and not bar.complete
    assert "-x = 1" in res.diff and "+x = 2" in res.diff
    assert any("edit old/new strings" in r for r in bar.reasons)


def test_abandoned_agent_excluded_from_diff_but_tokens_counted():
    """A subagent spawned on a rewound branch: NOT in the diff, but its tokens still count."""
    # u0 forks: a0 (Agent CALL1, abandoned) and u1 (the active continuation).
    agent, a_ag = _agent_call("g", "u0", "CALL1", "09:50")
    main = [
        _r({"type": "user", "uuid": "u0", "parentUuid": None, "timestamp": f"{DAY}T09:00:00Z",
            "cwd": CWD, "message": {"role": "user", "content": "go"}}),
        # active branch: a plain user re-prompt that supersedes the Agent attempt
        _r({"type": "user", "uuid": "u1", "parentUuid": "u0", "timestamp": f"{DAY}T11:00:00Z",
            "cwd": CWD, "message": {"role": "user", "content": "actually do it differently"}}),
    ] + agent
    main.append(_r({"type": "last-prompt", "leafUuid": "u1"}))   # active leaf = u1, so a0 is off-path
    sub = make_subagent("suba", "CALL1", [
        ("Write", {"file_path": "/proj/dead.py", "content": "abandoned\n"}, "10:00")])
    s = FakeSession(f"/x/{DAY}.jsonl", main, subagents=[sub])

    res = window_inputs(build_view([s]), [s])
    assert "dead.py" not in res.diff                          # abandoned subagent edit excluded
    # but its tokens (sonnet — a tier only the subagent used) still counted
    assert res.cost.by_tier.get("sonnet", {}).get("raw", 0) > 0


def test_no_false_drift_flag_after_subagent_edit():
    """Main reads, subagent edits, then main edits the same file: the later main edit's
    originalFile matches the post-subagent buffer, so NO 'untracked change' flag fires."""
    read, a_read = _read_pair("rd", "u0", "/proj/foo.py", "A\nB\n", "09:10")
    agent, a_ag = _agent_call("g", a_read, "CALL1", "09:50")
    medit, a_ed = _main_edit("me", a_ag, "/proj/foo.py", "B1", "B2", "A\nB1\n", "10:30")
    main = [_r({"type": "user", "uuid": "u0", "parentUuid": None, "timestamp": f"{DAY}T09:00:00Z",
                "cwd": CWD, "message": {"role": "user", "content": "go"}})]
    main += read + agent + medit
    main.append(_r({"type": "last-prompt", "leafUuid": "mer"}))
    sub = make_subagent("suba", "CALL1", [
        ("Edit", {"file_path": "/proj/foo.py", "old_string": "B", "new_string": "B1"}, "10:00")])
    s = FakeSession(f"/x/{DAY}.jsonl", main, subagents=[sub])

    res = window_inputs(build_view([s]), [s])
    foo = next(f for f in res.files if f.file_id == "foo.py")
    assert foo.complete and not any("untracked" in r for r in foo.reasons)
    assert "-B" in res.diff and "+B2" in res.diff            # net delta A\nB\n -> A\nB2\n


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
