"""Hacker News "Show HN" discovery channel (no token required).

Show HN surfaces projects their authors are proud of — heavy on solo developers, and
the comment thread is a human quality signal orthogonal to GitHub stars (§3c). We
pull recent Show HN stories, extract the GitHub repos they link, and hand them to the
GitHub client for enrichment.

Uses the public HN Algolia API: https://hn.algolia.com/api
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone

ALGOLIA_ROOT = "https://hn.algolia.com/api/v1"
USER_AGENT = "haid-calibration-harvester/0.1 (local research tool)"

# owner/repo from a github.com URL (no trailing path segments like /issues, /tree)
_GH_RE = re.compile(
    r"https?://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:/|\.git|#|\?|$)"
)
_NON_REPO_OWNERS = {"sponsors", "marketplace", "topics", "collections", "about",
                    "features", "settings", "orgs"}


def _iso_to_unix(iso_date: str) -> int:
    d = date.fromisoformat(iso_date)
    return int(datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp())


def extract_repo(url: str | None) -> str | None:
    """Return 'owner/repo' if the URL points at a GitHub repo root, else None."""
    if not url:
        return None
    m = _GH_RE.match(url.strip())
    if not m:
        return None
    owner, repo = m.group(1), m.group(2)
    if owner.lower() in _NON_REPO_OWNERS:
        return None
    if repo.endswith(".git"):
        repo = repo[:-4]
    return f"{owner}/{repo}"


def fetch_show_hn(created_since: str, *, max_stories: int = 500,
                  min_points: int = 0, verbose: bool = True) -> list[dict]:
    """Recent Show HN stories since `created_since` (ISO date) that link a GitHub repo.

    Returns dicts: {repo, hn_points, hn_comments, hn_url, hn_title, hn_created}.
    """
    since_unix = _iso_to_unix(created_since)
    results: list[dict] = []
    seen: set[str] = set()
    page = 0
    per_page = 100
    while len(results) < max_stories:
        params = {
            "tags": "show_hn",
            "numericFilters": f"created_at_i>{since_unix},points>={min_points}",
            "hitsPerPage": per_page,
            "page": page,
        }
        url = f"{ALGOLIA_ROOT}/search_by_date?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, urllib.error.HTTPError) as e:
            if verbose:
                print(f"[hn] error on page {page}: {e}", flush=True)
            break

        hits = data.get("hits", [])
        if not hits:
            break
        for h in hits:
            repo = extract_repo(h.get("url"))
            if not repo or repo in seen:
                continue
            seen.add(repo)
            results.append({
                "repo": repo,
                "hn_points": h.get("points"),
                "hn_comments": h.get("num_comments"),
                "hn_url": f"https://news.ycombinator.com/item?id={h.get('objectID')}",
                "hn_title": h.get("title"),
                "hn_created": h.get("created_at"),
            })
        nb_pages = data.get("nbPages", 0)
        page += 1
        if page >= nb_pages:
            break
        time.sleep(0.3)  # be polite to Algolia

    if verbose:
        print(f"[hn] {len(results)} Show HN stories link a GitHub repo "
              f"(since {created_since})", flush=True)
    return results[:max_stories]
