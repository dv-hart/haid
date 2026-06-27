"""HAID self-audit sessions must be excluded from a project's analysis window — a project is
never scored on the cost of measuring itself, and the live analysis session can't perturb its
own window. Detection is deterministic from the transcript (no model)."""

from __future__ import annotations

from haid.session import records as rec
from haid.window import _is_self_audit, partition_self_audit


class FakeSession:
    def __init__(self, path, records):
        self.path = path
        self.parse = type("P", (), {"records": records})()


def _user(text):
    return rec.from_dict({"type": "user", "uuid": "u", "parentUuid": None, "timestamp": "1",
                          "message": {"role": "user", "content": text}})


def _bash(cmd):
    return rec.from_dict({"type": "assistant", "uuid": "a", "parentUuid": "u", "timestamp": "2",
                          "message": {"role": "assistant", "content": [
                              {"type": "tool_use", "id": "t1", "name": "Bash",
                               "input": {"command": cmd}}]}})


def _skill_attr():
    raw = {"type": "assistant", "uuid": "a2", "parentUuid": "u", "timestamp": "3",
           "attributionSkill": "haid:haid-report",
           "message": {"role": "assistant", "content": [{"type": "text", "text": "running"}]}}
    return rec.from_dict(raw)


def test_haid_cli_call_marks_self_audit():
    s = FakeSession("/x/aaaa.jsonl", [_user("how am I doing?"),
                                      _bash("haid metrics --project . --json")])
    assert _is_self_audit(s)


def test_skill_attribution_marks_self_audit():
    s = FakeSession("/x/bbbb.jsonl", [_user("/haid-report"), _skill_attr()])
    assert _is_self_audit(s)


def test_normal_project_session_is_kept():
    # mentions "haid" in prose and runs an unrelated command — NOT a self-audit
    s = FakeSession("/x/cccc.jsonl", [_user("the haid tool is cool; now fix the pager"),
                                      _bash("pytest -q && python build.py")])
    assert not _is_self_audit(s)


def test_partition_splits_and_keeps_order():
    work1 = FakeSession("/x/1111.jsonl", [_user("add feature"), _bash("git status")])
    audit = FakeSession("/x/2222.jsonl", [_user("/haid-report"), _bash("haid report --metrics m.json")])
    work2 = FakeSession("/x/3333.jsonl", [_user("fix bug")])
    kept, excluded = partition_self_audit([work1, audit, work2])
    assert [s.path for s in kept] == ["/x/1111.jsonl", "/x/3333.jsonl"]
    assert [s.path for s in excluded] == ["/x/2222.jsonl"]
