"""Shared types for the four Tier-2 waste metrics.

Every metric returns the SAME shape: concrete instances (each traceable to ids), a rate
against an honest denominator, a token weight, the explicit "legitimate" carve-out it
respects, and any notes/limits (no-silent-caps). Metrics are computed WITHIN a timeline
(one root->leaf path) and aggregated across timelines, so repeats across abandoned rewind
branches never become phantom findings.

Deterministic, model-free. Stdlib only.
"""

from __future__ import annotations

from dataclasses import dataclass, field


def est_tokens(n_bytes: int) -> int:
    """Token count of a SPECIFIC artifact (a read's content, a rewritten line span) from its
    byte length (~4 bytes/token). This is deliberately NOT scoring/cost.py's normalized tokens:
    those come from the per-MESSAGE `usage` object (a whole-turn billing scalar, type/tier
    weighted) and cannot be attributed to one file read — wrong granularity. Here we need a
    per-artifact count, and every metric's output is a same-kind RATIO (wasted/total), so the
    byte/4 bias cancels. Model-free, never billed. (nTok enters later, at the waste→value
    reconciliation; see metrics-output-schema.md caps.)"""
    return round((n_bytes or 0) / 4)


# Deterministic scopes (computable with no model). `episode` is the planned third scope — an
# enrichment realized only after the model-in-the-loop episode segmentation (runtime step 4); it
# slots in as `iter_episodes` + episode-scope baseline. See docs/metrics-output-schema.md and
# plans/agent-analysis.md. Do not add it here until segmentation exists.
SCOPES = ("session", "window")


def iter_sessions(stream):
    """Yield (sid, substream) groups from an active stream of (sid, ToolCall), preserving
    order. This is how `session` scope is realized: the same metric core runs once per group
    (memory resets per session), vs once over the whole stream for `window` scope. Scope is
    only the memory window — there is no per-scope rule (see metrics-output-schema.md)."""
    groups: dict = {}
    for sid, tc in stream:
        groups.setdefault(sid, []).append((sid, tc))
    return groups.items()


@dataclass
class WindowView:
    """The analysis window the metrics run over — a multi-session unit, not one transcript.

    The metrics run ONE rule each over `active_stream`; `scope` is only the memory window
    (see metrics-output-schema.md), realized by how that stream is sliced:
      - active_stream: (session_label, ToolCall) across all sessions' ACTIVE timelines, in
        chronological order. `window` scope folds the whole stream (memory persists, surfacing
        cross-session signals); `session` scope folds it per session (memory resets). Abandoned
        rewind branches are excluded by construction, so they never make phantom findings.
      - timelines: (label, [ToolCall]) for every timeline — retained for inspection/tooling;
        the metrics themselves read `active_stream`.
    """
    active_stream: list = field(default_factory=list)     # [(session_label, ToolCall)]
    timelines: list = field(default_factory=list)         # [(label, [ToolCall])]
    n_sessions: int = 0
    label: str = ""
    notes: list = field(default_factory=list)


@dataclass
class Instance:
    timeline: str                 # which timeline this finding lives in ("active"/"rewind:..")
    detail: str                   # human-readable, objective ("auth.ts read 3x, no edit between")
    token_weight: int = 0
    refs: dict = field(default_factory=dict)   # ids for traceability (file, calls, etc.)


@dataclass
class MetricResult:
    name: str
    instances: list[Instance] = field(default_factory=list)
    denominator: int = 0          # count denominator (total reads / edits / calls)
    total_tokens: int = 0         # TOKEN denominator (total read / authored / tool tokens)
    token_denom_label: str = ""   # what total_tokens measures (for the report)
    carve_out: str = ""           # the legitimate case this metric deliberately excludes
    notes: list[str] = field(default_factory=list)   # hedges, limits, skips

    @property
    def count(self) -> int:
        return len(self.instances)

    @property
    def token_weight(self) -> int:
        return sum(i.token_weight for i in self.instances)

    @property
    def rate(self) -> float:
        """Count rate: flagged instances / total opportunities."""
        return (self.count / self.denominator) if self.denominator else 0.0

    @property
    def token_rate(self) -> float:
        """The benchmarkable quantity: wasted tokens / total tokens of that kind
        (e.g. rewrite tokens / authored tokens). Baseline-positioned, not a verdict."""
        return (self.token_weight / self.total_tokens) if self.total_tokens else 0.0

    def summary(self) -> dict:
        return {
            "name": self.name,
            "count": self.count,
            "denominator": self.denominator,
            "rate": round(self.rate, 3),
            "token_weight": self.token_weight,
            "total_tokens": self.total_tokens,
            "token_rate": round(self.token_rate, 4),
        }
