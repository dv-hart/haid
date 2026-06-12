"""Pass-2 CLI: select review-rich merged-PR units from the candidate pool.

Reads the Pass-1 candidate manifest, walks the *team-reviewed* population (repos most
likely to carry real review), extracts merged-PR units with their §4b review signals,
and writes out/units.jsonl + out/diffs/. These units are the input to the pilot's H5
oracle-vs-review check (docs/calibration-experiment.md §11).

Usage (from repo root, needs GITHUB_TOKEN in .env):
    python -m calibration.pass2 --target 20
    python -m calibration.pass2 --target 20 --min-changes 20 --max-changes 1500
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from . import config, pulls
from .github import GitHubClient, GitHubError

# scan order: repos in these buckets are likeliest to have team review
_BUCKET_RANK = {"1000+": 0, "200-1000": 1, "50-200": 2, "10-50": 3, "0-10": 4}


def load_candidates(path: str) -> list[dict]:
    if not os.path.exists(path):
        sys.exit(f"[pass2] candidate manifest not found: {path} (run Pass-1 first)")
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def select_scan_order(cands: list[dict], *, channels: list[str],
                      buckets: list[str]) -> list[dict]:
    """Repos to scan, round-robined across difficulty priors for plane spread."""
    pool = [c for c in cands
            if c.get("source_channel") in channels
            and (c.get("star_bucket") in buckets if buckets else True)
            and c.get("language")]
    groups: dict[str, list[dict]] = {"high": [], "mid": [], "low": []}
    for c in pool:
        groups.get(c.get("difficulty_prior") or "mid", groups["mid"]).append(c)
    for g in groups.values():
        g.sort(key=lambda c: _BUCKET_RANK.get(c.get("star_bucket") or "0-10", 9))
    # round-robin high/mid/low so the scan doesn't exhaust one tier first
    order: list[dict] = []
    while any(groups.values()):
        for tier in ("high", "mid", "low"):
            if groups[tier]:
                order.append(groups[tier].pop(0))
    return order


def is_review_rich(unit: pulls.Unit, min_reviewers: int = 1) -> bool:
    """Genuine-review gate. With min_reviewers>=2 this excludes solo rubber-stamps.

    A unit qualifies if it clears the reviewer bar OR shows real back-and-forth
    (a changes-requested round, or substantive inline review comments).
    """
    s = unit.review_signals
    return bool((s.get("num_reviewers") or 0) >= min_reviewers
                or (s.get("changes_requested") or 0) >= 1
                or (s.get("review_comments") or 0) >= 3)


def within_size(unit: pulls.Unit, lo: int, hi: int) -> bool:
    churn = (unit.additions or 0) + (unit.deletions or 0)
    return lo <= churn <= hi


def is_code_substantive(unit: pulls.Unit, min_code_churn: int) -> bool:
    """Exclude doc/RFC-only PRs — a *code*-difficulty oracle must judge code."""
    return unit.code_churn >= min_code_churn and unit.code_files >= 1


class UnitStore:
    def __init__(self, path: str):
        self.path = path
        self.seen: set[str] = set()
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        if os.path.exists(path):
            for line in open(path, encoding="utf-8"):
                if line.strip():
                    try:
                        self.seen.add(_key_from(json.loads(line)))
                    except (json.JSONDecodeError, KeyError):
                        pass

    def has(self, repo: str, ident) -> bool:
        return f"{repo}#{ident}" in self.seen

    def add(self, unit: pulls.Unit) -> None:
        ident = unit.sha if unit.kind == "commit" and unit.sha else unit.number
        self.seen.add(f"{unit.repo}#{ident}")
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(pulls.unit_to_dict(unit), ensure_ascii=False) + "\n")


def _key_from(d: dict) -> str:
    ident = d.get("sha") if d.get("kind") == "commit" and d.get("sha") else d["number"]
    return f"{d['repo']}#{ident}"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="HAID calibration Pass-2 unit selector")
    p.add_argument("--manifest", default="out/candidates.jsonl")
    p.add_argument("--out", default="out/units.jsonl")
    p.add_argument("--diffs-dir", default="out/diffs")
    p.add_argument("--mode", choices=["pr", "commit"], default="pr",
                   help="pr = merged PRs (team repos); commit = commits (solo/personal "
                        "repos with no PRs — needed for the low/mid of the ladder)")
    p.add_argument("--target", type=int, default=20, help="units to collect")
    p.add_argument("--per-repo", type=int, default=2, help="max units kept per repo")
    p.add_argument("--commits-scanned", type=int, default=12,
                   help="recent commits to inspect per repo (commit mode)")
    p.add_argument("--min-reviewers", type=int, default=1,
                   help="reviewer bar for the genuine-review gate (use 2 to exclude "
                        "solo rubber-stamps; still admits changes-requested rounds)")
    p.add_argument("--prs-per-repo", type=int, default=4, help="recent merged PRs to try")
    p.add_argument("--min-changes", type=int, default=20)
    p.add_argument("--max-changes", type=int, default=1500)
    p.add_argument("--min-code-churn", type=int, default=30,
                   help="min add+del in CODE files (excludes doc/RFC-only PRs)")
    p.add_argument("--max-repos-scan", type=int, default=120)
    p.add_argument("--channels", default="github_search")
    p.add_argument("--buckets", default="1000+,200-1000,50-200,10-50",
                   help="star buckets to draw the team population from")
    args = p.parse_args(argv)

    dotenv = config.load_dotenv()
    if dotenv:
        print(f"[pass2] loaded env from {dotenv}", flush=True)
    client = GitHubClient()
    if not client.authenticated:
        sys.exit("[pass2] GITHUB_TOKEN required for Pass-2 (PR + review extraction)")

    cands = load_candidates(args.manifest)
    order = select_scan_order(
        cands,
        channels=[s.strip() for s in args.channels.split(",")],
        buckets=[s.strip() for s in args.buckets.split(",") if s.strip()],
    )
    print(f"[pass2] mode={args.mode}; scanning up to "
          f"{min(args.max_repos_scan, len(order))} repos for {args.target} units",
          flush=True)

    store = UnitStore(args.out)
    collected = 0
    scanned = 0
    for cand in order:
        if collected >= args.target or scanned >= args.max_repos_scan:
            break
        repo = cand["full_name"]
        scanned += 1
        kept_here = 0
        if args.mode == "commit":
            collected += _scan_commits(client, repo, cand, store, args,
                                       remaining=args.target - collected)
        else:
            collected += _scan_prs(client, repo, cand, store, args,
                                   remaining=args.target - collected)

    _summary(store.path, collected, scanned)
    return 0


def _scan_prs(client, repo, cand, store, args, *, remaining) -> int:
    try:
        summaries = client.list_pulls(repo, state="closed", per_page=30, pages=1)
    except GitHubError as e:
        print(f"[pass2] {repo}: list_pulls failed: {e}", file=sys.stderr)
        return 0
    merged = [s for s in summaries if pulls.review_richness(s)][:args.prs_per_repo]
    kept = 0
    for s in merged:
        if kept >= min(args.per_repo, remaining):
            break
        if store.has(repo, s["number"]):
            continue
        try:
            unit = pulls.extract_unit(client, repo, s, repo_meta=cand)
        except GitHubError as e:
            print(f"[pass2] {repo}#{s['number']}: extract failed: {e}", file=sys.stderr)
            continue
        if unit is None or not within_size(unit, args.min_changes, args.max_changes):
            continue
        if not is_code_substantive(unit, args.min_code_churn):
            continue
        if not is_review_rich(unit, args.min_reviewers):
            continue
        pulls.attach_diff(client, unit, args.diffs_dir)
        store.add(unit); kept += 1
        sig = unit.review_signals
        print(f"[pass2] + {repo}#{unit.number} [{unit.difficulty_prior}/"
              f"{unit.volume_prior}] code_churn={unit.code_churn} "
              f"reviewers={sig['num_reviewers']} ttm={sig['time_to_merge_hours']}h",
              flush=True)
    return kept


def _scan_commits(client, repo, cand, store, args, *, remaining) -> int:
    try:
        commits = client.list_commits(repo, per_page=30, pages=1)
    except GitHubError as e:
        print(f"[pass2] {repo}: list_commits failed: {e}", file=sys.stderr)
        return 0
    kept = 0
    for c in commits[:args.commits_scanned]:
        if kept >= min(args.per_repo, remaining):
            break
        if store.has(repo, c["sha"]):
            continue
        try:
            unit = pulls.extract_commit_unit(client, repo, c, repo_meta=cand)
        except GitHubError as e:
            print(f"[pass2] {repo}@{c['sha'][:8]}: extract failed: {e}", file=sys.stderr)
            continue
        if unit is None or not within_size(unit, args.min_changes, args.max_changes):
            continue
        if not is_code_substantive(unit, args.min_code_churn):
            continue
        pulls.attach_diff(client, unit, args.diffs_dir)
        store.add(unit); kept += 1
        print(f"[pass2] + {repo}@{unit.sha[:8]} [{unit.difficulty_prior}/"
              f"{unit.volume_prior}] code_churn={unit.code_churn} "
              f"files={unit.changed_files} ({cand.get('star_bucket')})", flush=True)
    return kept


def _summary(path: str, collected: int, scanned: int) -> None:
    import statistics
    units = [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]
    print(f"\n=== pilot units: {len(units)} total ({collected} new this run; "
          f"scanned {scanned} repos) ===")
    from collections import Counter
    diff_c = Counter(u.get("difficulty_prior") for u in units)
    print("difficulty spread:", dict(diff_c))
    for name in ("num_reviewers", "changes_requested", "time_to_merge_hours"):
        vals = [u["review_signals"].get(name) for u in units
                if u["review_signals"].get(name) is not None]
        if vals:
            print(f"  {name:>22}: median={statistics.median(vals):.2f} "
                  f"min={min(vals):.2f} max={max(vals):.2f}")
    churn = [(u.get("additions") or 0) + (u.get("deletions") or 0) for u in units]
    if churn:
        print(f"  {'churn (add+del)':>22}: median={statistics.median(churn):.0f} "
              f"min={min(churn)} max={max(churn)}")
    print(f"units: {path}  |  diffs: out/diffs/")


if __name__ == "__main__":
    raise SystemExit(main())
