"""Minimal GitHub REST client (stdlib urllib only).

Token-aware and rate-limit-aware. Reads GITHUB_TOKEN (or GH_TOKEN) from the
environment; works unauthenticated but the Search API is throttled hard (10 req/min
vs 30 authenticated, and 60/hr core vs 5000), so a token is required for a real
harvest. See docs/calibration-experiment.md §3.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

API_ROOT = "https://api.github.com"
USER_AGENT = "haid-calibration-harvester/0.1 (+https://github.com/; local research tool)"
API_VERSION = "2022-11-28"


class GitHubError(RuntimeError):
    pass


class GitHubClient:
    def __init__(self, token: str | None = None, *, max_retries: int = 4,
                 verbose: bool = True, search_interval: float = 2.2):
        self.token = token or os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
        self.max_retries = max_retries
        self.verbose = verbose
        # Min seconds between Search API calls. The primary search limit is 30/min
        # (1 per 2s); spacing also avoids GitHub's stricter *secondary* burst limit.
        self.search_interval = search_interval
        self._last_search = 0.0
        self.authenticated = bool(self.token)

    # -- low level ----------------------------------------------------------------
    def _headers(self) -> dict[str, str]:
        h = {
            "Accept": "application/vnd.github+json",
            "User-Agent": USER_AGENT,
            "X-GitHub-Api-Version": API_VERSION,
        }
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        return h

    def _log(self, msg: str) -> None:
        if self.verbose:
            print(f"[github] {msg}", flush=True)

    def _get(self, path: str, params: dict[str, Any] | None = None) -> tuple[Any, dict[str, str]]:
        """GET path and parse JSON. Returns (json, response_headers)."""
        body, headers = self._open(path, params)
        return json.loads(body), headers

    def _get_text(self, path: str, accept: str,
                  params: dict[str, Any] | None = None) -> str:
        """GET path with a custom Accept (e.g. raw diff) and return the body text."""
        body, _ = self._open(path, params, accept=accept)
        return body

    def _open(self, path: str, params: dict[str, Any] | None = None, *,
              accept: str | None = None) -> tuple[str, dict[str, str]]:
        """GET path (absolute or /relative). Returns (body_text, response_headers)."""
        url = path if path.startswith("http") else API_ROOT + path
        if params:
            url = f"{url}?{urllib.parse.urlencode(params)}"

        attempt = 0
        rl_attempt = 0
        while True:
            attempt += 1
            headers_out = self._headers()
            if accept:
                headers_out["Accept"] = accept
            req = urllib.request.Request(url, headers=headers_out)
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    headers = {k.lower(): v for k, v in resp.headers.items()}
                    body = resp.read().decode("utf-8")
                    self._respect_rate_limit(headers)
                    return body, headers
            except urllib.error.HTTPError as e:
                headers = {k.lower(): v for k, v in (e.headers or {}).items()}
                body_text = e.read().decode("utf-8", "replace") if e.fp else ""
                # 403/429 from primary OR secondary rate limit -> wait and retry.
                # Secondary limits often omit Retry-After/x-ratelimit headers and
                # only say so in the body, so inspect the body too.
                if e.code in (403, 429) and self._is_rate_limited(headers, body_text):
                    rl_attempt += 1
                    if rl_attempt > 6:
                        raise GitHubError(
                            f"persistent rate limit for {url}: {body_text[:200]}") from e
                    wait = self._rate_limit_wait(headers, rl_attempt)
                    self._log(f"rate-limited ({e.code}); sleeping {wait:.0f}s "
                              f"(retry {rl_attempt})")
                    time.sleep(wait)
                    continue
                if e.code >= 500 and attempt <= self.max_retries:
                    back = min(2 ** attempt, 30)
                    self._log(f"server {e.code}; retry {attempt} in {back}s")
                    time.sleep(back)
                    continue
                raise GitHubError(f"HTTP {e.code} for {url}: {body_text[:300]}") from e
            except urllib.error.URLError as e:
                if attempt <= self.max_retries:
                    back = min(2 ** attempt, 30)
                    self._log(f"network error {e.reason}; retry {attempt} in {back}s")
                    time.sleep(back)
                    continue
                raise GitHubError(f"network error for {url}: {e.reason}") from e

    @staticmethod
    def _is_rate_limited(headers: dict[str, str], body_text: str = "") -> bool:
        return (headers.get("x-ratelimit-remaining") == "0"
                or "retry-after" in headers
                or "rate limit" in body_text.lower())

    @staticmethod
    def _rate_limit_wait(headers: dict[str, str], rl_attempt: int = 1) -> float:
        if "retry-after" in headers:
            try:
                return float(headers["retry-after"]) + 1.0
            except ValueError:
                pass
        reset = headers.get("x-ratelimit-reset")
        if reset and headers.get("x-ratelimit-remaining") == "0":
            try:
                return max(1.0, float(reset) - time.time() + 2.0)
            except ValueError:
                pass
        # secondary limit without a reset hint: exponential backoff, capped
        return min(30.0 * rl_attempt, 120.0)

    def _respect_rate_limit(self, headers: dict[str, str]) -> None:
        """Pre-emptively sleep if we're about to exhaust the window."""
        rem = headers.get("x-ratelimit-remaining")
        if rem is not None and rem.isdigit() and int(rem) == 0:
            wait = self._rate_limit_wait(headers)
            self._log(f"window exhausted; sleeping {wait:.0f}s until reset")
            time.sleep(wait)

    # -- high level ---------------------------------------------------------------
    def search_repositories(self, q: str, *, sort: str = "updated",
                            order: str = "desc", limit: int = 30) -> list[dict]:
        """Search repositories. Pages until `limit` results or exhaustion.

        The Search API caps at 1000 results per query; we stay well under by keeping
        queries narrow (one language x one star bucket x date window).
        """
        out: list[dict] = []
        page = 1
        per_page = min(100, limit)
        while len(out) < limit:
            params = {
                "q": q, "sort": sort, "order": order,
                "per_page": per_page, "page": page,
            }
            self._throttle_search()
            data, _ = self._get("/search/repositories", params)
            items = data.get("items", [])
            if not items:
                break
            out.extend(items)
            if len(items) < per_page or page >= 10:  # 10 pages = API hard cap
                break
            page += 1
        return out[:limit]

    def _throttle_search(self) -> None:
        """Space Search API calls by at least `search_interval` seconds."""
        elapsed = time.time() - self._last_search
        if elapsed < self.search_interval:
            time.sleep(self.search_interval - elapsed)
        self._last_search = time.time()

    def get_repo(self, full_name: str) -> dict:
        data, _ = self._get(f"/repos/{full_name}")
        return data

    # -- pull requests (Pass-2) ---------------------------------------------------
    def list_pulls(self, repo: str, *, state: str = "closed", per_page: int = 50,
                   pages: int = 1, sort: str = "updated",
                   direction: str = "desc") -> list[dict]:
        """List PRs (summary objects). state=closed includes merged + closed-unmerged."""
        out: list[dict] = []
        for page in range(1, pages + 1):
            data, _ = self._get(f"/repos/{repo}/pulls", {
                "state": state, "per_page": per_page, "page": page,
                "sort": sort, "direction": direction,
            })
            if not data:
                break
            out.extend(data)
            if len(data) < per_page:
                break
        return out

    def get_pull(self, repo: str, number: int) -> dict:
        """Full PR object: additions/deletions/changed_files/commits/merged_at/..."""
        data, _ = self._get(f"/repos/{repo}/pulls/{number}")
        return data

    def list_reviews(self, repo: str, number: int) -> list[dict]:
        """PR reviews: state (APPROVED/CHANGES_REQUESTED/COMMENTED) + reviewer."""
        data, _ = self._get(f"/repos/{repo}/pulls/{number}/reviews",
                            {"per_page": 100})
        return data if isinstance(data, list) else []

    def list_pull_files(self, repo: str, number: int) -> list[dict]:
        """Changed files: filename, additions, deletions, status (max 1 page=300)."""
        data, _ = self._get(f"/repos/{repo}/pulls/{number}/files", {"per_page": 100})
        return data if isinstance(data, list) else []

    def get_pull_diff(self, repo: str, number: int) -> str:
        """The unified diff text for a PR (the artifact the oracle scores)."""
        return self._get_text(f"/repos/{repo}/pulls/{number}",
                              accept="application/vnd.github.diff")

    # -- commits (units for solo/personal repos with no PRs, §3a) -----------------
    def list_commits(self, repo: str, *, per_page: int = 30, pages: int = 1) -> list[dict]:
        """Recent commit summaries (sha, message, parents). Default branch."""
        out: list[dict] = []
        for page in range(1, pages + 1):
            data, _ = self._get(f"/repos/{repo}/commits",
                                {"per_page": per_page, "page": page})
            if not isinstance(data, list) or not data:
                break
            out.extend(data)
            if len(data) < per_page:
                break
        return out

    def get_commit(self, repo: str, sha: str) -> dict:
        """Full commit: stats + files[] (filename, additions, deletions, patch)."""
        data, _ = self._get(f"/repos/{repo}/commits/{sha}")
        return data

    def get_commit_diff(self, repo: str, sha: str) -> str:
        """Unified diff text for a commit (the artifact the oracle scores)."""
        return self._get_text(f"/repos/{repo}/commits/{sha}",
                              accept="application/vnd.github.diff")

    def rate_limit(self) -> dict:
        data, _ = self._get("/rate_limit")
        return data
