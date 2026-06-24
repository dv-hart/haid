# How Am I Doing (HAID)

*A self-audit and coaching layer for Claude Code sessions.*

HAID reads your own Claude Code session transcripts, builds a graph of what
happened, and produces annotated, **coaching-oriented** reports. The aim is less
"here is your bill" and more "here is where you and the agent diverged, why, and
what to change."

**Nothing leaves your machine** unless you explicitly choose to submit aggregate
metrics.

> Status: **the full coaching pipeline runs end to end on real sessions** — stdlib-only,
> with no model in the loop inside the CLI (model judgment is delegated to the host agent
> via job manifests; see [Activating in Claude Code](#activating-in-claude-code)). The chain
> is `metrics → tag → episodes → score → why → report`:
> - **Session parsing** (`src/haid/session/`) — forest-aware JSONL parsing: dedup,
>   branch/rewind classification, subagent stitching, overflow resolution, SQLite cache.
> - **Session graph** (`src/haid/graph/`) — L0 spine + L1 action/IO graph
>   (reads/produces/edits from `structuredPatch`), signatures, per-timeline scoping.
> - **Waste metrics** (`src/haid/metrics/`) — `rereads`, `retries`, `retouched`,
>   `unused_context`: one rule each, run at **session and window scope**, as benchmarkable
>   token-rates placed against a per-scope baseline (`haid metrics`).
> - **Analysis window** (`src/haid/window.py`) — the multi-session unit metrics run over
>   (a project over a timeframe, default 30 days).
> - **Bridge** (`src/haid/bridge/`) — reconstructs a window's net code diff from the
>   **transcript alone** (replay, no git) plus its normalized-token cost (`haid bridge`).
> - **Scoring** (`src/haid/scoring/`) — the relative achievement/cost value scorer
>   (difficulty + cleanliness placement, volume, normalized-token cost, value combiner),
>   calibration-validated (`haid volume`/`cost`/`place`/`value`).
> - **Intent tagging** (`src/haid/intent/`) — move × work-type + purpose-snapshot labels
>   for every user message (`haid tag`).
> - **Episodes** (`src/haid/episodes/`) — group whole sessions into the git-free PR proxy and
>   score each as a per-episode value distribution (`haid episodes`, `haid score`).
> - **Why-pass** (`src/haid/why/`) — per-anchor investigation agents over the top waste
>   instances, with cited evidence and hedged remedies (`haid why`).
> - **Report** (`src/haid/report/`) — the compositor: a deterministic what/why digest plus a
>   composed coaching report, with validated recommendations (`haid report`).
> - **Visualization** (`src/haid/viz/`) — a self-contained HTML render of the window (the
>   time-layered bus diagram) from the same substrate (`haid viz`).
> - **Community benchmark** (`src/haid/report/benchmark.py`) — a summary-only, opt-in payload
>   (`haid benchmark`), a read-only local comparison vs the board (`haid rank`), and the
>   PR-based opt-in submission (`haid submit`).
>
> All validated on real transcripts (`python -m pytest`, stdlib-only). The user-facing
> **report and visualization are the final product**. See [plans/roadmap.md](plans/roadmap.md).

## Installation

HAID is on PyPI (stdlib-only, no dependencies, Python ≥ 3.10):

```bash
pip install haid
```

On Ubuntu/WSL without a venv set up, a user install works fine:

```bash
python3 -m pip install --user haid
# CLI lands at ~/.local/bin/haid — make sure that's on your PATH
```

Verify it works:

```bash
haid --help
haid metrics --project ~/path/to/some/project --days 30
```

`haid metrics` is fully deterministic (no model calls) and runs against the
Claude Code transcripts already on your machine — it's the quickest smoke test.

**Where to install:** HAID reads transcripts from `~/.claude/projects/` on the
machine where the sessions ran. If you use Claude Code inside WSL, install HAID
inside WSL too. (A Windows-side install can still reach WSL transcripts via UNC
`--session` paths like `//wsl.localhost/Ubuntu/home/<user>/.claude/projects/<slug>/*.jsonl`,
but `--project` discovery won't cross the boundary.)

## Activating in Claude Code

The `haid` CLI never calls a model itself. The full coaching pipeline
(tag → episodes → score → why → report) is driven *by Claude Code* through the
[`haid-report` skill](.claude/skills/haid-report/SKILL.md): the CLI writes job
manifests at each model boundary, and the skill tells Claude how to fulfill
them with subagents and resume.

1. Install the CLI (above) so `haid` is on the PATH of the machine/shell where
   Claude Code runs.
2. Copy the skill from this repo into Claude Code's skills directory:

   ```bash
   # available in every project:
   mkdir -p ~/.claude/skills/haid-report
   cp .claude/skills/haid-report/SKILL.md ~/.claude/skills/haid-report/

   # …or for a single project only:
   mkdir -p <project>/.claude/skills/haid-report
   cp .claude/skills/haid-report/SKILL.md <project>/.claude/skills/haid-report/
   ```

   (If you're working inside this repo, the skill is already active — it's a
   project skill here.)
3. Start a new Claude Code session and ask **"how am I doing?"**, or invoke
   `/haid-report` directly. Claude will run the chain and present the coaching
   report. For a zero-cost, fully deterministic answer, ask for the
   `--digest-only` report or just the waste metrics.

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
| [docs/metrics-output-schema.md](docs/metrics-output-schema.md) | The `haid metrics --json` contract consumed by the later passes |
| [docs/treatments.md](docs/treatments.md) | The remedy catalog matched mechanically in `haid report` |
| [docs/axis-calibration-playbook.md](docs/axis-calibration-playbook.md) | Self-contained recipe to calibrate a new scoring axis (worked example: cleanliness; originality calibrated then dropped) |
| [docs/visualization.md](docs/visualization.md) | The time-layered bus diagram (left-in/right-out, bundled) |
| [docs/claude-code-data-format.md](docs/claude-code-data-format.md) | **Verified** Claude Code on-disk data reference |
| [docs/data-inventory.md](docs/data-inventory.md) | Field catalog from 38 real sessions: what's auto-taggable vs. inferred |
| [docs/data-structure-report.md](docs/data-structure-report.md) | Real annotated records → the graph they produce (Tier 1 & Tier 2 walkthrough) |
| [docs/trust-discipline.md](docs/trust-discipline.md) | Cite-or-unknown, hedging, no-traceable-origin |
| [docs/tooling-landscape.md](docs/tooling-landscape.md) | Existing tools and what to build on |
| [docs/decisions/](docs/decisions/) | Architecture Decision Records (ADRs) |
| [plans/roadmap.md](plans/roadmap.md) | Phased delivery plan |
| [plans/agent-analysis.md](plans/agent-analysis.md) | The model-in-the-loop "why" pass design (episodes, anchors, two-stage) |
| [plans/community-benchmark.md](plans/community-benchmark.md) | The opt-in self-reported benchmark design (ADR-0005) |
| [plans/open-questions.md](plans/open-questions.md) | Decisions to make / behaviors to verify early |

The shipped Phase-1 build logs (`mvp.md`, `phase1-build.md`, `step4-build.md`) are kept for
history under [plans/archive/](plans/archive/).

## Repository layout

```
HAID/
├── README.md                 # you are here
├── docs/                     # design & reference documentation
│   └── decisions/            # ADRs
├── plans/                    # roadmap + active design notes (shipped build-logs in plans/archive/)
├── src/haid/                 # implementation
│   ├── session/              #   parse: forest model, subagents, overflow, cache
│   ├── graph/                #   L0 spine + L1 IO graph (incl. Bash read/write parsing)
│   ├── metrics/              #   the four waste metrics + baseline + `haid metrics` emitter
│   ├── window.py             #   the multi-session analysis window
│   ├── bridge/               #   transcript→(diff, usage) reconstruction (the bridge)
│   ├── scoring/              #   relative value scorer (difficulty/cleanliness/volume/cost/value)
│   ├── intent/               #   move × work-type message tagging (`haid tag`)
│   ├── episodes/             #   session→episode grouping + per-episode scoring
│   ├── why/                  #   per-anchor investigation agents (`haid why`)
│   └── report/               #   digest + composed coaching report (`haid report`)
├── tests/                    # session/ graph/ metrics/ scoring/ bridge/ intent/ episodes/ why/ report/
└── scripts/                  # build_metric_baselines.py (regenerates shipped data)
```

> The one-time scoring-axis **calibration harness** and the raw **research probes** that seeded
> the docs live on the `archive/experiments` branch — their validated output already ships in
> `src/haid/data/`, so they're kept for provenance rather than on `main`.
