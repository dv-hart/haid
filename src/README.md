# src — HAID product code

Installable package (`pyproject.toml` at repo root, src-layout): `pip install -e .`.
Stdlib only, model-free; the one model-judgment boundary is `scoring/compare.py`, which
delegates to host-agent subagents rather than calling an API in-process.

## The deterministic pipeline: PARSE → GRAPH → WINDOW → METRICS (+ BRIDGE → SCORING)

### `haid.session` — parse *(built 2026-06-06)*
Forest-aware JSONL parsing of Claude Code transcripts. `records.py` (typed records),
`parse.py` (tolerant reader), `forest.py` (dedup + branch/rewind classification → timelines),
`subagents.py` (stitch `subagents/agent-*.jsonl` via `meta.toolUseId`), `overflow.py`
(`tool-results/` overflow), `cache.py` (SQLite parse cache), `discover.py` (find a project's
sessions), `loader.py` (`load_session` → main + subagents + overflow + forest).

### `haid.graph` — the session graph *(built 2026-06-06)*
L0 spine + L1 action/IO graph, all Tier-1 (every field a transcript literal). `model.py`
(Turn / ToolCall / File / Region / Edge / SessionGraph; `is_read`/`is_write` predicates),
`build.py` (`build_graph`, `timeline_toolcalls`; reads/produces/edits from
`structuredPatch`; Bash status via `is_error`), `bash_read.py`/`bash_write.py` (parse
shell `cat`/`sed`/`>`/`tee`… into derived reads/writes, `via:"shell"`; `parse_heredoc_write` recovers `cat > f <<EOF`
content into `ToolCall.write_content`), `signature.py`
(normalized Read/Edit/Bash signatures).

### `haid.window` — the analysis window *(built 2026-06-06)*
The multi-session unit metrics run over. `for_project(path, days=30)` / `from_files(paths)`
→ `(WindowView, [Session])`. The `WindowView.active_stream` (active timelines across sessions,
chronological) is what the metrics fold over; abandoned rewind branches are excluded.

### `haid.metrics` — waste metrics + the emitter *(built 2026-06; metric × scope)*
Four metrics, **one rule each**, run at `session` and `window` scope (scope = the memory
window the same rule folds over; see [../docs/metrics-output-schema.md](../docs/metrics-output-schema.md)).
- **`rereads.py` / `retries.py` / `retouched.py` / `unused_context.py`** — each a single
  `_core(stream)` fold returning a `MetricResult`.
- **`base.py`** — `MetricResult` / `Instance` / `WindowView`, `est_tokens`, `iter_sessions`.
- **`__init__.py`** — `run_window(view)` (whole stream → cross-session signals) and
  `run_sessions(view)` (per session).
- **`baseline.py`** — per-scope placement (`position(metric, scope, rate)` / `verdict`),
  loaded from packaged `data/metric_baselines.json` (built by
  [../scripts/build_metric_baselines.py](../scripts/build_metric_baselines.py)).
- **`json_out.py`** — the JSON hand-off contract (Phase-2/3 input); **`view.py`** — the
  Markdown inspection view, rendered from the same dict. `haid metrics [--project|--session]`.

The product framing: this is the measured **substrate**, not the user-facing report. The
report + visualization compose it with the Phase-2/3 "why" passes and the value score.

## The scoring half (outcome / value)

### `haid.scoring` — relative achievement & value *(built 2026-06-05/06)*
Scores a diff by placing it against fixed reference ladders; `value = achievement / cost`.
- **`volume.py`** — weighted surviving-LOC by file kind (`haid volume`).
- **`anchors.py`** — loads the locked difficulty/cleanliness ladders + reference diffs.
- **`placement.py`** — `place(diff, axis, backend)` → relative rung (`haid place`).
- **`compare.py`** — the model-judgment boundary: `ReplayBackend` (saved verdicts) and
  `HarnessBackend` (delegates comparisons to host-agent subagents).
- **`cost.py`** — normalized-token cost (type × tier weights, never dollars; `haid cost`).
- **`value.py`** — `achievement = volumeᵅ·D(difficulty)·C(cleanliness)`, `value = achievement
  / normalized_tokens` (`haid value`).
- **`../diffio.py` / `../filekind.py`** — shared unified-diff parsing + file-kind weighting.

## The bridge (real sessions → scorer inputs)

### `haid.bridge` — transcript→(diff, usage) reconstruction *(built 2026-06-07)*
The join between the two halves: turns an analysis window into the scorer's two inputs.
**Replay-only, no git** (decision: the bash-write-to-source gap measured ~0–1% on real
projects — see [../plans/roadmap.md](../plans/roadmap.md) Phase-5 Bridge note).
- **`reconstruct.py`** — rebuilds each file's net diff from the transcript: Edit
  `oldString`→`newString` + `originalFile`, Write `content`, Bash heredoc `write_content`.
  Buffer-mode is net-correct and self-detects untracked shell writes (the `originalFile` chain
  must match the running content); a hunks-mode fallback uses `structuredPatch` when a
  pre-existing file's full baseline was never captured. `sed -i`/plain-`>` writes are detected
  and **flagged**, never silently dropped.
- **`usage.py`** — normalized-token cost over the window (counts **all** branches incl.
  abandoned + subagents; diff is active-branch end-state only — the deliberate asymmetry).
- **`__init__.window_inputs(view, sessions)`** → `BridgeResult(diff, cost, files, caveats)`.

## CLI
`haid.cli` / `__main__.py` — subcommands `metrics`, `volume`, `cost`, `place`, `value`,
`bridge`. `value` accepts EITHER `--diff/--usage` OR `--project/--session` (the bridge runs
the full stack on real sessions); `bridge` reconstructs + prints the diff/cost/caveats.

## Tests & status
`tests/` (163 passing, deterministic, model-free). The scoring stack now runs end-to-end on
real sessions via the bridge. Remaining for Phase 1: DoD validation on the maintainer's own
sessions. Deferred: the waste→value reconciliation (expressing waste in normalized tokens) and
the **episode-grain** bridge refinement (per-episode diffs, needs Phase-2 episodes). See
[../plans/roadmap.md](../plans/roadmap.md) and [../plans/phase1-build.md](../plans/phase1-build.md).
