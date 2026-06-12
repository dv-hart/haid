"""Unused context: large reads that were never edited in the timeline.

The SOFTEST of the four metrics, and deliberately the most hedged: reading to understand
is legitimate, so this is NOT a waste verdict. It surfaces only LARGE reads (above a token
floor) of files never subsequently edited in the same timeline, as *possible* context
bloat worth a look. We cannot see whether a read informed a decision, so the report must
frame it as a prompt, not a finding. Computed per timeline. Stdlib only.
"""

from __future__ import annotations

from collections import defaultdict

from ..graph.model import is_read, is_write
from .base import Instance, MetricResult, est_tokens


def _is_transcript_infra(fid: str) -> bool:
    """Transcript-infrastructure paths (persisted tool-result sidecars, project memory,
    transcripts themselves) live under ~/.claude/projects/ — reading them is harness
    plumbing, not project context, so they never count as context bloat."""
    f = fid.replace("\\", "/")
    return ".claude/projects/" in f


def _core(stream, unit: str = "window", min_tokens: int = 250) -> MetricResult:
    res = MetricResult(
        name="unused_context",
        token_denom_label="total read tokens",
        carve_out="Reading to understand is legitimate; this flags only large reads of "
                  "files never edited ANYWHERE in the window — possible bloat, not waste. "
                  "Transcript-infrastructure reads (files under ~/.claude/projects/, e.g. "
                  "persisted tool-result sidecars) are not project context and are "
                  "excluded entirely.",
        notes=[
            "Window-scoped: a file read in one session and edited in a LATER one gets "
            "credit (not flagged) — only files untouched across the whole window count.",
            "Soft signal: we cannot tell whether a read informed a decision. Treat as a "
            "prompt to check, not a verdict.",
            f"Ignores reads under ~{min_tokens} tokens.",
            "Excludes reads under ~/.claude/projects/ (transcripts, tool-result overflow "
            "sidecars, project memory) from instances AND the denominator.",
            "Benchmarkable as a token rate; some unedited reading is normal (baseline "
            "median is high) — only an above-baseline rate is notable.",
        ],
    )
    # A file is "used" if edited anywhere within the memory scope (the stream passed in).
    read_tokens: dict[str, int] = defaultdict(int)
    edited: set[str] = set()
    for sid, tc in stream:
        fid = tc.target_file_id
        if is_read(tc) and fid:       # native Read OR a Bash cat/sed/head parsed as a read
            if _is_transcript_infra(fid):
                continue
            read_tokens[fid] += est_tokens(tc.result_bytes)
        elif is_write(tc) and fid:        # native Edit/Write OR a Bash sed -i / > / tee
            edited.add(fid)
    for fid, toks in read_tokens.items():
        if fid not in edited and toks >= min_tokens:
            res.instances.append(Instance(
                timeline=unit,
                detail=f"{fid} read (~{toks} tok) but never edited within {unit} scope",
                token_weight=toks,
                refs={"file": fid},
            ))
    res.denominator = len(read_tokens)
    res.total_tokens = sum(read_tokens.values())
    return res
