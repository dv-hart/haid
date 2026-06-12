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

```
haid metrics ─┐  (no model)
haid tag ─────┤  haiku × ~1/user-message     labels
haid episodes ┤  haiku × 1 (optional)        grouping      ──►  haid report ──► opus × 1 ──► final report
haid score ───┤  haiku × ~9-11/episode/axis  verdicts                │
haid why ─────┘  sonnet × top anchors        notes                   └─► haid benchmark (opt-in only)
```

## The universal loop

Every model boundary follows the same cycle. Learn it once:

1. Run the `haid` command. **Exit 0** → done, output is final. **Exit 3** → pending: the
   command printed the manifest path(s) and the exact answers-file path + shape it wants.
2. Read the manifest. Fan out subagents over its job prompts (parallel — jobs are
   independent).
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

Exit 3 writes `out/jobs/tag.job.json`: `jobs[]` of `{uuid, session_id, prompt}` plus a
`schema` with **enum-constrained** `move` and `work_type`. Spawn one **haiku** subagent
per job on `prompt`, constrained to `schema`.

Write `out/jobs/tag.labels.json`:
`{"labels": [{"uuid": ..., "move": ..., "work_type": ..., "purpose": ...}, ...]}`.

**You are the schema enforcement.** The tag read-back path does not validate enums, so a
label like `move: "question"` (a work-type, not a move) silently poisons everything
downstream. Validate every label against the manifest's enums before writing the file.
On a violation, retry that one job once with the enum list restated; if it violates
again, spawn a fresh agent rather than arguing with the same one.

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

For each manifest, spawn one **haiku** judge per `comparisons[i].prompt`, constrained to
`schema`; collect each judge's `winner` (`"A"`, `"B"`, or `"tie"`). Write
`out/jobs/<episode>_<axis>.verdicts.json`:
`{"fingerprint": <copied from the manifest>, "winners": ["A", "B", "tie", ...]}` —
**in comparison order**, one winner per comparison, nothing else.

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

**Present the final rendered report to the user verbatim** (it's the product: credits
first, ≤5 evidence-cited recommendations, watchlist, hedges). Don't re-rank, soften, or
add remedies of your own — the hedges and caveats are load-bearing trust discipline.
Cheap path: `--digest-only` skips the model entirely; offer it when the user wants a
zero-cost or fully-deterministic answer.

### 7. Benchmark payload (explicit opt-in ONLY)

```
haid benchmark --scores out/report/scores.json --github-user USER --project NAME --out FILE
```

Summary-statistics-only payload for the ADR-0005 community benchmark (a leak check
refuses anything path- or title-shaped; `signature: null` until `haid submit` ships).
The benchmark is opt-in by design — build this **only when the user explicitly asks**,
and never imply submission is expected. Signing/PR submission is not built yet; say so.

## Runner rules (every boundary)

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
