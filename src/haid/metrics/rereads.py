"""Redundant re-reads: re-reading content already read, with no intervening edit.

ONE rule, applied at any scope (the scope only sets the memory window — see
metrics-output-schema.md): a read is redundant to the extent its line range overlaps ranges
already read since the file was last modified. Reading two *different* sections is not
flagged; re-reading the same span is. An edit/write clears the file's seen-ranges (a re-read
after a change is legitimate — the carve-out). Second carve-out: Claude Code requires a Read
before an Edit *within the conversation*, so a session's FIRST read of a file it goes on to
edit is structurally required even when the window already saw the content in an earlier
session — exempt when (a) the file has no prior read in THIS session since its last write
and (b) the next operation on the file in this session is an edit/write.

`_core` runs the rule over one stream of (sid, ToolCall) with a single fresh memory. Run it
over the whole window (`window` scope → catches cross-session re-reads, the "re-establishment
tax") or per session (`session` scope → memory resets each session). Stdlib only.
"""

from __future__ import annotations

from collections import defaultdict

from ..graph.model import is_read, is_write
from .base import Instance, MetricResult, est_tokens

_CARVE = ("A re-read after an edit/write to that file is legitimate (the file changed) and "
          "is excluded by construction. A session's first read of a file it then edits is "
          "structurally required (Claude Code's Read-before-Edit rule is per conversation) "
          "and is excluded even when the content was already read in an earlier session.")
_NOTES = [
    "One rule at any scope: only the re-read of an already-seen line range counts; scope "
    "only sets how far back 'already' reaches (session resets per session; window persists, "
    "surfacing cross-session rediscovery — the re-establishment tax).",
    "Read-before-Edit exemption: a read with no prior same-session read of the file (since "
    "its last write) whose next same-session operation on that file is an edit/write is "
    "not flagged — the harness requires it.",
    "Re-reading to locate an edit site can still be legitimate — rank by token rate and read "
    "with the task in mind, not flag every instance.",
]


def _overlap(span, seen: list[tuple[int, int]]) -> int:
    """Lines of [start,end) already covered by the `seen` intervals.

    `seen` is kept disjoint by `_merge`, so summing per-interval overlap counts
    each line of `span` at most once.
    """
    s, e = span
    covered = 0
    for a, b in seen:
        lo, hi = max(s, a), min(e, b)
        if hi > lo:
            covered += hi - lo
    return covered


def _merge(seen: list[tuple[int, int]], span) -> None:
    """Insert `span` into `seen`, keeping it a sorted set of disjoint intervals."""
    intervals = sorted(seen + [tuple(span)])
    merged: list[tuple[int, int]] = []
    for a, b in intervals:
        if merged and a <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], b))
        else:
            merged.append((a, b))
    seen[:] = merged


def _precedes_write(items: list) -> dict[int, bool]:
    """stream index of a file op -> whether the NEXT op on the same file in the SAME
    session is an edit/write (the Read-before-Edit structural pattern)."""
    seq: dict[tuple, list[int]] = defaultdict(list)
    for i, (sid, tc) in enumerate(items):
        fid = tc.target_file_id
        if fid and (is_read(tc) or is_write(tc)):
            seq[(sid, fid)].append(i)
    out: dict[int, bool] = {}
    for idxs in seq.values():
        for a, b in zip(idxs, idxs[1:]):
            out[a] = is_write(items[b][1])
    return out


def _core(stream, unit: str = "window") -> MetricResult:
    """Run the re-read rule over one stream of (sid, ToolCall) with fresh memory."""
    res = MetricResult(name="rereads", token_denom_label="total read tokens",
                       carve_out=_CARVE, notes=list(_NOTES))
    items = list(stream)
    precedes_write = _precedes_write(items)
    total_read_tokens = 0
    total_reads = 0
    seen: dict[str, list[tuple[int, int]]] = defaultdict(list)  # file -> read ranges
    read_sids: dict[str, set] = defaultdict(set)  # file -> sids that read it since last write
    for i, (sid, tc) in enumerate(items):
        fid = tc.target_file_id
        if is_read(tc) and fid:       # native Read OR a Bash cat/sed/head parsed as a read
            total_reads += 1
            toks = est_tokens(tc.result_bytes)
            total_read_tokens += toks
            span = tc.read_span
            # Read-before-Edit exemption: first read of the file in THIS session (since its
            # last write) AND the session's next op on the file is an edit/write — the
            # harness requires that read, however well the window already knows the content.
            required = sid not in read_sids[fid] and precedes_write.get(i, False)
            # Wording is evidence-based, not scope-based: coverage purely from earlier
            # sessions reads "earlier in window"; same-session coverage is "in context".
            how = ("already in context" if sid in read_sids[fid]
                   else "already read earlier in window")
            if span and span[1] > span[0]:
                cov = _overlap(span, seen[fid])
                frac = cov / (span[1] - span[0])
                frac = max(0.0, min(1.0, frac))   # a read is at most 100% redundant
                if frac >= 0.5 and not required:  # majority already seen = redundant
                    res.instances.append(Instance(
                        timeline=sid,
                        detail=f"{fid} lines {span[0]}-{span[1]} re-read "
                               f"({round(frac*100)}% {how}, no edit since)",
                        token_weight=round(toks * frac),
                        refs={"file": fid, "call": tc.id, "span": list(span)},
                    ))
                _merge(seen[fid], span)
            elif fid in seen and not required:   # no range info -> file-level fallback
                res.instances.append(Instance(
                    timeline=sid,
                    detail=f"{fid} re-read with no edit since the prior read (no range info)",
                    token_weight=toks,
                    refs={"file": fid, "call": tc.id},
                ))
            else:
                seen.setdefault(fid, [])   # register file so a later no-range read is caught
            read_sids[fid].add(sid)
        elif is_write(tc) and fid:        # native Edit/Write OR a Bash sed -i / > / tee
            seen[fid] = []                # the file changed -> next read of it is legitimate
            read_sids[fid].clear()        # …and the Read-before-Edit clock restarts
    res.denominator = total_reads
    res.total_tokens = total_read_tokens
    return res
