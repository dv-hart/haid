# How Am I Doing (HAID)

*A self-audit and coaching layer for Claude Code sessions.*

HAID reads your own Claude Code session transcripts, builds a graph of what
happened, and produces annotated, **coaching-oriented** reports. The aim is less
"here is your bill" and more "here is where you and the agent diverged, why, and
what to change."

**Nothing leaves your machine** unless you explicitly choose to submit aggregate
metrics.

> Status: **Phase 1 complete; the full scoring stack now runs on real sessions** — the
> deterministic pipeline runs end to end on real transcripts (163 tests, stdlib-only, no model
> in the loop):
> - **Session parsing** (`src/haid/session/`) — forest-aware JSONL parsing: dedup,
>   branch/rewind classification, subagent stitching, overflow resolution, SQLite cache.
> - **Session graph** (`src/haid/graph/`) — L0 spine + L1 action/IO graph
>   (reads/produces/edits from `structuredPatch`), signatures, per-timeline scoping.
> - **Waste metrics** (`src/haid/metrics/`) — `rereads`, `retries`, `retouched`,
>   `unused_context`: one rule each, run at **session and window scope**, as benchmarkable
>   token-rates placed against a per-scope baseline.
> - **Analysis window** (`src/haid/window.py`) — the multi-session unit metrics run over
>   (a project over a timeframe, default 30 days).
> - **Scoring** (`src/haid/scoring/`) — the relative achievement/cost value scorer
>   (difficulty + cleanliness placement, volume, normalized-token cost, value combiner),
>   calibration-validated. Built ahead of the earlier phases.
>
> - **`haid metrics`** (`src/haid/metrics/{json_out,view}.py` + CLI) — the measured substrate:
>   four waste metrics at **session and window scope**, each placed against a per-scope
>   baseline, as a Markdown inspection view + a JSON hand-off to the later "why" passes.
> - **Bridge** (`src/haid/bridge/`) — reconstructs an analysis window's net code diff from the
>   **transcript alone** (replay, no git) plus its normalized-token cost, so `haid bridge` and
>   `haid value --project/--session` now run the **full scoring stack on real sessions**
>   (previously the scorer only ran on supplied/calibration diffs).
>
> All validated on real transcripts (163 tests). The user-facing **report and visualization are
> the final product**, composing this substrate with the Phase-2/3 why-analysis and the value
> score. Next: the diagnosis router, episode segmentation (Phase 2), and the visualization
> (Phase 1.5). See [plans/roadmap.md](plans/roadmap.md) and [plans/phase1-build.md](plans/phase1-build.md).

## What this is not

Not another token counter. Raw usage accounting is already well covered
([ccusage](https://github.com/ryoppippi/ccusage) and similar). The entire value
lives one layer up, in **diagnosis and coaching** — telling you not what you
spent but how to get better. A tool that confidently misdiagnoses is worse than
nothing, because people act on it, so trustworthiness of the diagnosis is the
central design constraint throughout. See
[docs/trust-discipline.md](docs/trust-discipline.md).

## The one big idea: the session graph

Underneath everything is one data structure: a graph of the session(s). Turns
and tool-calls are nodes; edges capture *responds-to*, *reads*, and *produces*
relationships. The two headline features are just two operations on this one
graph:

- **"Why did you do X?"** → a backwards traversal from X to its trigger.
- **"Where did the tokens go?"** → a weighting over the same nodes.

Build the graph once; get both views from it. Design in
[docs/session-graph-design.md](docs/session-graph-design.md).

## Two orthogonal analysis passes

1. **User-anchored pass** — catches *misalignment*. Works backwards from user
   messages; **corrections are ground truth** ("no, I meant…", "that's wrong").
2. **Signature-scanning pass** — catches *silent inefficiency*. Scans for
   objective, reasoning-free waste signatures (redundant re-reads, retry loops,
   re-touched lines, unused context).

The two are orthogonal: one finds where the agent did the *wrong thing*, the
other where it did the *right thing wastefully*. See
[docs/architecture.md](docs/architecture.md).

## Documentation map

| Doc | What's in it |
|-----|--------------|
| [docs/vision.md](docs/vision.md) | The full concept, goals, and the canonical test case |
| [docs/architecture.md](docs/architecture.md) | The two-pass method and how the pieces fit |
| [docs/session-graph-design.md](docs/session-graph-design.md) | Node/edge taxonomy, episodes, the two core operations |
| [docs/detectors.md](docs/detectors.md) | Detector catalog + waste metrics as graph queries |
| [docs/intent-taxonomy.md](docs/intent-taxonomy.md) | Two-axis message classification + purpose timeline + drift |
| [docs/scoring-rubric.md](docs/scoring-rubric.md) | Achievement vs. cost — the **relative** value verdict (revised; see ladder/playbook) |
| [docs/difficulty-ladder.md](docs/difficulty-ladder.md) | The validated difficulty scorer (reference ladder + placement) |
| [docs/cleanliness-ladder.md](docs/cleanliness-ladder.md) | The cleanliness/parsimony scorer (reference ladder + placement) |
| [docs/axis-calibration-playbook.md](docs/axis-calibration-playbook.md) | Self-contained recipe to calibrate a new scoring axis (worked example: cleanliness; originality calibrated then dropped) |
| [docs/calibration-pilot-1.md](docs/calibration-pilot-1.md) | Pilot report: why mined review-signals don't validate difficulty |
| [docs/visualization.md](docs/visualization.md) | The time-layered bus diagram (left-in/right-out, bundled) |
| [docs/claude-code-data-format.md](docs/claude-code-data-format.md) | **Verified** Claude Code on-disk data reference |
| [docs/data-inventory.md](docs/data-inventory.md) | Field catalog from 38 real sessions: what's auto-taggable vs. inferred |
| [docs/data-structure-report.md](docs/data-structure-report.md) | Real annotated records → the graph they produce (Tier 1 & Tier 2 walkthrough) |
| [docs/trust-discipline.md](docs/trust-discipline.md) | Cite-or-unknown, hedging, no-traceable-origin |
| [docs/tooling-landscape.md](docs/tooling-landscape.md) | Existing tools and what to build on |
| [docs/decisions/](docs/decisions/) | Architecture Decision Records (ADRs) |
| [plans/roadmap.md](plans/roadmap.md) | Phased delivery plan |
| [plans/mvp.md](plans/mvp.md) | The minimum thing that tests the core risk |
| [plans/phase1-build.md](plans/phase1-build.md) | The concrete Phase-1 build sequence + progress |
| [plans/open-questions.md](plans/open-questions.md) | Decisions to make / behaviors to verify early |

## Repository layout

```
HAID/
├── README.md                 # you are here
├── docs/                     # design & reference documentation
│   └── decisions/            # ADRs
├── plans/                    # roadmap, MVP spec, open questions
├── research/                 # raw research reports (inputs to the docs)
├── calibration/              # the scoring-axis calibration harness (experiment code)
├── src/haid/                 # implementation
│   ├── session/              #   Phase-1 parse: forest model, subagents, overflow, cache
│   ├── graph/                #   L0 spine + L1 IO graph (incl. Bash read/write parsing)
│   ├── metrics/              #   the four waste metrics + baseline + `haid metrics` emitter
│   ├── window.py             #   the multi-session analysis window
│   ├── scoring/              #   relative value scorer (difficulty/cleanliness/volume/cost/value)
│   └── bridge/               #   transcript→(diff, usage) reconstruction (the bridge)
├── tests/                    # session/ graph/ metrics/ scoring/ bridge/ suites (+ fixtures/)
└── scripts/                  # build_metric_baselines.py + CLI helpers
```
