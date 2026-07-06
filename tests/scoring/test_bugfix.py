"""The why->score join: fix spans -> find-cost + placed -> eligibility-gated CuredBugs.

Deterministic (synthetic session + stub difficulty backend). Run:
PYTHONPATH=src python -m pytest tests/scoring/test_bugfix.py -q
"""

from __future__ import annotations

import os
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(_ROOT, "src"))

from haid.intent import TaggedMessage
from haid.scoring import bugfix, value
from haid.scoring.compare import Backend
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


def bugfix_session(stem, path, original, old, new, in_tok=5000):
    """A session: user asks for a fix, agent edits `old`->`new` on `path` (with usage)."""
    recs = [
        _r({"type": "user", "uuid": f"u_{stem}", "parentUuid": None,
            "timestamp": f"{stem}T10:00:00Z", "cwd": CWD,
            "message": {"role": "user", "content": "the parser crashes on tz-less dates, fix it"}}),
        _r({"type": "assistant", "uuid": f"a_{stem}", "parentUuid": f"u_{stem}",
            "timestamp": f"{stem}T10:05:00Z", "cwd": CWD,
            "message": {"role": "assistant", "model": "claude-sonnet-4-6",
                        "usage": {"input_tokens": in_tok, "output_tokens": 200,
                                  "cache_read_input_tokens": 40000},
                        "content": [{"type": "tool_use", "id": f"c_{stem}", "name": "Edit",
                                     "input": {"file_path": path, "old_string": old,
                                               "new_string": new}}]}}),
        _r({"type": "user", "uuid": f"r_{stem}", "parentUuid": f"a_{stem}",
            "timestamp": f"{stem}T10:05:01Z", "cwd": CWD,
            "message": {"role": "user",
                        "content": [{"type": "tool_result", "tool_use_id": f"c_{stem}"}]},
            "toolUseResult": {"filePath": path, "originalFile": original,
                              "oldString": old, "newString": new}}),
        _r({"type": "last-prompt", "leafUuid": f"a_{stem}"}),
    ]
    return FakeSession(f"/x/{stem}.jsonl", recs)


def _bugfix_tag(stem):
    return TaggedMessage(uuid=f"u_{stem}", session_id=stem, timeline="active",
                         ts=f"{stem}T10:00:00Z", index=0, text="fix the parser",
                         move="new_directive", work_type="implementation",
                         purpose="Fix the tz-less date crash", impl_kind="bugfix")


class _StubBackend(Backend):
    """Subject beats its first `beats` anchors (-> rung == beats)."""
    def __init__(self, beats):
        self.beats = beats

    def compare_batch(self, subject, anchors, axis):
        return ["subject" if i < self.beats else "anchor" for i in range(len(anchors))]


STEM = "20260601"
PATH = "/proj/parse.py"


# ---------------------------------------------------------------- is_eligible (the gate)
def test_is_eligible_gate():
    src = {"cause_class": "source", "scope": "unknown", "holding": "held"}
    cross = {"cause_class": "agent", "scope": "cross_episode", "holding": "held"}
    own = {"cause_class": "agent", "scope": "same_episode", "holding": "held"}
    assert bugfix.is_eligible(src)                       # inherited -> credit
    assert bugfix.is_eligible(cross)                     # other-thread -> credit
    assert not bugfix.is_eligible(own)                   # self-inflicted same thread -> no
    assert not bugfix.is_eligible({**src, "holding": "recurred"})   # didn't hold -> no
    assert not bugfix.is_eligible(None)                  # no attribution -> no credit


# ---------------------------------------------------------------- collect (deterministic)
def test_collect_candidate_has_diff_and_find_cost():
    s = bugfix_session(STEM, PATH, "def parse(x):\n  return dt(x)\n", "dt(x)", "dt(x, tz=UTC)")
    cands = bugfix.collect_candidates([s], [_bugfix_tag(STEM)])
    assert len(cands) == 1
    c = cands[0]
    assert c.bug_id.startswith("fix_") and "/" not in c.bug_id   # canonical, job-file-safe id
    assert "tz=UTC" in c.diff and "+def" not in c.diff   # the fix, span-relative
    assert c.earned_find_cost > 0                         # the hunt's normalized-token cost
    assert c.self_inflicted is False                      # the fixed line was pre-existing, not agent-authored


def test_collect_find_cost_tracks_context_size():
    """A bigger hunt (more cache-read context) -> bigger earned_find_cost."""
    small = bugfix.collect_candidates([bugfix_session(STEM, PATH, "a\n", "a", "b", in_tok=1000)],
                                      [_bugfix_tag(STEM)])[0]
    big = bugfix.collect_candidates([bugfix_session(STEM, PATH, "a\n", "a", "b", in_tok=90000)],
                                    [_bugfix_tag(STEM)])[0]
    assert big.earned_find_cost > small.earned_find_cost


# ---------------------------------------------------------------- deterministic gate (self-inflicted)
def feature_then_fix_session(stem, path):
    """Session that AUTHORS a buggy line (feature edit), then FIXES it later (bugfix span) — the
    self-inflicted case the gate must catch. Returns (session, [feature_tag, fix_tag])."""
    orig = "def f(value):\n    pass\n"
    after_feature = "def f(value):\n    return compute(value)\n"
    recs = [
        _r({"type": "user", "uuid": f"uf_{stem}", "parentUuid": None,
            "timestamp": f"{stem}T09:00:00Z", "cwd": CWD,
            "message": {"role": "user", "content": "add the compute call"}}),
        _r({"type": "assistant", "uuid": f"af_{stem}", "parentUuid": f"uf_{stem}",
            "timestamp": f"{stem}T09:05:00Z", "cwd": CWD,
            "message": {"role": "assistant", "model": "claude-sonnet-4-6",
                        "usage": {"input_tokens": 3000, "output_tokens": 100},
                        "content": [{"type": "tool_use", "id": f"cf_{stem}", "name": "Edit",
                                     "input": {"file_path": path, "old_string": "pass",
                                               "new_string": "return compute(value)"}}]}}),
        _r({"type": "user", "uuid": f"rf_{stem}", "parentUuid": f"af_{stem}",
            "timestamp": f"{stem}T09:05:01Z", "cwd": CWD,
            "message": {"role": "user",
                        "content": [{"type": "tool_result", "tool_use_id": f"cf_{stem}"}]},
            "toolUseResult": {"filePath": path, "originalFile": orig,
                              "oldString": "pass", "newString": "return compute(value)"}}),
        _r({"type": "user", "uuid": f"ux_{stem}", "parentUuid": f"rf_{stem}",
            "timestamp": f"{stem}T10:00:00Z", "cwd": CWD,
            "message": {"role": "user", "content": "that crashes on None, fix it"}}),
        _r({"type": "assistant", "uuid": f"ax_{stem}", "parentUuid": f"ux_{stem}",
            "timestamp": f"{stem}T10:05:00Z", "cwd": CWD,
            "message": {"role": "assistant", "model": "claude-sonnet-4-6",
                        "usage": {"input_tokens": 4000, "output_tokens": 100},
                        "content": [{"type": "tool_use", "id": f"cx_{stem}", "name": "Edit",
                                     "input": {"file_path": path,
                                               "old_string": "return compute(value)",
                                               "new_string": "return compute(value, safe=True)"}}]}}),
        _r({"type": "user", "uuid": f"rx_{stem}", "parentUuid": f"ax_{stem}",
            "timestamp": f"{stem}T10:05:01Z", "cwd": CWD,
            "message": {"role": "user",
                        "content": [{"type": "tool_result", "tool_use_id": f"cx_{stem}"}]},
            "toolUseResult": {"filePath": path, "originalFile": after_feature,
                              "oldString": "return compute(value)",
                              "newString": "return compute(value, safe=True)"}}),
        _r({"type": "last-prompt", "leafUuid": f"ax_{stem}"}),
    ]
    tags = [
        TaggedMessage(uuid=f"uf_{stem}", session_id=stem, timeline="active",
                      ts=f"{stem}T09:00:00Z", index=0, text="add compute", move="new_directive",
                      work_type="implementation", purpose="add compute", impl_kind="feature"),
        TaggedMessage(uuid=f"ux_{stem}", session_id=stem, timeline="active",
                      ts=f"{stem}T10:00:00Z", index=1, text="fix crash", move="new_directive",
                      work_type="implementation", purpose="fix the None crash", impl_kind="bugfix"),
    ]
    return FakeSession(f"/x/{stem}.jsonl", recs), tags


def test_gate_flags_self_inflicted_fix():
    """The fix rewrites a line the agent authored earlier THIS episode → self-inflicted."""
    s, tags = feature_then_fix_session(STEM, PATH)
    cands = bugfix.collect_candidates([s], tags)
    assert len(cands) == 1                                # the bugfix span (the feature isn't a fix)
    assert cands[0].self_inflicted is True
    # and resolve_cured drops it — no credit for cleaning up your own same-episode mess
    cured, _ = bugfix.resolve_cured(cands, lambda axis, sid: _StubBackend(5))
    assert cured == []


def test_gate_scores_inherited_fix():
    """A fix whose removed line was never agent-authored in the episode is inherited → scored."""
    s = bugfix_session(STEM, PATH, "x\n", "x", "y")      # 'x' is pre-existing, not authored here
    cands = bugfix.collect_candidates([s], [_bugfix_tag(STEM)])
    assert cands[0].self_inflicted is False
    cured, _ = bugfix.resolve_cured(cands, lambda axis, sid: _StubBackend(5))
    assert len(cured) == 1


# ------------------------------------------------ subagent-authored self-inflicted gate (Item D)
from haid.graph.model import ToolCall


def _sub_write_tc(call_id, path, new_string, ts):
    """A real ToolCall shaped like a subagent Edit (what saw.collect surfaces via w.tc): the gate
    reads only .tool/.params/.target_file_id/.ts and _authored_text's new_string. `target_file_id`
    is the repo-relative id the diff's `-` lines key on (build.py makes /proj/x.py -> x.py)."""
    return ToolCall(id=call_id, tool="Edit", turn_id="t", ts=ts,
                    params={"file_path": path, "old_string": "", "new_string": new_string},
                    target_file_id=path.split("/proj/")[-1])


def test_is_self_inflicted_sees_subagent_authorship():
    """The core Item-D proof, done directly on the gate. A fix removes lines a SUBAGENT authored
    earlier this episode. WITH the subagent write in the authorship stream the gate flags it
    self-inflicted; with a MAIN-ONLY stream (the pre-fix blind spot) it does not — so subagent
    farming would slip through without the augmentation."""
    fid = "svc.py"
    # nontrivial (>=8 char) lines a subagent authored before the fix, then the fix removes them.
    buggy = "result = compute(payload, retries=0)\nreturn normalize(result, strict=False)"
    diff = (f"diff --git a/{fid} b/{fid}\n--- a/{fid}\n+++ b/{fid}\n"
            + "\n".join(f"-{ln}" for ln in buggy.splitlines())
            + "\n+return normalize(compute(payload, retries=3), strict=True)\n")
    cutoff = f"{STEM}T10:00:00Z"
    sub_tc = _sub_write_tc("subc1", f"/proj/{fid}", buggy, f"{STEM}T09:00:00Z")  # before cutoff

    with_sub = [("", sub_tc)]                     # authorship = main(empty) + subagent write
    main_only = []                                # the old blind-spot stream (main writes only)
    assert bugfix.is_self_inflicted(diff, with_sub, cutoff) is True    # subagent-authored -> caught
    assert bugfix.is_self_inflicted(diff, main_only, cutoff) is False  # blind spot: uncaught


# ---------------------------------------------- collect_candidates sees subagent authorship (Item D)
class _SubFakeSubagent:
    """Minimal subagent sidecar for saw.collect: raw records + parent Agent call link."""
    def __init__(self, agent_id, tool_use_id, records):
        self.agent_id = agent_id
        self.meta = {"toolUseId": tool_use_id}
        self.parse = type("P", (), {"records": records})()
        self.jsonl_path = f"/x/{agent_id}.jsonl"

    @property
    def parent_tool_use_id(self):
        return self.meta.get("toolUseId")


def _sub_edit_records(agent_id, path, old, new, hh_mm):
    """A subagent sidecar (RAW api shape, no toolUseResult) that Edits `path` old->new."""
    root, aid, rid, cid = f"{agent_id}root", f"{agent_id}a", f"{agent_id}r", f"{agent_id}c"
    return [
        _r({"type": "user", "uuid": root, "parentUuid": None, "isSidechain": True,
            "agentId": agent_id, "timestamp": f"{STEM}T{hh_mm}:00Z", "cwd": CWD,
            "message": {"role": "user", "content": "introduce the code"}}),
        _r({"type": "assistant", "uuid": aid, "parentUuid": root, "isSidechain": True,
            "agentId": agent_id, "timestamp": f"{STEM}T{hh_mm}:30Z", "cwd": CWD,
            "message": {"role": "assistant", "model": "claude-sonnet-4-6",
                        "usage": {"input_tokens": 100, "output_tokens": 20},
                        "content": [{"type": "tool_use", "id": cid, "name": "Edit",
                                     "input": {"file_path": path, "old_string": old,
                                               "new_string": new}}]}}),
        _r({"type": "user", "uuid": rid, "parentUuid": aid, "isSidechain": True,
            "agentId": agent_id, "timestamp": f"{STEM}T{hh_mm}:31Z", "cwd": CWD,
            "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": cid,
                 "content": "The file has been updated successfully."}]}}),
    ]


def subagent_authored_then_fixed_session(stem, path):
    """Episode where a SUBAGENT (spawned via an active-branch Agent call) authors a buggy line
    early, then the MAIN agent fixes it later — the cross-actor self-inflicted case Item D closes.
    Returns (session, [fix_tag])."""
    buggy = "return compute(value, retries=0)"                # >=8 chars -> trackable
    fixed = "return compute(value, retries=3)"
    recs = [
        _r({"type": "user", "uuid": f"u0_{stem}", "parentUuid": None,
            "timestamp": f"{stem}T09:00:00Z", "cwd": CWD,
            "message": {"role": "user", "content": "wire up compute via a subagent"}}),
        # main spawns a subagent (Agent call CALL1) on the active branch
        _r({"type": "assistant", "uuid": f"ag_{stem}", "parentUuid": f"u0_{stem}",
            "timestamp": f"{stem}T09:01:00Z", "cwd": CWD,
            "message": {"role": "assistant", "model": "claude-sonnet-4-6",
                        "usage": {"input_tokens": 500, "output_tokens": 40},
                        "content": [{"type": "tool_use", "id": "CALL1", "name": "Agent",
                                     "input": {"description": "impl",
                                               "subagent_type": "general-purpose"}}]}}),
        _r({"type": "user", "uuid": f"agr_{stem}", "parentUuid": f"ag_{stem}",
            "timestamp": f"{stem}T09:40:00Z", "cwd": CWD,
            "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "CALL1", "content": "done"}]}}),
        # later: the user reports a crash, main fixes the subagent-authored line
        _r({"type": "user", "uuid": f"ux_{stem}", "parentUuid": f"agr_{stem}",
            "timestamp": f"{stem}T10:00:00Z", "cwd": CWD,
            "message": {"role": "user", "content": "that crashes with no retries, fix it"}}),
        _r({"type": "assistant", "uuid": f"ax_{stem}", "parentUuid": f"ux_{stem}",
            "timestamp": f"{stem}T10:05:00Z", "cwd": CWD,
            "message": {"role": "assistant", "model": "claude-sonnet-4-6",
                        "usage": {"input_tokens": 4000, "output_tokens": 100},
                        "content": [{"type": "tool_use", "id": f"cx_{stem}", "name": "Edit",
                                     "input": {"file_path": path, "old_string": buggy,
                                               "new_string": fixed}}]}}),
        _r({"type": "user", "uuid": f"rx_{stem}", "parentUuid": f"ax_{stem}",
            "timestamp": f"{stem}T10:05:01Z", "cwd": CWD,
            "message": {"role": "user",
                        "content": [{"type": "tool_result", "tool_use_id": f"cx_{stem}"}]},
            "toolUseResult": {"filePath": path, "originalFile": f"def f(value):\n    {buggy}\n",
                              "oldString": buggy, "newString": fixed}}),
        _r({"type": "last-prompt", "leafUuid": f"ax_{stem}"}),
    ]
    sub = _SubFakeSubagent(f"sub_{stem}", "CALL1",
                           _sub_edit_records(f"sub_{stem}", path, "pass", buggy, "09:10"))
    s = FakeSession(f"/x/{stem}.jsonl", recs)
    s.subagents = [sub]
    fix_tag = TaggedMessage(uuid=f"ux_{stem}", session_id=stem, timeline="active",
                            ts=f"{stem}T10:00:00Z", index=0, text="fix crash",
                            move="new_directive", work_type="implementation",
                            purpose="fix the retries crash", impl_kind="bugfix")
    return s, [fix_tag]


def test_collect_flags_subagent_authored_fix_as_self_inflicted():
    """collect_candidates wiring: a subagent introduced the fixed line earlier in the episode
    (on the active branch), so the later main-agent fix is self-inflicted rework — flagged, and
    dropped by resolve_cured. This is the farm hole Item D closes end-to-end."""
    s, tags = subagent_authored_then_fixed_session(STEM, PATH)
    cands = bugfix.collect_candidates([s], tags)
    assert len(cands) == 1
    assert cands[0].self_inflicted is True                    # subagent authorship now counted
    cured, _ = bugfix.resolve_cured(cands, lambda axis, sid: _StubBackend(5))
    assert cured == []                                        # no credit for curing a subagent bug


# ---------------------------------------------------------------- resolve (place + gate)
def test_resolve_places_and_builds_cured_bug():
    s = bugfix_session(STEM, PATH, "x\n", "x", "y")
    cands = bugfix.collect_candidates([s], [_bugfix_tag(STEM)])
    cured, pending = bugfix.resolve_cured(cands, lambda axis, sid: _StubBackend(5))
    assert not pending and len(cured) == 1
    cb = cured[0]
    assert isinstance(cb, value.CuredBug)
    assert round(cb.fix_difficulty.rung) == 5
    assert cb.earned_find_cost == cands[0].earned_find_cost
    # and it actually lifts achievement when folded in
    ach = value.achievement(0.0, _trivial_pl(), _clean(), cured_bugs=cured)
    assert ach.n_cured_bugs == 1 and ach.bugfix_term > 0 and ach.achievement > 0


def test_resolve_eligibility_gate_drops_ineligible():
    s = bugfix_session(STEM, PATH, "x\n", "x", "y")
    cands = bugfix.collect_candidates([s], [_bugfix_tag(STEM)])
    cured, _ = bugfix.resolve_cured(cands, lambda axis, sid: _StubBackend(5),
                                    eligible=lambda bug_id: False)
    assert cured == []


# ---------------------------------------------------------------- subagent hunt tokens (Item C)
from haid.scoring.bugfix import _hunt_find_cost


class _FakeSubagent:
    """Minimal subagent sidecar: raw assistant records (no toolUseResult), own usage+model+ts."""
    def __init__(self, records):
        self.parse = type("P", (), {"records": records})()


def _sub_assistant(stem, hh_mm, model="claude-sonnet-4-6", in_tok=8000, cache_read=60000):
    """One raw subagent assistant record with usage — the shape sidecars store (isSidechain,
    agentId, message.usage + message.model, no toolUseResult)."""
    return _r({"type": "assistant", "uuid": f"sa_{stem}_{hh_mm}", "parentUuid": None,
               "isSidechain": True, "agentId": f"sub_{stem}",
               "timestamp": f"{stem}T{hh_mm}:00Z", "cwd": CWD,
               "message": {"role": "assistant", "model": model,
                           "usage": {"input_tokens": in_tok, "output_tokens": 150,
                                     "cache_read_input_tokens": cache_read},
                           "content": [{"type": "text", "text": "hunting"}]}})


def test_hunt_find_cost_includes_subagent_turns():
    """A subagent whose assistant turns fall inside [lo,hi] adds real hunt cost: the WITH-subagent
    find-cost is strictly greater than the same session main-only."""
    s = bugfix_session(STEM, PATH, "x\n", "x", "y")
    lo, hi = f"{STEM}T10:00:00Z", f"{STEM}T10:05:00Z"
    baseline = _hunt_find_cost(s, lo, hi)                 # subagents == [] here
    # attach a subagent that hunted at 10:02, inside the window
    s.subagents = [_FakeSubagent([_sub_assistant(STEM, "10:02")])]
    with_sub = _hunt_find_cost(s, lo, hi)
    assert with_sub > baseline                            # subagent hunt tokens now counted


def test_hunt_find_cost_excludes_subagent_turns_outside_window():
    """The time filter still applies to subagents: a subagent turn OUTSIDE [lo,hi] adds nothing."""
    s = bugfix_session(STEM, PATH, "x\n", "x", "y")
    lo, hi = f"{STEM}T10:00:00Z", f"{STEM}T10:05:00Z"
    baseline = _hunt_find_cost(s, lo, hi)
    # subagent turn at 11:00 — after the hunt window
    s.subagents = [_FakeSubagent([_sub_assistant(STEM, "11:00")])]
    assert _hunt_find_cost(s, lo, hi) == baseline         # out-of-window subagent excluded


# small helpers reused from the value test surface
def _clean():
    from haid.scoring.defects import DefectResult
    return DefectResult.from_findings([], 50)


def _trivial_pl():
    from haid.scoring.anchors import load_ladder
    from haid.scoring.placement import PlacementResult
    n = load_ladder("difficulty").n_rungs
    return PlacementResult(axis="difficulty", rung=0.0, seen=n, n_rungs=n, samples=1,
                           per_anchor=[(a.id, "anchor") for a in load_ladder("difficulty").anchors])


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
