"""haid submit: the side-effect-free parts — preview, entry writing, repo detection, and
the git/gh command sequence. The network/git execution (run_pr) is not exercised here."""

import json

import pytest

from haid.report import benchmark, submit

SCORES_DOC = {"window": "w", "episodes": [
    {"id": "ep1", "has_artifact": True, "normalized_tokens": 100.0,
     "difficulty": {"rung": 8.0, "percentile": 0.89}, "cleanliness": {"percentile": 0.9},
     "achievement": 50.0, "value": 0.5,
     "achievement_components": {"volume_loc": 120.0, "volume_term": 11.0,
                                "difficulty_D": 9.0, "cleanliness_C": 0.81}},
]}


def _payload(user="dv-hart"):
    return benchmark.build_submission(SCORES_DOC, github_username=user, project="HAID",
                                      generated_at="T")


def test_preview_shows_public_fields_only():
    text = submit.render_public_preview(_payload())
    assert "PUBLIC and PERMANENT" in text
    assert "dv-hart" in text and "HAID" in text
    # the preview must never carry leak-shaped content
    assert "session" not in text.lower() and "/ep1" not in text


def test_write_entry_roundtrips_and_names_by_user(tmp_path):
    dest = submit.write_entry(_payload("alice"), tmp_path)
    assert dest == tmp_path / "entries" / "alice.json"
    saved = json.loads(dest.read_text(encoding="utf-8"))
    assert saved["github_username"] == "alice" and benchmark.verify(saved)


def test_write_entry_refuses_unverifiable(tmp_path):
    p = _payload()
    p["value_overall"] = 999.0          # break the content hash
    with pytest.raises(ValueError, match="content_hash"):
        submit.write_entry(p, tmp_path)


def test_pr_commands_touch_exactly_the_one_entry():
    cmds = submit.pr_commands("dv-hart", "HAID")
    adds = [c for c in cmds if c[:2] == ["git", "add"]]
    assert adds == [["git", "add", "entries/dv-hart.json"]]
    assert any(c[0] == "gh" and "pr" in c for c in cmds)
    # the PR targets the data-only repo, not the package repo
    assert any(submit.BENCHMARK_REPO in c for c in cmds if c[0] == "gh")


def test_find_repo_root_via_marker(tmp_path):
    (tmp_path / submit.REPO_MARKER).write_text("", encoding="utf-8")
    sub = tmp_path / "a" / "b"
    sub.mkdir(parents=True)
    assert submit.find_repo_root(sub) == tmp_path
    # a dir without the marker is not the data repo
    assert submit.find_repo_root(tmp_path.parent) is None
