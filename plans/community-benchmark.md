# Plan — community benchmark (v1, self-reported)

Opt-in public leaderboard of HAID achievement scores. **Default off.** Decision and
rationale: [ADR-0005](../docs/decisions/0005-community-benchmark.md). This is the
build plan for **v1 only** (self-reported; the verified tier is deferred — see ADR).

> **Status: v1 built (2026-06-23).** `haid benchmark`/`submit`/`rank` ship; the report
> renders a "Community benchmark" context section from the bundled board snapshot. The
> board lives in a **separate, data-only repo** (`dv-hart/haid-benchmark`) gated by a
> split `validate` (read-only) → `act` (privileged, `workflow_run`) → `build` (Pages)
> pipeline with SHA-pinned actions; the cross-repo snapshot sync is whitelist-sanitized
> (`benchmark.sanitize_board`). Built per the
> [ADR amendment](../docs/decisions/0005-community-benchmark.md#amendment-2026-06-23--v1-build):
> identity = GitHub PR author (no Ed25519); overall score = total achievement ÷ total
> tokens; distribution ships as package data. **Remaining (maintainer):** create + push the
> data repo, enable Pages + auto-merge + branch protection requiring `validate`, publish the
> pinned `haid` version, then seed the first row.

## Principles (carried from the ADR)

- **Not trustless — trust-but-verify with low stakes.** v1 is self-reported and
  *labeled as such*; the only gate is a plausibility check. Never claim the signature
  prevents fabrication.
- **Local-only stays the default.** Viewing uploads nothing; submission uploads a
  **signed summary, never logs**; the feature is opt-in.
- **Zero backend.** GitHub Pages + a GitHub Action. No server, no secrets, no API
  budget. (The verified tier is what would need a backend; it's deferred.)
- **GitHub account required to submit** (identity comes free from the PR);
  **viewing requires no account.**

## Pieces to build

### 1. Summary payload + local signing
- On first opt-in, generate a **local Ed25519 keypair**; the public key is the
  pseudonymous submitter id (lets a person own/update their entry over time).
- Build the **summary record**: per-axis relative positions, combined value,
  aggregate stats (session count, token totals by tier), **anchor-ladder version
  hash**, **combiner-config hash** (the value.py knobs — alpha, top_ratio, gamma,
  floor — that define how the axes fold into the comparable value; two users on the
  same ladders but different knobs are NOT comparable), tool version, public key,
  timestamp — then an Ed25519 signature over a canonical (sorted-key) JSON encoding.
- **No transcripts, no diffs, no prompts.** Just the numbers above.

### 2. `haid submit`
- Opt-in command. Renders exactly what will be made public, **public + permanent**,
  and asks for confirmation.
- Opens a **PR** (or fires `repository_dispatch`) against the HAID benchmark repo
  carrying the signed-summary JSON as one append-only entry. Uses the user's existing
  GitHub auth (`gh`/token) so the GitHub identity attaches for free.
- A separate read path (`haid rank` / shown in the report) downloads the **public
  distribution** and computes the user's percentile **client-side** — no submission
  required.

### 3. Validator GitHub Action (the only gate)
On each submission PR:
- **Signature** verifies against the embedded public key.
- **Schema** matches; **ladder-version hash AND combiner-config hash are both current**
  (reject stale/mismatched versions — scores are only comparable within a fixed ladder
  *and* a fixed combiner config; the distribution is bucketed by both).
- **Plausibility:** values in range, axes internally consistent, no impossible
  combinations (e.g. value vs. its volume/difficulty/cleanliness inputs), one active
  entry per public key (updates supersede). This is the entire anti-fabrication
  measure in v1 — intentionally.
- Pass → auto-merge (append to the log). Fail → comment with the reason, leave open.
- Never accept direct writes to the log; everything goes through the validated PR.

### 4. Board + public distribution (GitHub Pages)
- The merged log of signed summaries is the **append-only data store** (git history =
  tamper-evident audit trail).
- A build step regenerates two artifacts on merge: the **rendered board** (Pages
  landing page) and the **public distribution JSON** (percentile curve / histogram
  per ladder version) that `haid rank` consumes.
- Board shows self-reported entries clearly labeled; sortable; keyed by ladder
  version.

## Related: waste-metric baselines (candidate second use, not committed)

Phase 1 introduced a second thing that wants a population distribution: the **waste-metric
token-rates** (re-reads, retries, re-touched, unused-context — `src/haid/metrics/baseline.py`).
Today these are positioned against a **single-author bootstrap** baseline shipped as package
data — explicitly a placeholder. The same zero-backend mechanism here (signed summary →
validated PR → public per-bucket distribution) is the natural way to replace that bootstrap
with a real multi-author waste-rate population, so a user could see "your rework rate is p82
vs the community" the same way they see achievement percentile.

This is recorded as a **candidate extension, not v1 scope** (and not an opinion baked into
the ADR): it reuses the infra cleanly, but the summary payload, plausibility checks, and
bucketing would need their own design. Flagging the connection now so it isn't lost.

## Out of scope for v1 (→ v2, only if it catches on)

- **Verified tier:** server recomputes a submitter's **blinded diffs + usage counts**
  for a "verified" badge. Needs compute + LLM API key + rate-limiting → a small
  backend. Deferred behind real demand (ADR-0005 Consequences).
- Non-GitHub identity (email/OAuth) — would need a backend; deferred.
- Team/org boards, private leagues.

## Exit criteria (v1)

- A user can opt in, see their percentile **without submitting**, and — separately —
  submit a signed summary that appears on the Pages board after the Action validates
  it.
- No raw transcript, diff, or prompt ever leaves the machine in the submit path.
- The board renders as a public, no-account-needed page on the HAID repo, every entry
  labeled self-reported and keyed to a ladder version.

## Dependencies

- The **production relative scorer** must exist first (it produces the summary).
  See [scoring-rubric.md](../docs/scoring-rubric.md) "Open refinements" and roadmap
  [Phase 5](roadmap.md#phase-5--scoring-coaching-trend--packaging).
