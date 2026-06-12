"""Retry loops: the same action failing repeatedly (thrash).

ONE rule, applied at any scope (scope only sets the memory window): a signature that ERRORED
>=2 times *the same way* (the model kept failing identically). A single failure then a fix
is healthy and NOT flagged. A *changed* signature = adaptation, excluded automatically
because grouping is by signature. A repeated signature whose later attempt fails with a
DIFFERENT error is also adaptation (something changed between attempts — e.g. a missing
library was installed and the command got further), so error texts must match/overlap for
the loop to count. Failure = the `is_error` flag (open-questions V6); error text comes off
the paired tool_result block.

`session` scope groups failures within a session; `window` scope groups across the window
(the same wall hit in session after session). Stdlib only.
"""

from __future__ import annotations

import re

from collections import defaultdict

from .base import Instance, MetricResult, est_tokens

_CARVE = ("One failure then a successful retry is healthy and NOT flagged; only the same "
          "action (same signature) failing >=2x WITH matching error output counts. A "
          "changed approach is a different signature and is excluded; a later attempt "
          "failing with a different error (the environment/prerequisites changed between "
          "attempts) is progress, not a loop, and is excluded too.")

_SIM_THRESHOLD = 0.5   # Jaccard word-set similarity for "same error"


def _err_words(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9_./:-]+", text.lower()))


def _same_error(a: str | None, b: str | None) -> bool:
    """True when two error outputs look like the SAME failure (word-set Jaccard).

    Missing text on either side -> treated as same: we cannot disprove the loop, so the
    pre-error-text rule (flag on repeat failure alone) is preserved for old transcripts."""
    if not a or not b:
        return True
    wa, wb = _err_words(a), _err_words(b)
    if not wa or not wb:
        return True
    return len(wa & wb) / len(wa | wb) >= _SIM_THRESHOLD


def _loop_members(errors: list) -> list:
    """The errors that are part of a same-error loop: each later attempt whose error
    matches/overlaps an EARLIER attempt's error, plus the earlier one it matches."""
    marked: set[int] = set()
    for j in range(1, len(errors)):
        for i in range(j):
            if _same_error(errors[i].error_text, errors[j].error_text):
                marked.update((i, j))
                break
    return [errors[k] for k in sorted(marked)]


def _label(sig: tuple) -> str:
    if not sig:
        return "?"
    if sig[0] == "Bash":
        return f"Bash `{(sig[1] or '')[:48]}`"
    if len(sig) > 1 and sig[1]:
        return f"{sig[0]} on {sig[1]}"
    return str(sig[0])


def _attempt_cost(tc) -> int:
    p = tc.params or {}
    return est_tokens(len(str(p.get("command") or p.get("old_string") or p.get("content") or "")))


def _core(stream, unit: str = "window") -> MetricResult:
    """Run the retry-loop rule over one stream of (sid, ToolCall) with fresh memory."""
    res = MetricResult(name="retries", token_denom_label="total tool-attempt tokens",
                       carve_out=_CARVE)
    total_calls = 0
    total_attempt_tokens = 0
    groups: dict[tuple, list] = defaultdict(list)   # signature -> [(sid, tc)]
    for sid, tc in stream:
        total_calls += 1
        total_attempt_tokens += _attempt_cost(tc)
        if tc.signature is not None:
            groups[tc.signature].append((sid, tc))
    for sig, members in groups.items():
        errors = [m for _, m in members if m.status == "error"]
        if len(errors) < 2:
            continue
        looped = _loop_members(errors)   # same-error attempts only (carve-out above)
        if len(looped) >= 2:
            sids = {s for s, _ in members}
            where = next(iter(sids)) if len(sids) == 1 else unit
            res.instances.append(Instance(
                timeline=where,
                detail=f"{_label(sig)} failed {len(looped)}x (of {len(members)} attempts)"
                       + (f", {len(errors) - len(looped)} differing failure(s) excluded"
                          if len(looped) < len(errors) else "")
                       + (f", across {len(sids)} sessions" if len(sids) > 1 else ""),
                token_weight=sum(_attempt_cost(m) for m in looped),
                refs={"signature": str(sig), "calls": [m.id for _, m in members]},
            ))
    res.denominator = total_calls
    res.total_tokens = total_attempt_tokens
    return res
