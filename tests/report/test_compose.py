"""Compositor: symptom derivation + suppression, findings, digest, strict composition
validation, file handoff; benchmark payload determinism + leak refusal."""

import json

import pytest

from haid.report import (benchmark, build_findings, digest_json, load_catalog,
                         render_digest, render_report, validate_composition)
from haid.report.compose import (HarnessBackend, PendingComposition, ReplayBackend,
                                 symptoms_for_note)

WHY_DOC = {"notes": [
    {"anchor_id": "rereads/window/1", "metric": "rereads",
     "detail": "deploy.sh re-read in 2 sessions", "token_weight": 2436,
     "anchor_audit": "verified cross-session", "note": "re-establishment before deploys",
     "flags": ["recurred_across_sessions"], "evidence": [], "remedy": "CLAUDE.md lines",
     "estimated_avoidable_tokens": 2436, "avoidable_basis": "one read", "confidence": "high"},
    {"anchor_id": "retouched/window/1", "metric": "retouched",
     "detail": "main.py 50-line self-rewrite", "token_weight": 499,
     "anchor_audit": "verified", "note": "user-directed redesign",
     "flags": ["earned_iteration", "correction_preceded"], "evidence": [],
     "remedy": "no remedy needed", "estimated_avoidable_tokens": None,
     "avoidable_basis": "earned", "confidence": "high"},
    {"anchor_id": "retries/window/1", "metric": "retries",
     "detail": "same ssh command failed 2x", "token_weight": 36,
     "anchor_audit": "verified same error twice", "note": "error text ignored",
     "flags": ["error_message_ignored"], "evidence": [], "remedy": "hooks",
     "estimated_avoidable_tokens": 36, "avoidable_basis": "both attempts",
     "confidence": "medium"},
]}

TAGS_DOC = {"window": "w", "messages": (
    [{"session_id": "s1", "move": "correction", "purpose": "fix x"}] * 2
    + [{"session_id": "s1", "move": "new_directive", "purpose": f"d{i}"} for i in range(3)]
    + [{"session_id": "s2", "move": "approval", "purpose": "ok"}])}

SCORES_DOC = {"window": "w", "episodes": [
    {"id": "ep1", "title": "easy big", "has_artifact": True, "normalized_tokens": 9000.0,
     "difficulty": {"rung": 1.0, "percentile": 0.11},
     "cleanliness": {"percentile": 0.2}, "achievement": 2.0, "value": 0.0002,
     "achievement_components": {"volume_loc": 400.0, "volume_term": 20.0,
                                "difficulty_D": 0.1, "cleanliness_C": 0.04},
     "metrics": {}, "session_ids": ["s1"], "n_sessions": 1, "caveats": []},
    {"id": "ep2", "title": "hard small", "has_artifact": True, "normalized_tokens": 100.0,
     "difficulty": {"rung": 8.0, "percentile": 0.89},
     "cleanliness": {"percentile": 0.9}, "achievement": 50.0, "value": 0.5,
     "achievement_components": {"volume_loc": 120.0, "volume_term": 11.0,
                                "difficulty_D": 9.0, "cleanliness_C": 0.81},
     "metrics": {}, "session_ids": ["s2"], "n_sessions": 1, "caveats": []},
]}


def test_symptom_derivation_and_suppression():
    assert symptoms_for_note(WHY_DOC["notes"][0]) == ["rereads.cross_session"]
    assert symptoms_for_note(WHY_DOC["notes"][1]) == []        # earned -> suppressed
    assert symptoms_for_note(WHY_DOC["notes"][2]) == ["retries.error_ignored"]
    assert symptoms_for_note({"metric": "rereads", "flags": []}) == ["rereads.in_context"]
    assert symptoms_for_note({"metric": "retouched", "flags": ["no_user_trigger"]}) \
        == ["retouched.self_thrash"]
    assert symptoms_for_note({"metric": "unused_context",
                              "flags": ["fix_did_not_hold"]}) \
        == ["unused_context.bloat", "recurrence.fix_did_not_hold"]


def _findings():
    return build_findings(why_doc=WHY_DOC, tags_doc=TAGS_DOC, scores_doc=SCORES_DOC,
                          catalog=load_catalog())


def test_findings_join_all_sources():
    fs = _findings()
    srcs = {f.source for f in fs}
    assert srcs == {"why_note", "window_rule"}
    earned = [f for f in fs if f.suppressed]
    assert len(earned) == 1 and not earned[0].treatments
    active = [f for f in fs if f.symptoms]
    assert all(f.treatments for f in active)        # every active finding got treatments
    keys = {s for f in fs for s in f.symptoms}
    # window rules fired: 2 corrections, 3 directives in s1, ep1 low-diff high-spend +
    # low cleanliness
    assert {"alignment.corrections", "drift.multi_topic", "cost.model_overkill",
            "cleanliness.low"} <= keys


def test_digest_renders_without_model():
    fs = _findings()
    d = digest_json(metrics_doc=None, why_doc=WHY_DOC, scores_doc=SCORES_DOC,
                    tags_doc=TAGS_DOC, findings=fs, label="w")
    text = render_digest(d)
    assert "Credited" in text and "matched treatments" in text
    assert "ep2" in text and "->" in text


def _good_comp(fs):
    f = next(f for f in fs if f.symptoms)
    return {"headline": "h", "wins": [{"what": "hard ep", "evidence": "ep2 rung 8"}],
            "recommendations": [{"priority": 1, "finding_id": f.id,
                                 "treatment_id": f.treatments[0]["id"],
                                 "action": "do it", "expected_effect": "less waste"}],
            "watchlist": ["w1"], "hedges": "thin baseline"}


def test_composition_validation_strict():
    fs = _findings()
    comp = _good_comp(fs)
    assert validate_composition(comp, fs)["headline"] == "h"
    with pytest.raises(ValueError, match="unknown finding_id"):
        validate_composition({**comp, "recommendations": [
            {**comp["recommendations"][0], "finding_id": "F999"}]}, fs)
    with pytest.raises(ValueError, match="was not matched"):
        validate_composition({**comp, "recommendations": [
            {**comp["recommendations"][0], "treatment_id": "made-up-cure"}]}, fs)
    with pytest.raises(ValueError, match="missing key"):
        validate_composition({k: v for k, v in comp.items() if k != "hedges"}, fs)


def test_compose_file_handoff(tmp_path):
    fs = _findings()
    cat = load_catalog()
    d = digest_json(metrics_doc=None, why_doc=WHY_DOC, scores_doc=SCORES_DOC,
                    tags_doc=TAGS_DOC, findings=fs, label="w")
    be = HarnessBackend(job_dir=str(tmp_path))
    with pytest.raises(PendingComposition) as exc:
        be.compose(d, fs, cat)
    manifest = json.load(open(exc.value.manifest_path, encoding="utf-8"))
    assert manifest["recommended_model"] == "opus"
    assert "CREDIT FIRST" in manifest["prompt"]
    json.dump(_good_comp(fs), open(f"{tmp_path}/compose.composition.json", "w",
                                   encoding="utf-8"))
    comp = be.compose(d, fs, cat)
    out = render_report(d, comp)
    assert "What went well" in out and "Recommendations" in out
    assert ReplayBackend(_good_comp(fs)).compose(d, fs, cat)


# --- benchmark payload ----------------------------------------------------------------
def test_benchmark_payload_deterministic_and_clean():
    p1 = benchmark.build_submission(SCORES_DOC, github_username="jhart",
                                    project="HAID", generated_at="T")
    p2 = benchmark.build_submission(SCORES_DOC, github_username="jhart",
                                    project="HAID", generated_at="T")
    assert p1 == p2
    assert benchmark.verify(p1)
    assert p1["scores"]["value"]["n"] == 2
    assert p1["scores"]["difficulty_rungs"] == [1.0, 8.0]
    assert "ladder_versions" in p1 and len(p1["ladder_versions"]) == 2
    # nothing leak-shaped survives
    text = json.dumps(p1)
    assert "session_ids" not in text and "easy big" not in text


def test_benchmark_row_fields():
    p = benchmark.build_submission(SCORES_DOC, github_username="jhart", project="HAID",
                                   generated_at="T")
    # the leaderboard-row scalars the table renders
    assert p["achievement_total"] == 52.0           # 2 + 50
    assert p["volume_loc_total"] == 520.0           # 400 + 120
    assert p["normalized_tokens_total"] == 9100.0
    assert p["value_overall"] == round(benchmark._value.value_ratio(52.0, 9100.0), 6)  # tot/tot
    assert p["difficulty_rung_median"] == 8.0       # median of [1, 8] (upper)
    assert p["cleanliness_pct_median"] == 0.9
    # comparability keys present and stable
    assert p["combiner_config_hash"] == benchmark.combiner_config_hash()
    assert p["tool_version"] and "signature" not in p
    assert p["self_reported"] is True


def test_community_section_renders_with_cta():
    me = benchmark.build_submission(SCORES_DOC, github_username="you", project="HAID",
                                    generated_at="T")
    # a comp with no community block -> no section; with one -> section + CTA
    comp = {"headline": "h", "wins": [], "recommendations": [], "watchlist": [],
            "hedges": "thin"}
    bare = digest_json(metrics_doc=None, why_doc=None, scores_doc=SCORES_DOC,
                       tags_doc=None, findings=[], label="w")
    assert "Community benchmark" not in render_report(bare, comp)
    from haid.report import rank
    peer = {**me, "github_username": "other", "value_overall": 99.0}
    community = rank.rank_against({"rows": [peer]}, me)
    d = digest_json(metrics_doc=None, why_doc=None, scores_doc=SCORES_DOC,
                    tags_doc=None, findings=[], label="w", community=community)
    out = render_report(d, comp)
    assert "Community benchmark" in out and "haid submit" in out


def test_benchmark_leak_refusal():
    with pytest.raises(ValueError, match="leak"):
        benchmark.assert_no_leaks({"scores": {"file_path": "x"}})
    with pytest.raises(ValueError, match="leak"):
        benchmark.assert_no_leaks(
            {"note": "C:/Users/someone/Documents/very/long/identifying/path/file.py"})
    bad = benchmark.build_submission(SCORES_DOC, github_username="u", project="p",
                                     generated_at="T")
    bad["content_hash"] = "tampered"
    assert not benchmark.verify(bad)


def test_benchmark_requires_identity():
    with pytest.raises(ValueError, match="github_username"):
        benchmark.build_submission(SCORES_DOC, github_username="", project="p",
                                   generated_at="T")
