"""Span-grain reconstruction — `span_inputs` slices ONE fix span's net diff out of a branch.

The load-bearing property (the bug-fix reward's substrate, docs/plans/bugfix-reward.md): a span's
diff is the delta produced INSIDE [start_ts, end_ts) only — a change made before the span must NOT
appear, and the span-entry baseline must be reconstructed even when the in-span edit captured no
`originalFile` (a large file), where a window-entry baseline would leak pre-span edits in.

Run: PYTHONPATH=src python -m pytest tests/bridge/ -q
"""

from __future__ import annotations

import os
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(_ROOT, "src"))

from haid.bridge import span_inputs, window_inputs
from haid.session import records as rec
from haid.session.forest import Forest
from haid.window import build_view

CWD = "/proj"
DAY = "20260601"


class FakeSession:
    def __init__(self, path, records, subagents=None):
        self.path = path
        self.parse = type("P", (), {"records": records})()
        self.subagents = subagents or []
        self.forest = Forest(records)

    def warnings(self):
        return []


def _r(d):
    return rec.from_dict(d)


# --- subagent sidecar fixtures (RAW api shape: no toolUseResult) -------------------------
# A subagent belongs to a branch iff its spawning Agent call (main-transcript tool_use id ==
# the subagent's meta.toolUseId) is on that branch. So we spawn an Agent call in the main
# transcript and give the sidecar the same tool_use id.

class _SubParse:
    def __init__(self, records):
        self.records = records

    def warnings(self):
        return []


class FakeSubagent:
    """Enough of session.subagents.Subagent for the bridge (records + parent link)."""
    def __init__(self, agent_id, tool_use_id, records):
        self.agent_id = agent_id
        self.meta = {"toolUseId": tool_use_id}
        self.parse = _SubParse(records)
        self.jsonl_path = f"/x/{agent_id}.jsonl"

    @property
    def parent_tool_use_id(self):
        return self.meta.get("toolUseId")


def _sub_call(i, tool, inp, hh, parent, agent_id):
    """One subagent tool_use → plain-string tool_result at hour `hh` (no toolUseResult)."""
    aid, rid, cid = f"{agent_id}a{i}", f"{agent_id}r{i}", f"{agent_id}c{i}"
    recs = [
        _r({"type": "assistant", "uuid": aid, "parentUuid": parent, "isSidechain": True,
            "agentId": agent_id, "timestamp": f"{DAY}T{hh}:00Z", "cwd": CWD,
            "message": {"role": "assistant", "model": "claude-sonnet-5",
                        "usage": {"input_tokens": 50, "output_tokens": 10},
                        "content": [{"type": "tool_use", "id": cid, "name": tool, "input": inp}]}}),
        _r({"type": "user", "uuid": rid, "parentUuid": aid, "isSidechain": True,
            "agentId": agent_id, "timestamp": f"{DAY}T{hh}:01Z", "cwd": CWD,
            "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": cid,
                 "content": "The file has been updated successfully."}]}}),
    ]
    return recs, aid


def make_subagent(agent_id, tool_use_id, calls):
    """calls = [(tool, input_dict, "HH:MM")]. Linear sidecar tree, active timeline."""
    root = f"{agent_id}root"
    recs = [_r({"type": "user", "uuid": root, "parentUuid": None, "isSidechain": True,
                "agentId": agent_id, "timestamp": f"{DAY}T09:30:00Z", "cwd": CWD,
                "message": {"role": "user", "content": "do the work"}})]
    parent = root
    for i, (tool, inp, hh) in enumerate(calls):
        pair, parent = _sub_call(i, tool, inp, hh, parent, agent_id)
        recs.extend(pair)
    return FakeSubagent(agent_id, tool_use_id, recs)


def _agent_call(uuid, parent, call_id, hh):
    """A main-transcript Agent tool_use (spawns a subagent) chained under `parent`."""
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


def _edit_pair(i, path, old, new, original, hh, parent):
    """One (assistant tool_use → user tool_result) edit at hour `hh`, chained under `parent`.
    `original` is the captured pre-edit file state, or None to simulate a large-file edit that
    Claude Code omitted `originalFile` for. Returns (records, last_uuid)."""
    aid, rid, cid = f"a{i}", f"r{i}", f"c{i}"
    tur = {"filePath": path, "oldString": old, "newString": new}
    if original is not None:
        tur["originalFile"] = original
    recs = [
        _r({"type": "assistant", "uuid": aid, "parentUuid": parent,
            "timestamp": f"{DAY}T{hh}:00:00Z", "cwd": CWD,
            "message": {"role": "assistant", "model": "claude-haiku-4-5",
                        "usage": {"input_tokens": 100, "output_tokens": 20},
                        "content": [{"type": "tool_use", "id": cid, "name": "Edit",
                                     "input": {"file_path": path, "old_string": old,
                                               "new_string": new}}]}}),
        _r({"type": "user", "uuid": rid, "parentUuid": aid, "timestamp": f"{DAY}T{hh}:00:01Z",
            "cwd": CWD, "message": {"role": "user",
                                    "content": [{"type": "tool_result", "tool_use_id": cid}]},
            "toolUseResult": tur}),
    ]
    return recs, aid


def session_with_edits(edits):
    """A linear session applying `edits` = [(old, new, original, hour)] to /proj/foo.py, in order."""
    path = "/proj/foo.py"
    recs = [_r({"type": "user", "uuid": "u0", "parentUuid": None,
                "timestamp": f"{DAY}T09:00:00Z", "cwd": CWD,
                "message": {"role": "user", "content": "go"}})]
    parent, last = "u0", "u0"
    for i, (old, new, original, hh) in enumerate(edits):
        pair, last = _edit_pair(i, path, old, new, original, hh, parent)
        recs.extend(pair)
        parent = last
    recs.append(_r({"type": "last-prompt", "leafUuid": last}))
    return FakeSession(f"/x/{DAY}.jsonl", recs)


def _branch(view):
    return next(label for label, tcs in view.timelines if tcs)


# ----------------------------------------------------------------------------------------
def test_span_diff_excludes_a_pre_span_change():
    """A change made before the span must not appear in the span's diff (span-relative)."""
    s = session_with_edits([
        ("HEAD", "HEADX", "HEAD\nBUG\nTAIL\n", "10"),   # pre-span, unrelated line
        ("BUG", "FIXED", "HEADX\nBUG\nTAIL\n", "11"),    # the fix
    ])
    view = build_view([s])
    span = span_inputs(view, [s], branch=_branch(view), start_ts=f"{DAY}T10:30:00Z")

    assert "-BUG" in span.diff and "+FIXED" in span.diff   # the fix is present
    # the pre-span change is NOT made by the span: HEADX rides along as unchanged CONTEXT
    # (the span baseline already has it), never as an added/removed line.
    assert "+HEADX" not in span.diff and "-HEAD" not in span.diff

    full = window_inputs(view, [s]).diff                   # contrast: the window MAKES both changes
    assert "+HEADX" in full and "+FIXED" in full


def test_span_baseline_reconstructed_when_in_span_edit_lacks_original():
    """The fix edits a line a PRE-span edit already changed, and the fix captured no originalFile.
    span_inputs must rebuild the span-entry baseline (B1) by replaying the pre-span write — a
    window-entry baseline (B) would make the fix's oldString 'B1' unfindable and drop the diff."""
    s = session_with_edits([
        ("B", "B1", "A\nB\n", "10"),     # pre-span: B -> B1   (captures originalFile)
        ("B1", "B2", None, "11"),         # the fix: B1 -> B2   (NO originalFile — large file)
    ])
    view = build_view([s])
    span = span_inputs(view, [s], branch=_branch(view), start_ts=f"{DAY}T10:30:00Z")

    assert "-B1" in span.diff and "+B2" in span.diff       # span-entry baseline (B1) was rebuilt
    assert not span.incomplete                             # no 'oldString not found' fallback


def test_empty_span_yields_empty_diff():
    s = session_with_edits([("BUG", "FIXED", "BUG\n", "10")])
    view = build_view([s])
    span = span_inputs(view, [s], branch=_branch(view), start_ts=f"{DAY}T23:00:00Z")
    assert span.diff == ""


# --- subagent-authored spans ------------------------------------------------------------
def test_subagent_authored_fix_reconstructs_in_span():
    """The ONLY in-span write is a subagent Edit → its change appears in the span diff.
    Main-only reconstruction would have been empty (the fix silently dropped downstream)."""
    # Main: a full Read (seeds an exact baseline) then an Agent call, both on the active branch;
    # inside the span the subagent edits the read file.
    main = [_r({"type": "user", "uuid": "u0", "parentUuid": None, "timestamp": f"{DAY}T09:00:00Z",
                "cwd": CWD, "message": {"role": "user", "content": "go"}})]
    rd = [
        _r({"type": "assistant", "uuid": "rda", "parentUuid": "u0", "timestamp": f"{DAY}T09:10:00Z",
            "cwd": CWD, "message": {"role": "assistant", "model": "claude-opus-4-8",
                                    "usage": {"input_tokens": 80, "output_tokens": 5},
                                    "content": [{"type": "tool_use", "id": "rdc", "name": "Read",
                                                 "input": {"file_path": "/proj/foo.py"}}]}}),
        _r({"type": "user", "uuid": "rdr", "parentUuid": "rda", "timestamp": f"{DAY}T09:10:01Z",
            "cwd": CWD, "message": {"role": "user",
                                    "content": [{"type": "tool_result", "tool_use_id": "rdc"}]},
            "toolUseResult": {"file": {"filePath": "/proj/foo.py", "content": "alpha\nBUG\ngamma\n",
                                       "startLine": 1, "numLines": 3, "totalLines": 3,
                                       "truncatedByTokenCap": False}}}),
    ]
    agent, a_ag = _agent_call("g", "rda", "CALL1", "10:40")   # Agent call inside the hunt window
    main += rd + agent
    main.append(_r({"type": "last-prompt", "leafUuid": "gr"}))
    sub = make_subagent("suba", "CALL1", [
        ("Edit", {"file_path": "/proj/foo.py", "old_string": "BUG", "new_string": "FIXED"}, "10:45")])
    s = FakeSession(f"/x/{DAY}.jsonl", main, subagents=[sub])
    view = build_view([s])

    span = span_inputs(view, [s], branch=_branch(view), start_ts=f"{DAY}T10:30:00Z")
    assert "-BUG" in span.diff and "+FIXED" in span.diff      # subagent fix is in the span diff
    assert span.diff != ""


def test_subagent_pre_span_write_seeds_span_baseline():
    """A subagent creates a file BEFORE the span; a main edit inside the span modifies it →
    the in-span edit reconstructs against the subagent-produced baseline (no 'oldString not
    found'). The pre-span subagent write must join the baseline replay like a main pre-span write."""
    main = [_r({"type": "user", "uuid": "u0", "parentUuid": None, "timestamp": f"{DAY}T09:00:00Z",
                "cwd": CWD, "message": {"role": "user", "content": "go"}})]
    agent, a_ag = _agent_call("g", "u0", "CALL1", "09:50")    # Agent call is pre-span
    # in-span main edit of the file the subagent created (no originalFile — large-file simulation)
    medit = [
        _r({"type": "assistant", "uuid": "mea", "parentUuid": a_ag, "timestamp": f"{DAY}T11:00:00Z",
            "cwd": CWD, "message": {"role": "assistant", "model": "claude-opus-4-8",
                                    "usage": {"input_tokens": 90, "output_tokens": 20},
                                    "content": [{"type": "tool_use", "id": "mec", "name": "Edit",
                                                 "input": {"file_path": "/proj/gen.py",
                                                           "old_string": "seed",
                                                           "new_string": "SEED"}}]}}),
        _r({"type": "user", "uuid": "mer", "parentUuid": "mea", "timestamp": f"{DAY}T11:00:01Z",
            "cwd": CWD, "message": {"role": "user",
                                    "content": [{"type": "tool_result", "tool_use_id": "mec"}]},
            "toolUseResult": {"filePath": "/proj/gen.py", "oldString": "seed",
                              "newString": "SEED"}}),   # NO originalFile
    ]
    main += agent + medit
    main.append(_r({"type": "last-prompt", "leafUuid": "mer"}))
    sub = make_subagent("suba", "CALL1", [
        ("Write", {"file_path": "/proj/gen.py", "content": "head\nseed\ntail\n"}, "10:00")])  # pre-span
    s = FakeSession(f"/x/{DAY}.jsonl", main, subagents=[sub])
    view = build_view([s])

    span = span_inputs(view, [s], branch=_branch(view), start_ts=f"{DAY}T10:30:00Z")
    assert "-seed" in span.diff and "+SEED" in span.diff      # baseline (seed) rebuilt from subagent
    assert not span.incomplete                                # no 'oldString not found' fallback


def test_off_branch_subagent_excluded_from_span():
    """A subagent whose Agent call is on a rewound/off-active branch does NOT enter the span.
    u0 forks: the Agent attempt (abandoned) and the active re-prompt continuation."""
    agent, a_ag = _agent_call("g", "u0", "CALL1", "10:40")
    main = [
        _r({"type": "user", "uuid": "u0", "parentUuid": None, "timestamp": f"{DAY}T09:00:00Z",
            "cwd": CWD, "message": {"role": "user", "content": "go"}}),
        _r({"type": "user", "uuid": "u1", "parentUuid": "u0", "timestamp": f"{DAY}T11:00:00Z",
            "cwd": CWD, "message": {"role": "user", "content": "actually do it differently"}}),
    ] + agent
    main.append(_r({"type": "last-prompt", "leafUuid": "u1"}))   # active leaf = u1, so CALL1 is off-path
    sub = make_subagent("suba", "CALL1", [
        ("Write", {"file_path": "/proj/dead.py", "content": "abandoned\n"}, "10:45")])
    s = FakeSession(f"/x/{DAY}.jsonl", main, subagents=[sub])
    view = build_view([s])

    # The active branch (u1 leaf) carries no tool calls; the abandoned CALL1 lives on the other
    # timeline, so the subagent under it is not on the active branch and must not enter the span.
    active = next(label for label, _ in view.timelines)
    span = span_inputs(view, [s], branch=active, start_ts=f"{DAY}T10:30:00Z")
    assert "dead.py" not in span.diff                         # off-branch subagent excluded
    assert span.diff == ""


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
