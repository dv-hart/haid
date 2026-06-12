"""Cost measure: normalized-token weighting by token type and model tier.

The cost scalar is a RELATIVE effort figure in Haiku-input-token-equivalents (nTok), never
a currency. These tests pin the weight ratios that define that unit, the per-tier / per-type
breakdown, the cache-split handling, and the separation of process costs from the token total.

Run: PYTHONPATH=src python tests/scoring/test_cost.py   (or pytest tests/scoring/)
"""

from __future__ import annotations

import json
import os
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(_ROOT, "src"))

from haid.scoring import cost
from haid.scoring.cost import Usage


def test_type_weights_output_is_5x_input():
    """Within one tier, an output token weighs 5× an input token; cache-read 0.1×."""
    r = cost.measure([Usage(model="claude-haiku-4-5", input=100, output=100,
                            cache_read=100)])
    # haiku tier weight = 1, so normalized = 100*1 + 100*5 + 100*0.1 = 610
    assert r.normalized_tokens == 610.0
    assert r.raw_total == 300
    assert r.by_type["output"]["normalized"] == 500.0
    assert r.by_type["cache_read"]["normalized"] == 10.0


def test_tier_weighting_opus_over_haiku():
    """Identical token mix costs 15× more on Opus than Haiku (default tier ratio)."""
    haiku = cost.measure([Usage(model="claude-haiku-4-5", input=1000)])
    opus = cost.measure([Usage(model="claude-opus-4-8", input=1000)])
    assert haiku.normalized_tokens == 1000.0
    assert opus.normalized_tokens == 15000.0
    assert opus.by_tier["opus"]["raw"] == 1000


def test_cross_tier_consistency_with_price_structure():
    """tier×type must reproduce the full list-price ratio: an Opus OUTPUT token = 75 nTok
    (15 tier × 5 output), matching $75/$1-Haiku-input."""
    r = cost.measure([Usage(model="claude-opus-4-8", output=1000)])
    assert r.normalized_tokens == 75000.0


def test_cache_creation_aggregate_billed_at_5m():
    """A bare cache_creation count (no 5m/1h split) is weighted at the 5-minute rate."""
    r = cost.measure([Usage(model="claude-haiku-4-5", cache_creation=1000)])
    assert r.normalized_tokens == 1250.0                     # 1000 * 1.25
    assert r.by_type["cache_write_5m"]["raw"] == 1000


def test_cache_split_5m_and_1h_weighted_separately():
    r = cost.measure([Usage(model="claude-haiku-4-5",
                            cache_write_5m=1000, cache_write_1h=1000)])
    assert r.normalized_tokens == 1250.0 + 2000.0            # 1.25× and 2×


def test_from_dict_accepts_anthropic_names_and_nested_cache():
    u = Usage.from_dict({
        "model": "claude-sonnet-4-6",
        "input_tokens": 10, "output_tokens": 20,
        "cache_read_input_tokens": 30,
        "cache_creation": {"ephemeral_5m_input_tokens": 40,
                           "ephemeral_1h_input_tokens": 5},
    })
    assert (u.input, u.output, u.cache_read) == (10, 20, 30)
    assert u.cache_write_5m == 40 and u.cache_write_1h == 5
    counts = u.type_counts()
    assert counts["cache_write_5m"] == 40 and counts["cache_write_1h"] == 5


def test_unknown_model_uses_hedge_tier():
    r = cost.measure([Usage(model="some-future-model", input=1000)])
    assert r.normalized_tokens == cost.DEFAULT_TIER_WEIGHTS["unknown"] * 1000
    assert "unknown" in r.by_tier


def test_weights_are_overridable():
    r = cost.measure([Usage(model="claude-opus-4-8", input=1000)],
                     tier_weights={"opus": 1.0})
    assert r.normalized_tokens == 1000.0


def test_process_costs_reported_separately_not_in_token_total():
    r = cost.measure([Usage(model="claude-haiku-4-5", input=100)],
                     turns=12, tool_calls=40, compactions=2, wall_clock_s=1850)
    assert r.normalized_tokens == 100.0                      # process costs NOT folded in
    assert r.turns == 12 and r.tool_calls == 40
    assert r.compactions == 2 and r.wall_clock_s == 1850
    assert "compactions=2" in r.summary() and "wall=1850s" in r.summary()


def test_usage_file_roundtrip(tmp_path):
    payload = {
        "messages": [
            {"model": "claude-opus-4-8", "input": 1000, "output": 500},
            {"model": "claude-haiku-4-5", "input": 2000, "cache_read": 10000},
        ],
        "turns": 8, "compactions": 1,
    }
    p = tmp_path / "usage.json"
    p.write_text(json.dumps(payload), encoding="utf-8")
    r = cost.measure_usage_file(str(p))
    # opus: 1000*15 + 500*75 = 52500 ; haiku: 2000*1 + 10000*0.1 = 3000
    assert r.normalized_tokens == 52500.0 + 3000.0
    assert r.by_tier["opus"]["normalized"] == 52500.0
    assert r.by_tier["haiku"]["normalized"] == 3000.0
    assert r.turns == 8 and r.compactions == 1


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn) and fn.__code__.co_argcount == 0:
            fn()
            print(f"ok  {name}")
    print("\nALL COST TESTS PASSED")
