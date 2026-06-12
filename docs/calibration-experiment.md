# Calibration experiment — tuning the achievement scorer

> ⚠️ **PARTLY SUPERSEDED — read this first.** This doc is the *pre-registration +
> experiment history*. Several early sections were **falsified or dropped** by the
> pilots: §4b (mined review-signals validate difficulty) is **falsified** (§12); §4c
> tiering-as-score **leaked size** and absolute SEH grounding was **dropped** (§13).
> For the **validated, current approach and how to replicate it for a new axis**, use
> **[axis-calibration-playbook.md](axis-calibration-playbook.md)** (canonical) and
> **[difficulty-ladder.md](difficulty-ladder.md)** (worked example). Keep §1–3, §5–6,
> §12–13 here for context; treat §4b/§4c/§7-SEH as superseded.
>
> **Status: design-stage / pre-registration.** The scientific protocol behind the
> [scoring-rubric](scoring-rubric.md) §Calibration — the achievement number must be
> *defended by a measurement*. Read [trust-discipline.md](trust-discipline.md)
> alongside it.

## 1. What we are validating

The rubric claims it can estimate **achievement = Volume × Difficulty in
senior-engineer-hours (SEH)** for a session-sized diff, *absolutely* (anchored to
real code), by comparing the diff to a calibrated corpus. This experiment tests
whether that estimate is real:

- **Does it rank-order?** Do harder/larger diffs score higher than easier/smaller
  ones, agreeing with ground truth?
- **Which method earns its keep?** Quantitative, best-practice, and qualitative
  passes each cost something (Halstead tooling, Haiku tokens, latency). We measure
  the **marginal lift** of each so we ship only the parts that pay for themselves.

We do *not* claim a precise hour count is correct — only that the ordering and the
rough magnitude track reality well enough to drive coaching.

## 2. Hypotheses (pre-registered)

State these before tuning so we cannot rationalize after the fact.

- **H1 — combination wins.** A quant+qual blend rank-orders SEH better than either
  alone. *(The project's working hypothesis.)*
- **H2 — qual adds non-redundant lift.** The Haiku idea-level judgment improves
  difficulty ranking *beyond* what Halstead/cyclomatic already capture. If it does
  not, it is cost without value — cut it.
- **H3 — pairwise beats absolute.** Asking a model "which of these two diffs took
  deeper insight?" rank-orders better than asking it to rate one diff 1–10.
- **H5 — the oracle is trustworthy.** The Opus pairwise ordering (§4) correlates with
  the *independent* mined review signals (review rounds, reverts, reviewer count). If
  it does **not**, the oracle is unreliable and must not be baked into the SEH scale —
  catching that here, before shipping, is the whole point of the cross-check.
- **H4 — effort ≠ adoption.** Per-item SEH and popularity correlate weakly; their
  *divergence* is a real signal (leverage), not noise. *(Falsifies the original
  "stars = good" assumption if confirmed.)*

Any hypothesis can fail — a failed H2 is a *useful* result that makes HAID cheaper.

## 3. Unit and corpus

### 3a. Unit = a bounded change-set
HAID scores a session-sized diff. The OSS analog is a **merged PR** where one exists;
for solo projects that push straight to `main`, the unit is a **coherent commit
cluster** (one feature's worth of commits) or a release delta. What the oracle and
rubric score is always the same thing — a bounded diff. Only the *available validation
metadata* differs by source (§3c).

**Size.** Target **~150–300 units**, not 20–30 — small-N overfits a multi-parameter
scorer badly, and units are cheap to harvest. Coverage of the difficulty×volume plane
matters more than raw count (§6 — anchors, not fitted weights).

### 3b. Two populations, on purpose (transfer of trust)
The honesty check (§4b) only exists where humans reviewed: team PRs. Solo devs commit
to `main` with no reviewers, no review rounds — yet the **small elegant single-dev
gem** and the **10k-line AI-bloated tangle** are exactly the originality/cleanliness
extremes the rubric must learn. So we sample two populations and connect them:

- **Team-reviewed PRs** — rich mined-review signals → this is where we **validate the
  oracle** (H5).
- **Solo / small projects** — thin or no review signals, but they fill the plane's
  corners the rubric most needs.

**Transfer of trust:** establish the oracle's trustworthiness on the team population
where there *is* an external check, then apply the now-trusted instrument to the solo
population where there isn't. We never pretend solo projects carry review ground truth
— we extend a validated tool. Bonus synergy: small + recent + obscure is also the
**least training-contaminated** population, so the units we most want for coverage are
the best for blinding (§5).

### 3c. Discovery — deliberately off the popularity axis
Sorting GitHub by stars finds the same famous repos and misses the gems. Use channels
that surface quality *independent of virality*:

- **Star-bucket stratified search** — GitHub Search API, `created:` last 3–6 months,
  sampled *across* star tiers `[0–10] [10–50] [50–200] [200–1k] [1k+]` and across
  languages — **not** stars-descending. Deliberately oversample the long tail.
- **Show HN** (last 3 months) — solo devs showing their own work; the comment thread
  is a human quality signal orthogonal to stars. Harvest the repo links.
- **GitHub Trending** (daily/weekly) — rising-before-famous catches small projects early.
- **Package registries** (crates.io / PyPI / npm) — recent first-publish + nonzero
  download velocity but low stars → "boring useful" libs that never went viral.
  Downloads ≠ stars.
- **Curated human-judgment feeds** — language newsletters (This Week in Rust, Python
  Weekly), lobste.rs, recent awesome-list additions.

### 3d. The "genuinely good" filter (not stars)
Competence signals that mature in days and don't track virality: has tests + CI + a
real README + LICENSE; commit history shows *iteration*, not a single dump; maintainer
responsiveness (issue/PR latency); for libraries, real downloads/dependents. These
gate the difficulty/originality pools; popularity stays a coarse filter only (§4d).

### 3e. Cover the plane — and break the confounds
Fill every cell:

| | low volume | high volume |
|---|---|---|
| **low difficulty** | config/glue | **the 10k-line AI tangle** (bloat) |
| **mid** | typical CRUD endpoint | feature across many files |
| **high difficulty** | 30-line lock-free / parser fix | component library, allocator |

Place units with **cheap proxies** (language + topic tags + imported primitives for
difficulty; LOC for volume) — coverage, not precision; the oracle produces the real
ranking. Then actively break two confounds:

- **Domain/language ↔ difficulty.** If every hard unit is Rust and every easy one JS,
  the rubric learns "Rust = hard." Span languages *within* each difficulty tier (easy
  Rust, hard Python).
- **Volume ↔ difficulty collinearity.** Deliberately fill the **off-diagonal** —
  high-difficulty/low-volume (a 30-line lock-free fix) and low-difficulty/high-volume
  (the bloat tangle). If volume and difficulty correlate *in the corpus*, we can never
  separate them, defeating "never collapse."
- **Negative cleanliness anchors.** Include a few known-bloated / AI-generated repos as
  the cleanliness floor, curated *separately* so they don't pollute the "genuinely
  good" difficulty/originality pools.

**Recency knob.** 3 months is ideal for anti-contamination but starves review history.
Since **blinding (§5) is the primary contamination control**, relax to ~6 months for
the *team-validation* population to get richer review signals — accepting marginally
higher, blinding-mitigated, contamination risk.

## 4. Ground truth — no human labeling; the data already carries it

We have no panel of senior reviewers, and we do not need to hire one: **every merged
PR already contains its expert review.** Ground truth comes from two sources that
**validate each other** — a dense LLM oracle and the sparse human-review signals
already in the data. Neither alone; the second keeps the first honest.

### 4a. Dense signal — the Opus pairwise oracle
A model is unreliable at "rate this in a vacuum" and reliable at "which of these two
is harder." So we **don't ask for absolute scores and we don't sort by comparison.**

- **Sample pairs** of diffs and ask **Opus** the §4c question — one campaign per axis
  (difficulty / originality / cleanliness), never a single "which is better?".
- **Fit a Bradley-Terry / Elo model** over the (noisy, possibly non-transitive)
  pairwise outcomes → a **continuous latent score per PR**. This is robust to the bad
  individual calls a comparison sort would propagate into garbage, and it yields a
  *dense regression target* over hundreds of PRs — strictly better than sparse tier
  labels.
- **Adaptive pairing** (compare items with close current estimates, Elo-matchmaking
  style) gets a stable ranking in ~O(n·k) comparisons, not O(n²).
- **Counterbalance every pair** — present (A,B) and (B,A); keep only stable verdicts,
  treat flips as ties. Kills position bias and length bias, the two dominant
  LLM-judge confounds.
- Opus is the **offline oracle** (run once, expense is fine); the *shipped* scorer is
  the cheap deterministic + Haiku rubric being tuned to reproduce it.

### 4b. The honesty check — mine the review, don't recreate it
The oracle's risk is **circularity**: tune the rubric to match Opus and we've
*distilled Opus*, inheriting its blind spots — not anchored to reality. The external
check is the human review **already attached to each PR**, mined automatically:

- review rounds / "changes requested" cycles, # reviewers, review-comment depth,
  time-to-merge, tests added, CI status, and **later reverts**.

**Validate the oracle against these (H5).** If the Opus ordering correlates with the
independent review-effort signals → trust it as the dense training target. If it
**diverges** → we caught an unreliable oracle *before* baking it into the SEH scale,
and the qual track pauses until the construct is sharpened. (Optional, later: a tiny
human gold-set for a final spot-check — never a prerequisite.)

### 4c. Three axes, never collapsed
Run a **separate** pairwise campaign per axis. Each is a distinct construct, invisible
to the deterministic layer, and consumed by a different part of the rubric. **Each
prompt is framed to target the residual the deterministic layer can't see** — asking
"which is more complex?" just pays Opus to reproduce Halstead.

| Axis | Comparison prompt (blinded diffs) | Feeds | Why it's its own axis |
|---|---|---|---|
| **Difficulty** (skill-rarity) | *"Which change could a smaller fraction of working engineers have produced correctly?"* | SEH / achievement multiplier | isolates expertise; strips volume (a 2k-line boilerplate PR is writable by anyone) |
| **Originality** (necessity) — ⛔ **DROPPED 2026-06-05** (calibrated, but saturates + ρ=+0.68 vs difficulty + no resolution in the recombination space; see scoring-rubric.md "Axis decision"). Kept here as the original plan of record. | *"Which solves a problem that genuinely lacked an off-the-shelf solution, vs. reassembling patterns a library or standard idiom already provides?"* | ~~originality discount~~ → now a coaching-only reinvention discount | a hand-rolled quicksort is high-difficulty, **low-originality** → one axis can't hold both |
| **Cleanliness** (parsimony) | *"Which achieves its purpose with less unnecessary complexity — fewer moving parts, less duplication, no bloat — relative to what the task actually requires?"* | waste / quality passes | the **counterweight to volume**: penalizes the 10k-line tangle that a 3k solution would cover; rewards elegance |

**Cleanliness is parsimony, not lint-style.** Framing it as "minimal necessary
complexity for the task" is what makes a bloated AI-generated tangle *lose* to a tight
solution — raw volume is rewarded in achievement, so cleanliness is the axis that stops
bloat from gaming it. It stays bound to the **waste passes and never touches the SEH
number** (trust-discipline §3): a clean trivial PR must not out-achieve a messy
brilliant one. In the final value verdict, a strongly-negative cleanliness result
**discounts** high raw achievement — that is how the 10k tangle gets "scored properly."

**Do NOT sort on:** *volume* (computed deterministically — surviving LOC by kind,
Halstead volume); *"which PR is better overall"* (the collapsed axis — forbidden);
*value/usefulness* (not visible in a blinded diff — that's the adoption signal, §4d);
*correctness* (assumed for a merged PR; gated deterministically for a session diff).

### 4d. Popularity — secondary, weighted (not discarded)
Stars/adoption stay as a cross-check, used two ways:
1. the **sampling filter** (§3) — draw from competent repos;
2. the **divergence study** (§7): where SEH and adoption disagree is the *leverage*
   the value equation prizes (high adoption per SEH = great leverage; high SEH, no
   adoption = effort that didn't land). We **log the divergence; never collapse it.**

## 5. Contamination control — blind the judge

The real control is **blinding**, not recency: strip repo name, README, author,
package name, and any in-code comment that names the project, before the LLM sees the
diff. This prevents "oh, this is famous-lib X" pattern-matching, which recency alone
does not. Recency (§3) is the cheap second layer. Deterministic passes (Halstead,
cyclomatic) are contamination-immune and need no blinding.

## 6. Methods under test (the ablation)

Each method is scored standalone and in combination, so we can read each one's
marginal contribution.

| ID | Method | Kind | Cost |
|---|---|---|---|
| **M1** | Volume + complexity backbone (Halstead, cyclomatic, cognitive, surviving-LOC by kind) | deterministic | tooling only |
| **M2** | Best-practice / specialization scan (imports & primitives: torch/crypto/atomics/parser ⇒ specialized) | deterministic-ish | cheap |
| **M3** | Haiku condition-checks ("are tests present? is this a known-solved problem reimplemented? does it handle errors?") | LLM, structured | Haiku tokens |
| **M4** | Haiku **pairwise** idea-level ranking → Bradley-Terry/Elo over the corpus | LLM, structured | Haiku tokens ×pairs |

**Combinations evaluated:** M1 · M1+M2 · M1+M2+M3 · M1+M2+M3+M4 (and M4-only as a
sanity floor). H2/H3 are read directly off this table's deltas.

**M4 is the teacher→student test.** The §4a oracle uses *Opus* pairwise; M4 asks
whether *Haiku* (the shippable tier) reproduces that ordering cheaply. Its ceiling is
oracle agreement; M1's deterministic backbone is the part that can, in principle,
carry signal the oracle's process cross-check (§4b) confirms is real. Use the same
hygiene as the oracle — counterbalanced pairs, distinct lenses, aggregate.

## 7. Evaluation protocol

- **Split.** Tune set vs. **held-out** test set. At this N use **leave-one-out /
  k-fold cross-validation**; report test-set numbers, never tune-set.
- **Keep free parameters few.** Prefer the **anchor (k-NN-over-the-plane) approach to
  a fitted regression**: production scoring places a diff between its nearest
  calibrated anchors, which needs plane *coverage*, not many tuned weights — far more
  robust at N in the low hundreds. Fitted weights, if any, stay in single digits.
- **Pre-registered metrics:**
  - Difficulty: **Spearman / Kendall-τ** between the rubric's ordering and the
    **Opus-oracle latent ranking** (§4a) — the dense target.
  - Oracle trust (H5): **Spearman** between the oracle ranking and the **independent
    mined review signals** (§4b). This is the reliability ceiling — the rubric can't
    be trusted past the oracle, and the oracle can't be trusted past this number.
  - Marginal lift: Δ(metric) when each method is added — the H2/H3 test.
- **Success thresholds (set now, before seeing results):** e.g. ship-worthy if
  test-set Spearman ≥ 0.6 against the oracle *and* the combined model beats M1-alone
  by a margin exceeding its cross-validation noise. Gate the whole qual track on H5:
  if oracle↔review correlation is weak, the oracle is not a usable ground truth and
  we do not ship an SEH scale built on it (trust-discipline: no confident wrong
  number).

## 8. The divergence study (H4 / leverage)

Plot per-PR SEH (our estimate) vs. adoption (stars/forks/dependents, age-normalized).
Weak correlation **confirms** that popularity is the wrong SEH label and that the
*residual* is meaningful: it is the empirical handle on "leverage" that HAID's
`value = achievement ÷ cost` framing assumes exists. This is the one place
popularity earns its weighted keep.

## 9. Threats to validity (and mitigations)

| Threat | Mitigation |
|---|---|
| **Circularity / distillation** — tuning to Opus just clones Opus's blind spots | the independent mined-review cross-check (§4b/H5) is the external anchor; gate shipping on it |
| **Construct drift** — review metrics measure review burden, not pure effort | use them only to *validate the oracle's ordering*, not as the SEH scale directly; report disagreement |
| **Judge bias** — position & length bias, non-transitive pairwise calls | counterbalanced (A,B)/(B,A) pairs, flips→ties; Bradley-Terry absorbs non-transitivity (never a comparison sort) |
| **Small-N overfit** | hundreds of PRs, CV, anchors-not-weights, single-digit free params |
| **PR ≠ session** — PRs include human review back-and-forth a Claude diff lacks | calibrate on the *artifact's* intrinsic complexity; treat process-metadata as a difficulty *prior*, hedged |
| **Judge contamination** | blinding (§5) + recency; deterministic passes as a contamination-free floor |
| **Stratification gaps** | the §3 grid is filled deliberately; **log empty cells** (no silent caps — trust-discipline §5) |

## 10. What ships vs. what stays in the lab

- **Ships:** the tuned scorer (weights/prompts) + the **anchor set** (a compact set
  of labeled reference diffs spanning the plane) it compares against in production.
- **Stays:** the full 150–300-unit corpus and the experiment harness — instruments,
  not payload. Re-run them when a new model tier or language is added; calibration
  is not one-and-done because the format and the judge model both drift.

## 11. Minimal first cut (before the full corpus)

De-risk cheaply before harvesting hundreds of PRs. ~20 PRs hand-stratified across the
plane; all-pairs is only ~190 comparisons (×2 for counterbalancing) — trivially cheap.
1. **Run the Opus pairwise oracle** on the 20 → Bradley-Terry latent ranking.
2. **H5 check:** does that ranking correlate with the mined review signals (rounds,
   reviewers, reverts)? *If not, stop here* — the oracle isn't a usable ground truth
   and the whole qual track needs rethinking before any harvesting spend.
3. If H5 holds, run **M1 alone** and **M1+M4** → is there *any* deterministic signal,
   and does Haiku pairwise reproduce the Opus oracle? (A fast read on H1/H2/H3.)
4. Only if the pilot shows signal, scale to the full corpus and the §6 ablation.

If H5 is weak or M1 already saturates the metric, that reshapes the plan before we
spend the harvesting budget — which is the point of running it first.

## 12. Pilot 1 results (2026-06-04) — H5 FAILED, but the oracle did not

**Setup.** 25 code-substantive, review-rich merged-PR units from established repos
(dfinity/ic, rust-lang/crates.io, k8s/minikube, pnpm, seaweedfs, openai/codex, flux2,
karpenter, …), blinded + reassembled code-files-first, capped 16k chars (12/25
truncated). 75 **Opus** pairwise difficulty comparisons (k=3 offset schedule, balanced
A/B slots), regularized Bradley-Terry fit. Harness: `calibration/{blind,pulls,pass2,
bt_h5}.py` + a Workflow fan-out (one Opus subagent per comparison).

**Result — the oracle is sound, the validator is not:**
- Oracle **self-consistency 100%** (no contradicted comparisons; tempered: a sparse
  75-edge graph linearizes easily).
- Oracle **vs. churn ρ = −0.05** → difficulty cleanly **decoupled from size** (the
  "ignore volume" instruction worked — the hard part of the design).
- **H5: oracle vs. review-effort is mildly NEGATIVE** — composite ρ = −0.26, commits
  −0.43, time-to-merge −0.17, reviewers −0.16. Review signals do **not** track
  difficulty; they may invert it.

**Interpretation.** The oracle's reasoning is coherent and human-auditable (it
separates borrow-checker-as-protocol concurrency and distributed fail-open/closed
invariants from mechanical plumbing). The failure is in the **validator**: on mature
repos, PR review-effort is dominated by org/process factors (CODEOWNERS counts, review
queue latency, contributor seniority) — hard changes by senior maintainers merge fast
in few commits; trivial PRs languish. So **review-process metadata is not a usable
ground truth for the *difficulty* axis.** (It may still validate a different axis —
review *burden*/contentiousness, or cleanliness — TBD.)

**Caveats.** n=25, k=3 → **0 counterbalanced pairs**, so position bias is unmeasured
(but it would attenuate toward 0, not manufacture a systematic negative); 12/25 diffs
truncated; validator set skews high-difficulty (range restriction).

**Decision.** §4b's "mined review signals validate the difficulty oracle" (H5) is
**falsified for difficulty.** The circularity escape must change. Next: (a) **judge
replication** — a second *independent* strong judge pairwise-ranks the same units;
high oracle↔oracle Spearman = a reliable, reproducible construct (cheap, no humans);
(b) **known-difficulty anchors** — a few unambiguous diffs (leftpad-trivial → known
allocator/consensus) the oracle must order correctly (face validity); (c) defer the
small human gold-set. Review-signal validation is demoted from the difficulty gate;
re-test it against the cleanliness/originality axes instead.

## 13. Pilot 2 results (2026-06-04) — the anchor-ladder mechanism works (ρ=0.87)

**Reframe.** Production scores a Claude *session* diff — it has no reviewers/stars/
author signal. None of the calibration proxies exist at scoring time. So scoring must
be **anchored relative placement**: compare the diff to a fixed ladder of reference
diffs (rubric §Calibration). Pilot 2 tests that mechanism, and whether cheap **Haiku**
can run it. Also fixes Pilot 1's population error: HAID's users write **small/personal
projects**, so the ladder must span beginner→expert, not elite-repo-only.

**Setup.** Built a commit-based extractor (`pass2.py --mode commit`) since solo repos
have no PRs (§3a). Assembled a **55-unit full-spectrum ladder set**: 25 elite PRs + 30
personal-project commits (hobby AI tools/CLIs/beginner code), prior-balanced. Blinded,
code-first. **Opus full-sort** (165 pairwise, k=3) → Bradley-Terry ranking, 100%
consistent. Selected **9 anchor rungs** at even BT percentiles (`ladder.py select`).
Then **Haiku placed each of the 46 holdouts** against the 9 rungs (414 comparisons).

**Result:** **Spearman(Haiku anchored rung, Opus full-sort score) = +0.866.** Cheap
Haiku, using only the ladder, reproduces the expensive Opus sort. Extremes are nailed
(hardest holdouts → rung 7–9, easiest → 0–1); the **middle scatters** (coarse 9-rung
resolution). Personal projects span the *whole* range — the oracle discriminates
difficulty *within* the personal-project population (e.g. keyword-"low" `padctl` rated
genuinely hard by both Opus and Haiku), not by repo pedigree.

**Implication — production architecture (validated):** a fixed, **prompt-cached** anchor
ladder per axis + **Haiku** runtime placement (~9 cached comparisons/diff) → a faithful
difficulty score, no mined labels or Opus at runtime. This *is* the scorer and the
self-validation harness in one.

**Operational note — Workflow rate limits.** A single Opus fan-out caps ~80–83
comparisons (~2M tokens) before throttling makes agents fail their structured-output
call; the 165-pair sort needed a **resume** (cached completions + re-run failures) to
finish. Batch large Opus sorts or expect a resume. Haiku's 414-pair run completed in one.

**Caveats / refinements.** Extreme anchors are the least-constrained BT estimates and
showed it — the top anchor (a personal `codex-app-transfer` commit) was over-rated;
3 holdouts beat *all* anchors, confirming it isn't really hardest. → **hand-validate or
canonically-seat the extreme rungs**; add **finer mid-range resolution** or **2–3 Haiku
samples per placement** to denoise the middle. Difficulty axis only at the time of this
pilot; **cleanliness was since calibrated and originality calibrated then dropped**
(scoring-rubric.md "Axis decision"). The production scorer built on these ladders now
lives in `src/haid/scoring/` (2026-06-05).
