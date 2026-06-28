---
name: haid-report
description: >
  Runs HAID's self-audit pipeline over a project's recent Claude Code sessions and presents a
  coaching report. Use when the user asks "how am I doing?", wants a HAID report, a coaching or
  self-audit report, a session review, "score my work", "where are my tokens going", or "what
  should I do differently" — even if they don't say "HAID". Also handles partial asks (just waste
  metrics, just the deterministic digest, or just a score), which have cheap entry points.
---

# HAID report — drive the pipeline end to end

HAID separates **computation** from **judgment**. The `haid` CLI does all parsing, graph-building,
metric math, scoring, and report assembly deterministically — it never calls a model in-process.
Where a step needs judgment (a label, a pairwise comparison, an investigation, the narrative), the
CLI writes a **job manifest** and exits **3**. You are the **runner**: fulfill each manifest with
subagents, write the answers file beside it, and re-run the same command; the CLI reads them back,
validates, and continues. Manifests are self-contained (full prompt text + structured-output schema
per job) — you need no taxonomy/ladder/scoring knowledge.

**Prompt verbatim, author nothing** — pass each manifest `prompt` byte-for-byte; never paraphrase,
reframe, or debate wording (the judging prompts are calibrated and counterbalanced — edits silently
corrupt scores). Read `prompt`, attach `schema`, spawn, collect. See Runner rules.

```
haid metrics ─┐  (no model)                                ┌─► haid report ──► opus × 1 ──► final report
haid tag ─────┤  haiku × 1/session-branch     labels       │
haid episodes ┤  haiku × 1 (optional)        grouping   ───┼─► haid viz  (no model) ──► out/report/haid-viz.html
haid score ───┤  haiku: ~9-11 pairwise/ep (difficulty) + 1 detect +0-N verify (cleanliness) │
haid why ─────┘  sonnet × waste anchors + bug attribution (uses tags)  └─► haid benchmark (opt-in only)
```

Design rationale, forensics, and internals that don't change the next action live in
[`reference.md`](reference.md) — load it only when something looks wrong or you want the *why*.

## The universal loop

Every model boundary is the same cycle:

1. Run the `haid` command. **Exit 0** → done. **Exit 3** → pending: it printed the manifest path(s)
   and the exact answers-file path + shape.
2. Fan out subagents over the manifest's job prompts (parallel — jobs are independent), each
   `prompt` verbatim with its `schema`. Tool budget per boundary → Runner rules.
3. Write the answers file beside the manifest, in the shape printed.
4. Re-run the **same command verbatim** — it validates the answers and either completes or raises
   the next boundary.

Exit codes: `0` done · `2` usage error · `3` pending jobs · `4` empty diff (no reconstructable code
changes — report honestly, skip score, keep metrics/why).

## Setup and inputs

- Needs the `haid` CLI (`pip install -e .` or `pip install haid`); CLI and skill ship together and
  must match — see Preflight.
- **Target**: default current project, last 30 days (`--project PATH --days N`); override with
  `--session FILE...`. WSL transcripts need `--session` with UNC paths
  (`//wsl.localhost/<distro>/home/<user>/.claude/projects/<slug>/*.jsonl`) — `--project` won't find them.
- **Artifacts**: command JSON → `out/report/`; manifests → `--job-dir out/jobs` (default). **Start
  fresh with a clean job dir** (delete `out/jobs/*`, or use `--job-dir out/jobs/<run-id>` everywhere)
  — stale answer files get read back as yours. Reuse a job dir only to resume the same window.
- **Windows**: PowerShell `>` writes UTF-16; haid readers need UTF-8 no-BOM. Capture JSON with the
  Bash tool, or `cmd /c "haid ... --json > file"`.

## The chain

Run in order. Tell the user what's running and roughly how many agents a large step costs first.

### 0. Preflight — pin the CLI version (FIRST, before any computation)

Run `haid --version`; it must match the haid-report plugin version (they release in lockstep).
**On mismatch — or if `haid` resolves to an unrelated install — STOP**: the report would be computed
by stale code and is silently wrong (classic tell: the window value rounding to 0). Remedy:
`pip install -U haid`, or invoke the plugin-bundled CLI explicitly — don't proceed until the user
agrees. After step 1, confirm `metrics.json.haid_version` == `haid --version`. (Why → reference.md.)

### 1. Metrics — deterministic, free

`haid metrics --project P --days N --json` → `out/report/metrics.json`. No model, never pends. This
is also the standalone answer when the user only wants waste metrics — drop `--json` for the
readable view.

### 2. Tag — label every user message

`haid tag --project P --days N --json --backend harness`. Exit 3 writes `out/jobs/tag.job.json`:
`jobs[]`, **one per session branch**, each `{session_id, timeline, n_targets, targets, prompt}`. The
prompt marks each user message `>>> CLASSIFY THIS MESSAGE — ref: … <<<`; the agent echoes the short
**ref**, not the uuid — the CLI expands `targets[].{uuid, ref}` back on read-back, so the model never
copies a 36-char id. Schema is a `labels` array of `{ref, move, work_type, impl_kind, purpose}`. Fan
out like `score` — split, committed workflow, aggregate — so transcripts never enter your context:

1. `python .claude/skills/haid-report/scripts/split_tag_manifest.py --job-dir out/jobs` (capture
   stdout, UTF-8/no-BOM) → writes `out/jobs/tag_split/` files, prints
   `{base, schema, jobs:[{job_id, n_targets, path}]}`.
2. `Workflow({ scriptPath: ".claude/workflows/haid-tag.js", args: <that object> })` → one haiku agent
   per branch (each Reads its one file), returns `{job_id, n_targets, complete, labels}` per branch.
3. Write the returned array verbatim to `out/jobs/tag.raw.json` (one Write, no editing), then
   `python .claude/skills/haid-report/scripts/aggregate_tag_answers.py --job-dir out/jobs --answers out/jobs/tag.raw.json`.
   It validates each branch against the manifest (present, `complete`, exact `n_targets`, refs match
   with no missing/unknown/duplicate, enums valid) and writes `out/jobs/tag.answers.json`; on any
   problem it names the branch and writes nothing — re-run that branch (step 2) and re-aggregate.
4. Re-run `haid tag --json`. The CLI expands refs → uuids and authors `out/jobs/tag.labels.json` (the
   uuid-keyed file `score`/`episodes` consume). Save stdout → `out/report/tags.json`.

### 3. Episodes — group sessions (optional model step)

`haid score` groups deterministically by default; skip to step 4 for a hands-free run. Run this only
when episode titles/rationales are worth one extra agent (they read better in the report):
`haid episodes --project P --days N --labels out/jobs/tag.labels.json --json --backend harness`. Exit
3 writes `out/jobs/episodes.job.json` (one grouping job). One **haiku** agent. Write
`out/jobs/episodes.grouping.json`: `{"episodes":[{title, session_ids:[...], rationale}, ...]}` —
session ids round-trip exactly as given. Re-run, then pass `--grouping out/jobs/episodes.grouping.json`
to `haid score` so both layers share the grouping.

### 4. Score — place each episode on the ladders

`haid score --project P --days N --labels out/jobs/tag.labels.json --json [--grouping …]`.
**Difficulty** = pairwise placement (one `comparisons[]` manifest per episode). **Cleanliness** =
counted defect detection in two phases: `detect` (one cataloguing job over the episode diff), then
`verify` (one adversarial refuter per severe finding) only for episodes with severe defects. Exit 3
writes three manifest kinds, each with its own `schema` + `fingerprint`:

- `<ep>_difficulty.job.json` — pairwise (`comparisons[]`)
- `<ep>_detect.detect.job.json` — detection (single `prompt`)
- `<ep>_detect.verify.job.json` — verification (`verifications[]`)

The chain's one heavy fan-out — use the committed pipeline:

1. `python .claude/skills/haid-report/scripts/split_score_manifests.py --job-dir out/jobs` (capture
   stdout) → `out/jobs/score_split/` files + `{base, manifests:[{manifest, kind, n, fingerprint,
   schema}]}` (`kind` = pairwise | detect | verify).
2. `Workflow({ scriptPath: ".claude/workflows/haid-judge.js", args: <that object> })` → one haiku
   judge per job, returns one group per manifest carrying `kind, fingerprint, complete`, answers.
3. For each group with `complete: true`, write the answers file (suffix + key by `kind`):
   - `pairwise` → `<manifest>.verdicts.json` = `{fingerprint, winners:[...]}`
   - `verify`  → `<manifest>.verdicts.json` = `{fingerprint, verdicts:[...]}`
   - `detect`  → `<manifest>.findings.json` = `{fingerprint, findings:[...]}`
   (the `<manifest>` stem already includes the `.detect`/`.verify` segment.) `complete: false` = a
   judge died — re-run the workflow; **never** write a null/short list.
4. Re-run `haid score`. Detect answers build each episode's `DefectResult`; episodes with ≥1 severe
   defect then pend a `verify` manifest — repeat 1–3 for those and re-run (detect → re-run → verify →
   re-run). Save final stdout → `out/report/scores.json`. Cost: ~9–11 pairwise/episode + 1 detect +
   0–N verify.

Load-bearing: relay pairwise A/B/tie answers **in order** — which side is the subject is hidden
(counterbalancing); don't reveal or reorder. Severity is assigned by haid on read-back (the judge
only classifies/locates/confirms-or-refutes). `fingerprint` is the staleness guard: "stale" on re-run
means the manifest was regenerated — delete that answers file and re-judge, never hand-edit it. Wrong
count/shape fails loudly — fix the answers, don't pad. (Internals → reference.md.)

### 5. Why — investigate top waste anchors AND attribute every bug fix

`haid why --project P --days N --tags out/report/tags.json --json`. **Always pass `--tags`** so the
pass emits bug-source-attribution anchors (one per fix span: a `bugfix` impl_kind, or a `correction`
move on impl/investigation work) on their own `--bug-top` budget. `haid why` **hard-errors** without
`--tags` or `--no-bug-attribution`, so attribution is never silently dropped; use
`--no-bug-attribution` only if the user explicitly asked to skip it. Exit 3 writes
`out/jobs/why.job.json`: triaged anchors, `recommended_model` (default **sonnet** — honor unless
overridden), one self-contained `prompt` per job. **Each job carries its OWN `schema`** — waste
anchors use the why-note schema; bug anchors (`"metric":"bugfix"`) use the bug-attribution schema
(cause_class agent/user/source/undetermined, origin, mistake_kind, scope, holding) — attach each
job's own, not a shared one. Spawn one **tool-using** agent per job (Read/Grep/Glob; ~1–4 min,
~45–80k tokens each — batch ~4). A bug agent traces the defect to its introducing edit and obeys
cite-or-orphan (no traceable origin ⇒ `undetermined`) and a high bar on blaming the user. Write
`out/jobs/why.notes.json`: `{"notes":[{anchor_id, <that job's schema fields>}, ...]}` — every
anchor_id present (read-back validation is strict and names what's missing). Re-run → `out/report/why.json`.

### 6. Report — compose and present

`haid report --metrics out/report/metrics.json --why out/report/why.json --scores out/report/scores.json
--tags out/report/tags.json`. The deterministic what/why **digest** prints first (findings from rules,
treatments from the shipped catalog, suppressed findings shown as credits). Exit 3 writes
`out/jobs/compose.job.json`: ONE holistic job, `recommended_model:"opus"`, prompt embedding the
digest. Spawn one **opus** agent (no tools) on `prompt` + `schema`; write its bare composition object
(no wrapper) to `out/jobs/compose.composition.json`. Re-run: `validate_composition` rejects any
recommendation citing a finding/treatment the digest didn't produce — on rejection, show the composer
the error + the real ids and have it re-emit; never edit the composition to slip past. **Present the
rendered report verbatim** — it's the product (window score, credits first, ≤5 evidence-cited
recommendations, watchlist, hedges); don't re-rank, soften, or add remedies (the hedges are
load-bearing trust discipline). `--digest-only` skips the model — offer it for a zero-cost answer.

**Wrap-up discipline** (the bug this prevents: naming the achievement total but not the score, or
mentioning the leaderboard without asking). Lead with the result as a number, quoted verbatim from
the rendered blocks:
- **window score** — the `value` figure from the Scoreboard, not the achievement total or rung.
- **percentile** — the bundled board ships empty (its own "Community benchmark" block carries no
  percentile), so for a real one run read-only `haid rank --scores out/report/scores.json --refresh`
  and quote its `percentile`: "you scored X, which puts you in the Nth percentile of N entries." If
  `rank` finds no comparable peers (seed bucket) or can't reach the board, say exactly that — don't
  invent one.
- **then ask explicitly** whether to submit ("Want me to submit this to the community board?") —
  never imply submission is expected; don't submit until the user says yes.

If scores are absent (empty diff / `--digest-only`), say there's no score this run and skip the
percentile/submit line. Then point the user to the four things they'll want next: **the report**
(`out/report/`), **the window `value` score** (the number to track run-over-run), **the
visualization** (step 6b), and **the leaderboard** (step 7, opt-in).

### 6b. Visualize — deterministic, no model

`haid viz --project P --days N --scores out/report/scores.json --metrics out/report/metrics.json --out
out/report/haid-viz.html`. A self-contained HTML (CSS+JS+data inlined) opening from `file://`.
Episodes come from the best available: `--scores` (real grouping, titles, per-episode badges) >
`--grouping` > a single flat "window" episode (the command warns). `--metrics` adds the per-file flag
overlay. Run after `haid score`; point the user at the output path (item 3 above).

### 7. Community benchmark — view, then (opt-in) submit

The rendered report already includes a local **"Community benchmark"** section when scores exist
(computed locally, **uploads nothing**; `--board FILE` points at a different board). Two explicit,
opt-in, summary-only commands (a leak check refuses path/title-shaped input) — **never imply
submission is expected**:

```
haid rank   --scores out/report/scores.json [--github-user USER] [--refresh]
haid submit --scores out/report/scores.json --github-user USER --project NAME [--dry-run]
```

`haid rank` is read-only: percentile vs the community (`--refresh` pulls the live board, else the
shipped snapshot; no account needed). `haid submit` is the only path that leaves the machine, and only
on explicit ask: it prints **exactly the public + permanent row**, then opens a validated GitHub PR to
the data-only `dv-hart/haid-benchmark` repo. `--dry-run` first; pass `--yes` only after the user
confirms. (Submit internals → reference.md.)

## Runner rules (every boundary)

- **Prompt verbatim, author nothing**: the manifest `prompt` is the complete subagent prompt — never
  wrap, paraphrase, re-order, or "improve" it, and never debate phrasing.
- **Tool budget per boundary**: `episodes`, `compose` = **no tools** (prompt fully inlined → one
  structured answer). `tag`, `score` = **exactly one `Read`** (each agent reads its own split file via
  the committed workflow). `why` = **tool-using** (Read/Grep/Glob, many turns, correct here). More than
  this means the agent was spawned with tools it shouldn't have — constrain the surface, not just the schema.
- **Model tiers**: labels, grouping, pairwise = **haiku**; investigations = the manifest's
  `recommended_model` (sonnet default); composition = **opus**. The manifest's `recommended_model` wins.
- **Enforce schemas at spawn time** (structured-output/schema-constrained agents); validate after;
  re-spawn on repeat enum drift rather than arguing with the same agent.
- **Tolerant extraction**: extract the last complete JSON object from a reply that wraps it in prose
  or code fences before validating.
- **Parallel by default**: jobs within a manifest, and manifests within a step, are independent. Batch
  tool-using why-agents in smaller groups (~4) since each is heavy.
- **Never hand-author a `Workflow`**: `tag` and `score` use their *committed* workflows
  (`haid-tag.js`, `haid-judge.js`) after the splitter — invoke, don't rewrite; `episodes`/`compose` use
  direct `Agent` calls with the prompt inlined. (Why the shim → reference.md.)
- **Don't recompute or improvise**: never answer a job yourself in-line (your context is contaminated
  with the whole window); never fabricate or pad an answers file to pass a re-run — loud failures are
  the design.
- **Honest accounting**: relay the CLI's caveats (bridge incompleteness flags, baseline thinness,
  suppressed findings) rather than smoothing them over.

## Failure and resume

- The file-handoff design is **resumable** — artifacts on disk are the state. Any interruption: re-run
  the same command; completed answers are picked up, only missing ones pend.
- Validation error on read-back: the message names the file and the defect; fix the answers (or delete
  the stale file and re-judge), then re-run.
- A subagent that dies or returns garbage twice: replace it; one bad judge among many is recoverable,
  a fabricated answer is not.
- Transcripts are perishable (`~/.claude/projects` ages out). If a window comes back thinner than the
  user expects, say so — and prefer running sooner over later.
