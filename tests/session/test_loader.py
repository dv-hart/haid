"""Step-1 loose ends: subagent stitching, overflow resolution, SQLite cache, loader.

Hand-built on-disk fixtures (tmp_path) that mirror the real sidecar layout
(<uuid>/subagents/agent-*.jsonl + .meta.json, toolUseResult.persistedOutputPath).
Deterministic, model-free.

Run: PYTHONPATH=src python -m pytest tests/session/ -q
"""

from __future__ import annotations

import json
import os
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(_ROOT, "src"))

from haid.session import cache, overflow
from haid.session import records as rec
from haid.session.loader import load_session
from haid.session.subagents import discover_subagents


def _write_jsonl(path, objs):
    path.write_text("\n".join(json.dumps(o) for o in objs) + "\n", encoding="utf-8")


def _main_with_agent(tool_use_id="T1"):
    return [
        {"type": "user", "uuid": "u1", "parentUuid": None,
         "message": {"role": "user", "content": "spawn an agent"}},
        {"type": "assistant", "uuid": "a1", "parentUuid": "u1", "timestamp": "2026-01-01T00:00:01Z",
         "message": {"role": "assistant",
                     "content": [{"type": "tool_use", "id": tool_use_id, "name": "Agent", "input": {}}]}},
        {"type": "last-prompt", "leafUuid": "a1"},
    ]


def _make_session(tmp_path, tool_use_id="T1", meta_tool_use_id="T1", with_overflow_path=None):
    sess = tmp_path / "sess.jsonl"
    _write_jsonl(sess, _main_with_agent(tool_use_id))
    sub_dir = tmp_path / "sess" / "subagents"
    sub_dir.mkdir(parents=True)
    agent_recs = [
        {"type": "user", "uuid": "s1", "parentUuid": None, "agentId": "x1", "isSidechain": True,
         "message": {"role": "user", "content": "subagent task"}},
        {"type": "assistant", "uuid": "s2", "parentUuid": "s1", "agentId": "x1", "isSidechain": True,
         "timestamp": "2026-01-01T00:00:02Z",
         "message": {"role": "assistant", "content": "subagent done"}},
    ]
    if with_overflow_path is not None:
        agent_recs[1]["toolUseResult"] = {"persistedOutputPath": str(with_overflow_path)}
    _write_jsonl(sub_dir / "agent-x1.jsonl", agent_recs)
    (sub_dir / "agent-x1.meta.json").write_text(
        json.dumps({"agentType": "Explore", "description": "look", "toolUseId": meta_tool_use_id}),
        encoding="utf-8")
    return sess


# --- subagents --------------------------------------------------------------------------

def test_subagent_discovery_and_link(tmp_path):
    sess = _make_session(tmp_path)
    subs = discover_subagents(str(sess))
    assert len(subs) == 1
    sa = subs[0]
    assert sa.agent_id == "x1"
    assert sa.agent_type == "Explore"
    assert sa.parent_tool_use_id == "T1"
    assert len(sa.parse.records) == 2


def test_no_subagents_when_dir_absent(tmp_path):
    sess = tmp_path / "lonely.jsonl"
    _write_jsonl(sess, _main_with_agent())
    assert discover_subagents(str(sess)) == []


# --- overflow ---------------------------------------------------------------------------

def test_overflow_resolves_existing(tmp_path):
    big = tmp_path / "tool-results" / "x.txt"
    big.parent.mkdir()
    big.write_text("FULL OUTPUT", encoding="utf-8")
    r = rec.from_dict({"type": "user", "uuid": "r1", "sourceToolUseID": "T9",
                       "toolUseResult": {"persistedOutputPath": str(big)}})
    o = overflow.overflow_of(r)
    assert o and o.available and o.tool_use_id == "T9"
    assert o.load() == "FULL OUTPUT"
    assert overflow.was_truncated(r)


def test_overflow_missing_file_flagged(tmp_path):
    r = rec.from_dict({"type": "user", "uuid": "r1",
                       "toolUseResult": {"persistedOutputPath": str(tmp_path / "gone.txt")}})
    o = overflow.overflow_of(r)
    assert o and not o.available and o.load() is None


def test_truncated_by_token_cap():
    r = rec.from_dict({"type": "user", "uuid": "r1",
                       "toolUseResult": {"file": {"truncatedByTokenCap": True}}})
    assert overflow.was_truncated(r)
    r2 = rec.from_dict({"type": "user", "uuid": "r2", "toolUseResult": {"file": {}}})
    assert not overflow.was_truncated(r2)


# --- cache ------------------------------------------------------------------------------

def test_cache_miss_then_hit(tmp_path):
    sess = tmp_path / "s.jsonl"
    _write_jsonl(sess, _main_with_agent())
    db = tmp_path / "cache.db"
    res1, hit1 = cache.load_or_parse(str(sess), db_path=str(db))
    res2, hit2 = cache.load_or_parse(str(sess), db_path=str(db))
    assert hit1 is False and hit2 is True
    assert len(res1.records) == len(res2.records)


def test_partial_tail_not_cached(tmp_path):
    sess = tmp_path / "active.jsonl"
    sess.write_bytes(b'{"type":"user","uuid":"u1","parentUuid":null}\n{"type":"assist')
    db = tmp_path / "cache.db"
    _, hit1 = cache.load_or_parse(str(sess), db_path=str(db))
    _, hit2 = cache.load_or_parse(str(sess), db_path=str(db))
    assert hit1 is False and hit2 is False   # never cached while being written


# --- loader composition -----------------------------------------------------------------

def test_load_session_composes(tmp_path):
    sess = _make_session(tmp_path)
    s = load_session(str(sess), use_cache=False)
    assert s.forest.active_leaf == "a1"
    assert len(s.subagents) == 1
    assert "x1" in s.subagent_forests
    assert s.warnings() == []   # subagent links cleanly, no missing overflow


def test_loader_flags_unlinked_subagent(tmp_path):
    # meta.toolUseId points at a call that isn't in the main transcript
    sess = _make_session(tmp_path, tool_use_id="T1", meta_tool_use_id="MISSING")
    s = load_session(str(sess), use_cache=False)
    assert any("no parent Agent call" in w for w in s.warnings())


def test_loader_flags_null_tooluseid_subagent(tmp_path):
    # meta.toolUseId null (common version-drift case) — parsed but unattributable.
    sess = _make_session(tmp_path, tool_use_id="T1", meta_tool_use_id=None)
    s = load_session(str(sess), use_cache=False)
    assert any("no recorded toolUseId" in w for w in s.warnings())


def test_loader_flags_missing_overflow(tmp_path):
    sess = _make_session(tmp_path, with_overflow_path=tmp_path / "nope.txt")
    s = load_session(str(sess), use_cache=False)
    assert any("missing on disk" in w for w in s.warnings())


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
