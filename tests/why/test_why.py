"""Why-pass: anchor triage, prompt contract, strict note validation, file handoff."""

import json

import pytest

from haid import why
from haid.why.anchors import select_anchors
from haid.why.investigate import HarnessBackend, PendingInvestigations, validate_note
from haid.why.prompts import FLAGS, build_anchor_prompt


def _inst(metric, rank, tok, scope="window", **refs):
    return {"id": f"{metric}/{scope}/{rank}", "metric": metric, "scope": scope,
            "session_id": None, "timeline": scope,
            "detail": f"{metric} instance {rank}", "token_weight": tok,
            "refs": refs or {"file_id": "repo:a.py", "session_ids": ["s1"]}}


DOC = {
    "window": {"sessions": [{"id": "s1"}, {"id": "s2"}, {"id": "s3"}]},
    "instances": [
        _inst("rereads", 1, 5000), _inst("rereads", 2, 3000), _inst("rereads", 3, 2000),
        _inst("rereads", 4, 1500),                       # over per-metric cap of 3
        _inst("retouched", 1, 900), _inst("retouched", 2, 700),
        _inst("retries", 1, 36),                         # tiny but exempt from floor
        _inst("unused_context", 1, 150),                 # below min_tokens -> dropped
        _inst("rereads", 9, 9999, scope="session"),      # session scope -> dropped
    ],
}


def test_triage_ranking_caps_and_retries_exemption():
    anchors = select_anchors(DOC, top=6, per_metric_cap=3, min_tokens=200)
    ids = [a.id for a in anchors]
    assert ids[0] == "retries/window/1"            # retries float to front
    assert "rereads/window/4" not in ids           # per-metric cap
    assert "unused_context/window/1" not in ids    # token floor
    assert "rereads/session/9" not in ids          # window scope only
    assert len(ids) == 6
    # remainder is token-ordered
    assert ids[1:4] == ["rereads/window/1", "rereads/window/2", "rereads/window/3"]


def test_prompt_contains_contract():
    a = select_anchors(DOC, top=1)[0]
    p = build_anchor_prompt(a, transcript_dir="T:/dir", project_path="P:/repo",
                            all_session_ids=["s1", "s2", "s3"])
    assert "AUDIT THE ANCHOR FIRST" in p
    assert "Scope semantics:" in p
    assert "T:/dir" in p and "P:/repo" in p
    for flag in ("legitimate_by_context", "detector_overstates", "no_user_trigger"):
        assert flag in p
    assert "ONLY this JSON" in p


GOOD_NOTE = {"anchor_audit": "verified", "note": "because", "flags": ["earned_iteration"],
             "evidence": [{"session": "s1", "what": "quote"}], "remedy": "none needed",
             "estimated_avoidable_tokens": None, "avoidable_basis": "n/a",
             "confidence": "high"}


def test_validate_note_strict():
    assert validate_note(dict(GOOD_NOTE), "x")["flags"] == ["earned_iteration"]
    with pytest.raises(ValueError, match="unknown flags"):
        validate_note({**GOOD_NOTE, "flags": ["sounds_bad"]}, "x")
    with pytest.raises(ValueError, match="confidence"):
        validate_note({**GOOD_NOTE, "confidence": "certain"}, "x")
    with pytest.raises(ValueError, match="missing keys"):
        validate_note({k: v for k, v in GOOD_NOTE.items() if k != "remedy"}, "x")
    with pytest.raises(ValueError, match="estimated_avoidable_tokens"):
        validate_note({**GOOD_NOTE, "estimated_avoidable_tokens": "lots"}, "x")


def _investigate(backend):
    anchors = select_anchors(DOC, top=2)
    return why.investigate_window(DOC, anchors, backend,
                                  transcript_dir="T:/dir", project_path="P:/repo")


def test_file_handoff_round_trip(tmp_path):
    be = HarnessBackend(job_dir=str(tmp_path))
    with pytest.raises(PendingInvestigations) as exc:
        _investigate(be)
    manifest = json.load(open(exc.value.manifest_path, encoding="utf-8"))
    assert manifest["recommended_model"] == "sonnet"
    assert len(manifest["jobs"]) == 2
    assert all("AUDIT THE ANCHOR" in j["prompt"] for j in manifest["jobs"])

    notes = [{"anchor_id": j["anchor_id"], **GOOD_NOTE} for j in manifest["jobs"]]
    json.dump({"notes": notes}, open(f"{tmp_path}/why.notes.json", "w", encoding="utf-8"))
    results = _investigate(be)
    assert len(results) == 2
    assert results[0][1]["confidence"] == "high"


def test_file_handoff_missing_note_raises(tmp_path):
    be = HarnessBackend(job_dir=str(tmp_path))
    with pytest.raises(PendingInvestigations) as exc:
        _investigate(be)
    manifest = json.load(open(exc.value.manifest_path, encoding="utf-8"))
    json.dump({"notes": [{"anchor_id": manifest["jobs"][0]["anchor_id"], **GOOD_NOTE}]},
              open(f"{tmp_path}/why.notes.json", "w", encoding="utf-8"))
    with pytest.raises(ValueError, match="missing notes"):
        _investigate(be)


def test_runner_mode_validates():
    def runner(manifest):
        return [dict(GOOD_NOTE) for _ in manifest["jobs"]]
    results = _investigate(HarnessBackend(job_dir="/unused", runner=runner))
    assert len(results) == 2

    def bad_runner(manifest):
        return [{**GOOD_NOTE, "flags": ["nonsense"]} for _ in manifest["jobs"]]
    with pytest.raises(ValueError, match="unknown flags"):
        _investigate(HarnessBackend(job_dir="/unused", runner=bad_runner))


def test_render_and_json():
    def runner(manifest):
        return [{**GOOD_NOTE, "estimated_avoidable_tokens": 2436,
                 "avoidable_basis": "one full read"} for _ in manifest["jobs"]]
    results = _investigate(HarnessBackend(job_dir="/unused", runner=runner))
    text = why.render(results, label="t")
    assert "avoidable: ~2436 tok" in text
    doc = why.to_json(results, label="t")
    assert doc["notes"][0]["anchor_id"] == results[0][0].id
