"""Span-grain reconstruction — `span_inputs` slices ONE fix span's net diff out of a branch.

The load-bearing property (the bug-fix reward's substrate, docs/plans/bugfix-reward.md): a span's
diff is the delta produced INSIDE [start_ts, end_ts) only — a change made before the span must NOT
appear, and the span-entry baseline must be reconstructed even when the in-span edit captured no
`originalFile` (a large file), where a window-entry baseline would leak pre-span edits in.

Run: PYTHONPATH=src python -m pytest tests/bridge/ -q
"""

from __future__ import annotations

import os
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(_ROOT, "src"))

from haid.bridge import span_inputs, window_inputs
from haid.session import records as rec
from haid.session.forest import Forest
from haid.window import build_view

CWD = "/proj"
DAY = "20260601"


class FakeSession:
    def __init__(self, path, records):
        self.path = path
        self.parse = type("P", (), {"records": records})()
        self.subagents = []
        self.forest = Forest(records)

    def warnings(self):
        return []


def _r(d):
    return rec.from_dict(d)


def _edit_pair(i, path, old, new, original, hh, parent):
    """One (assistant tool_use → user tool_result) edit at hour `hh`, chained under `parent`.
    `original` is the captured pre-edit file state, or None to simulate a large-file edit that
    Claude Code omitted `originalFile` for. Returns (records, last_uuid)."""
    aid, rid, cid = f"a{i}", f"r{i}", f"c{i}"
    tur = {"filePath": path, "oldString": old, "newString": new}
    if original is not None:
        tur["originalFile"] = original
    recs = [
        _r({"type": "assistant", "uuid": aid, "parentUuid": parent,
            "timestamp": f"{DAY}T{hh}:00:00Z", "cwd": CWD,
            "message": {"role": "assistant", "model": "claude-haiku-4-5",
                        "usage": {"input_tokens": 100, "output_tokens": 20},
                        "content": [{"type": "tool_use", "id": cid, "name": "Edit",
                                     "input": {"file_path": path, "old_string": old,
                                               "new_string": new}}]}}),
        _r({"type": "user", "uuid": rid, "parentUuid": aid, "timestamp": f"{DAY}T{hh}:00:01Z",
            "cwd": CWD, "message": {"role": "user",
                                    "content": [{"type": "tool_result", "tool_use_id": cid}]},
            "toolUseResult": tur}),
    ]
    return recs, aid


def session_with_edits(edits):
    """A linear session applying `edits` = [(old, new, original, hour)] to /proj/foo.py, in order."""
    path = "/proj/foo.py"
    recs = [_r({"type": "user", "uuid": "u0", "parentUuid": None,
                "timestamp": f"{DAY}T09:00:00Z", "cwd": CWD,
                "message": {"role": "user", "content": "go"}})]
    parent, last = "u0", "u0"
    for i, (old, new, original, hh) in enumerate(edits):
        pair, last = _edit_pair(i, path, old, new, original, hh, parent)
        recs.extend(pair)
        parent = last
    recs.append(_r({"type": "last-prompt", "leafUuid": last}))
    return FakeSession(f"/x/{DAY}.jsonl", recs)


def _branch(view):
    return next(label for label, tcs in view.timelines if tcs)


# ----------------------------------------------------------------------------------------
def test_span_diff_excludes_a_pre_span_change():
    """A change made before the span must not appear in the span's diff (span-relative)."""
    s = session_with_edits([
        ("HEAD", "HEADX", "HEAD\nBUG\nTAIL\n", "10"),   # pre-span, unrelated line
        ("BUG", "FIXED", "HEADX\nBUG\nTAIL\n", "11"),    # the fix
    ])
    view = build_view([s])
    span = span_inputs(view, [s], branch=_branch(view), start_ts=f"{DAY}T10:30:00Z")

    assert "-BUG" in span.diff and "+FIXED" in span.diff   # the fix is present
    # the pre-span change is NOT made by the span: HEADX rides along as unchanged CONTEXT
    # (the span baseline already has it), never as an added/removed line.
    assert "+HEADX" not in span.diff and "-HEAD" not in span.diff

    full = window_inputs(view, [s]).diff                   # contrast: the window MAKES both changes
    assert "+HEADX" in full and "+FIXED" in full


def test_span_baseline_reconstructed_when_in_span_edit_lacks_original():
    """The fix edits a line a PRE-span edit already changed, and the fix captured no originalFile.
    span_inputs must rebuild the span-entry baseline (B1) by replaying the pre-span write — a
    window-entry baseline (B) would make the fix's oldString 'B1' unfindable and drop the diff."""
    s = session_with_edits([
        ("B", "B1", "A\nB\n", "10"),     # pre-span: B -> B1   (captures originalFile)
        ("B1", "B2", None, "11"),         # the fix: B1 -> B2   (NO originalFile — large file)
    ])
    view = build_view([s])
    span = span_inputs(view, [s], branch=_branch(view), start_ts=f"{DAY}T10:30:00Z")

    assert "-B1" in span.diff and "+B2" in span.diff       # span-entry baseline (B1) was rebuilt
    assert not span.incomplete                             # no 'oldString not found' fallback


def test_empty_span_yields_empty_diff():
    s = session_with_edits([("BUG", "FIXED", "BUG\n", "10")])
    view = build_view([s])
    span = span_inputs(view, [s], branch=_branch(view), start_ts=f"{DAY}T23:00:00Z")
    assert span.diff == ""


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
