"""The submission gate (benchmark.validate_entry) and the cross-repo snapshot sanitizer
(benchmark.sanitize_board / project_row) — now in the package so the data repo, the sync,
and these tests share one implementation."""

import pytest

from haid.report import benchmark

SCORES_DOC = {"window": "w", "episodes": [
    {"id": "ep1", "has_artifact": True, "normalized_tokens": 100.0,
     "difficulty": {"rung": 8.0, "percentile": 0.89},
     "cleanliness": {"severe_count": 1, "minor_count": 0, "other_count": 0,
                     "changed_lines": 100, "by_class": {"dead_code": 1}, "execution_C": 0.77},
     "achievement": 50.0, "value": 0.5,
     "achievement_components": {"volume_loc": 120.0, "volume_term": 11.0,
                                "difficulty_D": 9.0, "cleanliness_C": 0.77}},
]}


def _payload(user="alice"):
    return benchmark.build_submission(SCORES_DOC, github_username=user, project="HAID",
                                      generated_at="T")


def _rehash(p):
    p.pop("content_hash", None)
    p["content_hash"] = benchmark.content_hash(p)
    return p


# --- validate_entry (the submission gate) ---------------------------------------------
def test_accepts_clean_entry():
    benchmark.validate_entry(_payload("alice"), expected_user="alice",
                             entry_name="entries/alice.json")            # no raise


def test_rejects_identity_mismatch():
    with pytest.raises(benchmark.SubmissionRejected, match="identity mismatch"):
        benchmark.validate_entry(_payload("alice"), expected_user="mallory",
                                 entry_name="entries/alice.json")
    with pytest.raises(benchmark.SubmissionRejected, match="identity mismatch"):
        benchmark.validate_entry(_payload("alice"), expected_user="alice",
                                 entry_name="entries/bob.json")


def test_rejects_tampering():
    p = _payload("alice")
    p["value_overall"] = 999.0
    with pytest.raises(benchmark.SubmissionRejected, match="content_hash"):
        benchmark.validate_entry(p, expected_user="alice", entry_name="entries/alice.json")


def test_rejects_stale_versions():
    p = _rehash({**_payload("alice"), "ladder_versions": {"difficulty": "DEAD"}})
    with pytest.raises(benchmark.SubmissionRejected, match="ladder_versions are stale"):
        benchmark.validate_entry(p, expected_user="alice", entry_name="entries/alice.json")
    p = _rehash({**_payload("alice"), "combiner_config_hash": "0000000000000000"})
    with pytest.raises(benchmark.SubmissionRejected, match="combiner_config_hash is stale"):
        benchmark.validate_entry(p, expected_user="alice", entry_name="entries/alice.json")


def test_rejects_unsupported_schema_version():
    """A pre-defect-model payload (schema 1.1) must be rejected, not silently accepted —
    the cleanliness-axis change made old submissions incomparable (ADR-0005)."""
    p = _rehash({**_payload("alice"), "schema_version": "1.1"})
    with pytest.raises(benchmark.SubmissionRejected, match="unsupported schema_version"):
        benchmark.validate_entry(p, expected_user="alice", entry_name="entries/alice.json")


def test_rejects_leak():
    p = _rehash({**_payload("alice"), "session_id": "s-123"})
    with pytest.raises(benchmark.SubmissionRejected, match="leak"):
        benchmark.validate_entry(p, expected_user="alice", entry_name="entries/alice.json")


def test_rejects_implausible_value():
    p = _rehash({**_payload("alice"), "value_overall": 999.0})    # hash ok, math wrong
    with pytest.raises(benchmark.SubmissionRejected, match="inconsistent"):
        benchmark.validate_entry(p, expected_user="alice", entry_name="entries/alice.json")


# --- sanitize_board (the cross-repo whitelist boundary) -------------------------------
def test_sanitize_projects_to_whitelist_and_drops_extras():
    row = _rehash({**_payload("alice"), "evil_field": "<script>", "extra": {"a": 1}})
    # extra keys are part of the hash body, so re-hashing keeps it verifiable...
    board = {"kind": "haid_benchmark_board", "rows": [row]}
    clean = benchmark.sanitize_board(board)
    assert clean["n_entries"] == 1
    out = clean["rows"][0]
    # ...but the projection carries ONLY whitelisted scalar fields — extras are gone
    assert "evil_field" not in out and "extra" not in out and "content_hash" not in out
    assert set(out) == set(benchmark._ROW_STR) | set(benchmark._ROW_NUM) | {"ladder_versions"}
    assert out["github_username"] == "alice"


def test_sanitize_rejects_unverifiable_row():
    bad = _payload("alice")
    bad["value_overall"] = 999.0          # breaks the hash
    with pytest.raises(benchmark.SubmissionRejected, match="content_hash does not verify"):
        benchmark.sanitize_board({"kind": "haid_benchmark_board", "rows": [bad]})


def test_sanitize_rejects_leaky_row():
    leaky = _rehash({**_payload("alice"), "session_ids": ["s1"]})
    with pytest.raises(benchmark.SubmissionRejected, match="leak"):
        benchmark.sanitize_board({"kind": "haid_benchmark_board", "rows": [leaky]})


def test_sanitize_rejects_duplicates_and_nonboard():
    a, b = _payload("alice"), _payload("alice")
    with pytest.raises(benchmark.SubmissionRejected, match="duplicate"):
        benchmark.sanitize_board({"kind": "haid_benchmark_board", "rows": [a, b]})
    with pytest.raises(benchmark.SubmissionRejected, match="not a haid benchmark board"):
        benchmark.sanitize_board({"kind": "something_else", "rows": []})
