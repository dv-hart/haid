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
    assert c.bug_id == f"bugfix_{STEM}_1"
    assert "/" not in c.bug_id                            # safe as a job-file name (no nested dirs)
    assert "tz=UTC" in c.diff and "+def" not in c.diff   # the fix, span-relative
    assert c.earned_find_cost > 0                         # the hunt's normalized-token cost


def test_collect_find_cost_tracks_context_size():
    """A bigger hunt (more cache-read context) -> bigger earned_find_cost."""
    small = bugfix.collect_candidates([bugfix_session(STEM, PATH, "a\n", "a", "b", in_tok=1000)],
                                      [_bugfix_tag(STEM)])[0]
    big = bugfix.collect_candidates([bugfix_session(STEM, PATH, "a\n", "a", "b", in_tok=90000)],
                                    [_bugfix_tag(STEM)])[0]
    assert big.earned_find_cost > small.earned_find_cost


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
