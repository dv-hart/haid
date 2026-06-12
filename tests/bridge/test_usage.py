"""Cost extraction (the usage half of the bridge) + an end-to-end smoke test.

The unit tests use duck-typed record/session stubs so they stay hermetic; the smoke test runs
the real bridge over this machine's own HAID transcripts when present (skipped otherwise).
Run: PYTHONPATH=src python -m pytest tests/bridge/ -q
"""

from __future__ import annotations

import glob
import os
import sys
from types import SimpleNamespace

import pytest

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(_ROOT, "src"))

from haid.bridge import window_inputs
from haid.bridge.usage import extract_cost


def _rec(raw, type="assistant", content=None, ts="2026-06-01T00:00:00Z", user_prompt=False):
    return SimpleNamespace(raw=raw, type=type, content=content, timestamp=ts,
                           is_user_prompt=lambda: user_prompt)


def _assistant(model, **toks):
    return _rec({"message": {"model": model, "usage": toks}},
                content=[{"type": "tool_use", "id": "t1"}])


def _session(records, subagents=()):
    subs = [SimpleNamespace(parse=SimpleNamespace(records=list(r))) for r in subagents]
    return SimpleNamespace(parse=SimpleNamespace(records=list(records)), subagents=subs)


def test_extract_cost_weights_and_breaks_down():
    s = _session([_assistant("claude-opus-4-8", input_tokens=100, output_tokens=10)])
    res = extract_cost([s])
    # opus tier=15; input 100*1*15=1500, output 10*5*15=750 -> 2250
    assert res.normalized_tokens == pytest.approx(2250.0)
    assert res.raw_total == 110
    assert "opus" in res.by_tier and res.tool_calls == 1


def test_cost_counts_all_branches_and_subagents():
    # Two assistant turns (e.g. an active + an abandoned branch) AND a subagent — all spend.
    main = [_assistant("claude-haiku-4-5", input_tokens=10),
            _assistant("claude-haiku-4-5", input_tokens=10)]
    sub = [_assistant("claude-haiku-4-5", input_tokens=5)]
    res = extract_cost([_session(main, subagents=[sub])])
    assert res.raw_total == 25      # 10 + 10 (both branches) + 5 (subagent)


def test_process_costs_carried_separately():
    s = _session([
        _rec({}, type="user", content="do x", user_prompt=True),
        _assistant("claude-sonnet-4-6", input_tokens=10),
        _rec({"type": "system", "subtype": "compact_boundary"}, type="system"),
    ])
    res = extract_cost([s])
    assert res.turns == 1 and res.compactions == 1
    assert res.normalized_tokens > 0   # compaction/turn never folded into the token total


# --- end-to-end smoke on real local data (skipped if absent) ----------------------------

_HAID = os.path.join(os.path.expanduser("~"), ".claude", "projects",
                     "C--Users-jhart-Documents-software-HAID")


@pytest.mark.skipif(not glob.glob(os.path.join(_HAID, "*.jsonl")),
                    reason="no local HAID transcripts on this machine")
def test_bridge_end_to_end_on_real_session():
    from haid import diffio
    from haid.scoring import volume
    from haid.window import from_files

    fp = sorted(glob.glob(os.path.join(_HAID, "*.jsonl")))[0]
    view, sessions = from_files([fp])
    br = window_inputs(view, sessions)

    assert br.cost.normalized_tokens > 0
    fds = diffio.parse_diff(br.diff)
    assert len(fds) >= 1
    assert volume.measure(br.diff).weighted_loc >= 0     # parses + scores without error
    assert isinstance(br.caveats, list)                  # honesty surface present


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
