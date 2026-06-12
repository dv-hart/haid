"""The reconstruction engine — the diff half of the bridge.

Hermetic: drives `reconstruct()` with synthetic `writes` tuples (no transcript needed), so
each replay rule + honesty flag is pinned. Run: PYTHONPATH=src python -m pytest tests/bridge/ -q
"""

from __future__ import annotations

import os
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(_ROOT, "src"))

from haid import diffio
from haid.bridge import _is_external
from haid.bridge.reconstruct import reconstruct


def w(fid, tool, tur=None, op=None, content=None, derived=False):
    return (fid, tool, tur or {}, op, content, derived)


def _block(res, path):
    return next(f for f in res.files if f.file_id == path)


# --- native writes/edits ----------------------------------------------------------------

def test_write_create_is_all_added():
    res = reconstruct([w("a.py", "Write", {"content": "x\ny\n"})])
    fr = _block(res, "a.py")
    assert fr.complete and fr.baseline == "" and fr.final == "x\ny\n"
    fds = diffio.parse_diff(res.diff)
    assert fds[0].is_new and fds[0].n_added == 2 and fds[0].n_removed == 0


def test_edit_applies_old_to_new():
    res = reconstruct([w("a.py", "Edit",
                         {"originalFile": "a\nb\nc\n", "oldString": "b", "newString": "B"})])
    assert _block(res, "a.py").final == "a\nB\nc\n"
    fd = diffio.parse_diff(res.diff)[0]
    assert fd.added == ["B"] and fd.removed == ["b"]


def test_two_edits_net_to_final_only():
    # Write a line, then rewrite it: volume should see the surviving line once, not both.
    res = reconstruct([
        w("a.py", "Edit", {"originalFile": "x=1\n", "oldString": "x=1", "newString": "x=2"}),
        w("a.py", "Edit", {"oldString": "x=2", "newString": "x=3"}),
    ])
    fr = _block(res, "a.py")
    assert fr.final == "x=3\n" and fr.complete
    fd = diffio.parse_diff(res.diff)[0]
    assert fd.added == ["x=3"] and fd.removed == ["x=1"]   # net, not x=2


def test_multiedit_applies_all_pairs():
    res = reconstruct([w("a.py", "MultiEdit",
                         {"originalFile": "a\nb\n",
                          "edits": [{"old_string": "a", "new_string": "A"},
                                    {"old_string": "b", "new_string": "B"}]})])
    assert _block(res, "a.py").final == "A\nB\n"


# --- baseline sourcing + fallbacks ------------------------------------------------------

def test_missing_originalfile_seeded_from_baselines_map():
    # originalFile None on the edit, but the window-wide baseline map supplies the seed.
    res = reconstruct([w("a.py", "Edit", {"oldString": "b", "newString": "B"})],
                      baselines={"a.py": "a\nb\nc\n"})
    fr = _block(res, "a.py")
    assert fr.mode == "buffer" and fr.complete and fr.final == "a\nB\nc\n"


def test_no_baseline_falls_back_to_hunks_and_flags():
    sp = [{"oldStart": 1, "oldLines": 1, "newStart": 1, "newLines": 1,
           "lines": ["-old", "+new"]}]
    res = reconstruct([w("big.md", "Edit",
                         {"oldString": "old", "newString": "new", "structuredPatch": sp})])
    fr = _block(res, "big.md")
    assert fr.mode == "hunks" and not fr.complete
    fd = diffio.parse_diff(res.diff)[0]
    assert fd.added == ["new"] and fd.removed == ["old"]


def test_drift_before_edit_is_flagged_and_resynced():
    # Second edit's originalFile disagrees with our running content -> an untracked write
    # happened in between. Flag it, resync to truth, keep going.
    res = reconstruct([
        w("a.py", "Edit", {"originalFile": "v1\n", "oldString": "v1", "newString": "v2"}),
        w("a.py", "Edit", {"originalFile": "vX\n", "oldString": "vX", "newString": "v3"}),
    ])
    fr = _block(res, "a.py")
    assert not fr.complete and fr.final == "v3\n"
    assert any("untracked change" in r for r in fr.reasons)


# --- shell writes (heredoc recovered vs unrecoverable) ----------------------------------

def test_heredoc_append_content_appears():
    res = reconstruct([
        w("t.py", "Write", {"content": "base\n"}),
        w("t.py", "Bash", op="append", content="more\n", derived=True),
    ])
    assert _block(res, "t.py").final == "base\nmore\n"


def test_shell_unrecoverable_content_is_flagged():
    res = reconstruct([w("a.py", "Bash", op="overwrite", content=None, derived=True)])
    fr = _block(res, "a.py")
    assert not fr.complete and any("unrecoverable" in r for r in fr.reasons)


def test_shell_overwrite_after_edit_flags_divergence():
    res = reconstruct([
        w("a.py", "Edit", {"originalFile": "x\n", "oldString": "x", "newString": "y"}),
        w("a.py", "Bash", op="edit", content=None, derived=True),
    ])
    assert not _block(res, "a.py").complete


# --- external path guard ----------------------------------------------------------------

def test_is_external_paths():
    assert _is_external("/tmp/x.json")
    assert _is_external("C:/Users/x/a.py") and _is_external("C:\\Users\\x\\a.py")
    assert not _is_external("src/haid/a.py")
    assert not _is_external("tests/t.py")


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
