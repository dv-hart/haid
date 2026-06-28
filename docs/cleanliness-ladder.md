# Cleanliness ladder — RETIRED (replaced by counted defect density)

> **Status: the pairwise cleanliness ladder was RETIRED (2026-06).** Cleanliness is no
> longer a relative ladder placement. It is now **counted severe-defect density**:
> a single-diff judge catalogues falsifiable defects (each pinned to a verbatim locator),
> an adversarial pass verifies the severe ones, and the surviving severe count feeds a
> bounded penalty on achievement.
>
> - The taxonomy, severity lookup, and detect/verify contract: **[`src/haid/scoring/defects.py`](../src/haid/scoring/defects.py)**
> - The detect→verify backends (mirrors `compare.py`): **[`src/haid/scoring/detect.py`](../src/haid/scoring/detect.py)**
> - The penalty applied to the score: **`execution_factor()` in [`src/haid/scoring/value.py`](../src/haid/scoring/value.py)**
>   — `C = max(exec_floor, 1 - k_defect * severe / sqrt(max(changed_LOC, loc_floor)))`.

## Why the ladder was retired (a category error, not a calibration miss)

Cleanliness is **not an ordinal scalar**. Two diffs can be *differently* dirty — one ships
dead code, the other duplicates a block — with no true fact about which is "cleaner." The
pairwise placement forced that non-existent total order onto a ladder, and the validation
showed it: every cleanliness placement was non-monotonic (58 ordering inversions across 11
episodes, 0/11 coherent) while the *same* machinery on difficulty stayed coherent (5/11).
The inversions were the signature of an ordinal instrument measuring a non-ordinal quantity
— a better ladder cannot fix that.

So cleanliness is now measured the way reviewers actually read code: by **counting discrete,
evidence-bearing defects** against a closed taxonomy whose severities are fixed by lookup
(the judge classifies and locates; it never opines on severity). Only verified *severe*
defects move the score; minors and an "other" channel are coaching color with weight 0. See
`defects.py` for the full reasoning and contract.

---

## Historical record (the now-retired ladder, kept for context)

The original ladder was calibrated 2026-06-05 via the
[axis-calibration-playbook](axis-calibration-playbook.md) over 55 blinded diffs, with a v3
"size-invariant + decisive" prompt (two earlier prompt variants — naive "ignore size" and
over-hardened "usually answer tie" — were rejected for leaking size / collapsing to ties).
It placed a diff by ~1 pairwise comparison vs each of 11 anchor rungs (rung 0 = least clean
.. rung 10 = most clean), reusing the difficulty placement machinery, and shipped a locked
`cleanliness_anchors.json` as package data. It reported ρ≈+0.03 vs difficulty and ρ≈−0.37 vs
churn at calibration time — but those aggregate orthogonality numbers masked the per-episode
non-monotonicity above, which is what ultimately retired it. The anchor diffs and
`cleanliness_anchors.json` have been removed from package data; the full calibration log
lives on the `archive/experiments` branch.
