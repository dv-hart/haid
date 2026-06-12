# Pilot 1 report — difficulty oracle vs. review-signal validator

**Date:** 2026-06-04. **Scope:** one axis only (**difficulty**); the originality and
cleanliness axes were *not* run yet. **TL;DR:** the oracle is sound; the validator
(PR review-effort) does not measure difficulty. The "oracle is backwards" hypothesis
is tested and **rejected**.

---

## 1. The two sides of the experiment

The experiment pits two *independent* estimates of the same thing against each other:

- **Side A — the ORACLE** (the instrument we're trying to trust): an LLM that judges
  *which of two code diffs required deeper engineering skill*. This is the thing that
  will eventually drive HAID's difficulty score. It could be confabulating, so it
  needs an external check.
- **Side B — the VALIDATOR** (the external check): objective **review-effort signals**
  already attached to each merged PR — how many reviewers, how long to merge, how many
  commits/iterations, changes-requested rounds, inline review comments. The premise
  (rubric §4b): *harder code should attract more review effort*, so if the oracle is
  measuring real difficulty, its ranking should correlate with these signals.

**The H5 question:** does the oracle's difficulty ranking correlate with review-effort?
If yes → the oracle is externally grounded. If no → one of the two sides is wrong.

---

## 2. What each side actually did

### Side A — the oracle (mechanics)
1. Took 25 merged PRs from established, genuinely-reviewed repos (dfinity/ic,
   rust-lang/crates.io, k8s/minikube, pnpm, seaweedfs, openai/codex, flux2, karpenter,
   slatedb, warp, eleventy, …).
2. Filtered to **code-substantive** PRs (dropped doc/RFC-only ones — a code-difficulty
   judge must see code) and **blinded** each diff (stripped owner/repo/URLs/emails;
   verified 0/25 own-identity leaks) and reassembled it **code-files-first** (diffs are
   alphabetical, so docs would otherwise eat the length budget).
3. Ran **75 pairwise comparisons** (each unit vs ~6 others), each by an independent
   **Opus** subagent answering one question: *"which change could a smaller fraction of
   working engineers have produced correctly? Ignore size."* Structured verdict.
4. Fit a **Bradley-Terry** model over the 75 verdicts → a latent difficulty score per
   unit (the ranking in §4).

### Side B — the validator (mechanics)
For each of the same 25 PRs, mined from the GitHub API (no LLM): `num_reviewers`,
`time_to_merge_hours`, `commits`, `changes_requested`, `review_comments`. Combined
into a rank-sum **composite review-effort** score. These are pure process metadata —
the oracle never sees them.

---

## 3. Result A — the oracle is internally sound

- **Self-consistency: 100%** — the fitted ranking contradicts none of the 75
  comparisons. *(Tempered: a sparse 75-edge graph over 25 nodes is easy to linearize;
  this shows no self-contradiction, not high quality on its own.)*
- **Decoupled from size: ρ(oracle, churn) = −0.05** — the oracle did **not** just rank
  by diff size. This was the hard design goal ("ignore volume"), and it worked. A
  1,427-line PR (U19, mostly boilerplate) is ranked *easiest*; a 45-line concurrency
  change (U13) is near the *top*.
- **Agrees (weakly, positively) with the independent prior: ρ = +0.15** — the crude
  language/topic difficulty prior and the oracle point the *same* direction.
- **Reasoning is human-auditable and correct on inspection.** Example: it ranked a
  borrow-checker-as-protocol concurrency change (RAII flock guard using
  `PhantomData<&mut ()>` to statically enforce LIFO lock nesting, with SH→EX
  upgrade/downgrade revert-on-drop) **above** mechanical TOML-support plumbing — which
  is unambiguously the right call.

## 4. Result B — the validator does not track difficulty

The oracle ranking vs. each review signal (Spearman ρ, n=25):

| review signal | ρ with oracle difficulty |
|---|---|
| composite review-effort | **−0.26** |
| commits | **−0.43** |
| review_comments | −0.28 |
| time_to_merge | −0.17 |
| num_reviewers | −0.16 |
| changes_requested | −0.09 |

Every signal is **negative** — more oracle-difficulty associates with *less* review
effort, the opposite of the H5 premise.

---

## 5. Your question: is the oracle so wrong that its inverse is right?

A fair worry: a −0.26 correlation means flipping the oracle gives +0.26 — so maybe the
oracle is backwards and review-effort is fine. **Three independent checks say no:**

1. **The oracle-independent prior anti-correlates *too* — more strongly.**
   `ρ(difficulty_prior, review-effort) = −0.43`. The prior is computed purely from
   language + topic keywords, with **zero input from the oracle**. Two independent
   difficulty estimates both point away from review-effort. If the oracle were merely
   inverted, the prior wouldn't independently agree with it.
2. **Flipping doesn't rescue it.** −0.26 inverted is +0.26 — still weak — *and* it would
   put the oracle in **disagreement with the prior** (which it currently agrees with,
   +0.15). You can't flip the oracle into agreement with both review-effort and the
   prior; they conflict.
3. **The inversion mechanism is visible per-unit and is about process, not error:**

| unit | oracle | what it is | review-effort | why they diverge |
|---|---|---|---|---|
| U01 anylinuxfs#148 | **hardest** | RAII flock concurrency guard | 2 revs, 0.8h merge | hard work, fast solo-expert merge → *low* process |
| U05 crates.io#13842 | near-easiest | routine web-app change | **3 revs, 7 commits** | trivial change, heavy org process → *high* process |
| U11 eleventy#4247 | easy (low) | small change | **ttm 1647h** | trivial PR languished in review queue |
| U19 openhuman#3339 | **easiest** | 1,427-line boilerplate | 15 commits | huge but easy; review-effort tracked the *size*, oracle didn't |

## 6. What's actually going on

**Review-effort measures size + organizational process, not intrinsic difficulty.**
The data shows it directly:
- `ρ(commits, churn) = +0.45` — commits track **size**, not difficulty.
- `ρ(oracle, churn) = −0.05` — the oracle is **orthogonal to size** by design.
- Reviewer counts and merge latency are set by CODEOWNERS rules, queue depth, and
  contributor seniority — none of which is code difficulty.

So the two sides measure genuinely different things, and the slight *negative* arises
because hard changes here tend to be small/expert-authored (low process) while heavy-
process PRs tend to be large/routine. **The validator failed; the oracle didn't.**

## 7. What this does and doesn't tell us

**Establishes:** the difficulty oracle is internally coherent, size-decoupled, and
face-valid; **PR review-effort is not a usable ground truth for the difficulty axis**
(rubric §4b's H5 is falsified for difficulty).

**Does NOT establish:** that the oracle is *correct* in an absolute sense — we've shown
it's coherent and not size-driven and not inverted, but we have replaced the planned
external check, so validity is still only supported by face-inspection + the weak prior
agreement. That gap is the whole subject of "next steps."

**Caveats:** difficulty axis only; n=25; k=3 → **0 counterbalanced pairs** (position
bias unmeasured, though it would attenuate toward 0, not create the systematic
negative); 12/25 diffs truncated at 16k chars; the validator set skews high-difficulty
(range restriction weakens all correlations).

## 8. Decision space for next steps

We still need two distinct things for the difficulty oracle:
- **Validity** (does it measure *real* difficulty?): a **known-difficulty anchor set**
  (curated trivial→research reference diffs the oracle must order correctly), or a
  small **human gold-set**.
- **Reliability** (is it *reproducible*?): **judge replication** — a second independent
  strong judge (e.g. Sonnet) ranks the same units; high agreement = a stable construct,
  and it adds the counterbalancing this pilot lacked.

Separately: review-effort isn't useless — re-test whether it validates a *different*
axis (review burden / cleanliness / contentiousness) rather than difficulty.
