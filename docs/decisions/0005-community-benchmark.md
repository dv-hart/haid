# ADR-0005: Opt-in community benchmark (self-reported leaderboard)

**Status:** Accepted (2026-06-05). v1 scope locked; the verified tier is explicitly
deferred (see Consequences). Belongs to scoring/packaging (roadmap Phase 5); not
needed for the MVP.

## Context

HAID scores a session's *achievement* by **relative placement against a fixed
reference ladder** ([scoring-rubric.md](../scoring-rubric.md),
[difficulty-ladder.md](../difficulty-ladder.md),
[cleanliness-ladder.md](../cleanliness-ladder.md)). A relative score invites an
obvious question — *relative to whom?* So far the answer has been "yourself, over
time," because a cross-user leaderboard was treated as a **non-goal**: it appears to
break the **local-only default** and risks an involuntary ranking of users.

We are now **promoting** an opt-in community benchmark to add value: let people see
where their achievement scores land against the community, and (if they choose)
submit and appear on a public board. The hard constraint that kept this demoted is
real and must be designed around: **a score computed on the user's machine and
submitted as a summary cannot be made trustless.** The user controls the binary,
the runtime, and the network; an output signature proves integrity-in-transit and
identity, *not* honest computation. (Classic "never trust the client.") So the goal
is **trust-but-verify with low stakes**, never trustless — and saying so plainly is
itself a requirement of the project's [trust discipline](../trust-discipline.md).

How comparable systems handle this (the design lessons we drew on):

- **SWE-bench / HF Open LLM Leaderboard** — submitter sends inputs, *the server
  recomputes*. Strongest trust, but impossible here: the input is a private
  transcript and we refuse to make logs leave the machine by default.
- **MLPerf** — published rules + a review period + "reviewed vs unreviewed" tiers.
  → adopt **self-reported vs verified** tiers and a versioned ruleset.
- **Speedrun.com / esports** — most runs trusted; *records* get verified;
  pending → verified. → our escalation path.
- **Sigstore Rekor / Certificate Transparency** — public **append-only signed
  log**, git/Merkle history is the tamper-evident audit trail. → our hosting model
  (submissions as validated PRs; git history *is* the log).
- **Folding@home / BOINC** — redundant recompute across peers. → inapplicable; every
  transcript is private and unique, no peer can recompute it.

## Decision

Ship a **small, opt-in, self-reported community benchmark**, default **off**.

**v1 — self-reported only (the whole MVP of this feature):**

1. **Viewing requires nothing.** The server publishes a public **distribution**
   (percentile curve / histogram) per anchor-ladder version. The tool downloads it
   and shows *"you're at the Nth percentile"* **client-side** — no account, no
   submission, nothing uploaded.
2. **Submission is a separate, optional act, and submits only a signed summary —
   never logs.** Payload: per-axis relative positions + combined value + aggregate
   stats (session count, etc.) + the **anchor-ladder version hash** (scores are only
   comparable within a ladder version) + tool version, signed with a **local
   Ed25519 key** (pseudonymous continuity so a person can own/update their entry).
3. **GitHub account required to submit.** Identity comes **free from the submission
   mechanism**: `haid submit` opens a **PR** (or `repository_dispatch`) carrying the
   signed-summary JSON; the submitter is already GitHub-authenticated by opening the
   PR, so no OAuth app, no email service, no backend. A **GitHub Action validates**
   signature + schema and **checks the values are plausible** (in-range, internally
   consistent, ladder-version current), then auto-merges. Direct writes are never
   accepted.
4. **Everything is labeled "self-reported."** The Action's plausibility check is the
   *only* anti-fabrication measure in v1 — deliberately. No commit-reveal, no
   server-side scoring.
5. **Hosting = GitHub-native, zero backend.** The board is an append-only log of
   merged signed summaries in the HAID repo, rendered by **GitHub Pages** — a nice
   repo landing page. Entries are **public and permanent** (git history), which is
   stated at submit time and is another reason the payload is a minimal summary.

**v2 — verified tier (deferred; build only if the board catches on):** add an
opt-in "verified" badge where the tool uploads the **minimal scorer inputs — blinded,
identity-stripped diffs + usage counts** (not raw transcripts) and a **server
recomputes** the score. This is the part that needs compute + an LLM API key +
rate-limiting (running the placement scorer on arbitrary uploads is an API-budget
abuse vector), so it does **not** fit the zero-backend model and is intentionally
held back until demand justifies a small backend.

## Why not the alternatives

- **Commit-reveal cryptographic audit** (the first proposal) — over-engineered for a
  low-stakes vanity board; the two-tier self-reported/verified split achieves the
  same trust gradient more simply.
- **Email magic-link identity** — weakest of the options for this audience: emails
  are free and infinite (near-zero Sybil resistance), *and* sending mail + handling
  the click needs an email provider + secrets + an always-on endpoint, which
  **breaks the zero-backend model**. PR-based GitHub identity is both lower-effort
  and stronger. (Email/OAuth for non-GitHub submitters is a possible v2 backend
  feature, not v1.)
- **GitHub OAuth login** — needs a client secret + callback = a backend; PR-based
  submission gives the same GitHub identity for free.
- **Remote attestation (TPM/SGX) / ZK proofs of computation** — attestation of
  *open-source, user-modifiable* local code is weak; the LLM-placement step is
  non-deterministic and not ZK-friendly. Research-grade overkill for a coaching
  board.
- **Server-side recompute for all submissions** — violates the local-only default
  (logs would have to leave every machine) and costs compute per submission.

## Consequences

- **Reconciles a stated non-goal.** "Cross-user leaderboards / ranking" was a
  non-goal because it implied *involuntary* ranking that breaks local-only. That
  stays out; this is **opt-in, default off, summary-only, pseudonymous**, so the
  local-only default is preserved and only the explicit opt-in layer participates.
  Roadmap non-goals and Phase 5 updated accordingly.
- **Trust posture is honest, not oversold.** v1 is explicitly self-reported; the
  product copy says so. We do not claim the signature prevents fabrication.
- **Scores are versioned.** The board is keyed by anchor-ladder version **and the
  combiner-config hash** (the value.py knobs — alpha, top_ratio, gamma, floor — that
  fold the axes into the comparable value); changing either re-baselines the board.
  Same ladders but different knobs are not comparable, so the submission must carry
  both hashes.
- **Depends on the scorer existing.** Needs the production relative scorer (rubric
  "Open refinements") to produce a summary worth submitting. Sequenced after it.
- **Low operational cost.** v1 is GitHub Pages + an Actions validator — no server,
  no secrets, no API budget. The expensive piece (v2 verified tier) is deferred
  behind real demand.

## Related

- [scoring-rubric.md](../scoring-rubric.md) — the relative score being benchmarked.
- [plans/community-benchmark.md](../../plans/community-benchmark.md) — v1 build plan.
- [trust-discipline.md](../trust-discipline.md) — the "don't oversell trust" rule.
- Roadmap [Phase 5](../../plans/roadmap.md#phase-5--scoring-coaching-trend--packaging).
