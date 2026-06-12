"""Extract the cost denominator (normalized tokens) from a window's sessions.

The easy half of the bridge: walk every assistant record's `message.usage` block, map it to a
`cost.Usage`, and fold with `cost.measure`. Two deliberate choices:

  * **Cost counts ALL branches, including abandoned ones** — you paid for the tokens spent on a
    rewound/abandoned attempt even though its code didn't survive. (The DIFF, by contrast, is
    the *active* end-state only — that asymmetry is the point.) `parse.records` is the full,
    uuid-deduped record set across all branches, so summing over it is correct by construction.
  * **Subagent tokens count** — a spawned agent's tokens are real spend, so we include each
    subagent's records too.

Process costs (turns, tool-calls, compactions, wall-clock) are carried separately by
`cost.CostResult`, never folded into the token total. Stdlib only; no model.
"""

from __future__ import annotations

from datetime import datetime

from ..scoring import cost


def _all_records(session):
    yield from session.parse.records
    for sa in session.subagents:
        yield from sa.parse.records


def extract_cost(sessions) -> cost.CostResult:
    """Normalized-token cost over every session in the window (all branches + subagents)."""
    usages: list[cost.Usage] = []
    tool_calls = turns = compactions = 0
    timestamps: list[str] = []

    for s in sessions:
        for r in _all_records(s):
            msg = r.raw.get("message") or {}
            u = msg.get("usage")
            if isinstance(u, dict):
                d = dict(u)
                d["model"] = msg.get("model", "")
                usages.append(cost.Usage.from_dict(d))
            if r.type == "assistant" and isinstance(r.content, list):
                tool_calls += sum(1 for b in r.content
                                  if isinstance(b, dict) and b.get("type") == "tool_use")
            if r.is_user_prompt():
                turns += 1
            if r.raw.get("type") == "system" and r.raw.get("subtype") == "compact_boundary":
                compactions += 1
            if r.timestamp:
                timestamps.append(r.timestamp)

    return cost.measure(
        usages,
        turns=turns,
        tool_calls=tool_calls,
        compactions=compactions,
        wall_clock_s=_wall_clock(timestamps),
    )


def _wall_clock(timestamps: list[str]) -> float | None:
    if len(timestamps) < 2:
        return None
    try:
        t0 = datetime.fromisoformat(min(timestamps).replace("Z", "+00:00"))
        t1 = datetime.fromisoformat(max(timestamps).replace("Z", "+00:00"))
        return (t1 - t0).total_seconds()
    except ValueError:
        return None
