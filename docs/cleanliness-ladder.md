# Cleanliness ladder — the parsimony scorer

> **Status: cleanliness axis calibrated (2026-06-05)** via the
> [axis-calibration-playbook](axis-calibration-playbook.md), reusing the 55 blinded
> diffs. **The score is RELATIVE** — a diff's placement on this ladder — not an absolute
> grade. Built with the **v3 "size-invariant + decisive"** prompt (§prompt below); the
> two earlier prompt variants were rejected (one leaked size, one collapsed to ties).
>
> **Implemented (2026-06-05):** scored at runtime by [`src/haid/scoring/`](../src/haid/) via
> the same placement mechanism as difficulty; the locked `out/cleanliness_anchors.json` ladder
> ships as package data.

## What this axis measures

CLEANLINESS = **parsimony**: how little *avoidable* complexity a change carries
**relative to what its own task requires** — fewer unnecessary moving parts, less
duplication, no leftover/parallel code paths, no bloat. It is deliberately **⊥ LOC**: a
large change where every line is required is *maximally* clean; a trivial change (version
bump) is *neutral*, not vacuously clean. Orientation: **rung 0 = least clean (most
avoidable complexity), rung 10 = most clean (minimal-necessary)**.

## How a session diff gets a cleanliness score

1. Blind the diff (strip identifiers; reassemble code-files-first) — [blind.py](../calibration/blind.py).
2. **Place it on the ladder**: a cheap model (Haiku) does ~1 size-invariant pairwise
   comparison vs. each anchor rung → rung = how many anchors it is judged *cleaner than*.
3. **That rung (relative position) IS the cleanliness score**, compared against the
   reference corpus / past sessions / teammates — never an absolute number. Anchors are
   fixed, so they are **prompt-cached**; only the session diff varies.

## The locked anchor ladder (dense all-pairs order)

Order from the **dense all-pairs** comparison over the 11 anchors (110 verdicts,
counterbalanced, **96.1% consistent, 7.3% hard-flip position bias**). The `level` column
is the independent strict-curve low/mid/high classification; the two methods **agree**
(the level column is monotonic, `spearman(latent, level) = 0.868`), which is the
cross-method convergence we trust in lieu of human labels.

| rung | anchor | reference change (anonymized) | level | churn |
|---|---|---|---|---|
| 0 | U01 | Rust RAII `FlockGuard` lock-upgrade refactor that **keeps the old `AcquireLock` trait + free fn alongside** the new API → two parallel locking paths | mid | 193 |
| 1 | U16 | object-store "Phase-1" migration adding **6 unused per-instance fields cloned from globals** (speculative duplicate state, forces a hand-written `Debug`) | mid | 57 |
| 2 | U40 | `model_overrides` config + dedup'd fallback chain; **hardcoded duplicated provider strings + overlapping tests** | mid | 802 |
| 3 | U48 | Zig uninstall: relocate 5 `/etc` udev paths into an always-run sweep; **duplicated delete-loop + repetitive test scaffolding** | mid | 406 |
| 4 | U46 | docs-deploy shell script + `check-docs.sh` assertions that **re-enumerate the script's own command lines** | mid | 108 |
| 5 | U00 | add TOML language support across crates — table-driven, and **net-reduces duplication** (folds inline fixtures into a shared helper) | high | 324 |
| 6 | U43 | stress-test per-session priming barrier fixing a hook-processor race; reuses existing helpers, no parallel machinery | high | 40 |
| 7 | U22 | surface remote-plugin `mcp_servers` (replace a hardcoded `Vec::new()`); one field + a minimal deser struct | high | 55 |
| 8 | U29 | Homebrew formula version bump (version + 4 url/sha256) — trivial, **zero** avoidable complexity | high | 18 |
| 9 | U13 | `Arc<…>` copy-on-write refactor (`make_mut`, unique-owner opt) — tight, no waste | high | 45 |
| 10 | U07 | removal of `SizeCheck`/`verify_size`/cleanup-wrapper machinery — pure simplification | high | 293 |

## Orthogonality (the hard gates)

| gate | cleanliness | sibling: originality | sibling: difficulty |
|---|---|---|---|
| ⊥ difficulty (distinct axis) | **+0.029** (n=55) | +0.675 | — |
| ⊥ LOC / churn (size) | **−0.369** (n=55) | +0.336 | −0.05 |

- **Distinct axis ✓** — cleanliness is the *most* difficulty-orthogonal of the three
  axes (ρ≈0): a change can be hard-but-clean (U13) or easy-but-messy.
- **⊥ LOC** — ρ=−0.369 (n=55), essentially the accepted originality bar (0.336). The
  residual negative correlation is the **legitimate** parsimony-is-partly-economy
  component (bloat *is* extra lines), not a size proxy — the v3 prompt explicitly forbids
  judging by line count, and churn is visibly scattered across the ladder (clean end has
  both 18- and 324-churn changes; unclean end has 57 and 802). The combined score must
  still treat volume as the separate deterministic LOC term, so it does not double-count.

## The prompt (v3 — the ONLY substantive per-axis change)

Size-invariance is enforced by three rules: (1) judge **only** the ratio of avoidable to
required complexity — line/file count is *not* evidence; (2) a large fully-justified
change is maximally clean; (3) a trivial change is **neutral**, not "clean." Decisiveness
is enforced by telling the judge to **hunt for the finer avoidable-complexity
distinction** and tie *only* on genuine equivalence. See
[axis-calibration-playbook.md §3](axis-calibration-playbook.md) and the locked text in
`out/cleanliness_anchors.json` / the workflow scripts.

### Rejected prompt variants (do not repeat)

- ❌ **v1 (naive "ignore size")** — discriminated well (8% ties) but **leaked size badly**
  (ρ vs churn = **−0.55**): trivial small changes scored *vacuously* clean and large diffs
  accumulated incidental complexity. Failed the ⊥-LOC gate.
- ❌ **v2 (over-hardened, "usually answer tie")** — fixed the leak (ρ=−0.18) but **collapsed
  to 78% ties**, gutting the ladder's resolution. "Ignore size" and "prefer tie" are
  *different* instructions; conflating them destroys signal.
- ✅ **v3 (size-invariant + decisive)** — ρ vs churn −0.369, ρ vs difficulty +0.029, 5%
  ties, 100% rough-sort self-consistency, 96.1% dense consistency. Adopted.

## Honesty / known limits

- **Relative, not absolute.** The rung comes from comparison to canonical anchors; no
  absolute "cleanliness number" is ever asked of the model.
- **Corpus has no `low`-tier anchor.** All 55 units are real *merged* changes, so even the
  least-parsimonious anchor (U01) is only "mid" in absolute terms — the corpus contains no
  egregiously over-engineered code. The ladder still resolves the *relative* spread; a
  genuinely bloated session diff would place at/below rung 0.
- **Mid-range resolution is coarse** (shared with difficulty/originality): 11 rungs scatter
  in the middle. Mitigate with 2–3 placement samples averaged.
- **Extremes validated by reading:** rung 0 is a genuine retained-dual-API refactor; rung
  10 is a genuine subtractive simplification. Trust the blinded code judgment.
- This scores **parsimony only**. It feeds the combined score alongside the separate
  volume (LOC) and difficulty terms — `achievement = f(volume, difficulty, cleanliness)`
  — and never stands alone. (Originality was a fourth axis but was dropped; see
  scoring-rubric.md Axis decision.)
