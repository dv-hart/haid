"""HarnessBackend counterbalancing + integrity contract.

The subject's side (Diff A vs B) is flipped per comparison by a deterministic hash, and
the verdicts file must echo the manifest fingerprint. These tests pin the properties the
agent-orchestrated (file-handoff) path depends on:

  - the flip pattern is identical across manifest builds (emit pass == read-back pass);
  - un-flipping is correct, proven against ground truth derived from the PROMPT TEXT
    (where the subject diff actually sits), not from the flip function itself;
  - stale fingerprints, wrong winner counts, and out-of-vocabulary winners raise loudly
    instead of silently mis-scoring.
"""

import json

import pytest

from haid.scoring.compare import (CompareItem, HarnessBackend, PendingComparisons,
                                  _flip)

SUBJECT = CompareItem(diff="--- a/s.py\n+++ b/s.py\n+SUBJECT-MARKER unique payload\n")
ANCHORS = [CompareItem(diff=f"--- a/x{i}.py\n+++ b/x{i}.py\n+anchor body {i}\n",
                       id=f"anc{i}") for i in range(12)]
AXIS = "difficulty"


def _subject_side(prompt: str) -> str:
    """Ground truth from the emitted prompt: which side holds the subject diff."""
    a_pos = prompt.index("--- Diff A ---")
    b_pos = prompt.index("--- Diff B ---")
    s_pos = prompt.index("SUBJECT-MARKER")
    return "A" if a_pos < s_pos < b_pos else "B"


def _emit(tmp_path):
    be = HarnessBackend(job_dir=str(tmp_path))
    with pytest.raises(PendingComparisons) as exc:
        be.compare_batch(SUBJECT, ANCHORS, AXIS)
    manifest = json.load(open(exc.value.manifest_path, encoding="utf-8"))
    return be, manifest


def test_flip_deterministic_and_two_sided(tmp_path):
    flips = [_flip(AXIS, SUBJECT.diff, a.id, i) for i, a in enumerate(ANCHORS)]
    assert flips == [_flip(AXIS, SUBJECT.diff, a.id, i) for i, a in enumerate(ANCHORS)]
    assert True in flips and False in flips  # both orientations occur

    _, m1 = _emit(tmp_path / "one")
    _, m2 = _emit(tmp_path / "two")
    assert m1["fingerprint"] == m2["fingerprint"]
    assert [c["prompt"] for c in m1["comparisons"]] == \
           [c["prompt"] for c in m2["comparisons"]]
    # the prompt orientation matches the flip function on every comparison
    for i, c in enumerate(m1["comparisons"]):
        assert (_subject_side(c["prompt"]) == "B") == flips[i]


@pytest.mark.parametrize("who_wins", ["subject", "anchor"])
def test_round_trip_unflips_correctly(tmp_path, who_wins):
    be, manifest = _emit(tmp_path)
    raw = []
    for c in manifest["comparisons"]:
        side = _subject_side(c["prompt"])
        if who_wins == "subject":
            raw.append(side)                          # answer the subject's side
        else:
            raw.append("B" if side == "A" else "A")   # answer the anchor's side
    json.dump({"fingerprint": manifest["fingerprint"], "winners": raw},
              open(f"{tmp_path}/placement.verdicts.json", "w", encoding="utf-8"))
    winners = be.compare_batch(SUBJECT, ANCHORS, AXIS)
    assert winners == [who_wins] * len(ANCHORS)


def test_tie_passes_through(tmp_path):
    be, manifest = _emit(tmp_path)
    json.dump({"fingerprint": manifest["fingerprint"],
               "winners": ["tie"] * len(ANCHORS)},
              open(f"{tmp_path}/placement.verdicts.json", "w", encoding="utf-8"))
    assert be.compare_batch(SUBJECT, ANCHORS, AXIS) == ["tie"] * len(ANCHORS)


def test_stale_fingerprint_raises(tmp_path):
    be, _ = _emit(tmp_path)
    json.dump({"fingerprint": "deadbeefdeadbeef", "winners": ["A"] * len(ANCHORS)},
              open(f"{tmp_path}/placement.verdicts.json", "w", encoding="utf-8"))
    with pytest.raises(ValueError, match="stale verdicts"):
        be.compare_batch(SUBJECT, ANCHORS, AXIS)


def test_missing_fingerprint_raises(tmp_path):
    """The pre-counterbalance verdict shape ({'winners': [...]}) must not be accepted."""
    be, _ = _emit(tmp_path)
    json.dump({"winners": ["A"] * len(ANCHORS)},
              open(f"{tmp_path}/placement.verdicts.json", "w", encoding="utf-8"))
    with pytest.raises(ValueError, match="stale verdicts"):
        be.compare_batch(SUBJECT, ANCHORS, AXIS)


def test_wrong_count_raises(tmp_path):
    be, manifest = _emit(tmp_path)
    json.dump({"fingerprint": manifest["fingerprint"], "winners": ["A"]},
              open(f"{tmp_path}/placement.verdicts.json", "w", encoding="utf-8"))
    with pytest.raises(ValueError, match="expected 12 winners"):
        be.compare_batch(SUBJECT, ANCHORS, AXIS)


def test_bad_winner_value_raises(tmp_path):
    be, manifest = _emit(tmp_path)
    raw = ["A"] * len(ANCHORS)
    raw[3] = "Q"
    json.dump({"fingerprint": manifest["fingerprint"], "winners": raw},
              open(f"{tmp_path}/placement.verdicts.json", "w", encoding="utf-8"))
    with pytest.raises(ValueError, match="winner #3"):
        be.compare_batch(SUBJECT, ANCHORS, AXIS)


def test_runner_mode_unflips(tmp_path):
    """Injected-runner (workflow) mode goes through the same validation + un-flip."""
    seen = {}

    def runner(manifest):
        seen["fp"] = manifest["fingerprint"]
        return [_subject_side(c["prompt"]) for c in manifest["comparisons"]]

    be = HarnessBackend(job_dir=str(tmp_path), runner=runner)
    assert be.compare_batch(SUBJECT, ANCHORS, AXIS) == ["subject"] * len(ANCHORS)
    assert seen["fp"]

    def short_runner(manifest):
        return ["A"]

    be2 = HarnessBackend(job_dir=str(tmp_path), runner=short_runner)
    with pytest.raises(ValueError, match="expected 12 winners"):
        be2.compare_batch(SUBJECT, ANCHORS, AXIS)
