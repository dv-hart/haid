"""Re-touched lines: the agent rewriting code it just wrote (rework/churn).

Drift-immune and high-precision by being CONTENT-based, not line-number based: we track
the non-trivial lines each Edit/Write produces, per file, within a timeline. An Edit whose
`old_string` contains a line that an earlier edit/write in the same timeline produced means
the agent is rewriting its own fresh output — the canonical churn signal (and half the
acid-test). Editing pre-existing code is excluded because those lines were never "produced"
here.

This sidesteps the still-open region-identity granularity question for v1 (we match lines,
not regions). Computed per timeline. Stdlib only.
"""

from __future__ import annotations

from collections import defaultdict

from .base import Instance, MetricResult, est_tokens

_EDIT = {"Edit", "MultiEdit"}


def _nontrivial(text: str) -> set[str]:
    """Stripped lines worth tracking: length >= 8 and containing an alphanumeric char
    (skips braces/blank/short punctuation lines that match spuriously)."""
    out = set()
    for line in (text or "").splitlines():
        s = line.strip()
        if len(s) >= 8 and any(ch.isalnum() for ch in s):
            out.add(s)
    return out


def _core(stream, unit: str = "window") -> MetricResult:
    res = MetricResult(
        name="retouched",
        token_denom_label="total authored tokens (edits + writes)",
        carve_out="Editing pre-existing code is normal and excluded; only rewriting lines "
                  "the agent itself produced earlier in the WINDOW counts as rework.",
        notes=["Tracked ACROSS sessions (chronological active stream): rework compounds — "
               "writing code in one session and rewriting it in a later one is the real "
               "churn signal a per-session view misses.",
               "Benchmarkable as a TOKEN RATE (rewritten-own-output tokens / authored "
               "tokens). Normal iteration lives in the baseline; only an above-baseline "
               "rate is notable. Not a per-instance verdict."],
    )
    total_edits = 0
    total_authored_tokens = 0
    produced: dict[str, set[str]] = defaultdict(set)   # threaded chronologically over the stream
    for sid, tc in stream:
        fid = tc.target_file_id
        if not fid:
            continue
        if tc.tool in _EDIT:
            total_edits += 1
            new_str = tc.params.get("new_string", "") or ""
            total_authored_tokens += est_tokens(len(new_str))
            overlap = _nontrivial(tc.params.get("old_string", "")) & produced[fid]
            if overlap:
                overlap_tokens = est_tokens(sum(len(s) for s in overlap))
                res.instances.append(Instance(
                    timeline=sid,
                    detail=f"{fid}: edit rewrites {len(overlap)} line(s) written earlier "
                           "in this window",
                    token_weight=overlap_tokens,        # only the rewritten lines, not the whole edit
                    refs={"file": fid, "call": tc.id, "sample_lines": list(overlap)[:3]},
                ))
            produced[fid] |= _nontrivial(new_str)
        elif tc.tool == "Write":
            content = tc.params.get("content", "") or ""
            total_authored_tokens += est_tokens(len(content))
            produced[fid] |= _nontrivial(content)
    res.denominator = total_edits
    res.total_tokens = total_authored_tokens
    return res
