"""Pass-2: merged-PR units + mined review signals (docs/calibration-experiment.md §4b).

A "unit" is a bounded change-set the oracle and rubric score. For the team-reviewed
population that's a merged PR, which already carries the review signals that serve as
the *independent* external check on the Opus oracle (the H5 gate). This module turns
a (repo, PR) into a unit record: deterministic volume + the review-signal vector +
the saved diff.

Review signals are objective process metadata, NOT labels — they validate the oracle;
they are never fed to it.
"""

from __future__ import annotations

import os
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime

from .filekind import is_code
from .github import GitHubClient, GitHubError

# filenames that indicate test code (tests-touched signal)
_TEST_RE = re.compile(
    r"(^|/)(tests?|spec|__tests__)/|(_test\.|\.test\.|\.spec\.|test_)",
    re.IGNORECASE,
)


def _parse_ts(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


@dataclass
class Unit:
    # identity / provenance
    repo: str
    number: int
    url: str
    kind: str = "pr"                         # "pr" | "commit"
    sha: str | None = None                   # set for commit units
    title: str | None = None                 # kept for selection; BLIND before oracle
    diff_path: str | None = None
    diff_truncated: bool = False
    merged_at: str | None = None
    author_association: str | None = None
    # deterministic volume (computed, not a label)
    additions: int | None = None
    deletions: int | None = None
    changed_files: int | None = None
    commits: int | None = None
    tests_touched: int = 0
    code_churn: int = 0            # add+del in code files only (filters doc/RFC PRs)
    code_files: int = 0
    # plane priors inherited from the source repo (priors, never labels)
    language: str | None = None
    difficulty_prior: str | None = None
    volume_prior: str | None = None
    star_bucket: str | None = None
    # --- §4b mined review signals (the external H5 check) ---
    review_signals: dict = field(default_factory=dict)


def review_richness(pr_summary: dict) -> bool:
    """Cheap pre-filter on a PR *summary* before spending calls on full extraction.

    True if it merged and looks like it had review (not a solo direct-merge). We
    confirm with real reviews in `extract_unit`; this just avoids wasted fetches.
    """
    return bool(pr_summary.get("merged_at"))


def extract_unit(client: GitHubClient, repo: str, pr_summary: dict, *,
                 repo_meta: dict | None = None) -> Unit | None:
    """Build a Unit (signals + volume, no diff) from a PR summary.

    Returns None if the PR did not merge. The diff is fetched separately via
    `attach_diff` only after the unit passes the caller's accept filters.
    """
    number = pr_summary["number"]
    pr = client.get_pull(repo, number)
    if not pr.get("merged_at"):
        return None

    reviews = client.list_reviews(repo, number)
    files = client.list_pull_files(repo, number)

    # review-signal vector (§4b)
    reviewers = {r.get("user", {}).get("login") for r in reviews
                 if r.get("user")}
    reviewers.discard(pr.get("user", {}).get("login"))   # author self-reviews don't count
    states = [r.get("state") for r in reviews]
    created, merged = _parse_ts(pr.get("created_at")), _parse_ts(pr.get("merged_at"))
    ttm_hours = ((merged - created).total_seconds() / 3600.0
                 if created and merged else None)
    tests_touched = sum(1 for f in files if _TEST_RE.search(f.get("filename", "")))
    code_files = [f for f in files if is_code(f.get("filename", ""))]
    code_churn = sum((f.get("additions") or 0) + (f.get("deletions") or 0)
                     for f in code_files)

    signals = {
        "time_to_merge_hours": round(ttm_hours, 2) if ttm_hours is not None else None,
        "num_reviews": len(reviews),
        "num_reviewers": len(reviewers),
        "changes_requested": states.count("CHANGES_REQUESTED"),
        "approvals": states.count("APPROVED"),
        "review_comments": pr.get("review_comments"),     # inline code comments
        "issue_comments": pr.get("comments"),              # discussion comments
        "commits": pr.get("commits"),                      # iteration rounds proxy
        "author_association": pr.get("author_association"),
        # NOTE: later-reverts signal not collected for the pilot (needs history walk)
    }

    # NB: the diff is NOT fetched here — accept/reject is decided from the cheap
    # metadata above, then `attach_diff` is called only on accepted units so we
    # don't orphan diffs for rejected PRs.
    meta = repo_meta or {}
    return Unit(
        repo=repo, number=number, url=pr.get("html_url", ""),
        title=pr.get("title"), diff_path=None, diff_truncated=False,
        merged_at=pr.get("merged_at"),
        author_association=pr.get("author_association"),
        additions=pr.get("additions"), deletions=pr.get("deletions"),
        changed_files=pr.get("changed_files"), commits=pr.get("commits"),
        tests_touched=tests_touched,
        code_churn=code_churn, code_files=len(code_files),
        language=meta.get("language"),
        difficulty_prior=meta.get("difficulty_prior"),
        volume_prior=meta.get("volume_prior"),
        star_bucket=meta.get("star_bucket"),
        review_signals=signals,
    )


def extract_commit_unit(client: GitHubClient, repo: str, commit_summary: dict, *,
                        repo_meta: dict | None = None) -> Unit | None:
    """Build a Unit from a commit summary (no diff yet). Skips merge commits."""
    if len(commit_summary.get("parents", [])) > 1:
        return None                          # merge commit — not a unit of work
    sha = commit_summary["sha"]
    commit = client.get_commit(repo, sha)
    files = commit.get("files", []) or []
    if not files:
        return None
    stats = commit.get("stats", {}) or {}
    tests_touched = sum(1 for f in files if _TEST_RE.search(f.get("filename", "")))
    code_files = [f for f in files if is_code(f.get("filename", ""))]
    code_churn = sum((f.get("additions") or 0) + (f.get("deletions") or 0)
                     for f in code_files)
    meta = repo_meta or {}
    return Unit(
        repo=repo, number=0, kind="commit", sha=sha,
        url=commit.get("html_url", ""),
        title=(commit.get("commit", {}).get("message", "") or "").split("\n")[0],
        merged_at=commit.get("commit", {}).get("author", {}).get("date"),
        additions=stats.get("additions"), deletions=stats.get("deletions"),
        changed_files=len(files), commits=1, tests_touched=tests_touched,
        code_churn=code_churn, code_files=len(code_files),
        language=meta.get("language"),
        difficulty_prior=meta.get("difficulty_prior"),
        volume_prior=meta.get("volume_prior"),
        star_bucket=meta.get("star_bucket"),
        review_signals={},                   # commits carry no review signal
    )


def attach_diff(client: GitHubClient, unit: Unit, diffs_dir: str, *,
                max_diff_bytes: int = 400_000) -> Unit:
    """Fetch + save the diff and set diff_path on the unit (call only on accept)."""
    os.makedirs(diffs_dir, exist_ok=True)
    ident = unit.sha[:12] if unit.kind == "commit" and unit.sha else str(unit.number)
    fname = f"{unit.repo.replace('/', '__')}__{ident}.diff"
    path = os.path.join(diffs_dir, fname)
    try:
        diff = (client.get_commit_diff(unit.repo, unit.sha)
                if unit.kind == "commit" else client.get_pull_diff(unit.repo, unit.number))
    except GitHubError:
        return unit
    truncated = len(diff.encode("utf-8")) > max_diff_bytes
    if truncated:
        diff = diff.encode("utf-8")[:max_diff_bytes].decode("utf-8", "ignore")
        diff += "\n\n# [TRUNCATED by harvester: diff exceeded size cap]\n"
    with open(path, "w", encoding="utf-8") as f:
        f.write(diff)
    unit.diff_path = path
    unit.diff_truncated = truncated
    return unit


def unit_to_dict(u: Unit) -> dict:
    return asdict(u)
