"""Forest model + parser unit tests — hand-built fixtures, deterministic, model-free.

Each fixture isolates one on-disk branch shape verified in the real corpus
(plans/phase1-build.md §0.5): linear, sibling-fork rewind, off-path-chain rewind,
command-noise false-positive, dangling-leafUuid fallback, structural (non-rewind) fork,
uuid dedup, and parser drift/partial-tail tolerance.

Run: PYTHONPATH=src python -m pytest tests/session/ -q
"""

from __future__ import annotations

import os
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(_ROOT, "src"))

from haid.session import records as rec
from haid.session.forest import Forest
from haid.session.parse import parse_file


def mk(typ, uuid=None, parent=None, ts=None, role=None, text=None,
       tool_result=False, tool_use=False, sidechain=False, **extra):
    """Build a raw record dict and lift it into a Record."""
    raw = {"type": typ, "uuid": uuid, "parentUuid": parent, "timestamp": ts,
           "isSidechain": sidechain, **extra}
    if role or text is not None or tool_result or tool_use:
        if tool_result:
            content = [{"type": "tool_result", "content": "ok"}]
        elif tool_use:
            content = [{"type": "tool_use", "name": "Read", "input": {}}]
        else:
            content = text
        raw["message"] = {"role": role or ("user" if typ == "user" else "assistant"),
                          "content": content}
    return rec.from_dict(raw)


def last_prompt(leaf):
    return rec.from_dict({"type": "last-prompt", "leafUuid": leaf})


# --- linear -----------------------------------------------------------------------------

def test_linear_no_rewinds():
    recs = [
        mk("user", "u1", None, "2026-01-01T00:00:00Z", text="do the thing"),
        mk("assistant", "a1", "u1", "2026-01-01T00:00:01Z", tool_use=True),
        mk("user", "r1", "a1", "2026-01-01T00:00:02Z", tool_result=True),
        mk("assistant", "a2", "r1", "2026-01-01T00:00:03Z", text="done"),
        last_prompt("a2"),
    ]
    f = Forest(recs)
    assert f.active_leaf == "a2"
    assert f.active_leaf_method == "leafUuid"
    assert len(f.roots) == 1
    assert f.rewinds == []
    assert len(f.timelines()) == 1
    assert f.timelines()[0].is_active


# --- sibling-fork rewind (edit-and-resubmit) --------------------------------------------

def test_sibling_fork_rewind():
    # Parent p0; user submits A (abandoned), rewinds, submits B (active continuation).
    recs = [
        mk("assistant", "p0", None, "2026-01-01T00:00:00Z", text="plan ready"),
        mk("user", "A", "p0", "2026-01-01T00:01:00Z", text="do step 1"),     # abandoned
        mk("assistant", "Aa", "A", "2026-01-01T00:01:01Z", text="step1 work"),
        mk("user", "B", "p0", "2026-01-01T00:02:00Z", text="do step 2"),     # active
        mk("assistant", "Bb", "B", "2026-01-01T00:02:01Z", text="step2 work"),
        last_prompt("Bb"),
    ]
    f = Forest(recs)
    assert f.active_leaf == "Bb"
    assert len(f.rewinds) == 1
    rw = f.rewinds[0]
    assert rw.prompt_uuid == "A"
    assert rw.shape == "sibling-fork"
    assert rw.divergence_uuid == "p0"   # the shared parent is on the active path
    # Two timelines: active + the abandoned step-1 branch.
    labels = {t.label for t in f.timelines()}
    assert "active" in labels and "rewind:A" in labels


# --- off-path-chain rewind --------------------------------------------------------------

def test_off_path_chain_rewind():
    # Diverges further up: abandoned prompt's parent has NO active child.
    recs = [
        mk("user", "u1", None, "2026-01-01T00:00:00Z", text="start"),
        mk("assistant", "a1", "u1", "2026-01-01T00:00:01Z", text="ok"),
        # active continuation off u1->a1
        mk("user", "B", "a1", "2026-01-01T00:03:00Z", text="active path"),
        mk("assistant", "Bb", "B", "2026-01-01T00:03:01Z", text="active work"),
        # abandoned chain hanging off a1 via an intermediate assistant turn
        mk("assistant", "x1", "a1", "2026-01-01T00:01:00Z", text="abandoned mid"),
        mk("user", "C", "x1", "2026-01-01T00:01:30Z", text="abandoned prompt"),
        last_prompt("Bb"),
    ]
    f = Forest(recs)
    assert {rw.prompt_uuid for rw in f.rewinds} == {"C"}
    assert f.rewinds[0].shape == "off-path-chain"


# --- command-noise is NOT a rewind ------------------------------------------------------

def test_command_noise_excluded():
    recs = [
        mk("user", "u1", None, "2026-01-01T00:00:00Z", text="start"),
        mk("assistant", "a1", "u1", "2026-01-01T00:00:01Z", text="ok"),
        mk("user", "B", "a1", "2026-01-01T00:05:00Z", text="active"),
        # off-path /login synthetic cluster — looks like a prompt, is not an instruction
        mk("user", "n1", "a1", "2026-01-01T00:01:00Z",
           text="<command-name>/login</command-name>\n<command-message>login</command-message>"),
        mk("user", "n2", "n1", "2026-01-01T00:01:00Z",
           text="<local-command-stdout>Login successful</local-command-stdout>"),
        last_prompt("B"),
    ]
    f = Forest(recs)
    assert f.rewinds == []   # the /login cluster must not be flagged


def test_interrupt_excluded():
    recs = [
        mk("user", "u1", None, "2026-01-01T00:00:00Z", text="start"),
        mk("assistant", "a1", "u1", "2026-01-01T00:00:01Z", text="ok"),
        mk("user", "B", "a1", "2026-01-01T00:05:00Z", text="active"),
        mk("user", "i1", "a1", "2026-01-01T00:01:00Z", text="[Request interrupted by user]"),
        last_prompt("B"),
    ]
    assert Forest(recs).rewinds == []


# --- dangling leafUuid -> timestamp fallback --------------------------------------------

def test_dangling_leafuuid_fallback():
    recs = [
        mk("user", "u1", None, "2026-01-01T00:00:00Z", text="start"),
        mk("assistant", "a1", "u1", "2026-01-01T00:00:01Z", text="early"),
        mk("assistant", "a2", "a1", "2026-01-01T00:09:00Z", text="latest"),
        last_prompt("does-not-exist"),   # dangling
    ]
    f = Forest(recs)
    assert f.active_leaf_method == "timestamp-fallback"
    assert f.active_leaf == "a2"          # latest-timestamp main-thread leaf


# --- structural fork is not a rewind ----------------------------------------------------

def test_structural_fork_not_rewind():
    # One assistant turn with two tool_result children (parallel tool calls).
    recs = [
        mk("user", "u1", None, "2026-01-01T00:00:00Z", text="go"),
        mk("assistant", "a1", "u1", "2026-01-01T00:00:01Z", tool_use=True),
        mk("user", "tr1", "a1", "2026-01-01T00:00:02Z", tool_result=True),
        mk("user", "tr2", "a1", "2026-01-01T00:00:02Z", tool_result=True),
        mk("assistant", "a2", "tr1", "2026-01-01T00:00:03Z", text="done"),
        last_prompt("a2"),
    ]
    f = Forest(recs)
    assert len(f.structural_forks()) == 1   # a1 has two children
    assert f.rewinds == []                   # but neither child is a user prompt


# --- dedup ------------------------------------------------------------------------------

def test_dedup_by_uuid():
    recs = [
        mk("user", "u1", None, "2026-01-01T00:00:00Z", text="start"),
        mk("user", "u1", None, "2026-01-01T00:00:00Z", text="start (resumed dup)"),
        mk("assistant", "a1", "u1", "2026-01-01T00:00:01Z", text="ok"),
        last_prompt("a1"),
    ]
    f = Forest(recs)
    assert f.n_duplicate_uuids == 1
    assert len(f.by_uuid) == 2


# --- parser tolerance -------------------------------------------------------------------

def test_parser_unknown_shape_and_partial_tail(tmp_path):
    p = tmp_path / "s.jsonl"
    # last line intentionally truncated (no newline, broken json) = active-write case
    p.write_bytes(
        b'{"type":"user","uuid":"u1","parentUuid":null,"message":{"role":"user","content":"hi"}}\n'
        b'{"type":"weird-new-type","uuid":"w1","parentUuid":"u1"}\n'
        b'{"type":"assistant","uuid":"a1","parentUuid":"u1","message":{"rol'
    )
    res = parse_file(str(p))
    assert res.had_partial_tail
    assert res.unknown_types["weird-new-type"] == 1
    assert any("unknown type" in w for w in res.warnings())
    # unknown record is kept, not dropped
    assert any(r.type == "weird-new-type" for r in res.records)


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
