"""Pass-1 candidate harvester CLI.

Discovers candidate repos off the popularity axis (stratified GitHub search across
star buckets x languages + Show HN), places each with cheap proxies on the
difficulty x volume plane, and writes a reviewable JSONL manifest with a coverage
table. Pass-2 (per-unit diff + review-signal extraction) runs later on accepted
candidates only.

Usage (from repo root):
    python -m calibration.harvest --out out/candidates.jsonl
    python -m calibration.harvest --channels showhn --out out/candidates.jsonl   # token-free
    python -m calibration.harvest --created-since 2026-03-01 --per-cell 15

Set GITHUB_TOKEN in the environment for the GitHub search channel (required for
usable Search API rate limits).
"""

from __future__ import annotations

import argparse
import sys

from . import classify, config, hn
from .config import HarvestConfig, bucket_label, bucket_qualifier
from .github import GitHubClient, GitHubError
from .manifest import Candidate, Manifest, from_repo_json


def star_bucket_label(stars: int | None) -> str | None:
    if stars is None:
        return None
    for bucket in config.STAR_BUCKETS:
        lo, hi = bucket
        if stars >= lo and (hi is None or stars < hi):
            return bucket_label(bucket)
    return None


def _build_query(cfg: HarvestConfig, language: str, bucket) -> str:
    parts = [
        bucket_qualifier(bucket),
        f"language:{language}",
        f"{cfg.date_field}:>={cfg.created_since}",
    ]
    if cfg.require_not_fork:
        parts.append("fork:false")
    if cfg.require_not_archived:
        parts.append("archived:false")
    return " ".join(parts)


def harvest_github(client: GitHubClient, cfg: HarvestConfig, manifest: Manifest) -> int:
    added = 0
    for language in cfg.languages:
        for bucket in cfg.star_buckets:
            q = _build_query(cfg, language, bucket)
            try:
                repos = client.search_repositories(
                    q, sort=cfg.sort, order=cfg.order, limit=cfg.per_cell)
            except GitHubError as e:
                print(f"[github] query failed ({language} {bucket_label(bucket)}): {e}",
                      file=sys.stderr, flush=True)
                continue
            cell_added = 0
            for repo in repos:
                if manifest.has(repo["full_name"]):
                    continue
                cand = from_repo_json(repo, source_channel="github_search")
                cand.population = cfg.population
                _place(cand)
                cand.star_bucket = bucket_label(bucket)
                if manifest.add(cand):
                    cell_added += 1
            added += cell_added
            print(f"[github] {language:<11} {bucket_label(bucket):>7}: "
                  f"+{cell_added} (q={q})", flush=True)
    return added


def harvest_show_hn(client: GitHubClient | None, cfg: HarvestConfig,
                    manifest: Manifest, *, enrich: bool, min_points: int = 0) -> int:
    stories = hn.fetch_show_hn(cfg.created_since, min_points=min_points)
    added = 0
    for story in stories:
        repo_name = story["repo"]
        if manifest.has(repo_name):
            continue
        if enrich and client is not None:
            try:
                repo = client.get_repo(repo_name)
            except GitHubError as e:
                print(f"[hn] enrich failed {repo_name}: {e}", file=sys.stderr)
                continue
            cand = from_repo_json(repo, source_channel="show_hn")
            cand.star_bucket = star_bucket_label(cand.stars)
        else:
            # token-free path: record what HN gave us; priors from name/title only
            cand = Candidate(full_name=repo_name, source_channel="show_hn",
                             html_url=f"https://github.com/{repo_name}",
                             description=story.get("hn_title"))
        cand.hn_points = story.get("hn_points")
        cand.hn_comments = story.get("hn_comments")
        cand.hn_url = story.get("hn_url")
        cand.hn_title = story.get("hn_title")
        _place(cand)
        if manifest.add(cand):
            added += 1
    print(f"[hn] +{added} candidates (enrich={'on' if enrich else 'off'})", flush=True)
    return added


def _place(cand: Candidate) -> None:
    """Attach cheap-proxy difficulty/volume priors to a candidate."""
    placement = classify.cell(
        cand.language, cand.topics, cand.full_name.split("/")[-1],
        cand.description, cand.size_kb)
    cand.difficulty_prior = placement["difficulty_prior"]
    cand.volume_prior = placement["volume_prior"]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="HAID calibration Pass-1 candidate harvester")
    p.add_argument("--out", default=None,
                   help="manifest JSONL path (default depends on --validator)")
    p.add_argument("--validator", action="store_true",
                   help="harvest the H5-validator population: established, active, "
                        "high-star repos (filters on `pushed:` over a wide window, "
                        "GitHub channel only) instead of the recent oracle pool")
    p.add_argument("--created-since", default=None,
                   help="ISO date bound for date_field; default = 3mo created (oracle) "
                        "/ 6mo pushed (validator)")
    p.add_argument("--languages", default=None,
                   help="comma-separated; default = the config language set")
    p.add_argument("--per-cell", type=int, default=20,
                   help="candidates per (language x star bucket)")
    p.add_argument("--channels", default=None,
                   help="comma-separated: github, showhn "
                        "(default: github,showhn for oracle; github for validator)")
    p.add_argument("--no-enrich", action="store_true",
                   help="skip per-repo GitHub enrichment for Show HN (token-free)")
    p.add_argument("--min-hn-points", type=int, default=0,
                   help="drop Show HN stories below this point count (quality filter)")
    args = p.parse_args(argv)

    dotenv_path = config.load_dotenv()
    if dotenv_path:
        print(f"[harvest] loaded env from {dotenv_path}", flush=True)

    languages = ([s.strip() for s in args.languages.split(",")]
                 if args.languages else list(config.DEFAULT_LANGUAGES))
    if args.validator:
        # `pushed:` within ~6mo = actively maintained (has a recent PR backlog to
        # mine), while NOT constraining creation date — old established repos qualify.
        cfg = HarvestConfig(
            created_since=args.created_since or config.default_created_since(6),
            languages=languages,
            star_buckets=list(config.VALIDATOR_STAR_BUCKETS),
            per_cell=args.per_cell,
            channels=[s.strip() for s in (args.channels or "github").split(",")],
            population="validator",
            date_field="pushed",     # established + actively maintained, not "new"
        )
        out_path = args.out or "out/validator_candidates.jsonl"
    else:
        cfg = HarvestConfig(
            created_since=args.created_since or config.default_created_since(3),
            languages=languages,
            per_cell=args.per_cell,
            channels=[s.strip() for s in (args.channels or "github,showhn").split(",")],
        )
        out_path = args.out or "out/candidates.jsonl"

    client = GitHubClient()
    if not client.authenticated:
        print("[warn] no GITHUB_TOKEN -- Search API is throttled (10 req/min, 60/hr "
              "core). Show HN works token-free; GitHub search will be slow/limited.",
              file=sys.stderr, flush=True)

    manifest = Manifest(out_path)
    print(f"[harvest] population={cfg.population} {cfg.date_field}-since={cfg.created_since} "
          f"per-cell={cfg.per_cell} channels={cfg.channels} "
          f"authenticated={client.authenticated}", flush=True)

    if "github" in cfg.channels:
        if not client.authenticated:
            print("[github] skipping search channel without a token "
                  "(use --channels showhn for a token-free run)", file=sys.stderr)
        else:
            harvest_github(client, cfg, manifest)

    if "showhn" in cfg.channels:
        enrich = client.authenticated and not args.no_enrich
        harvest_show_hn(client, cfg, manifest, enrich=enrich,
                        min_points=args.min_hn_points)

    print("\n=== coverage (cheap-proxy priors, NOT labels) ===")
    print(manifest.coverage_table())
    print(f"\nchannels: {manifest.channel_breakdown()}")
    print(f"manifest: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
