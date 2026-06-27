"""Bug-source attribution: fix-span anchor detection, the bug prompt, strict bug-note validation.

Deterministic, model-free. The detector turns tagged messages + the window's edit footprint
into bug anchors; the why-pass then dispatches them to the bug-attribution prompt/schema.
"""

import json

import pytest

from haid import why
from haid.intent import TaggedMessage
from haid.why.bug_anchors import select_bug_anchors
from haid.why.investigate import HarnessBackend, validate_bug_note, validate_for_anchor
from haid.why.prompts import build_bug_prompt


# --- a tiny WindowView stand-in: only .timelines is read by the detector ------------------
class FakeTC:
    def __init__(self, tool, ts, file_id, rbytes):
        self.tool, self.ts, self.target_file_id, self.result_bytes = tool, ts, file_id, rbytes
        self.derived_write = False


class FakeView:
    def __init__(self, timelines):
        self.timelines = timelines


def _tag(uuid, ts, move, work_type, impl_kind=None, sid="aaaaaaaa", timeline="active"):
    return TaggedMessage(uuid=uuid, session_id=sid, timeline=timeline, ts=ts, index=0,
                         text=f"msg {uuid}", move=move, work_type=work_type,
                         purpose=f"purpose {uuid}", impl_kind=impl_kind)


def test_seeds_from_bugfix_and_correction_not_feature():
    tagged = [
        _tag("u1", "1", "new_directive", "implementation", "feature"),   # not a seed
        _tag("u2", "3", "new_directive", "implementation", "bugfix"),    # seed (bugfix)
        _tag("u3", "5", "correction", "implementation"),                 # seed (correction)
        _tag("u4", "7", "correction", "question"),                       # not a seed (Q)
        _tag("u5", "9", "refinement", "implementation", "refactor"),     # not a seed
    ]
    anchors = select_bug_anchors(tagged, None, top=10)
    purposes = {a.refs["fix_uuid"] for a in anchors}
    assert purposes == {"u2", "u3"}
    assert all(a.metric == "bugfix" and a.id.startswith("bugfix/window/") for a in anchors)


def test_footprint_and_ranking_by_resolving_edit_cost():
    tagged = [
        _tag("u1", "10", "new_directive", "implementation", "bugfix"),
        _tag("u2", "30", "correction", "implementation"),
        _tag("u3", "50", "new_directive", "question"),     # span boundary for u2
    ]
    # u1's span [10,30): two edits to a.py/b.py; u2's span [30,50): one small edit to c.py
    view = FakeView([("aaaaaaaa:active", [
        FakeTC("Edit", "12", "repo:a.py", 4000),
        FakeTC("Read", "15", "repo:z.py", 9999),           # not a write — ignored
        FakeTC("Write", "20", "repo:b.py", 4000),
        FakeTC("Edit", "35", "repo:c.py", 400),
        FakeTC("Edit", "60", "repo:d.py", 8000),           # after u3 boundary — out of any span
    ])])
    anchors = select_bug_anchors(tagged, view, top=10)
    by_uuid = {a.refs["fix_uuid"]: a for a in anchors}
    assert by_uuid["u1"].refs["fix_files"] == ["repo:a.py", "repo:b.py"]
    assert by_uuid["u1"].refs["n_edits"] == 2
    assert by_uuid["u1"].token_weight == (4000 + 4000) // 4
    assert by_uuid["u2"].refs["fix_files"] == ["repo:c.py"]
    assert by_uuid["u2"].token_weight == 400 // 4
    # ranked by cost: u1 (bigger) before u2, and ids reflect rank order
    assert [a.id for a in anchors] == ["bugfix/window/1", "bugfix/window/2"]
    assert anchors[0].refs["fix_uuid"] == "u1"


def test_top_cap_and_no_edit_fix_still_qualifies():
    tagged = [_tag("u1", "1", "new_directive", "implementation", "bugfix")]
    anchors = select_bug_anchors(tagged, FakeView([]), top=10)   # no edits located
    assert len(anchors) == 1
    assert anchors[0].refs["fix_files"] == [] and anchors[0].token_weight == 0
    assert "no resolving edit located" in anchors[0].detail


GOOD_BUG_NOTE = {
    "anchor_audit": "confirmed a real fix", "cause_class": "agent", "origin": "traced",
    "origin_ref": {"session": "aaaaaaaa", "ts": "2", "what": "wrote parse() w/o tz"},
    "mistake_kind": "incomplete_edit", "scope": "same_episode", "holding": "held",
    "note": "the agent introduced this earlier in the same feature", "evidence": [],
    "remedy": "run the test before moving on", "estimated_rework_tokens": 3200,
    "confidence": "high"}


def test_bug_note_validation_enums_and_agent_requires_mistake_kind():
    assert validate_bug_note(dict(GOOD_BUG_NOTE), "x")["cause_class"] == "agent"
    with pytest.raises(ValueError, match="cause_class"):
        validate_bug_note({**GOOD_BUG_NOTE, "cause_class": "everyone"}, "x")
    with pytest.raises(ValueError, match="origin"):
        validate_bug_note({**GOOD_BUG_NOTE, "origin": "vibes"}, "x")
    with pytest.raises(ValueError, match="mistake_kind"):   # agent w/o mistake_kind
        validate_bug_note({**GOOD_BUG_NOTE, "mistake_kind": None}, "x")
    # user/source need no mistake_kind
    ok = validate_bug_note({**GOOD_BUG_NOTE, "cause_class": "user", "mistake_kind": None}, "x")
    assert ok["cause_class"] == "user"
    with pytest.raises(ValueError, match="missing keys"):
        validate_bug_note({k: v for k, v in GOOD_BUG_NOTE.items() if k != "holding"}, "x")


def test_bug_prompt_and_manifest_dispatch(tmp_path):
    tagged = [_tag("u1", "10", "new_directive", "implementation", "bugfix")]
    view = FakeView([("aaaaaaaa:active", [FakeTC("Edit", "12", "repo:a.py", 800)])])
    anchor = select_bug_anchors(tagged, view, top=1)[0]

    p = build_bug_prompt(anchor, transcript_dir="T:/d", project_path="P:/r",
                         all_session_ids=["aaaaaaaa", "bbbbbbbb"])
    assert "bug-attribution agent" in p
    assert "HIGHEST BAR" in p and "origin=orphan" in p
    assert "repo:a.py" in p and "T:/d" in p
    # every controlled enum value the schema allows must be EXPLAINED in the prompt body,
    # and every output field must have usage guidance — no value/field left undocumented.
    assert "origin=ambiguous" in p                       # the previously-unexplained enum
    assert "evidence" in p and "estimated_rework_tokens" in p and "remedy" in p

    # dispatch: a bug anchor must validate against the BUG schema, not the waste schema
    assert validate_for_anchor(dict(GOOD_BUG_NOTE), anchor)["origin"] == "traced"

    be = HarnessBackend(job_dir=str(tmp_path))
    runner_note = dict(GOOD_BUG_NOTE)

    def runner(manifest):
        assert manifest["jobs"][0]["metric"] == "bugfix"
        assert manifest["jobs"][0]["schema"]["properties"].get("cause_class")  # per-job schema
        return [runner_note]
    results = why.investigate_window(DOC := {"window": {"sessions": [{"id": "aaaaaaaa"}]}},
                                     [anchor],
                                     HarnessBackend(job_dir="/unused", runner=runner),
                                     transcript_dir="T:/d", project_path="P:/r")
    assert results[0][1]["cause_class"] == "agent"
    text = why.render(results, label="t")
    assert "CAUSE: AGENT" in text and "incomplete_edit" in text
