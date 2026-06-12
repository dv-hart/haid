# Calibration corpus harvester

Pass-1 instrument for the calibration experiment
([../docs/calibration-experiment.md](../docs/calibration-experiment.md)). It discovers
candidate OSS repositories **off the popularity axis**, places each with cheap proxies
on the difficulty×volume plane, and writes a reviewable manifest. It does **not** pull
diffs or score anything — that's Pass-2, run only on candidates a human accepts.

**Zero third-party dependencies** — stdlib `urllib` only (no `gh`, no `requests`).

## Why this design
- **Two discovery channels** (§3c): stratified GitHub search *across* star buckets
  (not stars-descending) + Hacker News **Show HN** (solo-dev-heavy, quality signal
  orthogonal to stars).
- **Cheap-proxy placement** (§3e): language + topic hints → difficulty *prior*; repo
  size → volume *prior*. **Priors, never labels** — the Opus oracle produces the real
  ranking in the experiment proper.
- **Coverage over count**: the run prints a difficulty×volume grid so you can see
  empty cells (e.g. high-difficulty/low-volume) and target them before Pass-2.

## Setup
```sh
# GitHub search channel needs a token (Search API is throttled hard without one).
# A classic or fine-grained PAT with public-repo read scope is enough.
export GITHUB_TOKEN=ghp_xxx          # PowerShell: $env:GITHUB_TOKEN = "ghp_xxx"
```

## Usage (from repo root)
```sh
# Full run (both channels) — needs GITHUB_TOKEN
python -m calibration.harvest --out out/candidates.jsonl

# Token-free smoke test (Show HN only, no per-repo enrichment)
python -m calibration.harvest --channels showhn --no-enrich --out out/candidates.jsonl

# Tuning the sample
python -m calibration.harvest --created-since 2026-03-01 --per-cell 15 \
    --languages Rust,Zig,Python,TypeScript
```

Output `out/candidates.jsonl` is gitignored (the `/out/` rule). One JSON object per
candidate; re-running appends and dedups by `full_name`.

## Manifest fields (per candidate)
`full_name`, `source_channel` (`github_search` | `show_hn`), `html_url`,
`description`, `language`, `stars`, `forks`, `open_issues`, `license`, `created_at`,
`pushed_at`, `size_kb`, `topics`, `is_fork`, `archived`, `has_issues`,
`difficulty_prior`, `volume_prior`, `star_bucket`, and Show HN provenance
(`hn_points`, `hn_comments`, `hn_url`, `hn_title`).

## What's been built since (see ../docs/axis-calibration-playbook.md)
- **Pass-2 is built** — `pass2.py` extracts per-unit diffs: `--mode pr` (merged PRs,
  team repos) and `--mode commit` (commits, for solo/personal repos with no PRs).
  `pulls.py` also mines review signals, but note: **using review signals as a
  difficulty ground truth was falsified** ([calibration-pilot-1.md](../docs/calibration-pilot-1.md)).
- **blind.py / bt_h5.py / ladder.py / filekind.py** — blinding (code-first,
  identity-stripped), Bradley-Terry + Spearman, anchor selection + placement analysis.
- The validated scorer is **dense-anchor ordering + placement**, not review-signal
  validation — start from [axis-calibration-playbook.md](../docs/axis-calibration-playbook.md).

## What's still next (not built)
- The **"genuinely good" filter** (§3d): tests/CI/README file-tree checks (contents-API).

## What this fed into
- The **relative scorer is built** (2026-06-05) — it lives in [`src/haid/scoring/`](../src/haid/),
  not here. `calibration/` stays the experiment harness that *produced* the ladders;
  `src/haid` is the product runtime that *uses* them. The locked anchors were copied into
  package data (`src/haid/data/`). `calibration/build_difficulty_anchors.py` writes the
  canonical `out/difficulty_anchors.json` (fit from the dense all-pairs verdicts; the older
  `out/ladder_anchors.json` is the **stale sparse sort** — superseded). Difficulty +
  cleanliness ladders are built; originality was calibrated then **dropped** (see
  docs/scoring-rubric.md "Axis decision").
