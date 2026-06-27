"""Anchor triage — which metric instances earn an investigation agent.

The why-pass is the expensive layer (a tool-using subagent per anchor, ~50-80k tokens
each), so the cheap metrics substrate budgets it: window-scope instances ranked by token
weight, capped per metric so one noisy metric can't eat the whole budget. `retries`
instances are exempt from the token floor — they are tiny in tokens but gate trust
(a genuine verbatim retry loop matters far beyond its size).
"""

from __future__ import annotations

from dataclasses import dataclass, field

DEFAULT_TOP = 6
DEFAULT_PER_METRIC_CAP = 3
DEFAULT_MIN_TOKENS = 200

# Metrics excluded from the (expensive) why-pass: `unused_context` is the softest signal —
# a large read of a never-edited file is usually legitimate (understanding the codebase),
# so investigating it burns a tool-using agent to almost always conclude "no remedy". Session
# meandering, surfaced cheaply from the purpose timeline (drift.multi_topic), is the more
# useful version of "context that didn't pay off". The metric is still MEASURED as substrate;
# it just no longer seeds an investigation or a treatment (decision 2026-06-26).
_EXCLUDED_METRICS = frozenset({"unused_context"})


@dataclass(frozen=True)
class WhyAnchor:
    """One metrics-JSON instance selected for investigation (a pure pointer + detail)."""
    id: str                      # instance id, e.g. "rereads/window/1"
    metric: str
    scope: str
    detail: str                  # the human-readable instance line from the metrics doc
    token_weight: int
    file_id: str | None = None
    session_ids: list = field(default_factory=list)
    refs: dict = field(default_factory=dict)


def _to_anchor(inst: dict) -> WhyAnchor:
    refs = inst.get("refs", {})
    sids = list(refs.get("session_ids", []))
    if not sids and inst.get("session_id"):
        sids = [inst["session_id"]]
    return WhyAnchor(
        id=inst["id"], metric=inst["metric"], scope=inst["scope"],
        detail=inst.get("detail", ""), token_weight=int(inst.get("token_weight", 0)),
        file_id=refs.get("file_id"), session_ids=sids, refs=refs,
    )


def select_anchors(doc: dict, *, top: int = DEFAULT_TOP,
                   per_metric_cap: int = DEFAULT_PER_METRIC_CAP,
                   min_tokens: int = DEFAULT_MIN_TOKENS) -> list[WhyAnchor]:
    """Pick the window-scope instances worth an agent, ranked by token weight.

    Per metric: top `per_metric_cap` instances over `min_tokens` (retries exempt from the
    floor). Across metrics: merged, re-ranked, cut to `top`. Deterministic.
    """
    by_metric: dict[str, list[dict]] = {}
    for inst in doc.get("instances", []):
        if inst.get("scope") != "window":
            continue
        if inst.get("metric") in _EXCLUDED_METRICS:
            continue
        tok = int(inst.get("token_weight", 0))
        if tok < min_tokens and inst.get("metric") != "retries":
            continue
        by_metric.setdefault(inst["metric"], []).append(inst)

    picked: list[dict] = []
    for metric, insts in by_metric.items():
        insts.sort(key=lambda i: int(i.get("token_weight", 0)), reverse=True)
        picked += insts[:per_metric_cap]

    # retries float to the front regardless of tokens (trust-gating), then token order.
    picked.sort(key=lambda i: (i.get("metric") != "retries",
                               -int(i.get("token_weight", 0))))
    return [_to_anchor(i) for i in picked[:top]]
