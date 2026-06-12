"""The four waste metrics — hand-built fixtures, deterministic, model-free.

Each metric is tested for both a true positive AND its carve-out (the legitimate case it
must NOT flag), plus the timeline-scoping guarantee (a repeat across an abandoned rewind
branch is not a finding). Run: PYTHONPATH=src python -m pytest tests/metrics/ -q
"""

from __future__ import annotations

import os
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(_ROOT, "src"))

from haid.session import records as rec
from haid.session.forest import Forest
from haid.graph.build import build_graph, timeline_toolcalls
from haid.metrics.base import WindowView, est_tokens
from haid import metrics

CWD = "/proj"


def view_of(*session_recs):
    """Build a WindowView from one or more sessions' record lists (in order)."""
    active, timelines = [], []
    for i, recs in enumerate(session_recs):
        g = build_graph(recs)
        for tl in Forest(recs).timelines():
            tcs = timeline_toolcalls(g, tl)
            timelines.append((f"s{i}:{tl.label}", tcs))
            if tl.is_active:
                active.extend((f"s{i}", tc) for tc in tcs)
    return WindowView(active_stream=active, timelines=timelines, n_sessions=len(session_recs))


def asst(uuid, parent, ts, blocks):
    return rec.from_dict({"type": "assistant", "uuid": uuid, "parentUuid": parent, "timestamp": ts,
                          "cwd": CWD, "message": {"role": "assistant", "content": blocks}})


def tu(cid, name, inp):
    return {"type": "tool_use", "id": cid, "name": name, "input": inp}


def res(uuid, parent, cid, tur=None, is_error=False, ts=None):
    raw = {"type": "user", "uuid": uuid, "parentUuid": parent, "timestamp": ts, "cwd": CWD,
           "message": {"role": "user",
                       "content": [{"type": "tool_result", "tool_use_id": cid, "is_error": is_error}]}}
    if tur is not None:
        raw["toolUseResult"] = tur
    return rec.from_dict(raw)


def res_err(uuid, parent, cid, text, ts=None):
    """An error result whose text lives in the tool_result block (real error results
    carry NO toolUseResult dict — the text is block content)."""
    return rec.from_dict({"type": "user", "uuid": uuid, "parentUuid": parent, "timestamp": ts,
                          "cwd": CWD,
                          "message": {"role": "user",
                                      "content": [{"type": "tool_result", "tool_use_id": cid,
                                                   "is_error": True, "content": text}]}})


def user(uuid, parent, ts, text):
    return rec.from_dict({"type": "user", "uuid": uuid, "parentUuid": parent, "timestamp": ts,
                          "cwd": CWD, "message": {"role": "user", "content": text}})


def read_tur(path, nbytes=200):
    return {"file": {"filePath": path, "content": "x" * nbytes, "startLine": 1,
                     "numLines": 10, "totalLines": 10}}


def read_tur_span(path, start, numlines, nbytes=200):
    return {"file": {"filePath": path, "content": "x" * nbytes, "startLine": start,
                     "numLines": numlines, "totalLines": 1000}}


def bash_read_tur(stdout):
    """Real Bash result shape: stdout/stderr only, no filePath — the parser recovers
    the file + span from the command string."""
    return {"stdout": stdout, "stderr": "", "interrupted": False}


def run(recs, name):
    return metrics.run_all(view_of(recs))[name]


# --- redundant re-reads -----------------------------------------------------------------

def test_reread_flagged():
    recs = [
        user("u1", None, "0", "go"),
        asst("a1", "u1", "1", [tu("c1", "Read", {"file_path": "/proj/a.py"})]),
        res("r1", "a1", "c1", read_tur("/proj/a.py")),
        asst("a2", "r1", "2", [tu("c2", "Read", {"file_path": "/proj/a.py"})]),
        res("r2", "a2", "c2", read_tur("/proj/a.py")),
        rec.from_dict({"type": "last-prompt", "leafUuid": "r2"}),
    ]
    m = run(recs, "rereads")
    assert m.count == 1 and m.denominator == 2 and m.token_weight > 0


def test_reread_after_edit_not_flagged():
    recs = [
        asst("a1", None, "1", [tu("c1", "Read", {"file_path": "/proj/a.py"})]),
        res("r1", "a1", "c1", read_tur("/proj/a.py")),
        asst("a2", "r1", "2", [tu("c2", "Edit", {"file_path": "/proj/a.py",
              "old_string": "x", "new_string": "y"})]),
        res("r2", "a2", "c2", {"filePath": "/proj/a.py", "structuredPatch": []}),
        asst("a3", "r2", "3", [tu("c3", "Read", {"file_path": "/proj/a.py"})]),
        res("r3", "a3", "c3", read_tur("/proj/a.py")),
        rec.from_dict({"type": "last-prompt", "leafUuid": "r3"}),
    ]
    assert run(recs, "rereads").count == 0   # carve-out: re-read after edit


def test_reread_frac_never_exceeds_100pct():
    # The SAME span read 4 times with no edit between. Each re-read is fully redundant,
    # but a read can be at most 100% redundant: token_weight must not exceed the read's
    # own token estimate and no detail may report a percentage above 100.
    recs = [
        asst("a1", None, "1", [tu("c1", "Read", {"file_path": "/proj/a.py"})]),
        res("r1", "a1", "c1", read_tur_span("/proj/a.py", 1, 50)),
        asst("a2", "r1", "2", [tu("c2", "Read", {"file_path": "/proj/a.py"})]),
        res("r2", "a2", "c2", read_tur_span("/proj/a.py", 1, 50)),
        asst("a3", "r2", "3", [tu("c3", "Read", {"file_path": "/proj/a.py"})]),
        res("r3", "a3", "c3", read_tur_span("/proj/a.py", 1, 50)),
        asst("a4", "r3", "4", [tu("c4", "Read", {"file_path": "/proj/a.py"})]),
        res("r4", "a4", "c4", read_tur_span("/proj/a.py", 1, 50)),
        rec.from_dict({"type": "last-prompt", "leafUuid": "r4"}),
    ]
    m = metrics.run_window(view_of(recs))["rereads"]
    assert m.count >= 1                                   # the re-reads are still flagged
    per_read_toks = est_tokens(200)                   # each read's own token estimate
    for inst in m.instances:
        assert inst.token_weight <= per_read_toks         # no 2-4x inflation
        pct = int(inst.detail.split("(")[1].split("%")[0])
        assert pct <= 100                                 # never >100% redundant


def test_reread_overlapping_spans_frac_clamped():
    # Two overlapping-but-distinct prior reads (1-50, 40-90), then a re-read of 1-90.
    # The union of priors covers all of 1-90, so frac must be exactly 1.0 (<= 1.0),
    # never inflated by double-counting the 40-50 overlap.
    recs = [
        asst("a1", None, "1", [tu("c1", "Read", {"file_path": "/proj/a.py"})]),
        res("r1", "a1", "c1", read_tur_span("/proj/a.py", 1, 49)),    # lines 1-50
        asst("a2", "r1", "2", [tu("c2", "Read", {"file_path": "/proj/a.py"})]),
        res("r2", "a2", "c2", read_tur_span("/proj/a.py", 40, 50)),   # lines 40-90
        asst("a3", "r2", "3", [tu("c3", "Read", {"file_path": "/proj/a.py"})]),
        res("r3", "a3", "c3", read_tur_span("/proj/a.py", 1, 89)),    # lines 1-90
        rec.from_dict({"type": "last-prompt", "leafUuid": "r3"}),
    ]
    m = metrics.run_window(view_of(recs))["rereads"]
    assert m.count >= 1
    per_read_toks = est_tokens(200)
    for inst in m.instances:
        assert inst.token_weight <= per_read_toks
        pct = int(inst.detail.split("(")[1].split("%")[0])
        assert pct <= 100


# --- shell reads counted like native reads ----------------------------------------------

def test_reread_flagged_via_shell_cat():
    # `cat f` twice with no edit between is a redundant re-read, same as Read twice.
    recs = [
        asst("a1", None, "1", [tu("c1", "Bash", {"command": "cat /proj/a.py"})]),
        res("r1", "a1", "c1", bash_read_tur("line\n" * 10)),
        asst("a2", "r1", "2", [tu("c2", "Bash", {"command": "cat /proj/a.py"})]),
        res("r2", "a2", "c2", bash_read_tur("line\n" * 10)),
        rec.from_dict({"type": "last-prompt", "leafUuid": "r2"}),
    ]
    m = run(recs, "rereads")
    assert m.count == 1 and m.denominator == 2 and m.token_weight > 0


def test_reread_flagged_across_read_and_shell():
    # Native Read of lines 1-10, then `cat` of the same span: cross-tool re-read.
    recs = [
        asst("a1", None, "1", [tu("c1", "Read", {"file_path": "/proj/a.py"})]),
        res("r1", "a1", "c1", read_tur_span("/proj/a.py", 1, 10)),
        asst("a2", "r1", "2", [tu("c2", "Bash", {"command": "sed -n '1,10p' /proj/a.py"})]),
        res("r2", "a2", "c2", bash_read_tur("line\n" * 10)),
        rec.from_dict({"type": "last-prompt", "leafUuid": "r2"}),
    ]
    assert run(recs, "rereads").count == 1


def test_shell_grep_is_not_a_read():
    # grep is search, not a read — it must not enter the read denominator at all.
    recs = [
        asst("a1", None, "1", [tu("c1", "Bash", {"command": "grep -n foo /proj/a.py"})]),
        res("r1", "a1", "c1", bash_read_tur("12:foo\n")),
        asst("a2", "r1", "2", [tu("c2", "Bash", {"command": "grep -n foo /proj/a.py"})]),
        res("r2", "a2", "c2", bash_read_tur("12:foo\n")),
        rec.from_dict({"type": "last-prompt", "leafUuid": "r2"}),
    ]
    assert run(recs, "rereads").denominator == 0


def test_unused_context_via_shell_cat():
    recs = [
        asst("a1", None, "1", [tu("c1", "Bash", {"command": "cat /proj/big.py"})]),
        res("r1", "a1", "c1", bash_read_tur("x" * 4000)),   # ~1000 tok, never edited
        rec.from_dict({"type": "last-prompt", "leafUuid": "r1"}),
    ]
    assert run(recs, "unused_context").count == 1


def test_shell_write_clears_reread():
    # Read a.py, `sed -i` it, then read again: the re-read is legitimate (file changed),
    # so it must NOT be flagged — the write side clearing the seen-ranges is the whole point.
    recs = [
        asst("a1", None, "1", [tu("c1", "Read", {"file_path": "/proj/a.py"})]),
        res("r1", "a1", "c1", read_tur("/proj/a.py")),
        asst("a2", "r1", "2", [tu("c2", "Bash", {"command": "sed -i 's/a/b/' /proj/a.py"})]),
        res("r2", "a2", "c2", bash_read_tur("")),
        asst("a3", "r2", "3", [tu("c3", "Read", {"file_path": "/proj/a.py"})]),
        res("r3", "a3", "c3", read_tur("/proj/a.py")),
        rec.from_dict({"type": "last-prompt", "leafUuid": "r3"}),
    ]
    assert run(recs, "rereads").count == 0   # carve-out now honors shell edits too


def test_shell_write_grants_unused_context_credit():
    # Large read of big.py, then a shell `sed -i` edits it -> the file was used, not bloat.
    recs = [
        asst("a1", None, "1", [tu("c1", "Read", {"file_path": "/proj/big.py"})]),
        res("r1", "a1", "c1", read_tur("/proj/big.py", nbytes=4000)),
        asst("a2", "r1", "2", [tu("c2", "Bash", {"command": "sed -i 's/a/b/' /proj/big.py"})]),
        res("r2", "a2", "c2", bash_read_tur("")),
        rec.from_dict({"type": "last-prompt", "leafUuid": "r2"}),
    ]
    assert run(recs, "unused_context").count == 0   # edited via shell -> credited as used


# --- retry loops ------------------------------------------------------------------------

def test_retry_loop_flagged():
    recs = [
        asst("a1", None, "1", [tu("c1", "Bash", {"command": "npm test"})]),
        res("r1", "a1", "c1", is_error=True),
        asst("a2", "r1", "2", [tu("c2", "Bash", {"command": "npm test"})]),
        res("r2", "a2", "c2", is_error=True),
        rec.from_dict({"type": "last-prompt", "leafUuid": "r2"}),
    ]
    m = run(recs, "retries")
    assert m.count == 1 and "failed 2x" in m.instances[0].detail


def test_retry_same_error_text_flagged():
    err = "Error: ECONNREFUSED 127.0.0.1:5432 — could not connect to server"
    recs = [
        asst("a1", None, "1", [tu("c1", "Bash", {"command": "npm test"})]),
        res_err("r1", "a1", "c1", err),
        asst("a2", "r1", "2", [tu("c2", "Bash", {"command": "npm test"})]),
        res_err("r2", "a2", "c2", err),
        rec.from_dict({"type": "last-prompt", "leafUuid": "r2"}),
    ]
    m = run(recs, "retries")
    assert m.count == 1 and "failed 2x" in m.instances[0].detail


def test_retry_different_error_not_flagged():
    # The boxBot signal-cli case: same command twice, but the 2nd error is COMPLETELY
    # different (the missing library was installed between attempts) — adaptation, not a loop.
    recs = [
        asst("a1", None, "1", [tu("c1", "Bash", {"command": "signal-cli register --voice"})]),
        res_err("r1", "a1", "c1",
                "Missing required native library dependencies: failed to load libsignal_jni"),
        asst("a2", "r1", "2", [tu("c2", "Bash", {"command": "signal-cli register --voice"})]),
        res_err("r2", "a2", "c2",
                "Captcha invalid or user not registered: please request SMS verification first"),
        rec.from_dict({"type": "last-prompt", "leafUuid": "r2"}),
    ]
    assert run(recs, "retries").count == 0   # carve-out: a differing error is progress


def test_retry_mixed_errors_counts_only_matching():
    # Errors A, B (different), A again: only the two matching failures form the loop.
    err_a = "Error: address already in use 0.0.0.0:8080 bind failed"
    recs = [
        asst("a1", None, "1", [tu("c1", "Bash", {"command": "npm start"})]),
        res_err("r1", "a1", "c1", err_a),
        asst("a2", "r1", "2", [tu("c2", "Bash", {"command": "npm start"})]),
        res_err("r2", "a2", "c2", "SyntaxError: unexpected token in config.json at line 3"),
        asst("a3", "r2", "3", [tu("c3", "Bash", {"command": "npm start"})]),
        res_err("r3", "a3", "c3", err_a),
        rec.from_dict({"type": "last-prompt", "leafUuid": "r3"}),
    ]
    m = run(recs, "retries")
    assert m.count == 1
    assert "failed 2x (of 3 attempts)" in m.instances[0].detail


def test_single_failure_then_fix_not_flagged():
    recs = [
        asst("a1", None, "1", [tu("c1", "Bash", {"command": "npm test"})]),
        res("r1", "a1", "c1", is_error=True),
        asst("a2", "r1", "2", [tu("c2", "Bash", {"command": "npm test"})]),
        res("r2", "a2", "c2", {"stdout": "ok", "stderr": "", "interrupted": False}),
        rec.from_dict({"type": "last-prompt", "leafUuid": "r2"}),
    ]
    assert run(recs, "retries").count == 0   # one failure + fix is healthy


# --- re-touched lines -------------------------------------------------------------------

def test_retouch_flagged_when_rewriting_own_output():
    recs = [
        asst("a1", None, "1", [tu("c1", "Write", {"file_path": "/proj/f.py",
              "content": "def foo():\n    return 1"})]),
        res("r1", "a1", "c1", {"filePath": "/proj/f.py", "content": "def foo():\n    return 1",
                               "structuredPatch": []}),
        asst("a2", "r1", "2", [tu("c2", "Edit", {"file_path": "/proj/f.py",
              "old_string": "    return 1", "new_string": "    return 2"})]),
        res("r2", "a2", "c2", {"filePath": "/proj/f.py", "structuredPatch": []}),
        rec.from_dict({"type": "last-prompt", "leafUuid": "r2"}),
    ]
    assert run(recs, "retouched").count == 1


def test_retouch_not_flagged_editing_preexisting():
    recs = [
        asst("a1", None, "1", [tu("c1", "Edit", {"file_path": "/proj/f.py",
              "old_string": "preexisting line here", "new_string": "changed line here now"})]),
        res("r1", "a1", "c1", {"filePath": "/proj/f.py", "structuredPatch": []}),
        rec.from_dict({"type": "last-prompt", "leafUuid": "r1"}),
    ]
    assert run(recs, "retouched").count == 0   # old_string was never produced here


# --- unused context ---------------------------------------------------------------------

def test_unused_context_large_unedited():
    recs = [
        asst("a1", None, "1", [tu("c1", "Read", {"file_path": "/proj/big.py"})]),
        res("r1", "a1", "c1", read_tur("/proj/big.py", nbytes=4000)),  # ~1000 tok
        rec.from_dict({"type": "last-prompt", "leafUuid": "r1"}),
    ]
    assert run(recs, "unused_context").count == 1


def test_unused_context_excludes_edited_and_tiny():
    recs = [
        asst("a1", None, "1", [tu("c1", "Read", {"file_path": "/proj/big.py"})]),
        res("r1", "a1", "c1", read_tur("/proj/big.py", nbytes=4000)),
        asst("a2", "r1", "2", [tu("c2", "Edit", {"file_path": "/proj/big.py",
              "old_string": "a", "new_string": "b"})]),
        res("r2", "a2", "c2", {"filePath": "/proj/big.py", "structuredPatch": []}),
        asst("a3", "r2", "3", [tu("c3", "Read", {"file_path": "/proj/tiny.py"})]),
        res("r3", "a3", "c3", read_tur("/proj/tiny.py", nbytes=40)),     # ~10 tok < floor
        rec.from_dict({"type": "last-prompt", "leafUuid": "r3"}),
    ]
    assert run(recs, "unused_context").count == 0


# --- timeline scoping -------------------------------------------------------------------

def test_reread_different_section_not_flagged():
    # Read lines 1-50, then lines 200-250 of the same file, no edit between: NOT redundant.
    recs = [
        asst("a1", None, "1", [tu("c1", "Read", {"file_path": "/proj/a.py", "offset": 1})]),
        res("r1", "a1", "c1", read_tur_span("/proj/a.py", 1, 50)),
        asst("a2", "r1", "2", [tu("c2", "Read", {"file_path": "/proj/a.py", "offset": 200})]),
        res("r2", "a2", "c2", read_tur_span("/proj/a.py", 200, 50)),
        rec.from_dict({"type": "last-prompt", "leafUuid": "r2"}),
    ]
    assert run(recs, "rereads").count == 0   # range-level: different sections


def test_reread_same_section_flagged():
    recs = [
        asst("a1", None, "1", [tu("c1", "Read", {"file_path": "/proj/a.py"})]),
        res("r1", "a1", "c1", read_tur_span("/proj/a.py", 1, 50)),
        asst("a2", "r1", "2", [tu("c2", "Read", {"file_path": "/proj/a.py"})]),
        res("r2", "a2", "c2", read_tur_span("/proj/a.py", 1, 50)),
        rec.from_dict({"type": "last-prompt", "leafUuid": "r2"}),
    ]
    assert run(recs, "rereads").count == 1


def test_token_rate_within_unit_interval():
    recs = [
        asst("a1", None, "1", [tu("c1", "Write", {"file_path": "/proj/f.py",
              "content": "def foo():\n    return 1"})]),
        res("r1", "a1", "c1", {"filePath": "/proj/f.py", "structuredPatch": []}),
        asst("a2", "r1", "2", [tu("c2", "Edit", {"file_path": "/proj/f.py",
              "old_string": "    return 1", "new_string": "    return 2"})]),
        res("r2", "a2", "c2", {"filePath": "/proj/f.py", "structuredPatch": []}),
        rec.from_dict({"type": "last-prompt", "leafUuid": "r2"}),
    ]
    m = run(recs, "retouched")
    assert m.total_tokens > 0
    assert 0.0 < m.token_rate <= 1.0          # rewrite tokens / authored tokens


def test_reread_across_branches_not_flagged():
    # A reads a.py (abandoned); B reads a.py (active). Different timelines => not a re-read.
    recs = [
        asst("p0", None, "0", []),
        user("A", "p0", "1", "step 1"),
        asst("Aa", "A", "2", [tu("cA", "Read", {"file_path": "/proj/a.py"})]),
        res("rA", "Aa", "cA", read_tur("/proj/a.py")),
        user("B", "p0", "3", "step 2"),
        asst("Bb", "B", "4", [tu("cB", "Read", {"file_path": "/proj/a.py"})]),
        res("rB", "Bb", "cB", read_tur("/proj/a.py")),
        rec.from_dict({"type": "last-prompt", "leafUuid": "rB"}),
    ]
    assert run(recs, "rereads").count == 0   # would be 1 if flattened


# --- cross-session window behavior (the point of the window refactor) -------------------

def test_retouch_detected_across_sessions():
    # Session A writes a function; a LATER session rewrites its line. Per-session this is
    # invisible; over the window it's rework.
    sess_a = [
        asst("a1", None, "1", [tu("c1", "Write", {"file_path": "/proj/f.py",
              "content": "def foo():\n    return 1"})]),
        res("r1", "a1", "c1", {"filePath": "/proj/f.py", "structuredPatch": []}),
        rec.from_dict({"type": "last-prompt", "leafUuid": "r1"}),
    ]
    sess_b = [
        asst("b1", None, "9", [tu("c9", "Edit", {"file_path": "/proj/f.py",
              "old_string": "    return 1", "new_string": "    return 2"})]),
        res("rb", "b1", "c9", {"filePath": "/proj/f.py", "structuredPatch": []}),
        rec.from_dict({"type": "last-prompt", "leafUuid": "rb"}),
    ]
    # per-session: neither shows rework
    assert metrics.run_all(view_of(sess_a))["retouched"].count == 0
    assert metrics.run_all(view_of(sess_b))["retouched"].count == 0
    # over the window (A then B): rework appears
    assert metrics.run_all(view_of(sess_a, sess_b))["retouched"].count == 1


def test_unused_context_gets_credit_for_later_session_edit():
    # Read big.py in session A (not edited there), edit it in session B -> NOT unused.
    sess_a = [
        asst("a1", None, "1", [tu("c1", "Read", {"file_path": "/proj/big.py"})]),
        res("r1", "a1", "c1", read_tur("/proj/big.py", nbytes=4000)),
        rec.from_dict({"type": "last-prompt", "leafUuid": "r1"}),
    ]
    sess_b = [
        asst("b1", None, "9", [tu("c9", "Edit", {"file_path": "/proj/big.py",
              "old_string": "a", "new_string": "b"})]),
        res("rb", "b1", "c9", {"filePath": "/proj/big.py", "structuredPatch": []}),
        rec.from_dict({"type": "last-prompt", "leafUuid": "rb"}),
    ]
    assert metrics.run_all(view_of(sess_a))["unused_context"].count == 1          # alone: unused
    assert metrics.run_all(view_of(sess_a, sess_b))["unused_context"].count == 0  # window: used later


def test_reread_across_sessions_is_window_scope_only():
    # Reading the same file in two sessions: fresh context per SESSION (not flagged at session
    # scope), but the WINDOW scope catches the cross-session rediscovery (re-establishment tax).
    sess_a = [
        asst("a1", None, "1", [tu("c1", "Read", {"file_path": "/proj/a.py"})]),
        res("r1", "a1", "c1", read_tur("/proj/a.py")),
        rec.from_dict({"type": "last-prompt", "leafUuid": "r1"}),
    ]
    sess_b = [
        asst("b1", None, "9", [tu("c9", "Read", {"file_path": "/proj/a.py"})]),
        res("rb", "b1", "c9", read_tur("/proj/a.py")),
        rec.from_dict({"type": "last-prompt", "leafUuid": "rb"}),
    ]
    view = view_of(sess_a, sess_b)
    # session scope: each session re-reads nothing of its own -> 0 in every session
    per_sess = metrics.run_sessions(view)
    assert all(ms["rereads"].count == 0 for ms in per_sess.values())
    # window scope: same rule, longer memory -> the cross-session re-read shows up
    assert metrics.run_window(view)["rereads"].count == 1


def test_reread_before_edit_not_flagged_cross_session():
    # boxBot deploy.sh case: session A read the file; session B re-reads it and edits it
    # seconds later. The B read is structurally required (Read-before-Edit is per
    # conversation), so window scope must NOT flag it.
    sess_a = [
        asst("a1", None, "1", [tu("c1", "Read", {"file_path": "/proj/deploy.sh"})]),
        res("r1", "a1", "c1", read_tur("/proj/deploy.sh")),
        rec.from_dict({"type": "last-prompt", "leafUuid": "r1"}),
    ]
    sess_b = [
        asst("b1", None, "9", [tu("c9", "Read", {"file_path": "/proj/deploy.sh"})]),
        res("rb", "b1", "c9", read_tur("/proj/deploy.sh")),
        asst("b2", "rb", "10", [tu("c10", "Edit", {"file_path": "/proj/deploy.sh",
              "old_string": "x", "new_string": "y"})]),
        res("rb2", "b2", "c10", {"filePath": "/proj/deploy.sh", "structuredPatch": []}),
        rec.from_dict({"type": "last-prompt", "leafUuid": "rb2"}),
    ]
    assert metrics.run_window(view_of(sess_a, sess_b))["rereads"].count == 0


def test_reread_before_edit_still_flagged_intra_session():
    # Read, re-read, THEN edit within one session: the prior same-session read already
    # satisfied Read-before-Edit, so the re-read is a true repeat and stays flagged.
    recs = [
        asst("a1", None, "1", [tu("c1", "Read", {"file_path": "/proj/a.py"})]),
        res("r1", "a1", "c1", read_tur("/proj/a.py")),
        asst("a2", "r1", "2", [tu("c2", "Read", {"file_path": "/proj/a.py"})]),
        res("r2", "a2", "c2", read_tur("/proj/a.py")),
        asst("a3", "r2", "3", [tu("c3", "Edit", {"file_path": "/proj/a.py",
              "old_string": "x", "new_string": "y"})]),
        res("r3", "a3", "c3", {"filePath": "/proj/a.py", "structuredPatch": []}),
        rec.from_dict({"type": "last-prompt", "leafUuid": "r3"}),
    ]
    m = run(recs, "rereads")
    assert m.count == 1
    assert "already in context" in m.instances[0].detail   # same-session coverage wording


def test_cross_session_reread_wording_says_window():
    # A window-scope reread whose coverage comes from an EARLIER session must not render
    # "already in context" (reads as intra-session) — it spans sessions.
    sess_a = [
        asst("a1", None, "1", [tu("c1", "Read", {"file_path": "/proj/a.py"})]),
        res("r1", "a1", "c1", read_tur("/proj/a.py")),
        rec.from_dict({"type": "last-prompt", "leafUuid": "r1"}),
    ]
    sess_b = [
        asst("b1", None, "9", [tu("c9", "Read", {"file_path": "/proj/a.py"})]),
        res("rb", "b1", "c9", read_tur("/proj/a.py")),
        rec.from_dict({"type": "last-prompt", "leafUuid": "rb"}),
    ]
    m = metrics.run_window(view_of(sess_a, sess_b))["rereads"]
    assert m.count == 1
    assert "already read earlier in window" in m.instances[0].detail
    assert "already in context" not in m.instances[0].detail


def test_unused_context_excludes_transcript_infra():
    # A persisted tool-results sidecar under ~/.claude/projects/ is harness plumbing,
    # not project context — excluded from instances AND the denominator.
    infra = "/home/u/.claude/projects/-proj/tool-results/toolu_overflow.json"
    recs = [
        asst("a1", None, "1", [tu("c1", "Read", {"file_path": infra})]),
        res("r1", "a1", "c1", read_tur(infra, nbytes=34000)),   # ~8.6k tok, never edited
        rec.from_dict({"type": "last-prompt", "leafUuid": "r1"}),
    ]
    m = run(recs, "unused_context")
    assert m.count == 0 and m.denominator == 0 and m.total_tokens == 0


def test_retouch_session_scope_misses_what_window_catches():
    # The same data as test_retouch_detected_across_sessions, asserted via the scope API:
    # session scope sees no rework; window scope sees the cross-session rework.
    sess_a = [
        asst("a1", None, "1", [tu("c1", "Write", {"file_path": "/proj/f.py",
              "content": "def foo():\n    return 1"})]),
        res("r1", "a1", "c1", {"filePath": "/proj/f.py", "structuredPatch": []}),
        rec.from_dict({"type": "last-prompt", "leafUuid": "r1"}),
    ]
    sess_b = [
        asst("b1", None, "9", [tu("c9", "Edit", {"file_path": "/proj/f.py",
              "old_string": "    return 1", "new_string": "    return 2"})]),
        res("rb", "b1", "c9", {"filePath": "/proj/f.py", "structuredPatch": []}),
        rec.from_dict({"type": "last-prompt", "leafUuid": "rb"}),
    ]
    view = view_of(sess_a, sess_b)
    assert all(ms["retouched"].count == 0 for ms in metrics.run_sessions(view).values())
    assert metrics.run_window(view)["retouched"].count == 1


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
