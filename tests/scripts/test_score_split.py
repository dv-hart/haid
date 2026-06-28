"""split_score_manifests.py — the score (pairwise / detect / verify) fan-out splitter.

The splitter writes one prompt file per judge job (out of the orchestrator's context) and prints a
tiny `args` index for the haid-judge workflow. The load-bearing invariant tested here: each kind's
`schema` is lifted to the TOP LEVEL of the index, never nested per manifest. Nested-in-array data is
dropped when the host model marshals the workflow `args`, which leaves judges with `schema:undefined`
and silently disables structured-output forcing (the diagnosed free-text / token-blowup failure).
Top-level fields survive, so forcing stays reliable. A kind missing its schema must abort loudly.

Run: PYTHONPATH=src python -m pytest tests/scripts/test_score_split.py -q
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(_ROOT, "src"))

from haid.scoring import compare, defects

_SCRIPTS = os.path.join(_ROOT, ".claude", "skills", "haid-report", "scripts")
_SPLIT = os.path.join(_SCRIPTS, "split_score_manifests.py")


def _pairwise(fp="fp_pair"):
    return {"fingerprint": fp, "schema": compare.VERDICT_SCHEMA,
            "subject": {"id": "ep1", "diff": "..."},
            "comparisons": [{"anchor_id": "a0", "prompt": "PAIR cmp 0"},
                            {"anchor_id": "a1", "prompt": "PAIR cmp 1"}]}


def _detect(fp="fp_det"):
    return {"fingerprint": fp, "task": "detect_defects", "schema": defects.DEFECT_SCHEMA,
            "prompt": "DETECT catalog the defects"}


def _verify(fp="fp_ver"):
    return {"fingerprint": fp, "task": "verify_defects", "schema": defects.VERIFY_SCHEMA,
            "verifications": [{"prompt": "VERIFY finding 0"}]}


def _write(job_dir, name, manifest):
    job_dir.mkdir(exist_ok=True)
    (job_dir / name).write_text(json.dumps(manifest), encoding="utf-8")


def _run(*a):
    return subprocess.run([sys.executable, _SPLIT, *a], capture_output=True, text=True, cwd=_ROOT)


def test_schemas_ride_top_level_by_kind(tmp_path):
    job_dir = tmp_path / "jobs"
    _write(job_dir, "ep1_difficulty.job.json", _pairwise())
    _write(job_dir, "ep1_detect.detect.job.json", _detect())
    _write(job_dir, "ep1_detect.verify.job.json", _verify())

    r = _run("--job-dir", str(job_dir))
    assert r.returncode == 0, r.stderr
    index = json.loads(r.stdout)

    # schemas are top-level, keyed by kind, and are the real haid schemas (no drift)
    assert set(index["schemas"]) == {"pairwise", "detect", "verify"}
    assert index["schemas"]["pairwise"] == compare.VERDICT_SCHEMA
    assert index["schemas"]["detect"] == defects.DEFECT_SCHEMA
    assert index["schemas"]["verify"] == defects.VERIFY_SCHEMA

    # manifests are lean — NO per-manifest schema (the field that gets dropped in marshalling)
    assert all("schema" not in m for m in index["manifests"])
    assert {(m["manifest"], m["kind"], m["n"]) for m in index["manifests"]} == {
        ("ep1_difficulty", "pairwise", 2),
        ("ep1_detect.detect", "detect", 1),
        ("ep1_detect.verify", "verify", 1),
    }

    # prompt files written verbatim, one per job
    split_dir = job_dir / "score_split"
    assert (split_dir / "ep1_difficulty__1.txt").read_text(encoding="utf-8") == "PAIR cmp 1"
    assert (split_dir / "ep1_detect.detect__0.txt").read_text(encoding="utf-8").startswith("DETECT")


def test_aborts_loudly_when_a_kind_has_no_schema(tmp_path):
    job_dir = tmp_path / "jobs"
    bad = _pairwise()
    del bad["schema"]                                   # manifest with no schema -> forcing impossible
    _write(job_dir, "ep1_difficulty.job.json", bad)

    r = _run("--job-dir", str(job_dir))
    assert r.returncode == 1
    assert "pairwise" in r.stderr and "forcing would be disabled" in r.stderr
    assert r.stdout.strip() == ""                       # writes no index the host could pass on


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
