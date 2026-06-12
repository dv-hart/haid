"""Baseline placement — positions a window's token-rate against the shipped distribution.

Uses the package-data baseline (built by scripts/build_metric_baselines.py). Asserts the
placement mechanics, not specific numbers (the bootstrap distribution will change).

Run: PYTHONPATH=src python -m pytest tests/metrics/test_baseline.py -q
"""

from __future__ import annotations

import os
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(_ROOT, "src"))

from haid.metrics import baseline


def test_position_has_shape():
    pos = baseline.position("retouched", "window", 0.0)
    assert pos is not None
    assert pos["n"] > 0
    assert 0 <= pos["percentile"] <= 100
    assert "median" in pos and "source" in pos and pos["scope"] == "window"


def test_both_scopes_present():
    # Each metric has its own per-scope baseline.
    assert baseline.position("retouched", "session", 0.0) is not None
    assert baseline.position("retouched", "window", 0.0) is not None


def test_high_rate_is_high_percentile():
    # A rate above any baseline sample should land at/near the top.
    assert baseline.position("retouched", "window", 1.0)["percentile"] >= 90
    # Zero should not be top.
    assert baseline.position("retouched", "window", 0.0)["percentile"] <= 60


def test_verdict_is_readable():
    v = baseline.verdict("unused_context", "window", 0.99)
    assert "p" in v and "%" in v and "median" in v


def test_unknown_metric_returns_none():
    assert baseline.position("nonexistent_metric", "window", 0.5) is None
    assert baseline.position("retouched", "nonexistent_scope", 0.5) is None
    assert baseline.verdict("nonexistent_metric", "window", 0.5) == "no baseline available"


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
