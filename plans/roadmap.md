# Roadmap

Phased plan. Each phase is independently useful and de-risks the next. The
overriding rule: **prove the diagnoses are trustworthy on the smallest possible
build before adding anything fancier on top of them.**

## Phase 0 — Foundation *(this repo, now)*
Structure, research, documentation, plans. ✅ mostly done.
- [x] Folder structure
- [x] Verified data-format reference
- [x] Graph design, detector catalog, trust discipline
- [x] Tooling-landscape research + ADRs
- [x] Confirm language/stack — **Python** ([ADR-0001](../docs/decisions/0001-language-and-stack.md))
- [x] Empirical data analysis across 38 real sessions →
  [data-inventory.md](../docs/data-inventory.md),
  [data-structure-report.md](../docs/data-structure-report.md); resolved most open
  behaviors (compaction, subagents, usage — see [open-questions](open-questions.md))
- [x] Determinism tiers (1/2/3) + build layers (L0–L3) defined in
  [session-graph-design.md](../docs/session-graph-design.md#construction-tiers-and-layers)
- [ ] Capture anonymized sample transcripts as test fixtures
- [ ] Settle the 4 remaining design questions (region identity, Bash-failure rule,
  attachment modeling, resumed-session dedup)

## Phase 1 — MVP: objective metrics
The cheapest test of the core risk (is the diagnosis trustworthy?). Full spec in
[mvp.md](archive/mvp.md); the concrete sequenced build is in
[phase1-build.md](archive/phase1-build.md). In build-layer terms this is **L0 + L1 + the
Tier-2 metrics** (no episodes, no agents yet). **Scope amended 2026-06-06:** the unit
of analysis is the **analysis window** — a project's sessions over a timeframe (default
30 days), not one transcript (coaching value is cumulative). Metrics are emitted as
**benchmarkable token-rates positioned against a baseline**, not raw verdicts. SQLite
parse cache (zero user friction); git/PR-grouping and cross-session line lineage deferred
to Phase 4. See [phase1-build.md §0 + §0.5](archive/phase1-build.md).
1. Parse the JSONL (incl. subagent stitching + overflow tool-results). ✅ **DONE
   2026-06-06** — `src/haid/session/` (forest-aware parse, dedup, branch/rewind
   classifier, subagent stitching, overflow, SQLite cache), validated on 65 real
   transcripts; see [phase1-build.md](archive/phase1-build.md) Step 1.
2. Build the session graph (L0 spine + L1 action/IO graph, all Tier 1). ✅ **DONE
   2026-06-06** — `src/haid/graph/` (Turn/ToolCall/File/Region nodes,
   reads/produces/edits from `structuredPatch`, signatures, per-timeline scoping),
   validated on 65 transcripts; see [phase1-build.md](archive/phase1-build.md) Step 2.
3. Compute the four objective waste metrics (re-reads, retry loops, re-touched
   lines, unused-context) — Tier 2 rules. ✅ **DONE 2026-06-06** — `src/haid/metrics/`,
   validated on 65 transcripts (retries high-precision; the volume metrics are
   correct-but-noisy and need report-level ranking/hedging). Bash-failure rule resolved
   (`is_error`). See [phase1-build.md](archive/phase1-build.md) Steps 3+4.
4. Emit the measured substrate via **`haid metrics`** ✅ **BUILT 2026-06-07**
   (`src/haid/metrics/{json_out,view}.py` + CLI) — *not* "the report"
   ("report" is reserved for the Phase-5 final product). Pure measurement: each metric +
   baseline placement + ranked traceable instances + a no-silent-caps footer, reported at
   **both `session` and `window` scope** (the metric × scope model), as an inspection view
   (Markdown, for DoD validation) **and a JSON hand-off** to the later subagent passes
   (intent-tagging P2, error-attribution / `why()` P3) that answer *why*. No remedy/"this
   suggests…" lines — inferring cause and fix is those phases' job. Delta over the four built
   computations: the **`window` scope** of each metric (where the cross-session signals live —
   `rereads @ window` is the re-establishment tax, a headline visualizer signal) and per-scope
   baselines. It must rank the correct-but-noisy volume metrics, per the DoD finding above.

**Exit criteria:** run on a handful of the maintainer's own real sessions; the
flagged waste is *recognizably real* (low false-positive rate) on manual review.

## Phase 1.5 — Visualization *(MVP — moved up from the old Phase 4.5)*
The time-layered bus diagram ([visualization.md](../docs/visualization.md)):
left-in/right-out gutters, per-file bundled buses, width-by-tokens, subagent
sub-spines, cross-session shared file column. **Part of the MVP, not a late
embellishment** — seeing where the tokens go is half the point of the tool, and a
text report alone undersells it.

It is a **rendering of the analysis already built, not a shortcut around it.** The
bus widths *are* the metric token-weights; the read/edit edges *are* the L1 IO graph.
So Phase 1's PARSE→GRAPH→METRICS pipeline is a hard **prerequisite** — the diagram has
nothing to draw without it. It doubles as the triage surface for the metrics: fat
buses, re-touch clusters, and retry loops are where the eye (and later phases'
expensive attention) goes first.

**Why it can come this early:** its two structural dependencies are already met. The
**cross-session shared-file column** needs only the multi-session window + repo-relative
file identity — both built ([window.py](../src/haid/window.py); `File.id` is
repo-relative so sessions share one node). The only piece that genuinely waits for git
is **commit anchors on the time axis** (a deferred embellishment, not a blocker).

**Exit criteria:** renders a real 30-day window; the visual hotspots line up with the
metric findings from the report on manual review.

## Phase 2 — Episodes & the user-anchored pass
> Phases 2–3 are the **agent-analysis phase** (the model-in-the-loop "why" pass that turns the
> metrics substrate into the report). Consolidated design: [agent-analysis.md](agent-analysis.md)
> (anchor-driven, two-stage, episode-grain). This section lists the Phase-2 pieces.
- **Two-axis message classifier + purpose snapshot** per user message. ✅ **BUILT 2026-06-08**
  — `src/haid/intent/` + `haid tag` (move × work-type + purpose; manifest/backend pattern
  mirroring the scorer, `ReplayBackend` for CI / `HarnessBackend` for the live host-agent path,
  with a dynamic workflow as an optional runner). Walks **all branches** (a rewound stretch of
  work is captured, deduped by uuid, context built per-branch); pure LLM judgment with **no
  deterministic priors** (built then dropped — redundant with the model; the one out-of-band
  re-edit signal moves to Phase 3). Live model-labeling validation still pending.
  ([intent-taxonomy.md](../docs/intent-taxonomy.md)).
- **Episode detection** — the git-free PR proxy: **group whole SESSIONS** by shared
  component/topic (per-session purpose rollup + file-set overlap + cross-session re-read signal);
  the **session is atomic — never subdivided** (grain decision 2026-06-08; one session = one
  context window = the only clean cost boundary). Within-session topic drift is a *coaching
  signal*, not a boundary. Episodes span ≥1 whole sessions and are the **difficulty-scoring
  grain** ([agent-analysis.md §1, §5](agent-analysis.md)). *First cut was built at message grain
  (`src/haid/episodes/`); reworking to session grain.*
- Purpose timeline → thread/drift detection at wrap-up (the
  [Purpose-drift detector](../docs/detectors.md), cost-quantified, hedged).
- `why()` backward traversal with typed `Resolution` (incl. ORPHAN rate).
- Correction-as-ground-truth misalignment findings.

**Exit criteria:** correctly segments episodes and reports orphan rate on real
sessions; corrections reliably close episodes; the purpose timeline surfaces drift.

## Phase 3 — Behavioral & narration detectors + error attribution
- Behavioral contradiction (read docs → did opposite).
- Goodhart confession scan.
- Co-churn (impl + tests) — and the **canonical-case acid test**: run on a
  reconstruction of the spec→test→impl→cleanup disaster and confirm co-churn +
  Goodhart confession catch it.
- **Error attribution:** anchor on a correction/fix episode → trace the introducing
  edit backward → `why()` to its cause; route blame *up* to a contract when co-churn
  says so; separate agent-defect from user-changed-requirements.
- **Cross-session recurrence** — a symptom fixed in one session and re-reported in a later one
  (a fix that didn't hold): the highest-value attribution anchor. Backward window search +
  project-tree read for the missed context ([detectors.md → Recurrence](../docs/detectors.md),
  [agent-analysis.md §4](agent-analysis.md)).

**Exit criteria:** the canonical test case is caught.

## Phase 4 — Git & blame-chain *(post-MVP)*
> **Cross-session is already in place** — the analysis window is the multi-session unit
> (Phase 1), with shared repo-relative file nodes. What remains here is strictly the
> **git** layer, which the MVP does not need. **Note (2026-06-07):** the diff the scorer
> consumes does NOT come from git — it is reconstructed from the transcript (replay-only;
> see the Phase-5 Bridge note, where the ~0–1% bash gap that made git not worth it was
> measured). Git here is for blame-chain / commit anchors, and at most an opt-in diff
> *verifier* — never the diff source, and never required.
- Commit anchors on the session graph (the one cross-session piece not yet built — and
  the only visualization embellishment that waits for git).
- Blame-chain (final-diff line → origin session/turn/instruction).
- Git reconciliation ([ADR-0004](../docs/decisions/0004-git-session-tagging.md)),
  opt-in post-commit hook.
- "Working backwards from end state" code-review-window view.

## Phase 5 — Scoring, coaching, trend & packaging
> **Partially built ahead of the earlier phases (2026-06-05):** the relative **placement
> scorer** (difficulty + cleanliness) and the deterministic **volume** measure now exist in
> `src/haid/scoring/`, replay-validated against the calibration result (ρ=0.866) with no
> model in the loop. They were built standalone against calibration diffs; wiring them to a
> real session's window diff still depends on Phases 1–4. **Cost side also built (2026-06-06):**
> `src/haid/scoring/cost.py` reports cost as **normalized tokens, not dollars** (relative
> type/tier weights, configurable; process costs kept separate). **Value combiner also built
> (2026-06-06):** `src/haid/scoring/value.py` folds `achievement = LOC**α · D(difficulty) ·
> C(cleanliness)` and `value = achievement / normalized_tokens` (α=0.5; convex BT-latent
> difficulty, top/median=10x; steep penalty-only cleanliness, γ=2, floor=0.001; linear cost).
> 14 deterministic tests; `haid value` CLI (replay + harness paths) validated end-to-end. So the
> whole scoring scalar now runs on a supplied diff+usage. **The bridge that feeds it a REAL session
> is now built too (2026-06-07):** `src/haid/bridge/` reconstructs the window's net diff + cost from
> the transcript and `haid value --project/--session` runs the full stack on real sessions (see the
> Bridge note below). The **episode-grain** scoring is now BUILT (2026-06-08, Phase-2 step 4):
> `bridge.episode_inputs` runs the bridge over an episode's session subset and `episodes/score.py`
> (`haid score`) emits a **per-episode `WindowDistribution`** (not a blended window diff, so the
> critical 5% isn't buried; see [agent-analysis.md §5](agent-analysis.md) + [step4-build.md](archive/step4-build.md)).
> Remaining in this phase: the **diagnosis router**, the **skill glue** that drives the live
> comparison subagents (placement + grouping + classification share the manifest/backend pattern),
> and the plugin packaging.
>
> **BRIDGE — DIFF SOURCE DECIDED: replay-only, NO git (2026-06-07).** `src/haid/bridge/`
> (reconstruct.py + usage.py) rebuilds the diff from the transcript (Edit `oldString`/`newString` +
> `originalFile`, Write `content`, Bash heredoc content) — never git. **Why git was rejected:** the
> bash-write-to-*source* gap was measured at **~0–1%** across three real projects (0/110 HAID, 0/128
> c7-connector, 5/198 boxBot — and 3 of those 5 don't count), so git's marginal coverage is tiny
> while its cost is high (commit↔episode misalignment with no commit discipline, mutable/rebased
> history read long after the session, worktree state, and it bundles human edits the agent didn't
> make — wrong attribution for *agent* scoring). Residual handled in-transcript: **heredoc content is
> recovered**, and the few unrecoverable shell writes (`sed -i`/plain `>`) are **detected and flagged**
> (`BridgeResult.caveats`), never silently dropped. Git stays a possible *gated opt-in verifier* only;
> **episode↔PR(git) alignment is explicitly TBD, not v1.** Full analysis in the project memory.

- **Achievement-vs-cost value verdict** with the **relative** rubric
  ([scoring-rubric.md](../docs/scoring-rubric.md)) — score a diff by **placement
  against a reference ladder** of real code changes (difficulty axis validated:
  [difficulty-ladder.md](../docs/difficulty-ladder.md)); + cleanliness
  ([cleanliness-ladder.md](../docs/cleanliness-ladder.md)), both via
  [axis-calibration-playbook.md](../docs/axis-calibration-playbook.md). Achievement =
  `f(volume[LOC], difficulty, cleanliness)`; **originality was calibrated then dropped**
  (failed the orthogonality/usefulness bar — see scoring-rubric.md Axis decision). **Combiner
  built** ([value.py](../src/haid/scoring/value.py)): the per-axis placements are relative, but the
  combined value is a stable absolute scalar per ladder version — comparable across users, which is
  what the benchmark needs (scoring-rubric.md § Combining into achievement and value).
- Personal trend score *over time* — the per-session value is **relative** (placement
  vs. the reference corpus), tracked across your own sessions. Team comparison is
  opt-in (it breaks the local-only default); no involuntary cross-user leaderboard.
- Data-grounded recommendations via the diagnosis decision tree (smaller model,
  add a skill, single planning doc, fresh session, fix the contract).
- Ship as a Claude Code **plugin + skill** (`/haid:report`).
- **Opt-in community benchmark** (default off) — a public, self-reported leaderboard
  of achievement scores, viewable without an account; submission uploads a **signed
  summary, never logs**, via a GitHub-PR mechanism validated by an Action (plausibility
  check only), rendered on GitHub Pages. Trust-but-verify, not trustless: v1 is
  self-reported and labeled as such; a server-recompute **verified tier is deferred to
  v2** behind real demand. See [ADR-0005](../docs/decisions/0005-community-benchmark.md)
  + [community-benchmark.md](community-benchmark.md).

## Non-goals (explicitly)
- Token/cost accounting as a primary feature (ccusage owns it).
- *Involuntary* cross-user ranking. (An **opt-in, default-off** community benchmark is
  now in scope — see Phase 5 + [ADR-0005](../docs/decisions/0005-community-benchmark.md);
  what stays out is any leaderboard you're entered into without explicitly choosing it.)
- Anything that sends transcript content off-machine by default. (The opt-in benchmark
  uploads only a signed score *summary*, never logs; the deferred v2 verified tier would
  send blinded diffs only on explicit opt-in.)
- Confident causal claims without citations (see
  [trust-discipline](../docs/trust-discipline.md)).
