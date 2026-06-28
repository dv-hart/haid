"""split_tag_manifest.py + aggregate_tag_answers.py — the programmatic tag fan-out plumbing.

These two scripts replace the host's hand-assembly of tag.answers.json: the splitter writes one
branch-transcript file per job (out of the orchestrator's context), and the aggregator validates
the haid-tag workflow's grouped output against the manifest and concatenates it. Tested via
subprocess on a manifest built from the real haid schema (so enum validation can't drift).

Run: PYTHONPATH=src python -m pytest tests/scripts/test_tag_pipeline.py -q
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(_ROOT, "src"))

from haid.intent import taxonomy

_SCRIPTS = os.path.join(_ROOT, ".claude", "skills", "haid-report", "scripts")
_SPLIT = os.path.join(_SCRIPTS, "split_tag_manifest.py")
_AGG = os.path.join(_SCRIPTS, "aggregate_tag_answers.py")


def _manifest():
    """A two-branch manifest in the exact shape HarnessBackend._manifest emits."""
    return {
        "task": "classify_messages",
        "schema": taxonomy.SESSION_LABELS_SCHEMA,
        "jobs": [
            {"session_id": "aaaaaaaa", "timeline": "active", "n_targets": 2,
             "targets": [{"uuid": "uuid-1", "ref": "r1"}, {"uuid": "uuid-2", "ref": "r2"}],
             "prompt": "branch A transcript… ref: r1 … ref: r2 …"},
            {"session_id": "aaaaaaaa", "timeline": "rewind:bbbb", "n_targets": 1,
             "targets": [{"uuid": "uuid-3", "ref": "r3"}],
             "prompt": "branch B transcript… ref: r3 …"},
        ],
    }


def _write_manifest(job_dir) -> str:
    job_dir.mkdir(exist_ok=True)
    mpath = job_dir / "tag.job.json"
    mpath.write_text(json.dumps(_manifest()), encoding="utf-8")
    return str(mpath)


def _run(script, *a):
    return subprocess.run([sys.executable, script, *a], capture_output=True, text=True, cwd=_ROOT)


def _label(ref, move="new_directive", wt="implementation", ik="feature", purpose="do a thing"):
    return {"ref": ref, "move": move, "work_type": wt, "impl_kind": ik, "purpose": purpose}


def test_split_writes_prompt_files_and_index(tmp_path):
    job_dir = tmp_path / "jobs"
    _write_manifest(job_dir)
    r = _run(_SPLIT, "--job-dir", str(job_dir))
    assert r.returncode == 0, r.stderr
    index = json.loads(r.stdout)
    assert {j["job_id"] for j in index["jobs"]} == {"aaaaaaaa::active", "aaaaaaaa::rewind:bbbb"}
    # the colon in the rewind id is folded out of the filename, prompt written verbatim
    split_dir = job_dir / "tag_split"
    files = sorted(p.name for p in split_dir.glob("*.txt"))
    assert files == ["aaaaaaaa__active.txt", "aaaaaaaa__rewind-bbbb.txt"]
    assert "ref: r3" in (split_dir / "aaaaaaaa__rewind-bbbb.txt").read_text(encoding="utf-8")
    assert index["schema"]["properties"]["labels"]["items"]["required"][0] == "ref"


def test_aggregate_happy_path(tmp_path):
    job_dir = tmp_path / "jobs"
    _write_manifest(job_dir)
    raw = [
        {"job_id": "aaaaaaaa::active", "n_targets": 2, "complete": True,
         "labels": [_label("r1"), _label("r2", move="refinement")]},
        {"job_id": "aaaaaaaa::rewind:bbbb", "n_targets": 1, "complete": True,
         "labels": [_label("r3", wt="investigation", ik=None)]},
    ]
    raw_path = job_dir / "tag.raw.json"
    raw_path.write_text(json.dumps(raw), encoding="utf-8")
    r = _run(_AGG, "--job-dir", str(job_dir), "--answers", str(raw_path))
    assert r.returncode == 0, r.stderr
    out = json.loads((job_dir / "tag.answers.json").read_text(encoding="utf-8"))
    assert [x["ref"] for x in out["labels"]] == ["r1", "r2", "r3"]   # manifest order, flat
    assert all("uuid" not in x for x in out["labels"])               # refs only; uuids added by haid


def test_aggregate_rejects_wrong_count(tmp_path):
    job_dir = tmp_path / "jobs"
    _write_manifest(job_dir)
    raw = [
        {"job_id": "aaaaaaaa::active", "n_targets": 2, "complete": True,
         "labels": [_label("r1")]},                                  # only 1 of 2
        {"job_id": "aaaaaaaa::rewind:bbbb", "n_targets": 1, "complete": True,
         "labels": [_label("r3")]},
    ]
    raw_path = job_dir / "tag.raw.json"
    raw_path.write_text(json.dumps(raw), encoding="utf-8")
    r = _run(_AGG, "--job-dir", str(job_dir), "--answers", str(raw_path))
    assert r.returncode == 1
    assert "aaaaaaaa::active" in r.stderr and "expected 2" in r.stderr
    assert not (job_dir / "tag.answers.json").exists()              # writes nothing on failure


def test_aggregate_rejects_unknown_ref_and_bad_enum(tmp_path):
    job_dir = tmp_path / "jobs"
    _write_manifest(job_dir)
    raw = [
        {"job_id": "aaaaaaaa::active", "n_targets": 2, "complete": True,
         "labels": [_label("r1", move="question"),                  # 'question' is a work_type
                    _label("rX")]},                                 # unknown ref (not r2)
        {"job_id": "aaaaaaaa::rewind:bbbb", "n_targets": 1, "complete": True,
         "labels": [_label("r3")]},
    ]
    raw_path = job_dir / "tag.raw.json"
    raw_path.write_text(json.dumps(raw), encoding="utf-8")
    r = _run(_AGG, "--job-dir", str(job_dir), "--answers", str(raw_path))
    assert r.returncode == 1
    assert "bad move" in r.stderr and "unknown ref" in r.stderr


def test_aggregate_rejects_incomplete_branch(tmp_path):
    job_dir = tmp_path / "jobs"
    _write_manifest(job_dir)
    raw = [
        {"job_id": "aaaaaaaa::active", "n_targets": 2, "complete": False, "labels": None},
        {"job_id": "aaaaaaaa::rewind:bbbb", "n_targets": 1, "complete": True,
         "labels": [_label("r3")]},
    ]
    raw_path = job_dir / "tag.raw.json"
    raw_path.write_text(json.dumps(raw), encoding="utf-8")
    r = _run(_AGG, "--job-dir", str(job_dir), "--answers", str(raw_path))
    assert r.returncode == 1
    assert "incomplete" in r.stderr


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
