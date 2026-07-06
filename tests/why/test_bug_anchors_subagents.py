"""Subagent-authored fixes are folded into the fix-span FOOTPRINT (Item B).

A fix delegated to a spawned subagent writes on `Session.subagents`, not on the main
transcript's `view.timelines`, so `fix_spans` used to score it as empty (no files, weight 0)
— which sinks it in `select_bug_anchors`' cost ranking and drops it from the coaching top-N.
Passing `sessions` folds those in-span subagent writes into the footprint; omitting it (the
default) reproduces the historical main-only behavior exactly.

The subagent sidecar fixtures reuse the raw-shape builders from tests/bridge (sidecars carry
NO toolUseResult), and the tagged/view builders mirror tests/why/test_bug_anchors.py.

Run: PYTHONPATH=src python -m pytest tests/why/test_bug_anchors_subagents.py -q
"""

import os
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(_ROOT, "src"))

# reuse the RAW-shape sidecar / fake-session builders from the bridge stitch test. These carry
# NO toolUseResult (exactly as real sidecars) — so a subagent write's ToolCall has result_bytes 0,
# and its weight MUST come from the authored INPUT content (the point of _authored_bytes).
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bridge"))
from test_subagent_stitch import make_subagent, FakeSession  # noqa: E402

from haid.intent import TaggedMessage  # noqa: E402
from haid.why.bug_anchors import fix_spans, select_bug_anchors  # noqa: E402


DAY = "20260601"
# The subagent Writes this multi-line content; its length (// 4) is the expected fix_token_weight,
# and it is what makes a delegated fix rank realistically despite result_bytes being 0.
FIX_CONTENT = "def fixed():\n    # resolve the bug\n    return 42\n"


# --- view fakes: on_branch reads view.timelines and each tc.id (branch_call_ids) ---------
class FakeTC:
    def __init__(self, tool, ts, file_id, rbytes, id=None):
        self.tool, self.ts, self.target_file_id, self.result_bytes = tool, ts, file_id, rbytes
        self.derived_write = False
        self.id = id


class FakeView:
    def __init__(self, timelines):
        self.timelines = timelines


def _tag(uuid, ts, move, work_type, impl_kind=None, sid="aaaaaaaa", timeline="active"):
    return TaggedMessage(uuid=uuid, session_id=sid, timeline=timeline, ts=ts, index=0,
                         text=f"msg {uuid}", move=move, work_type=work_type,
                         purpose=f"purpose {uuid}", impl_kind=impl_kind)


# The subagent writes stamp ts as "{DAY}T{hh}:00Z". We seed the span with a matching ISO ts and
# place the branch's Agent call id = the subagent's parent tool_use id.
def _subagent_fix_setup(sub_hh="10", parent_id="CALL1"):
    """A bugfix seed whose branch spawns a subagent (CALL1) that writes one file in-span.

    Uses the raw-shape `make_subagent`: the sidecar carries only the tool_use INPUT (no
    toolUseResult), so the write's result_bytes is 0 — the realistic case."""
    sub = make_subagent("suba", parent_id, [
        ("Write", {"file_path": "/proj/fix.py", "content": FIX_CONTENT}, sub_hh)])
    s = FakeSession(f"/x/{DAY}.jsonl", [], subagents=[sub])
    # branch has ONLY the Agent call (no main writes); its id must equal the subagent's parent
    view = FakeView([("aaaaaaaa:active", [
        FakeTC("Agent", f"{DAY}T09:50:00Z", None, 0, id=parent_id)])])
    return sub, s, view


def test_subagent_write_folded_into_footprint_only_when_sessions_given():
    """The delegated fix is empty without `sessions`, populated with it (proves the param gates)."""
    _sub, s, view = _subagent_fix_setup()
    # span: seed at 09:40, next user at 11:00 → [09:40, 11:00) contains the subagent's 10:00 write
    tagged = [
        _tag("u1", f"{DAY}T09:40:00Z", "new_directive", "implementation", "bugfix"),
        _tag("u2", f"{DAY}T11:00:00Z", "new_directive", "question"),
    ]

    # sessions=None (default): main-only footprint is EMPTY for this branch
    (main_only,) = [sp for sp in fix_spans(tagged, view) if sp.seed_uuid == "u1"]
    assert main_only.files == [] and main_only.edits == () and main_only.fix_token_weight == 0

    # the subagent write's ToolCall has result_bytes 0 (raw sidecar) — so if the fold used
    # result_bytes the weight would still be 0. It must instead come from the AUTHORED content.
    # sessions provided: the subagent write is now the span's footprint, weighted by its content
    (folded,) = [sp for sp in fix_spans(tagged, view, [s]) if sp.seed_uuid == "u1"]
    assert folded.files == ["fix.py"]
    assert folded.n_edits == 1
    assert folded.edits[0].result_bytes == 0            # raw sidecar → no result bytes
    assert folded.fix_token_weight == len(FIX_CONTENT) // 4 > 0   # weight from authored input

    # and it surfaces through select_bug_anchors' ranking with that same non-zero weight
    (anchor,) = [a for a in select_bug_anchors(tagged, view, top=10, sessions=[s])
                 if a.refs["fix_uuid"] == "u1"]
    assert anchor.refs["fix_files"] == ["fix.py"]
    assert anchor.token_weight == len(FIX_CONTENT) // 4


def test_subagent_write_outside_span_not_counted():
    """A subagent write AFTER the span's end_ts is excluded from the footprint."""
    _sub, s, view = _subagent_fix_setup(sub_hh="12")   # subagent writes at 12:00
    tagged = [
        _tag("u1", f"{DAY}T09:40:00Z", "new_directive", "implementation", "bugfix"),
        _tag("u2", f"{DAY}T11:00:00Z", "new_directive", "question"),   # span ends 11:00 < 12:00
    ]
    (folded,) = [sp for sp in fix_spans(tagged, view, [s]) if sp.seed_uuid == "u1"]
    assert folded.files == [] and folded.edits == () and folded.fix_token_weight == 0


def test_sessions_none_reproduces_main_only_footprint():
    """Regression guard: with main writes present and sessions=None, footprint is unchanged
    whether or not subagents exist on the sessions (the param is the only thing that folds)."""
    _sub, s, _ = _subagent_fix_setup()
    view = FakeView([("aaaaaaaa:active", [
        FakeTC("Edit", f"{DAY}T09:45:00Z", "repo:a.py", 4000, id="M1"),
        FakeTC("Agent", f"{DAY}T09:50:00Z", None, 0, id="CALL1"),
    ])])
    tagged = [
        _tag("u1", f"{DAY}T09:40:00Z", "new_directive", "implementation", "bugfix"),
        _tag("u2", f"{DAY}T11:00:00Z", "new_directive", "question"),
    ]
    (main_only,) = [sp for sp in fix_spans(tagged, view) if sp.seed_uuid == "u1"]
    (with_sessions_arg,) = [sp for sp in fix_spans(tagged, view, [s]) if sp.seed_uuid == "u1"]

    # main-only: exactly the one main edit, subagent NOT folded
    assert main_only.files == ["repo:a.py"] and main_only.fix_token_weight == 4000 // 4
    assert main_only.n_edits == 1
    # with sessions: main edit + the subagent's fix.py, chronological
    assert with_sessions_arg.files == ["fix.py", "repo:a.py"]
    assert with_sessions_arg.n_edits == 2


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
