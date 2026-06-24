# Difficulty ladder — the production scorer

> **Status: difficulty mechanism validated (2026-06-04).** Haiku-on-ladder reproduces
> the Opus sort at ρ=0.87 (calibration experiment log §13, archived on the
> `archive/experiments` branch).
> **The score is RELATIVE** — a diff's placement on the ladder — **not an absolute tier
> or SEH** (absolute grounding was dropped; tiering leaked size). The tier scale below
> is the **convergence cross-check** and an optional coarse *rendering*, never the score.
>
> **Implemented (2026-06-05):** this scorer is built in [`src/haid/scoring/`](../src/haid/)
> (`placement.py` places a diff against the ladder; the host agent provides the comparisons).
> The locked order is the canonical `out/difficulty_anchors.json` (fit from the dense
> all-pairs verdicts by `calibration/build_difficulty_anchors.py`) and is shipped as package
> data. **Note:** the older `out/ladder_anchors.json` is the *stale sparse sort* (it mis-ranked
> U13 to rung 7) — superseded; do not use it.

## How a session diff gets a difficulty score

1. Blind the diff (strip identifiers; reassemble code-files-first) — see
   [blind.py](../calibration/blind.py).
2. **Place it on the ladder**: a cheap model (Haiku) does ~1 pairwise comparison vs.
   each anchor rung → the diff's **rung = how many rungs it is judged harder than**.
3. **That rung (relative position) IS the difficulty score.** It is compared against
   the reference corpus / your past sessions / teammates (opt-in) — not converted to an
   absolute SEH number. A coarse tier *label* may be shown for readability (below), but
   it is a rendering, not the load-bearing value.

The anchor diffs are fixed, so they are **prompt-cached**; only the session diff
varies. No mined labels, no review signals, no Opus at runtime.

## The tier scale — convergence cross-check + optional rendering (NOT the score)

This coarse scale is used two ways: (a) the independent low/mid/high-style
classification whose agreement with the dense pairwise order *validates* the ladder
(cross-method convergence), and (b) an optional human-readable label. **It is not the
score** — absolute tiering correlated with size (ρ tier-vs-LOC = +0.39 vs the pairwise
oracle's −0.05), so it can't carry a value that must stay orthogonal to volume. The SEH
column is **illustrative order-of-magnitude only**, retained for intuition, never emitted
as a number.

| tier | name | what it looks like | SEH (illustrative only) |
|---|---|---|---|
| **T0** | Trivial / mechanical | version bumps, hash/lockfile updates, generated files, pure renames, formatting | ~0–0.2 |
| **T1** | Junior | straightforward config/glue, simple CRUD, copy an existing pattern, one-line bugfix | ~0.2–1 |
| **T2** | Mid | normal feature with real but routine logic, some edge cases, standard-library use | ~1–4 |
| **T3** | Senior | non-trivial logic, domain knowledge, careful edge-case/error handling, system interactions | ~4–12 |
| **T4** | Expert | concurrency, parsers, numerics, crash-safety/durability invariants, subtle protocol/lifetime reasoning | ~12–40 |
| **T5** | Research / elite | novel algorithms/data structures, deep specialization, original problem-solving | ~40+ |

**Corpus ceiling caveat:** the calibration corpus (personal projects + OSS PRs) tops
out at **T4 (expert systems work)**; it contains little-to-no T5 research-tier code.
Appropriate for HAID's users (everyday coders rarely produce research-tier diffs in a
session).

## The locked anchor ladder (dense all-pairs order)

Order from the **dense all-pairs** comparison (36 pairs, counterbalanced, 100%
consistent, **0% position bias**) — not the original sparse k=3 sort, which mis-ranked
the middle (see §convergence below). Tiers from the independent tiering pass. The two
methods **agree** (the tier column is monotonic), which is the cross-method convergence
we trust in lieu of human labels.

| rung | anchor | reference change (anonymized class) | tier |
|---|---|---|---|
| 0 | U37 | Homebrew formula version + sha256 bump | **T0** |
| 1 | U39 | one-line model-family guard (`!HasPrefix("kimi-")`) on cache_control | **T1** |
| 2 | U19 | React/TS MCP enable-disable toggle + status/i18n | **T2** |
| 3 | U11 | eleventy: TypeScript data-file support (ESM/CJS detection, ordering) | **T2** |
| 4 | U24 | ACP `customNotifications` capability flag threaded through call sites | **T2** |
| 5 | U13 | warp `Arc<…>` copy-on-write refactor (`make_mut`, unique-owner opt) | **T2** |
| 6 | U10 | karpenter capacity-reservation stale-cache drift fix | **T3** |
| 7 | U18 | OS-keychain master-key fail-safe (NoEntry-vs-denied secret-loss bug) | **T3** |
| 8 | U50 | crash-safe SQLite migration/VACUUM 3-state retry state machine | **T4** |

**Convergence / the k=3 correction.** The original sparse sort put U13 (Arc COW) at
rung 7 — *above* senior bug-fixes — a transitivity artifact of k=3 (it never met the
hard cluster). Dense all-pairs dropped it to its true position 5; the result now agrees
with the absolute tiering. Lesson: **don't sparse-sort many units; densely all-pairs a
small anchor set, then place everything against it.** Difficulty is **relative** (no
absolute SEH) and must stay **orthogonal to LOC** (pairwise oracle ρ vs size = −0.05;
tiering leaked size at +0.39, so tiering is a cross-check, not the score).

## Honesty / known limits

- **Relative-then-anchored, not absolute-from-thin-air.** The tier comes from
  comparison to described canonical levels, which LLMs do reliably; the raw SEH
  number is never asked of the model.
- **Mid-range resolution is coarse** (§13): 9 rungs scatter in the middle. Mitigations
  to apply: more mid rungs, and 2–3 placement samples averaged.
- **Extremes validated by reading** (not just BT): rung 0 is a genuine version bump;
  rung 8 is a genuine crash-safety state machine. Trust the blinded code judgment over
  surface cues (the rung-8 repo *name* looked trivial; the code is expert-level).
- This scores the **what** (achievement). It never stands alone — the process/waste
  passes correct it ([trust-discipline.md](trust-discipline.md) §3).
