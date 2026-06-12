"""Cost — the denominator of value = achievement ÷ cost, expressed in NORMALIZED TOKENS.

Deliberately NOT dollars. Different users pay different rates, and subscription users pay
nothing per token, so a currency figure is both unstable and often meaningless. Instead we
report a weighted token count: every token is converted to a common unit using DIMENSIONLESS
RELATIVE weights — the *ratios* between token kinds and between model tiers — never a rate.
Those ratios are fixed by Anthropic's pricing STRUCTURE and are identical for everyone
regardless of what (if anything) they actually pay.

Two weight families, both configurable (pass overrides to `measure`):

  1. Token-TYPE weights — uniform across every model tier in Anthropic's pricing:
       output      = 5×   input   (Opus 15→75, Sonnet 3→15, Haiku 1→5: always 5×)
       cache write = 1.25× input  (5-minute TTL) / 2× input (1-hour TTL)
       cache read  = 0.1×  input
     Using these is "adjusting for relative cost," not assuming a rate — the multiples
     hold whatever your per-token price is.

  2. Model-TIER weights — the one pricing-DERIVED assumption (user-approved, overridable).
     An Opus token costs more "intelligence budget" than a Haiku one; the only available
     ratio is the list-price input ratio, normalized to Haiku = 1 unit:
       Haiku = 1   Sonnet = 3   Opus = 15   (current 4.x list prices; verify as they drift)

The unit ("normalized token", nTok) is therefore one Haiku *input* token-equivalent.
Because tier_weight × type_weight reproduces the full cross-tier price ratio (e.g. an Opus
output token = 15 × 5 = 75 nTok, matching $75/$1), the scalar is internally consistent — but
it is a relative effort figure, NOT a bill. We ALWAYS report the raw unweighted breakdown and
the per-tier / per-type split alongside the scalar, so nothing hides behind one number.

Process costs that are NOT tokens — turns, tool-calls, compaction events, wall-clock — are
carried separately (the rubric keeps cost components separate; compaction is both a real cost
and a context-overflow smell). They are reported, never folded into the normalized-token total.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

# --- token-TYPE relative weights (Anthropic multipliers, uniform across tiers) ---------
DEFAULT_TYPE_WEIGHTS: dict[str, float] = {
    "input": 1.0,
    "output": 5.0,
    "cache_write_5m": 1.25,
    "cache_write_1h": 2.0,
    "cache_read": 0.1,
}

# --- model-TIER relative weights (list-price input ratios, normalized to Haiku = 1) ----
# The single pricing-derived assumption; fully overridable. Verify as pricing drifts.
DEFAULT_TIER_WEIGHTS: dict[str, float] = {
    "haiku": 1.0,
    "sonnet": 3.0,
    "opus": 15.0,
    "unknown": 3.0,   # hedge an unrecognized model at the mid (Sonnet) tier, surfaced in by_tier
}


def model_tier(model: str) -> str:
    """Map a raw model id (e.g. 'claude-opus-4-8[1m]') to a pricing tier."""
    m = (model or "").lower()
    if "opus" in m:
        return "opus"
    if "sonnet" in m:
        return "sonnet"
    if "haiku" in m:
        return "haiku"
    return "unknown"


@dataclass(frozen=True)
class Usage:
    """Per-message token usage. Mirrors the Anthropic usage object; the upstream
    session-graph extractor (Phase 1, unbuilt) will populate these from assistant records.

    `cache_creation` is the aggregate cache-write count; if the detailed 5m/1h split is
    available, set `cache_write_5m`/`cache_write_1h` instead and leave `cache_creation` 0.
    When only the aggregate is known it is billed at the 5-minute weight (Claude Code's
    default cache TTL) — the common, slightly conservative choice.
    """
    model: str
    input: int = 0
    output: int = 0
    cache_creation: int = 0
    cache_write_5m: int | None = None
    cache_write_1h: int | None = None
    cache_read: int = 0

    @classmethod
    def from_dict(cls, d: dict) -> "Usage":
        """Build from a raw usage record. Accepts both flat keys and Anthropic's names
        (input_tokens, output_tokens, cache_creation_input_tokens, cache_read_input_tokens,
        and the nested cache_creation.{ephemeral_5m,ephemeral_1h}_input_tokens split)."""
        cc = d.get("cache_creation")
        cw5 = cw1 = None
        cc_agg = 0
        if isinstance(cc, dict):
            cw5 = cc.get("ephemeral_5m_input_tokens")
            cw1 = cc.get("ephemeral_1h_input_tokens")
        else:
            cc_agg = d.get("cache_creation", d.get("cache_creation_input_tokens", 0)) or 0
        return cls(
            model=d.get("model", ""),
            input=d.get("input", d.get("input_tokens", 0)) or 0,
            output=d.get("output", d.get("output_tokens", 0)) or 0,
            cache_creation=cc_agg,
            cache_write_5m=cw5 if cw5 is not None else d.get("cache_write_5m"),
            cache_write_1h=cw1 if cw1 is not None else d.get("cache_write_1h"),
            cache_read=d.get("cache_read", d.get("cache_read_input_tokens", 0)) or 0,
        )

    def type_counts(self) -> dict[str, int]:
        """Raw token count per weight-type for this message."""
        w5 = self.cache_write_5m or 0
        w1 = self.cache_write_1h or 0
        # aggregate cache_creation (no split known) is treated as 5-minute writes
        w5 += self.cache_creation
        return {
            "input": self.input,
            "output": self.output,
            "cache_write_5m": w5,
            "cache_write_1h": w1,
            "cache_read": self.cache_read,
        }


@dataclass(frozen=True)
class CostResult:
    normalized_tokens: float                       # THE cost scalar (Haiku-input-equivalents)
    raw_total: int                                 # all tokens, unweighted
    by_type: dict = field(default_factory=dict)    # type -> {raw, normalized}
    by_tier: dict = field(default_factory=dict)    # tier -> {raw, normalized}
    # process costs — reported separately, NOT in normalized_tokens
    turns: int | None = None
    tool_calls: int | None = None
    compactions: int | None = None
    wall_clock_s: float | None = None

    def summary(self) -> str:
        types = "  ".join(f"{k}:{v['raw']}(->{v['normalized']:.0f})"
                          for k, v in sorted(self.by_type.items()) if v["raw"])
        tiers = "  ".join(f"{k}:{v['raw']}(->{v['normalized']:.0f})"
                          for k, v in sorted(self.by_tier.items()) if v["raw"])
        proc = []
        if self.turns is not None:
            proc.append(f"turns={self.turns}")
        if self.tool_calls is not None:
            proc.append(f"tools={self.tool_calls}")
        if self.compactions:
            proc.append(f"compactions={self.compactions}")
        if self.wall_clock_s is not None:
            proc.append(f"wall={self.wall_clock_s:.0f}s")
        out = (f"normalized_tokens={self.normalized_tokens:.0f} nTok  "
               f"(raw {self.raw_total} tok)\n"
               f"  by type:  {types}\n"
               f"  by tier:  {tiers}")
        if proc:
            out += "\n  process:  " + "  ".join(proc)
        return out


def measure(usages: list[Usage], *,
            type_weights: dict[str, float] | None = None,
            tier_weights: dict[str, float] | None = None,
            turns: int | None = None,
            tool_calls: int | None = None,
            compactions: int | None = None,
            wall_clock_s: float | None = None) -> CostResult:
    """Normalized-token cost of a list of per-message usages.

    Each message's tokens are weighted by token TYPE (output 5×, cache-read 0.1×, …) and by
    the message's model TIER (Opus > Sonnet > Haiku), then summed. Both weight tables are
    overridable. Process costs are passed through verbatim, separate from the token total.
    """
    tw = {**DEFAULT_TYPE_WEIGHTS, **(type_weights or {})}
    mw = {**DEFAULT_TIER_WEIGHTS, **(tier_weights or {})}

    by_type = {k: {"raw": 0, "normalized": 0.0} for k in tw}
    by_tier: dict[str, dict] = {}
    total_norm = 0.0
    total_raw = 0

    for u in usages:
        tier = model_tier(u.model)
        tier_w = mw.get(tier, mw["unknown"])
        tslot = by_tier.setdefault(tier, {"raw": 0, "normalized": 0.0})
        for ttype, count in u.type_counts().items():
            if not count:
                continue
            norm = count * tw[ttype] * tier_w
            by_type[ttype]["raw"] += count
            by_type[ttype]["normalized"] += norm
            tslot["raw"] += count
            tslot["normalized"] += norm
            total_raw += count
            total_norm += norm

    return CostResult(
        normalized_tokens=total_norm,
        raw_total=total_raw,
        by_type=by_type,
        by_tier=by_tier,
        turns=turns,
        tool_calls=tool_calls,
        compactions=compactions,
        wall_clock_s=wall_clock_s,
    )


def measure_usage_file(path: str) -> CostResult:
    """Load a usage JSON and measure it. Shape:

      {"messages": [{"model": "...", "input": N, "output": N,
                     "cache_creation": N, "cache_read": N}, ...],
       "turns": N, "tool_calls": N, "compactions": N, "wall_clock_s": N}

    `messages` may also be the top-level value (a bare list). Token fields accept either the
    flat names above or Anthropic's *_tokens names (see Usage.from_dict).
    """
    data = json.load(open(path, encoding="utf-8"))
    if isinstance(data, list):
        data = {"messages": data}
    usages = [Usage.from_dict(m) for m in data.get("messages", [])]
    return measure(
        usages,
        turns=data.get("turns"),
        tool_calls=data.get("tool_calls"),
        compactions=data.get("compactions"),
        wall_clock_s=data.get("wall_clock_s"),
    )
