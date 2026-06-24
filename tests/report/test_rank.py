"""haid rank: percentile math, the comparability filter (same ladders + combiner only),
and the rendered view."""

from haid.report import benchmark, rank

SCORES_DOC = {"window": "w", "episodes": [
    {"id": "ep1", "has_artifact": True, "normalized_tokens": 100.0,
     "difficulty": {"rung": 8.0, "percentile": 0.89}, "cleanliness": {"percentile": 0.9},
     "achievement": 50.0, "value": 0.5,
     "achievement_components": {"volume_loc": 120.0, "volume_term": 11.0,
                                "difficulty_D": 9.0, "cleanliness_C": 0.81}},
]}


def _me(user="you"):
    return benchmark.build_submission(SCORES_DOC, github_username=user, project="HAID",
                                      generated_at="T")


def test_percentile_basic():
    assert rank.percentile([1, 2, 3, 4], 3) == 0.75
    assert rank.percentile([], 5) != rank.percentile([], 5)   # nan
    assert rank.percentile([10], 10) == 1.0


def test_lone_submitter_is_top_not_nan():
    r = rank.rank_against({"rows": []}, _me())
    assert r["n_peers"] == 0
    assert r["axes"]["value_overall"]["percentile"] == 1.0   # self counted -> p100, not nan


def test_comparability_filter_excludes_other_versions_and_self():
    me = _me("you")
    same = {**me, "github_username": "peer", "value_overall": 999.0}
    other_ladder = {**me, "github_username": "x", "ladder_versions": {"difficulty": "DEAD"}}
    other_combiner = {**me, "github_username": "y", "combiner_config_hash": "BEEF"}
    mine_again = {**me, "github_username": "you"}              # same user -> excluded
    board = {"rows": [same, other_ladder, other_combiner, mine_again]}
    r = rank.rank_against(board, me)
    assert r["n_peers"] == 1                                   # only `same` is comparable
    assert r["n_incomparable"] == 2                            # the two version-mismatched
    assert r["axes"]["value_overall"]["percentile"] == 0.5     # peer beats me -> p50


def test_render_includes_submit_cta():
    out = rank.render_rank(rank.rank_against({"rows": []}, _me()), _me())
    assert "haid submit" in out and "uploads nothing" in out
