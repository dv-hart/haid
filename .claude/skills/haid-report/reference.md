# HAID report — reference (design rationale, forensics, internals)

Loaded on demand, not on every run. **None of this changes the next action** — it explains *why*
the rules in [`SKILL.md`](SKILL.md) are what they are, and carries deep internals for when something
looks wrong. Read the section you need; skip the rest.

## Contents
- [Version provenance & the stale-CLI tell](#version-provenance--the-stale-cli-tell)
- [Why per-branch tagging](#why-per-branch-tagging)
- [Why tag & score use committed workflows (the args-shim story)](#why-tag--score-use-committed-workflows-the-args-shim-story)
- [Scoring internals: counterbalancing, severity, fingerprint](#scoring-internals-counterbalancing-severity-fingerprint)
- [`haid submit` / `rank` internals](#haid-submit--rank-internals)

## Version provenance & the stale-CLI tell

The chain is only as correct as the `haid` that runs it; the CLI and the plugin release in lockstep
from one repo. A *separate* pip install can shadow the plugin's CLI on PATH, so computation silently
runs on stale code while `/plugin` reports a current version. The classic tell is the **window value
rounding to 0**: pre-0.0.7 CLIs lack the GnTok `cost_unit` rescale, so `achievement / raw_nTok ≈
1e-7 → 0.0`.

Provenance carries through every artifact — `metrics.json` (and downstream docs) stamp `haid_version`
= the version that actually computed. After step 1, confirm `metrics.json.haid_version` equals
`haid --version`; if they disagree, there are two haids installed — surface it, and quote the
computing version in the final report. Remedy for a mismatch: `pip install -U haid` to align the PATH
CLI with the plugin, or invoke the plugin-bundled CLI explicitly.

## Why per-branch tagging

One agent per message re-embedded each message's context, so the manifest grew quadratically (the
~800KB-too-big-to-relay failure). Per-branch shows each transcript once → the manifest is linear in
transcript size, the agent count drops to ~one per session, and each message's causal context is
simply the transcript above it. Causality is preserved by instruction (judge each mark by what
precedes it, no hindsight); branches are split so a rewound stretch of work is still labeled and
never bleeds into the active branch.

## Why tag & score use committed workflows (the args-shim story)

Both steps fan out enough self-contained, prompt-on-disk jobs that splitting the I/O out of your
context beats inlining — so each uses a *committed* split → workflow → aggregate chain
(`.claude/workflows/haid-tag.js`, `.claude/workflows/haid-judge.js`), never a model-authored one.

A model-authored `Workflow` receives `args` verbatim and routinely marshals nested data as a JSON
*string*, so `jobs.map(...)` throws `jobs.map is not a function` (the original tag-step failure). The
committed workflows already carry the normalization shim
(`const x = typeof args === 'string' ? JSON.parse(args) : args`); a prose instruction to "pass raw
JSON" is not a guarantee, the shim is. Splitting also keeps each agent to exactly one `Read` of its
own job file, so pairwise counterbalancing and per-finding verification isolation are preserved.
`episodes` and `compose` stay direct `Agent` calls because their prompts are small enough to inline.

## Scoring internals: counterbalancing, severity, fingerprint

- **Counterbalancing** — in a pairwise comparison, which side is the subject is deliberately hidden in
  the prompt (the exact phrasing and hidden A/B order in `compare.py` are load-bearing). Relay the raw
  A/B/tie answers in order; revealing or reordering corrupts placement.
- **Severity** is assigned by haid on read-back — the detect/verify judge only classifies + locates
  (detect) or confirms/refutes (verify). Relay the structured `findings`/`verdict` exactly; don't
  pre-rank them.
- **Fingerprint** is the staleness guard: "stale" on a re-run means the manifest was regenerated since
  the answers were written — delete that answers file and re-judge. Never hand-edit a fingerprint to
  force a pass; wrong count/shape failing loudly is the design.

## `haid submit` / `rank` internals

`haid rank` is read-only: it prints the user's percentile vs the community. `--refresh` pulls the live
board from Pages; otherwise the shipped snapshot. No account needed.

`haid submit` is the only path that leaves the machine, and only when the user explicitly asks. It
prints **exactly the row that becomes public + permanent**, then opens a validated GitHub PR adding
`entries/<user>.json` to the separate **data-only** benchmark repo (`dv-hart/haid-benchmark`).
Identity is the authenticated PR author (no local signature). By default it is **clone-free** — it
forks the repo as the user and writes the one file entirely over the GitHub API via `gh`, so it needs
only `gh auth login`, no checkout (the repo owner pushes to the upstream directly since you can't fork
your own repo). `--repo PATH` selects the legacy local-checkout flow (`git` + `gh`, marker
`.haid-benchmark-repo`). Use `--dry-run` first to print exactly what would run without pushing; pass
`--yes` only when the user has confirmed. The repo-side workflows validate (hashes, leak guard,
plausibility, author == username) and auto-merge. `haid benchmark` still emits the raw payload alone
if that's all the user wants.
