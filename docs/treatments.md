# The treatment catalog — symptom + why → known remedy

**Canonical data: [`src/haid/data/treatments.json`](../src/haid/data/treatments.json)** (loaded by
`haid.report.treatments`). This doc is the human-readable companion: provenance, evidence tiers,
known gaps, and the refresh protocol. The report compositor pairs a detected SYMPTOM (metric
pattern) + the why-pass's WHY (flags + note) with entries here.

## Provenance

Compiled 2026-06-10/11 from three evidence tiers — every entry's `maturity` field records which:

1. **Adversarially verified** (the deep-research run, 102 agents): 5 search angles → 20 sources
   fetched → 99 falsifiable claims extracted → top 25 put through 3-vote adversarial verification
   (claims killed on 2/3 refutes) → **25/25 survived** → synthesized into 12 findings. Sources are
   overwhelmingly Anthropic first-party (the living best-practices docs + three engineering posts),
   verified verbatim against live pages, plus Karpathy's March 2026 attestation (date verified via
   snowflake ID).
2. **Single-pass gap-fill** (3 Sonnet research agents, cited but not adversarially verified):
   model tiering/pricing, the AGENTS.md cross-tool ecosystem, and community tools (maintenance
   checked: stars/releases/last-commit). Entries from this tier say so in `caveats`.
3. **Validated in-house**: treatments HAID's own live why-pass derived from real sessions
   (c7-connector, boxBot — 2026-06-09) before the research ran. Two of them (behavioral-contract
   lines, runbook skills) independently converged with the official guidance.

## The symptom → treatment map (summary)

| HAID symptom key | Treatments (catalog ids) |
|---|---|
| `rereads.cross_session` | claude-md-concise · claude-md-behavioral-contract · skills-progressive-disclosure · skill-authoring-from-failures · runbook-skill-for-recurring-sequence · persistent-progress-artifacts · agents-md-cross-tool · repomix-context-packing |
| `cost.cache_dominated` | subagent-isolation-for-exploration · just-in-time-retrieval · ccusage-cost-visibility |
| `retries.error_ignored` | runnable-verification-stop-hooks · hook-collections-guardrails · clear-discipline-two-strikes · runbook-skill-for-recurring-sequence |
| `retouched.self_thrash` | plan-mode-bounded · spec-kit-spec-driven · design-before-code-on-shared-subsystem |
| `alignment.corrections` / `alignment.re_prompts` | spec-first-interview · clear-discipline-two-strikes · plan-mode-bounded · spec-kit-spec-driven · design-before-code |
| `bug.agent_self_inflicted` / `bug.regression` | runnable-verification-stop-hooks · hook-collections-guardrails |
| `bug.incomplete_edit` | runnable-verification-stop-hooks · design-before-code-on-shared-subsystem |
| `bug.user_spec_churn` | spec-first-interview · spec-kit-spec-driven |
| `bug.external_source` | runnable-verification-stop-hooks |
| `recurrence.fix_did_not_hold` | runnable-verification-stop-hooks · hook-collections-guardrails · skill-authoring-from-failures |
| `cost.model_overkill` | model-tiering-official · claude-code-model-controls · tiered-routing-evidence · ccusage-cost-visibility |
| `cleanliness.low` | scoped-review-pass-for-parsimony |
| `drift.multi_topic` | one-feature-per-session · worktrees-parallel-isolation · spec-first-interview · clear-discipline-two-strikes |

> `unused_context.bloat` was **retired 2026-06-26** as a coaching signal (too soft — a large read
> of a never-edited file is usually legitimate). The metric is still measured; session meandering
> (`drift.multi_topic`) covers the useful version. Bug-attribution rows (`bug.*`) added the same
> day — see [detectors.md → Bug-source attribution](detectors.md).

## Load-bearing nuances (the compositor must respect these)

- **Subagents treat main-context cleanliness, NOT total spend** — Anthropic's own multi-agent
  research reports ~15× token usage. Never recommend subagents as a *cost* cure; recommend them
  for context-rot protection. Contested for write tasks (Cognition, June 2025).
- **Bounded planning** — "If you could describe the diff in one sentence, skip the plan." A
  coaching tool that prescribes planning for everything is mis-coaching.
- **The two-strikes /clear rule is about USER corrections**; applying it to autonomous retry loops
  is our interpretive extension (flagged in the entry).
- **Instructions alone don't fix bloat** (Karpathy: agents "do not listen to my instructions") —
  for `cleanliness.low`, recommend a *scoped* review pass, and warn that unscoped adversarial
  reviewers *cause* over-engineering (official warning).
- **Context files must be short and hand-written** — official 200-line CLAUDE.md target; ETH
  Zurich (Feb 2026): LLM-generated context files *reduce* success ~3% and add >20% cost.
- **Earned iteration is not a symptom.** No treatment fires without the why-pass; flags like
  `earned_iteration` / `correction_preceded` suppress or reframe recommendations.

## Known gaps & open questions (from the verification run)

- **Independent quantitative validation is thin everywhere**: the headline treatments (skills,
  subagent isolation, Stop-hook gates) rest on official-but-first-party qualitative evidence. No
  usage-telemetry studies or controlled comparisons surfaced. (HAID's community benchmark could
  eventually BE this evidence.)
- Model-tier *prices* and Claude Code feature surface (aliases, env vars, hook semantics) are
  volatile — these entries carry live-page verification dates and go stale fastest.
- aider native AGENTS.md auto-detection unconfirmed; OpenAI's Codex AGENTS.md doc page 404'd at
  verification time.

## Refresh protocol

The catalog is perishable by design. Each entry carries `last_verified`; refresh by re-running the
deep-research pass (the question text is archived in the run journal and in this doc's git
history) and the three gap-fill prompts, then reconcile:

1. Re-verify `official` entries against the live pages (they are living docs — quote drift is
   expected; the old engineering-blog URL already 308-redirects).
2. Re-check `community-consensus`/`emerging` tools for maintenance (a tool verified ABANDONED gets
   dropped, as rulebricks/claude-code-guardrails was).
3. Bump `version` and `last_updated`; never edit an entry's claims without re-verifying — stale
   confident advice is exactly the failure mode HAID exists to prevent.
4. Suggested cadence: quarterly, or on major tool releases (new model generations, Claude Code
   major versions).
