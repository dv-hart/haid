# How Am I Doing (HAID)

*Score, visualize, and coach your Claude Code sessions — from the transcripts already on your machine.*

HAID turns your own Claude Code session transcripts into **one number you can track and
rank**, a **visualization** of where the work and the tokens actually went, and a
**coaching report** on what to do differently next time. It runs locally, against the
`~/.claude/projects/` transcripts you already have — no instrumentation, no account, nothing
leaves your machine unless you explicitly opt in.

```bash
pip install haid
```

Then, inside Claude Code, just ask: **"how am I doing?"**

---

## 🏆 The leaderboard

Every analyzed window of work gets a single **value score**:

```
value  =  Σ achievement  ÷  Σ normalized tokens
```

— *how much you got done, per token spent.* That one number is what you track run over run,
and it's what the **opt-in community leaderboard** ranks. The board is:

- **Default-off and local-first.** Viewing uploads *nothing*: `haid rank` downloads the
  public distribution and computes your percentile **client-side**.
- **Summary-only when you do submit.** `haid submit` posts a signed *score summary* —
  per-axis positions, the value figure, token totals, ladder/config version hashes. **Never
  your transcripts, diffs, or prompts** (a leak check refuses anything path- or title-shaped).
- **Zero-backend and tamper-evident.** Submitting opens a GitHub PR against a separate
  data-only repo ([`dv-hart/haid-benchmark`](https://github.com/dv-hart/haid-benchmark));
  identity comes free from the PR author, and the merged, append-only log is rendered by
  GitHub Pages → [the public board](https://dv-hart.github.io/haid-benchmark/).

It's deliberately **trust-but-verify, low-stakes** — a self-reported board, labeled as such,
with a plausibility check as the only gate. See [ADR-0005](docs/decisions/0005-community-benchmark.md).

## The big idea: an achievement ladder, not a token counter

A token count tells you what you *spent*. It says nothing about what you *got*. HAID's core
idea is to measure achievement **relatively**, by placing your work against a fixed, calibrated
**reference ladder** of real code changes — then dividing by what it cost.

```
achievement  =  volume^0.5  ·  Difficulty  ·  Cleanliness
value        =  achievement  ÷  normalized-token cost
```

- **Volume** — deterministic surviving lines in the final diff, weighted by file kind
  (hand-written logic > config > generated). Sub-linear, so a small excellent change beats a
  big mediocre one.
- **Difficulty** — *not* lines of code. An LLM judge places your diff on a reference ladder
  by asking **"what fraction of working engineers could produce THIS correctly?"** — explicitly
  ignoring size and surface sophistication. Calibration-validated (Spearman ρ ≈ 0.87 vs. an
  expensive full pairwise sort). See [difficulty-ladder.md](docs/difficulty-ladder.md).
- **Cleanliness** — a parsimony placement against its own ladder ("achieves the purpose with
  less unnecessary complexity?"), a steep penalty that stops LOC-padding from buying score.
  See [cleanliness-ladder.md](docs/cleanliness-ladder.md).
- **Cost** — tokens weighted by type and model tier into a single *normalized-token* unit
  (relative effort, not a dollar bill), always reported with the full per-type/per-tier breakdown.

Achievement is scored on the **final artifact**, judged as if a human handed it to you cold —
it has nothing to do with how many tokens it took. That decoupling is the whole design: a
flawless-looking session that burned a fortune on a tiny, unoriginal change is a *bad ratio*,
and HAID will say so. The axes are reported separately and **never collapsed**, so the score
is always auditable. Method and calibration: [scoring-rubric.md](docs/scoring-rubric.md).

## The visualization

The score's companion is a self-contained **HTML visualization** of the window — a
time-layered bus diagram with the agent spine down the middle, files in the gutters
(**left = reads in, right = writes out**), color by file, width by token volume, and
per-episode achievement badges from the same scoring run. Opens in any browser, no server:

```bash
haid viz --project ~/path/to/project --days 30 --scores out/report/scores.json
```

Same substrate, two views: *where the work went* and *where the tokens went*. Design in
[visualization.md](docs/visualization.md).

## The coaching report

The score tells you **where** you stand; the report tells you **what to change**. HAID runs
two orthogonal passes over the session graph:

1. **User-anchored pass** — catches *misalignment*. Works backward from your messages;
   **corrections are ground truth** ("no, I meant…", "that's wrong").
2. **Signature-scanning pass** — catches *silent inefficiency*: redundant re-reads, retry
   loops, re-touched lines, unused context — objective, reasoning-free waste signatures.

Flagged hotspots are investigated by per-anchor agents that **cite their evidence and hedge
their remedies**, then matched against a [vetted treatment catalog](docs/treatments.md). The
guiding constraint everywhere: **a tool that confidently misdiagnoses is worse than nothing**,
so the bar is cite-or-say-unknown ([trust-discipline.md](docs/trust-discipline.md)). The two
passes are complementary — one finds where the agent did the *wrong thing*, the other where
it did the *right thing wastefully* ([architecture.md](docs/architecture.md)).

## Install

HAID is on PyPI — **stdlib-only, no dependencies, Python ≥ 3.10**:

```bash
pip install haid
haid --help

# the quickest, fully-deterministic smoke test (no model calls):
haid metrics --project ~/path/to/some/project --days 30
```

On Ubuntu/WSL without a venv, a user install works fine (ensure `~/.local/bin` is on `PATH`):

```bash
python3 -m pip install --user haid
```

**Where to install:** HAID reads transcripts from `~/.claude/projects/` *on the machine where
the sessions ran*. If you use Claude Code inside WSL, install HAID inside WSL too. (A
Windows-side install can still reach WSL transcripts via UNC `--session` paths like
`//wsl.localhost/Ubuntu/home/<user>/.claude/projects/<slug>/*.jsonl`, but `--project`
discovery won't cross the boundary.)

## Activating in Claude Code

The `haid` CLI **never calls a model itself.** The full pipeline
(`metrics → tag → episodes → score → why → report`) is driven *by Claude Code* through the
[`haid-report` skill](.claude/skills/haid-report/SKILL.md): the CLI writes a job manifest at
each model boundary, and the skill tells Claude how to fulfill it with subagents and resume.

1. Install the CLI (above) so `haid` is on the PATH where Claude Code runs.
2. Copy the skill into Claude Code's skills directory:

   ```bash
   # available in every project:
   mkdir -p ~/.claude/skills/haid-report
   cp .claude/skills/haid-report/SKILL.md ~/.claude/skills/haid-report/

   # …or scoped to a single project:
   mkdir -p <project>/.claude/skills/haid-report
   cp .claude/skills/haid-report/SKILL.md <project>/.claude/skills/haid-report/
   ```

   (Working inside this repo, the skill is already active — it's a project skill here.)
3. Start a new session and ask **"how am I doing?"**, or invoke `/haid-report`. Claude runs
   the chain and presents the report. For a **zero-cost, fully deterministic** answer, ask for
   the `--digest-only` report or just the waste metrics.

## How it works under the hood: one session graph

Everything above is computed from a single data structure: a **graph of the session(s)**.
Turns and tool-calls are nodes; edges capture *responds-to*, *reads*, and *produces*
relationships. Build the graph once and every feature falls out as an operation on it —
*"why did you do X?"* is a backward traversal to X's trigger; *"where did the tokens go?"* is a
weighting over the same nodes; achievement is a placement of the net diff it produces. The
pipeline is stdlib-only and deterministic up to the model-judgment boundaries:

- **Session parsing** (`src/haid/session/`) — forest-aware JSONL parsing: dedup, branch/rewind
  classification, subagent stitching, overflow resolution, SQLite parse-cache.
- **Session graph** (`src/haid/graph/`) — L0 spine + L1 action/IO graph (reads/produces/edits
  from `structuredPatch`), signatures, per-timeline scoping.
- **Waste metrics** (`src/haid/metrics/`) — `rereads`, `retries`, `retouched`,
  `unused_context`; one rule each, as benchmarkable token-rates vs. a per-scope baseline.
- **Bridge** (`src/haid/bridge/`) — reconstructs a window's net code diff from the **transcript
  alone** (replay, no git) plus its normalized-token cost.
- **Scoring** (`src/haid/scoring/`) — the relative achievement/cost value scorer
  (difficulty + cleanliness placement, volume, cost, value combiner), calibration-validated.
- **Intent / Episodes / Why / Report** (`src/haid/{intent,episodes,why,report}/`) — message
  tagging, the git-free PR-proxy grouping, the cited investigation pass, and the compositor.

Design: [session-graph-design.md](docs/session-graph-design.md). The whole chain is validated
on real transcripts (`python -m pytest`); see [plans/roadmap.md](plans/roadmap.md).

## What this is not

Not another token counter. Raw usage accounting is already well covered by
[ccusage](https://github.com/ryoppippi/ccusage) and similar. HAID's entire value lives one
layer up — in **scoring, diagnosis, and coaching**: telling you not what you spent, but how
much you achieved per token and how to get better.

## Documentation map

| Doc | What's in it |
|-----|--------------|
| [docs/vision.md](docs/vision.md) | The full concept, goals, and the canonical test case |
| [docs/architecture.md](docs/architecture.md) | The two-pass method and how the pieces fit |
| [docs/scoring-rubric.md](docs/scoring-rubric.md) | Achievement vs. cost — the **relative** value verdict |
| [docs/difficulty-ladder.md](docs/difficulty-ladder.md) | The validated difficulty scorer (reference ladder + placement) |
| [docs/cleanliness-ladder.md](docs/cleanliness-ladder.md) | The cleanliness/parsimony scorer (reference ladder + placement) |
| [docs/axis-calibration-playbook.md](docs/axis-calibration-playbook.md) | Self-contained recipe to calibrate a new scoring axis |
| [docs/treatments.md](docs/treatments.md) | The remedy catalog matched mechanically in `haid report` |
| [docs/visualization.md](docs/visualization.md) | The time-layered bus diagram (left-in/right-out, bundled) |
| [docs/session-graph-design.md](docs/session-graph-design.md) | Node/edge taxonomy, episodes, the two core operations |
| [docs/detectors.md](docs/detectors.md) | Detector catalog + waste metrics as graph queries |
| [docs/intent-taxonomy.md](docs/intent-taxonomy.md) | Two-axis message classification + purpose timeline + drift |
| [docs/metrics-output-schema.md](docs/metrics-output-schema.md) | The `haid metrics --json` contract |
| [docs/claude-code-data-format.md](docs/claude-code-data-format.md) | **Verified** Claude Code on-disk data reference |
| [docs/trust-discipline.md](docs/trust-discipline.md) | Cite-or-unknown, hedging, no-traceable-origin |
| [docs/decisions/](docs/decisions/) | Architecture Decision Records (ADRs) |
| [plans/roadmap.md](plans/roadmap.md) | Phased delivery plan |
| [plans/community-benchmark.md](plans/community-benchmark.md) | The opt-in self-reported leaderboard design (ADR-0005) |

## Repository layout

```
HAID/
├── README.md                 # you are here
├── docs/                     # design & reference documentation (decisions/ = ADRs)
├── plans/                    # roadmap + active design notes (shipped build-logs in plans/archive/)
├── src/haid/                 # implementation
│   ├── session/              #   parse: forest model, subagents, overflow, cache
│   ├── graph/                #   L0 spine + L1 IO graph (incl. Bash read/write parsing)
│   ├── metrics/              #   the four waste metrics + baseline + `haid metrics`
│   ├── window.py             #   the multi-session analysis window
│   ├── bridge/               #   transcript→(diff, usage) reconstruction
│   ├── scoring/              #   relative value scorer (difficulty/cleanliness/volume/cost/value)
│   ├── intent/               #   move × work-type message tagging (`haid tag`)
│   ├── episodes/             #   session→episode grouping + per-episode scoring
│   ├── why/                  #   per-anchor investigation agents (`haid why`)
│   ├── report/               #   digest + composed report + benchmark payload (`haid report`)
│   └── viz/                  #   self-contained HTML render (`haid viz`)
├── tests/                    # session/ graph/ metrics/ scoring/ bridge/ intent/ episodes/ why/ report/
└── scripts/                  # baseline/benchmark-pin regeneration
```

> The one-time scoring-axis **calibration harness** and the raw **research probes** that
> seeded the docs live on the `archive/experiments` branch — their validated output already
> ships in `src/haid/data/`, so they're kept for provenance rather than on `main`.
