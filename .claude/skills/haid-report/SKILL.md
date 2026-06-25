---
name: haid-report
description: >
  Run HAID's full self-audit pipeline over a project's recent Claude Code sessions and
  present the coaching report: metrics → tag → episodes → score → why → report. Use this
  whenever the user asks "how am I doing?", asks for a HAID report, a coaching/self-audit
  report, a session review, "score my work", "where are my tokens going", "what should I
  do differently", or wants the community-benchmark payload — even if they don't say
  "HAID". Also use it for partial asks (just the waste metrics, just the deterministic
  digest, just a score) — the chain has cheap entry points for those.
---

# HAID report — drive the pipeline end to end

HAID separates **computation** from **judgment**. The `haid` CLI does all parsing,
graph-building, metric math, scoring arithmetic, and report assembly deterministically —
it never calls a model in-process. Wherever a step needs model judgment (a message label,
a pairwise diff comparison, an investigation, the final narrative), the CLI stops, writes
a **job manifest**, and exits with code **3**. Your job as the host agent is to be the
**runner**: fulfill each manifest with subagents, write the answers file beside it, and
re-run the same command. The CLI reads the answers back, validates them, and continues.

Every manifest is self-contained — it carries the full prompt text and the
structured-output schema for each job. You never need taxonomy, ladder, or scoring
knowledge; you only spawn agents on the prompts given and write back their answers in
the documented shape.

**This is mechanical, not creative.** The manifest `prompt` is the agent's *entire*
prompt — pass it **verbatim**, byte-for-byte. You author nothing, paraphrase nothing,
add no preamble or framing, and never deliberate over wording. The pairwise judging
prompts are calibrated text (the exact phrasing and the hidden A/B counterbalancing in
`compare.py` are load-bearing); rewriting them silently invalidates the scoring. If you
ever catch yourself composing a subagent prompt, or weighing "which phrasing is better,"
stop — that's a bug. Read `prompt`, attach `schema`, spawn, collect. Nothing else.

```
haid metrics ─┐  (no model)                                ┌─► haid report ──► opus × 1 ──► final report
haid tag ─────┤  haiku × 1/session-branch     labels       │
haid episodes ┤  haiku × 1 (optional)        grouping   ───┼─► haid viz  (no model) ──► out/report/haid-viz.html
haid score ───┤  haiku × ~9-11/episode/axis  verdicts      │
haid why ─────┘  sonnet × top anchors        notes         └─► haid benchmark (opt-in only)
```

## The universal loop

Every model boundary follows the same cycle. Learn it once:

1. Run the `haid` command. **Exit 0** → done, output is final. **Exit 3** → pending: the
   command printed the manifest path(s) and the exact answers-file path + shape it wants.
2. Read the manifest. Fan out subagents over its job prompts (parallel — jobs are
   independent), passing each `prompt` verbatim with its `schema`. **Most boundaries are
   tool-free** (see "Two kinds of boundary" below) — give those agents no file tools.
3. Write the answers file beside the manifest, in the shape the command printed.
4. Re-run the **same command verbatim**. It reads the answers back (strictly validated
   where it matters) and either completes or raises the next pending boundary.

Exit codes: `0` done · `2` usage error (fix your flags) · `3` pending jobs (the loop
signal) · `4` bridge produced an empty diff (window has no reconstructable code changes —
report that honestly, skip score, continue with metrics/why).

This file-handoff design makes the whole chain **resumable**: the artifacts on disk are
the state. If anything is interrupted, re-run the same command — completed answers are
picked up, only missing ones are pending.

## Setup and inputs

- Requires the `haid` CLI (`pip install -e .` in the HAID repo, or `pip install haid`).
- **Target selection**: default is the current project, last 30 days
  (`--project PATH --days N`). Explicit transcripts override: `--session FILE...`.
  WSL transcripts work via UNC paths with `--session`
  (e.g. `//wsl.localhost/Ubuntu/home/<user>/.claude/projects/<slug>/*.jsonl`) —
  Windows-side `--project` discovery will not find them.
- **Artifact layout**: save command JSON outputs under `out/report/`, manifests under the
  default `--job-dir out/jobs`. **Start a fresh analysis with a clean job dir** (delete
  `out/jobs/*` or pass `--job-dir out/jobs/<run-id>` consistently to every command):
  answer files from a previous window sitting in the job dir get silently read back as if
  they were yours. Reuse the same job dir only when deliberately resuming the same window.
- **Windows redirection gotcha**: PowerShell's `>` writes UTF-16; haid's readers require
  UTF-8 without BOM. Capture JSON with the Bash tool, or `cmd /c "haid ... --json > file"`.

## The chain

Run the steps in order. Tell the user what's running and roughly how many agents each
step costs before fanning out a large step.

### 1. Metrics (deterministic, free)

```
haid metrics --project P --days N --json   → out/report/metrics.json
```

No model, never pends. This is also the standalone answer when the user only wants the
waste metrics — render with no `--json` for the readable view.

### 2. Tag — label every user message

```
haid tag --project P --days N --json --backend harness
```

Exit 3 writes `out/jobs/tag.job.json`: `jobs[]`, **one per session branch** (not per
message), each `{session_id, timeline, n_targets, targets, prompt}`. Each `prompt` is that
branch's whole transcript with its user messages marked `>>> CLASSIFY THIS MESSAGE — uuid: … <<<`;
the agent reads the branch once and labels every marked message in order. The top-level
`schema` is a `labels` **array** (one entry per mark: `{uuid, move, work_type, purpose}`,
`move`/`work_type` enum-constrained).

Spawn **one haiku subagent per job** on `prompt`, constrained to `schema`, **no tools** (the
transcript is inlined — there is nothing to fetch). Use a **direct `Agent` call per job** with
the `prompt` passed verbatim as the agent prompt — **do not wrap the fan-out in a `Workflow`**
and **do not pass file paths** for the agent to `Read`; the `prompt` already is the whole
transcript. Each returns a `labels` array covering that branch's marks. **Aggregate every job's array into one** `out/jobs/tag.labels.json`:
`{"labels": [{"uuid": ..., "move": ..., "work_type": ..., "purpose": ...}, ...]}` — concatenate
the per-job arrays as-is; the `uuid` each entry echoes is how they fold back, so no per-job
bookkeeping is needed.

**Two enforcement layers, and you own the second.** On re-run the read-back checks
*coverage* — a missing or stray uuid fails loudly (`tag labels don't match the window`), so a
job that got dropped or hallucinated can't slip through. But it does **not** check enum
*values*: a label like `move: "question"` (a work-type, not a move) still poisons everything
downstream. Use schema-constrained output so the enums hold at the source, and validate every
label's `move`/`work_type` against the manifest enums before writing the file. On a violation,
re-run that one **job** once with the enums restated; if it violates again, spawn a fresh
agent rather than arguing with the same one.

Why per-branch: one agent per message re-embedded each message's context, so the manifest grew
quadratically (the ~800KB-too-big-to-relay failure). Per-branch shows each transcript once →
the manifest is linear in transcript size, the agent count drops to ~one-per-session, and each
message's causal context is just the transcript above it. Causality is preserved by
instruction (judge each mark by what precedes it, no hindsight); branches are split so a
rewound stretch is still labeled and never bleeds into the active branch.

Re-run with `--json` and save stdout → `out/report/tags.json`. Keep `tag.labels.json`
too — `score` (and `episodes`) consume it via `--labels`.

### 3. Episodes — group sessions (optional model step)

`haid score` groups sessions with a deterministic heuristic by default — for a
hands-free run you can skip straight to step 4. Run this step with `--backend harness`
only when episode titles/rationales are worth one extra agent (they make the final
report read better):

```
haid episodes --project P --days N --labels out/jobs/tag.labels.json --json --backend harness
```

Exit 3 writes `out/jobs/episodes.job.json` (one job: a single grouping prompt + schema).
One **haiku** agent. Write `out/jobs/episodes.grouping.json`:
`{"episodes": [{"title": ..., "session_ids": [...], "rationale": ...}, ...]}` — session
ids must round-trip exactly as given in the prompt. Re-run; then pass
`--grouping out/jobs/episodes.grouping.json` to `haid score` so both layers use the
same grouping.

### 4. Score — place each episode on the ladders

```
haid score --project P --days N --labels out/jobs/tag.labels.json --json
           [--grouping out/jobs/episodes.grouping.json]
```

Exit 3 writes one manifest **per episode per axis**:
`out/jobs/<episode>_<axis>.job.json`. Each contains `comparisons[]` (one fully-built
pairwise prompt each), a `schema` for the verdict, and a `fingerprint`.

This is the **one heavy fan-out** in the chain (~28 manifests × ~10 comparisons ≈ 280
judges, each prompt carrying two inlined diffs). Don't inline 280 big prompts into your
context or marshal them through args — both blow up. Use the committed, deterministic
pipeline instead; you **author nothing**:

1. **Split mechanically** (keeps every diff out of your context). Capture stdout with the
   Bash tool (UTF-8, no BOM — see the redirection gotcha):
   ```
   python .claude/skills/haid-report/scripts/split_score_manifests.py --job-dir out/jobs
   ```
   It writes one prompt file per comparison under `out/jobs/score_split/` and prints the
   tiny `args` object: `{"base": ..., "manifests": [{manifest, n, fingerprint}, ...]}`.
2. **Fan out with the committed workflow** — pass that object straight through (as a JSON
   value, not a string; the script normalizes either way):
   ```
   Workflow({ scriptPath: ".claude/workflows/haid-judge.js", args: <the splitter's object> })
   ```
   It spawns **one independent haiku judge per comparison** (reads exactly its one file, no
   other), and returns `[{manifest, fingerprint, winners, complete}]` with `winners` in
   comparison order.
3. **Write verdicts** for each returned group where `complete` is `true`:
   `out/jobs/<manifest>.verdicts.json` = `{"fingerprint": <from the group>, "winners":
   [...]}` — nothing else. A group with `complete: false` had a judge die (null winner) —
   re-run the workflow before writing; **never** write a null or short `winners` list.

Why a workflow *here* and direct `Agent` calls everywhere else: score is the only step
heavy enough that keeping the prompts on disk (judges `Read` one file) beats inlining. The
judge still sees exactly one comparison and nothing else, so the calibrated counterbalancing
and per-verdict isolation are preserved — it is **not** a license to read files in any other
step.

Notes that matter here:
- Which side is the subject is deliberately hidden (deterministic counterbalancing baked
  into the prompt text only). Don't try to infer or normalize it — just relay the raw
  A/B/tie answers in order.
- The fingerprint is the staleness guard. If a re-run fails with "stale verdicts", the
  manifest was regenerated since the answers were written: delete that verdicts file and
  re-judge from the fresh manifest. Never hand-edit a fingerprint.
- Wrong count or a value outside A/B/tie fails loudly by design — fix the answers, don't
  pad them.

Re-run and save stdout → `out/report/scores.json`. Typical cost: ~9–11 judgments per
episode per axis, two axes (difficulty, cleanliness).

### 5. Why — investigate the top waste anchors

```
haid why --project P --days N --json
```

Exit 3 writes `out/jobs/why.job.json`: triaged anchors (token-ranked, per-metric capped,
retries always considered), `recommended_model` (default **sonnet** — honor it unless the
user overrides), and one self-contained `prompt` per job with transcript paths, repo
path, and the note `schema`.

Spawn one **tool-using** agent per job at the recommended tier — these agents read
transcripts and the repo (Read/Grep/Glob), run 1–4 minutes and ~45–80k tokens each, so
fan out but don't be surprised by the cost. Each must return exactly one JSON note
matching `schema`. Write `out/jobs/why.notes.json`:
`{"notes": [{"anchor_id": ..., <schema fields>}, ...]}` — every anchor_id from the
manifest must be present. Read-back validation is strict and will name what's missing
or malformed.

Re-run and save stdout → `out/report/why.json`.

### 6. Report — compose and present

```
haid report --metrics out/report/metrics.json --why out/report/why.json
            --scores out/report/scores.json --tags out/report/tags.json
```

The deterministic what/why **digest** always prints first (findings from stated rules,
treatments matched mechanically from the shipped catalog, suppressed findings shown as
credits). Exit 3 writes `out/jobs/compose.job.json`: ONE holistic job,
`recommended_model: "opus"`, prompt embedding the full digest. Spawn one **opus** agent
(no tools needed) on `prompt`, constrained to `schema`. Write its structured output —
the bare composition object, no wrapper — to `out/jobs/compose.composition.json`.

Re-run: `validate_composition` rejects any recommendation citing a finding or treatment
the deterministic layer didn't produce. If it rejects, show the composer the validation
error and the digest's actual finding/treatment ids and have it re-emit — never edit the
composition to slip past the validator.

**Present the final rendered report to the user verbatim** (it's the product: window
score, credits first, ≤5 evidence-cited recommendations, watchlist, hedges). Don't
re-rank, soften, or add remedies of your own — the hedges and caveats are load-bearing
trust discipline. Cheap path: `--digest-only` skips the model entirely; offer it when the
user wants a zero-cost or fully-deterministic answer.

**Lead your wrap-up with the result, stated as a number — don't bury it in prose.** The
render computes the score and the percentile for you; your job is to *say them out loud*,
not paraphrase around them. The very first line of your wrap-up must quote, verbatim from
the rendered blocks:
- **the window score** — the `value` figure from the Scoreboard (`window score: X value`),
  not just the achievement total or difficulty rung; and
- **the percentile** — the percentile of that score against the community board. Note the
  **bundled** board snapshot ships empty, so the report's own "Community benchmark" block is
  the seed-bucket fallback and will *not* carry a percentile. To get a real one, run the
  read-only `haid rank --scores out/report/scores.json --refresh` (pulls the live board from
  Pages, uploads nothing) and quote its `percentile`. State it as "**you scored X, which puts
  you in the Nth percentile** of N comparable entries." If `rank` reports no comparable peers
  yet (seed bucket) or the live board can't be reached, say exactly that — there is no
  percentile to quote, and submitting would seed the bucket — rather than inventing one.
- **then ask, explicitly, whether they want to submit** — a plain opt-in question
  ("Want me to submit this to the community board?"), never an implication that submission
  is expected. Do not submit until the user says yes.

A wrap-up that names the achievement total and difficulty rung but not the `value` score
and percentile, or that mentions the leaderboard without asking, is the bug this section
exists to prevent. If scores are absent (empty diff / `--digest-only`), say there's no
score this run and skip the percentile/submit line.

**Then point the user to the four things they'll want next** (the render already includes a
"Scoreboard" block and a "Where to look" footer — reinforce them, don't bury them):
1. **The report** — saved under `out/report/` (the rendered narrative + the JSON inputs).
2. **The window score** — the single `value` figure (Σ achievement / Σ normalized tokens
   across scored episodes) plus the difficulty ceiling, shown in the Scoreboard. This is
   the number to track run-over-run — and the number you led with above.
3. **The visualization** — generate it from the live window with `haid viz` (step 6b) and
   point the user at the self-contained `out/report/haid-viz.html` (opens in any browser,
   no server). It reflects THIS run: real episode grouping + per-episode score badges.
4. **The leaderboard** — submitting is **opt-in and explicit** (step 7). You already asked
   above; here just remind them the window score is what it ranks and that `haid submit`
   shows the exact public row before pushing. Never submit or imply submission is expected.
   `haid rank --refresh` (read-only) is where the live percentile you quoted came from; the
   report's own "Community benchmark" block is the offline seed-bucket fallback.

### 6b. Visualize — render the window (deterministic, no model)

```
haid viz --project P --days N --scores out/report/scores.json
         --metrics out/report/metrics.json --out out/report/haid-viz.html
```

A self-contained HTML (CSS+JS+data inlined) opening from `file://`. **Episodes come from
the real pipeline, best-available first**: `--scores` (normal case → real grouping, titles,
and per-episode achievement/rung badges) > `--grouping` (grouping without scores) > a single
flat "window" episode if neither is given (the command warns and tells you to pass
`--scores`). `--metrics` adds the per-file flag overlay. No model, never pends. Sessions
with no active-timeline spine are skipped with a stderr note. Run it after `haid score`
(it reads that JSON); point the user at the output path in your wrap-up (item 3 above).

### 7. Community benchmark — view, then (opt-in) submit

The report's final rendering already includes a deterministic **"Community benchmark"**
context section when scores exist: it shows where this window lands against the shipped
board snapshot (same ladders + combiner only) and an opt-in invite to submit. That
section is computed locally and **uploads nothing** — `haid report --board FILE` can
point at a different board, otherwise the bundled snapshot is used.

Two explicit commands, both opt-in and summary-only (a leak check refuses anything path-
or title-shaped). **Never imply submission is expected** — it is default-off:

```
haid rank   --scores out/report/scores.json [--github-user USER] [--refresh]
haid submit --scores out/report/scores.json --github-user USER --project NAME [--dry-run]
```

- **`haid rank`** is read-only: prints the user's percentile vs the community. `--refresh`
  pulls the live board from Pages; otherwise the shipped snapshot. No account needed.
- **`haid submit`** is the only path that leaves the machine, and only when the user
  explicitly asks. It prints **exactly the row that becomes public + permanent**, then
  opens a validated GitHub PR (`git` + `gh`) adding `entries/<user>.json` to the separate
  **data-only** benchmark repo (`dv-hart/haid-benchmark`). Identity is the authenticated PR
  author (no local signature). Use `--dry-run` first to show the entry + the git/gh
  commands without pushing; pass `--yes` only when the user has confirmed. Needs a local
  checkout of the benchmark repo (`--repo PATH`, or auto-detected via its
  `.haid-benchmark-repo` marker). The repo-side workflows validate (hashes, leak guard,
  plausibility, author == username) and auto-merge. `haid benchmark` still emits the raw
  payload alone if that's all the user wants.

## Two kinds of boundary (tool-free vs tool-using)

Get this right or runs come back inconsistent and over-budget:

- **Tool-FREE judgment** — `tag`, `episodes`, `compose`. The prompt already carries
  everything the agent needs (message text, the whole digest are inlined). The agent's only
  legal action is to emit one structured-output object. Spawn these with **no tools**
  (schema-constrained output only) via direct `Agent` calls. A judge that reads a file, greps
  the repo, or re-fetches a diff is misconfigured — there is nothing to fetch, and every such
  tool turn is pure waste. One job → one structured answer, full stop.
- **Tool-free judgment, file-delivered** — `score` **only**. The verdict is logically
  tool-free (the pairwise comparison is fully self-contained), but at ~280 large prompts the
  whole batch can't live in your context or in `args`. So the splitter puts each comparison in
  its own file and the `haid-judge` workflow's judges take **exactly one `Read`** — of their
  own comparison file — then emit one structured verdict. One Read, one answer; reading any
  second file (or the repo) is the same misconfiguration as above. See step 4.
- **Tool-USING investigation** — `why` **only**. These agents are *supposed* to spend
  multiple tool turns reading transcripts and the repo (Read/Grep/Glob). This is the one
  boundary where 1–4 minutes and many tool calls per agent is correct.

If a ranking or judging agent took more than its allotted tool use (zero for tag/episodes/
compose, one Read for score), it was spawned with tools it should not have had. Constrain the
surface, not just the output schema.

## Runner rules (every boundary)

- **Prompt verbatim, author nothing**: the manifest `prompt` is the complete subagent
  prompt. Never wrap, paraphrase, re-order, or "improve" it; never debate phrasing. The
  judging prompts are calibrated and counterbalanced — your edits silently corrupt scores.
- **Model tiers**: labels, grouping, and pairwise placement = **haiku**; investigations =
  the manifest's `recommended_model` (sonnet default); composition = **opus**. Where a
  manifest carries `recommended_model`, it wins over this table.
- **Enforce schemas at spawn time.** Use structured-output/schema-constrained agents
  wherever available. Plain-text agents drift off enums and repeat the mistake when
  corrected — constrain first, validate after, re-spawn on repeat offenses.
- **Tolerant extraction**: agents wrap JSON in prose or code fences despite instructions.
  Extract the last complete JSON object from the reply before validating.
- **Parallel by default**: jobs within a manifest, and manifests within a step, are
  independent. Batch tool-using why-agents in smaller groups (~4) since each is heavy.
- **Fan out with direct `Agent` calls — and never hand-author a `Workflow`.** Every step
  except score fans out with direct parallel `Agent` calls: one subagent per job, its `prompt`
  verbatim with its `schema`, no tools for tool-free boundaries, and never a file path for the
  agent to `Read` (the transcript/diff is already inlined). **Score is the one exception** — it
  invokes the *committed* `haid-judge` workflow (`.claude/workflows/haid-judge.js`); invoke it,
  don't rewrite it. Do **not** author a one-off `Workflow` for any step: a model-authored
  script receives `args` verbatim and routinely marshals nested data as a JSON *string*, so
  `jobs.map(...)` throws `jobs.map is not a function` (the real tag-step failure). The committed
  workflow already carries the normalization shim
  (`const x = typeof args === 'string' ? JSON.parse(args) : args`); a prose instruction to
  "pass raw JSON" is not a guarantee, the shim is.
- **Don't recompute, don't improvise**: never answer a manifest job yourself in-line as
  the orchestrator — your context is contaminated with the whole window. Fresh subagents
  only. Never fabricate or pad an answers file to make a re-run pass; loud failures are
  the design.
- **Honest accounting**: relay the CLI's caveats (bridge incompleteness flags, baseline
  thinness, suppressed findings) to the user rather than smoothing them over.

## Failure and resume

- Any interruption: re-run the same command — disk state resumes the chain.
- Validation error on read-back: the message names the file and the defect; fix the
  answers (or delete the stale file and re-judge), then re-run.
- A subagent that dies or returns garbage twice: replace it; one bad judge among many is
  recoverable, a fabricated answer is not.
- Transcripts are perishable (`~/.claude/projects` ages out). If a requested window comes
  back thinner than the user expects, say so — and prefer running sooner over later.
