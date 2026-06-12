"""Position a metric's token-rate against a population baseline — PER SCOPE.

The waste metrics aren't verdicts — they're rates that only mean something *relative to
normal*. Because the same rule at a wider scope sees more (a window rate runs higher than a
session rate), each (metric, scope) needs its OWN baseline; a window rate must never be placed
against a session population. This module loads a nested distribution
(`{metric: {scope: {rates, n, source}}}`, shipped as package data) and reports where a rate
falls (percentile + median).

The shipped baseline is a BOOTSTRAP from the maintainer's own corpus (single author) — a
placeholder until the community benchmark (ADR-0005). The `source` string says so, and the
report must surface that caveat. Stdlib only.
"""

from __future__ import annotations

import bisect
import json
from pathlib import Path

_BASELINE_PATH = Path(__file__).resolve().parent.parent / "data" / "metric_baselines.json"
_cache: dict | None = None


def _load() -> dict:
    global _cache
    if _cache is None:
        try:
            _cache = json.loads(_BASELINE_PATH.read_text(encoding="utf-8"))
        except OSError:
            _cache = {}
    return _cache


def position(metric: str, scope: str, token_rate: float) -> dict | None:
    """Where does `token_rate` fall in the `scope`-scope baseline for `metric`?

    Returns {percentile, median, n, scope, source} or None if no baseline is available."""
    b = (_load().get(metric) or {}).get(scope)
    if not b or not b.get("rates"):
        return None
    rates = sorted(b["rates"])
    n = len(rates)
    pct = round(100 * bisect.bisect_right(rates, token_rate) / n)
    median = rates[n // 2]
    return {"percentile": pct, "median": round(median, 4), "n": n, "scope": scope,
            "source": b.get("source", "")}


def band(percentile: int) -> str:
    return ("far above normal" if percentile >= 90 else "above normal" if percentile >= 75
            else "around normal" if percentile >= 25 else "below normal")


def verdict(metric: str, scope: str, token_rate: float) -> str:
    """A short, hedged phrase for the report."""
    pos = position(metric, scope, token_rate)
    if pos is None:
        return "no baseline available"
    if token_rate <= 0:
        return "0% — none flagged"
    p = pos["percentile"]
    return (f"{round(token_rate*100, 1)}% — p{p} vs ~{round(pos['median']*100, 1)}% median "
            f"({band(p)}; {scope} baseline n={pos['n']})")
