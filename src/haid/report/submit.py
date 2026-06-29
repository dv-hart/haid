"""`haid submit` — opt-in: push your summary row to the community benchmark (ADR-0005 v1).

This is the only path that leaves the machine, and it leaves ONLY the summary-only
benchmark row (benchmark.build_submission — leak-guarded, no logs/diffs/paths). Identity
is the authenticated GitHub PR author; there is no local signature in v1.

Two ways to open the PR, both deliberately split so the side-effect-free parts are testable:

  DEFAULT — clone-free, entirely over the GitHub API via `gh` (no local checkout):
    submit_via_api  -> fork (if needed) → create branch ref → PUT entries/<user>.json →
                       open the PR. Works for any GitHub user with `gh auth login`.
    api_plan        -> the happy-path `gh` argv the above runs (pure; used by --dry-run).

  LEGACY — `--repo PATH`, writes into a local benchmark-repo checkout:
    write_entry     -> benchmark/entries/<username>.json in a checkout (fs)
    pr_commands     -> the git+gh sequence that opens the validated PR (pure: returns argv)

Shared, pure: build_submission (the row), render_public_preview (exactly what becomes
public + permanent). `haid submit` prints the preview, requires confirmation (--yes or a
TTY), then opens the PR. The repo-side GitHub Action validates and auto-merges; direct
writes to the log are never accepted.
"""

from __future__ import annotations

import base64
import json
import subprocess
from pathlib import Path

from . import benchmark

# The benchmark is its OWN data-only repo (no application code, no release secrets), so a
# bad merge there can't reach the package or PyPI. Rows live at entries/<user>.json.
BENCHMARK_REPO = "dv-hart/haid-benchmark"
ENTRIES_DIR = ("entries",)
REPO_MARKER = ".haid-benchmark-repo"     # sentinel file at the data-repo root
UPSTREAM_BRANCH = "main"                 # the data repo's default branch (PR base)


def find_repo_root(start: Path | None = None) -> Path | None:
    """Walk up from `start` (default cwd) for a local checkout of the benchmark data repo,
    identified by its `.haid-benchmark-repo` marker file."""
    start = (start or Path.cwd()).resolve()
    for d in (start, *start.parents):
        if (d / REPO_MARKER).is_file():
            return d
    return None


def entry_relpath(username: str) -> str:
    return "/".join((*ENTRIES_DIR, f"{username}.json"))


def entry_text(payload: dict) -> str:
    """Canonical on-disk/over-the-wire serialization of an entry (one source of truth so
    the local file and the API upload are byte-identical)."""
    return json.dumps(payload, indent=1, sort_keys=True) + "\n"


def encode_entry(payload: dict) -> str:
    """entry_text base64-encoded for the GitHub contents API."""
    return base64.b64encode(entry_text(payload).encode("utf-8")).decode("ascii")


def submission_title(username: str, project: str) -> str:
    return f"benchmark: {username} — {project}"


def submission_body() -> str:
    return ("Self-reported HAID benchmark row (ADR-0005 v1). Summary statistics only.\n"
            "Validated by the benchmark-validate workflow; auto-merges on pass.")


def render_public_preview(payload: dict) -> str:
    """Human-readable 'this exact row becomes public and permanent' table."""
    g = payload
    rows = [
        ("github_username", g["github_username"]),
        ("project", g["project"]),
        ("overall score (value)", g["value_overall"]),
        ("achievement total", g["achievement_total"]),
        ("  volume (weighted LOC)", g["volume_loc_total"]),
        ("  difficulty (median rung)", g["difficulty_rung_median"]),
        ("  severe-defect density (median)", g["severe_density_median"]),
        ("normalized tokens total", g["normalized_tokens_total"]),
        ("episodes (scored/total)", f"{g['window']['n_scored']}/{g['window']['n_episodes']}"),
        ("ladder versions", ", ".join(f"{k}:{v}" for k, v in g["ladder_versions"].items())),
        ("combiner config", g["combiner_config_hash"]),
        ("tool version", g["tool_version"]),
        ("content hash", g["content_hash"][:16] + "…"),
    ]
    w = max(len(k) for k, _ in rows)
    body = "\n".join(f"  {k.ljust(w)}  {v}" for k, v in rows)
    return ("This is the ENTIRE payload that becomes PUBLIC and PERMANENT (git history).\n"
            "It is summary statistics only — no transcripts, diffs, prompts, or paths.\n"
            "Entries are labeled self-reported.\n\n" + body)


def write_entry(payload: dict, repo_root: Path) -> Path:
    """Write benchmark/entries/<username>.json. Re-checks the leak guard + hash first."""
    benchmark.assert_no_leaks(payload)
    if not benchmark.verify(payload):
        raise ValueError("refusing to write an entry whose content_hash does not verify")
    dest = repo_root.joinpath(*ENTRIES_DIR, f"{payload['github_username']}.json")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(entry_text(payload), encoding="utf-8")
    return dest


def pr_commands(username: str, project: str) -> list[list[str]]:
    """The git + gh sequence that opens the validated submission PR. Pure (returns argv);
    the caller runs them. One file is touched: benchmark/entries/<username>.json."""
    branch = f"benchmark/{username}"
    rel = entry_relpath(username)
    title = submission_title(username, project)
    return [
        ["git", "checkout", "-B", branch],
        ["git", "add", rel],
        ["git", "commit", "-m", title],
        ["git", "push", "-u", "origin", branch],
        ["gh", "pr", "create", "--repo", BENCHMARK_REPO, "--head", branch,
         "--title", title, "--body", submission_body()],
    ]


def run_pr(repo_root: Path, cmds: list[list[str]]) -> str:
    """Execute pr_commands in repo_root, returning the gh PR url (last command's stdout)."""
    out = ""
    for cmd in cmds:
        res = subprocess.run(cmd, cwd=repo_root, capture_output=True, text=True)
        if res.returncode != 0:
            raise RuntimeError(f"`{' '.join(cmd)}` failed:\n{res.stderr.strip()}")
        out = res.stdout.strip()
    return out


# --- clone-free path: open the PR entirely over the GitHub API via `gh` -----------------
#
# No working tree, no `--repo PATH`. The payload is one JSON file, so the whole submission
# is: ensure a fork, point benchmark/<user> at the upstream tip, PUT the file, open the PR.
# The repo owner can't fork their own repo, so when the submitter IS the upstream owner we
# operate on the upstream directly and use an unqualified head.

def _owner(slug: str) -> str:
    return slug.split("/", 1)[0]


def _repo_name(slug: str) -> str:
    return slug.split("/", 1)[1]


def _targets(username: str, upstream: str) -> tuple[str, str, str]:
    """(target_repo, branch, head) for this submitter. target_repo is the fork to write
    into (or upstream itself if they own it); head is what `gh pr create --head` wants."""
    branch = f"benchmark/{username}"
    if username == _owner(upstream):
        return upstream, branch, branch                 # owner: push to upstream directly
    fork = f"{username}/{_repo_name(upstream)}"
    return fork, branch, f"{username}:{branch}"          # contributor: fork + qualified head


def api_plan(payload: dict, project: str, *, upstream: str = BENCHMARK_REPO) -> list[list[str]]:
    """The happy-path `gh` argv submit_via_api runs (first submission). Pure — used by
    --dry-run. A re-submission adds a ref force-update and an existing-blob-sha lookup."""
    user = payload["github_username"]
    target, branch, head = _targets(user, upstream)
    rel = entry_relpath(user)
    title = submission_title(user, project)
    plan: list[list[str]] = []
    if target != upstream:
        plan.append(["gh", "repo", "fork", upstream, "--clone=false"])
    plan += [
        ["gh", "api", f"repos/{upstream}/git/ref/heads/{UPSTREAM_BRANCH}", "--jq", ".object.sha"],
        ["gh", "api", f"repos/{target}/git/refs",
         "-f", f"ref=refs/heads/{branch}", "-f", "sha=<UPSTREAM_SHA>"],
        ["gh", "api", "-X", "PUT", f"repos/{target}/contents/{rel}",
         "-f", f"message={title}", "-f", "content=<BASE64_ENTRY>", "-f", f"branch={branch}"],
        ["gh", "pr", "create", "--repo", upstream, "--head", head,
         "--title", title, "--body", submission_body()],
    ]
    return plan


def _default_run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True)


def _gh(run, args: list[str], *, check: bool = True) -> subprocess.CompletedProcess:
    res = run(["gh", *args])
    if check and res.returncode != 0:
        raise RuntimeError(f"`gh {' '.join(args)}` failed:\n{(res.stderr or '').strip()}")
    return res


def submit_via_api(payload: dict, project: str, *, upstream: str = BENCHMARK_REPO,
                   run=None) -> str:
    """Open the submission PR over the GitHub API (no checkout). Returns the PR url. `run`
    is an injectable command runner (cmd -> CompletedProcess) so the call sequence is
    testable; it defaults to subprocess."""
    run = run or _default_run
    benchmark.assert_no_leaks(payload)
    if not benchmark.verify(payload):
        raise ValueError("refusing to submit an entry whose content_hash does not verify")
    user = payload["github_username"]
    target, branch, head = _targets(user, upstream)
    rel = entry_relpath(user)
    title = submission_title(user, project)

    if target != upstream:                      # ensure the contributor's fork exists
        _gh(run, ["repo", "fork", upstream, "--clone=false"])

    sha = _gh(run, ["api", f"repos/{upstream}/git/ref/heads/{UPSTREAM_BRANCH}",
                    "--jq", ".object.sha"]).stdout.strip()

    # Point benchmark/<user> at the current upstream tip (create, or force-update if a prior
    # submission left it behind). The fork shares object storage with upstream, so its refs
    # can name an upstream sha.
    made = _gh(run, ["api", f"repos/{target}/git/refs",
                     "-f", f"ref=refs/heads/{branch}", "-f", f"sha={sha}"], check=False)
    if made.returncode != 0:
        _gh(run, ["api", "-X", "PATCH", f"repos/{target}/git/refs/heads/{branch}",
                  "-f", f"sha={sha}", "-F", "force=true"])

    # Updating an existing file (re-submission, or the row already merged into main) needs
    # the current blob sha; creating a new one must omit it.
    cur = _gh(run, ["api", f"repos/{target}/contents/{rel}?ref={branch}", "--jq", ".sha"],
              check=False)
    put = ["api", "-X", "PUT", f"repos/{target}/contents/{rel}",
           "-f", f"message={title}", "-f", f"content={encode_entry(payload)}",
           "-f", f"branch={branch}"]
    if cur.returncode == 0 and cur.stdout.strip():
        put += ["-f", f"sha={cur.stdout.strip()}"]
    _gh(run, put)

    made_pr = _gh(run, ["pr", "create", "--repo", upstream, "--head", head,
                        "--title", title, "--body", submission_body()], check=False)
    if made_pr.returncode == 0:
        return made_pr.stdout.strip()
    # A PR from this head may already be open — a re-submission updates it in place. The
    # head filter is owner-qualified for fork PRs (same `head` spec gh pr create took).
    existing = _gh(run, ["pr", "list", "--repo", upstream, "--head", head,
                         "--json", "url", "--jq", ".[0].url"], check=False)
    if existing.returncode == 0 and existing.stdout.strip():
        return existing.stdout.strip()
    raise RuntimeError(f"opening the submission PR failed:\n{(made_pr.stderr or '').strip()}")
