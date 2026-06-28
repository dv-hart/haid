"""Single-diff defect-detection backends (detect → verify), the cleanliness boundary.

Run: PYTHONPATH=src python tests/scoring/test_detect.py   (or pytest tests/scoring/)
"""

from __future__ import annotations

import json
import os
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(_ROOT, "src"))

from haid.scoring import detect

_SEV = {"defect_class": "error_swallowing", "locator": "except: pass", "note": "swallows"}
_MIN = {"defect_class": "verbosity", "locator": "for x...", "note": "could be comp"}


# ----------------------------------------------------------------- ReplayBackend
def test_replay_applies_findings_and_verify():
    saved = {"ep1": {"findings": [_SEV, _MIN],
                     "verify": [{"verdict": "refuted", "reason": "intentional"}]}}
    b = detect.ReplayBackend(saved).for_subject("ep1")
    r = b.detect("diff", changed_lines=200)
    assert r.severe_count == 0          # the one severe was refuted
    assert r.minor_count == 1


def test_replay_missing_subject_raises():
    b = detect.ReplayBackend({}).for_subject("nope")
    try:
        b.detect("d", 10); assert False
    except KeyError:
        pass


# ----------------------------------------------------------------- runner mode
def test_runner_mode_runs_detect_then_verify():
    calls = []
    def runner(manifest):
        calls.append(manifest["task"])
        if manifest["task"] == "detect_defects":
            return [_SEV, _MIN]
        return [{"verdict": "confirmed", "reason": "real"}]   # confirm the severe
    b = detect.HarnessBackend(job_dir="(unused)", runner=runner)
    r = b.detect("diff", changed_lines=100)
    assert calls == ["detect_defects", "verify_defects"]
    assert r.severe_count == 1 and r.minor_count == 1


def test_runner_mode_skips_verify_when_no_severe():
    def runner(manifest):
        return [_MIN] if manifest["task"] == "detect_defects" else []
    calls = []
    def traced(m):
        calls.append(m["task"]); return runner(m)
    r = detect.HarnessBackend(job_dir="x", runner=traced).detect("d", 50)
    assert calls == ["detect_defects"]          # no verify phase
    assert r.severe_count == 0 and r.minor_count == 1


# ----------------------------------------------------------------- file handoff (two-phase)
def test_file_handoff_defers_detect_then_verify_then_resolves(tmp_path):
    jd = str(tmp_path)
    b = detect.HarnessBackend(job_dir=jd, job_name="ep1_detect")

    # phase 1: no findings yet -> PendingDetection(detect), manifest written
    try:
        b.detect("the diff", 200); assert False
    except detect.PendingDetection as p:
        assert p.phase == "detect"
    dman = json.load(open(os.path.join(jd, "ep1_detect.detect.job.json"), encoding="utf-8"))
    assert dman["task"] == "detect_defects"

    # write findings (echo fingerprint) -> now defers on verify
    json.dump({"fingerprint": dman["fingerprint"], "findings": [_SEV, _MIN]},
              open(os.path.join(jd, "ep1_detect.detect.findings.json"), "w", encoding="utf-8"))
    try:
        b.detect("the diff", 200); assert False
    except detect.PendingDetection as p:
        assert p.phase == "verify"
    vman = json.load(open(os.path.join(jd, "ep1_detect.verify.job.json"), encoding="utf-8"))
    assert len(vman["verifications"]) == 1       # one severe finding to verify

    # write verdicts (refute) -> resolves to a clean result
    json.dump({"fingerprint": vman["fingerprint"],
               "verdicts": [{"verdict": "refuted", "reason": "ok"}]},
              open(os.path.join(jd, "ep1_detect.verify.verdicts.json"), "w", encoding="utf-8"))
    r = b.detect("the diff", 200)
    assert r.severe_count == 0 and r.minor_count == 1


def test_file_handoff_stale_findings_fingerprint_raises(tmp_path):
    jd = str(tmp_path)
    b = detect.HarnessBackend(job_dir=jd, job_name="ep1_detect")
    json.dump({"fingerprint": "deadbeefdeadbeef", "findings": [_SEV]},
              open(os.path.join(jd, "ep1_detect.detect.findings.json"), "w", encoding="utf-8"))
    try:
        b.detect("the diff", 200); assert False
    except ValueError as e:
        assert "stale" in str(e)


if __name__ == "__main__":
    import tempfile
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            if fn.__code__.co_argcount == 1:           # tmp_path fixture
                with tempfile.TemporaryDirectory() as d:
                    class _P:
                        def __init__(s, p): s._p = p
                        def __str__(s): return s._p
                    fn(_P(d))
            else:
                fn()
            print(f"ok  {name}")
    print("\nALL DETECT TESTS PASSED")
