"""The message classifier (the why-pass, step 2) — deterministic, model-free.

Tested without any model:
  - EXTRACTION: user messages are pulled in chronological order; context is prior user
    messages + agent FINAL TEXT only (no thinking/tools); EVERY branch is walked (a rewound
    stretch of work is captured, not ignored) with each message's context built from ITS OWN
    branch; the shared prefix is deduped to one message.
  - ORCHESTRATION: tag_window folds saved labels (ReplayBackend) onto the messages, and the
    HarnessBackend writes a manifest + raises PendingClassifications when no labels exist yet.

Run: PYTHONPATH=src python -m pytest tests/intent/ -q
"""

from __future__ import annotations

import json
import os
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(_ROOT, "src"))

from haid import intent
from haid.intent import messages as msgmod
from haid.intent.classify import HarnessBackend, PendingClassifications, ReplayBackend
from haid.session import records as rec
from haid.session.forest import Forest

CWD = "/proj"


# --- record builders (mirror tests/metrics) ---------------------------------------------
def asst(uuid, parent, ts, blocks):
    return rec.from_dict({"type": "assistant", "uuid": uuid, "parentUuid": parent, "timestamp": ts,
                          "cwd": CWD, "message": {"role": "assistant", "content": blocks}})


def asst_text(uuid, parent, ts, text):
    return asst(uuid, parent, ts, [{"type": "text", "text": text}])


def tu(cid, name, inp):
    return {"type": "tool_use", "id": cid, "name": name, "input": inp}


def res(uuid, parent, cid, tur=None, is_error=False, ts=None):
    raw = {"type": "user", "uuid": uuid, "parentUuid": parent, "timestamp": ts, "cwd": CWD,
           "message": {"role": "user",
                       "content": [{"type": "tool_result", "tool_use_id": cid, "is_error": is_error}]}}
    if tur is not None:
        raw["toolUseResult"] = tur
    return rec.from_dict(raw)


def user(uuid, parent, ts, text):
    return rec.from_dict({"type": "user", "uuid": uuid, "parentUuid": parent, "timestamp": ts,
                          "cwd": CWD, "message": {"role": "user", "content": text}})


def last_prompt(leaf):
    return rec.from_dict({"type": "last-prompt", "leafUuid": leaf})


class FakeSession:
    """Minimal stand-in: tagging only needs path + parse.records + forest."""
    def __init__(self, path, records):
        self.path = path
        self.parse = type("P", (), {"records": records})()
        self.forest = Forest(records)


def two_message_convo():
    return [
        user("u1", None, "1", "implement foo in f.py"),
        asst_text("a1", "u1", "2", "Done — wrote foo()."),
        user("u2", "a1", "3", "now add a test"),
        last_prompt("u2"),
    ]


def rewound_step_a_then_b():
    """Planning prefix forks: step A (rewound) and step B (active). Classic 'rewind so the
    earlier work doesn't cloud the later work' pattern — step A must still be captured."""
    return [
        user("u0", None, "1", "draft a plan"),
        asst_text("a0", "u0", "2", "Here's the plan."),
        user("uA", "a0", "3", "do step A"),                 # branch A (abandoned)
        asst_text("aA", "uA", "4", "Did step A."),
        user("uB", "a0", "5", "do step B instead"),         # branch B (active, sibling fork)
        asst_text("aB", "uB", "6", "Did step B."),
        last_prompt("aB"),
    ]


# --- extraction -------------------------------------------------------------------------
def test_extract_picks_user_prompts_in_order():
    s = FakeSession("/x/aaaaaaaa.jsonl", two_message_convo())
    ms = msgmod.extract_window_messages([s])
    assert [m.text for m in ms] == ["implement foo in f.py", "now add a test"]
    assert [m.index for m in ms] == [0, 1]
    assert all(m.session_id == "aaaaaaaa" and m.timeline == "active" for m in ms)


def test_context_is_prior_user_and_agent_text_only():
    s = FakeSession("/x/aaaaaaaa.jsonl", two_message_convo())
    ms = msgmod.extract_window_messages([s])
    ctx = ms[1].context
    assert "USER: implement foo in f.py" in ctx
    assert "AGENT: Done — wrote foo()." in ctx
    assert "USER:" not in ms[0].context          # first message has no prior user turn


def test_walks_rewind_branch_and_dedups_prefix():
    s = FakeSession("/x/aaaaaaaa.jsonl", rewound_step_a_then_b())
    ms = msgmod.extract_window_messages([s])
    texts = {m.text for m in ms}
    # step A is on an abandoned branch but must NOT be dropped
    assert "do step A" in texts and "do step B instead" in texts
    # the shared planning prefix appears exactly once
    assert sum(1 for m in ms if m.text == "draft a plan") == 1
    stepA = next(m for m in ms if m.text == "do step A")
    assert stepA.timeline.startswith("rewind")


def test_per_branch_context_does_not_leak_across_branches():
    s = FakeSession("/x/aaaaaaaa.jsonl", rewound_step_a_then_b())
    ms = msgmod.extract_window_messages([s])
    stepA = next(m for m in ms if m.text == "do step A")
    stepB = next(m for m in ms if m.text == "do step B instead")
    # each branch sees the shared plan, but NOT the other branch's work
    assert "Here's the plan." in stepA.context and "step B" not in stepA.context
    assert "Here's the plan." in stepB.context and "Did step A." not in stepB.context


def test_head_tail_truncation_keeps_both_ends():
    long_reply = "START" + ("x" * 1000) + "END"
    s = FakeSession("/x/aaaaaaaa.jsonl", [
        user("u1", None, "1", "go"),
        asst_text("a1", "u1", "2", long_reply),
        user("u2", "a1", "3", "next"),
        last_prompt("u2"),
    ])
    ms = msgmod.extract_window_messages([s])
    ctx = ms[1].context
    assert "START" in ctx and "END" in ctx and "…" in ctx
    assert ("x" * 1000) not in ctx               # the middle was dropped


# --- orchestration (ReplayBackend, no model) --------------------------------------------
def _labels(*rows):
    return ReplayBackend({r["uuid"]: r for r in rows})


def test_tag_window_replay_end_to_end():
    s = FakeSession("/x/aaaaaaaa.jsonl", two_message_convo())
    backend = _labels(
        {"uuid": "u1", "move": "new_directive", "work_type": "implementation",
         "purpose": "Implement foo in f.py"},
        {"uuid": "u2", "move": "refinement", "work_type": "implementation",
         "purpose": "Add a test for foo"},
    )
    tagged = intent.tag_window(None, [s], backend)
    assert [t.move for t in tagged] == ["new_directive", "refinement"]
    assert [t.work_type for t in tagged] == ["implementation", "implementation"]
    assert tagged[1].purpose == "Add a test for foo"


def test_tag_window_labels_rewound_branch():
    s = FakeSession("/x/aaaaaaaa.jsonl", rewound_step_a_then_b())
    ms = msgmod.extract_window_messages([s])
    backend = _labels(*[
        {"uuid": m.uuid, "move": "new_directive", "work_type": "implementation",
         "purpose": m.text} for m in ms])
    tagged = intent.tag_window(None, [s], backend)
    # every branch's message got a label, including the rewound step A
    assert any(t.text == "do step A" and t.timeline.startswith("rewind") for t in tagged)


def test_to_json_shape():
    s = FakeSession("/x/aaaaaaaa.jsonl", two_message_convo())
    backend = _labels(
        {"uuid": "u1", "move": "new_directive", "work_type": "implementation", "purpose": "p1"},
        {"uuid": "u2", "move": "refinement", "work_type": "implementation", "purpose": "p2"},
    )
    doc = intent.to_json(intent.tag_window(None, [s], backend), label="test")
    assert doc["kind"] == "message_tags" and len(doc["messages"]) == 2
    m = doc["messages"][1]
    assert m["move"] == "refinement" and m["timeline"] == "active" and "reason" not in m


def test_replay_missing_label_raises():
    s = FakeSession("/x/aaaaaaaa.jsonl", two_message_convo())
    backend = _labels({"uuid": "u1", "move": "new_directive",
                       "work_type": "implementation", "purpose": "p"})
    try:
        intent.tag_window(None, [s], backend)
        assert False, "expected KeyError for the unlabeled u2"
    except KeyError:
        pass


# --- session-job grouping (R1) ----------------------------------------------------------
def test_session_jobs_one_branch_marks_every_message():
    s = FakeSession("/x/aaaaaaaa.jsonl", two_message_convo())
    jobs = msgmod.extract_session_jobs([s])
    assert len(jobs) == 1                              # one branch → one agent job
    j = jobs[0]
    assert j.targets == ["u1", "u2"]                   # both messages labeled in this job
    assert "uuid: u1 <<<" in j.transcript and "uuid: u2 <<<" in j.transcript
    assert "AGENT: Done — wrote foo()." in j.transcript


def test_session_jobs_rewind_splits_and_dedups_prefix():
    s = FakeSession("/x/aaaaaaaa.jsonl", rewound_step_a_then_b())
    jobs = msgmod.extract_session_jobs([s])
    assert len(jobs) == 2                              # active + rewind branch
    owned = [t for j in jobs for t in j.targets]
    assert sorted(owned) == ["u0", "uA", "uB"]         # shared prefix u0 owned exactly once
    rewind = next(j for j in jobs if j.timeline.startswith("rewind"))
    assert "uA" in rewind.targets and "u0" not in rewind.targets
    assert "do step A" in rewind.transcript and "draft a plan" in rewind.transcript  # u0 = context


# --- HarnessBackend file handoff --------------------------------------------------------
def test_harness_writes_manifest_and_raises(tmp_path):
    s = FakeSession("/x/aaaaaaaa.jsonl", two_message_convo())
    job_dir = str(tmp_path / "jobs")
    try:
        intent.tag_window(None, [s], HarnessBackend(job_dir=job_dir))
        assert False, "expected PendingClassifications"
    except PendingClassifications as p:
        assert p.n_jobs == 1                          # one session branch
        manifest = json.load(open(p.manifest_path, encoding="utf-8"))
    assert manifest["task"] == "classify_messages" and len(manifest["jobs"]) == 1
    job = manifest["jobs"][0]
    assert job["targets"] == ["u1", "u2"] and job["n_targets"] == 2
    assert "AXIS A — conversational move" in job["prompt"]
    assert manifest["schema"]["required"] == ["labels"]
    assert manifest["schema"]["properties"]["labels"]["items"]["required"] == \
        ["uuid", "move", "work_type", "purpose"]


def test_harness_reads_back_labels(tmp_path):
    s = FakeSession("/x/aaaaaaaa.jsonl", two_message_convo())
    job_dir = tmp_path / "jobs"
    job_dir.mkdir()
    (job_dir / "tag.labels.json").write_text(json.dumps({"labels": [
        {"uuid": "u1", "move": "new_directive", "work_type": "implementation", "purpose": "p1"},
        {"uuid": "u2", "move": "refinement", "work_type": "implementation", "purpose": "p2"},
    ]}), encoding="utf-8")
    tagged = intent.tag_window(None, [s], HarnessBackend(job_dir=str(job_dir)))
    assert [t.move for t in tagged] == ["new_directive", "refinement"]


def test_harness_reads_back_rejects_coverage_gap(tmp_path):
    s = FakeSession("/x/aaaaaaaa.jsonl", two_message_convo())
    job_dir = tmp_path / "jobs"
    job_dir.mkdir()
    (job_dir / "tag.labels.json").write_text(json.dumps({"labels": [   # u2 missing
        {"uuid": "u1", "move": "new_directive", "work_type": "implementation", "purpose": "p1"},
    ]}), encoding="utf-8")
    try:
        intent.tag_window(None, [s], HarnessBackend(job_dir=str(job_dir)))
        assert False, "expected a loud coverage error"
    except ValueError as e:
        assert "missing" in str(e)


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
