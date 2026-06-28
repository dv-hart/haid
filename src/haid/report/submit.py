"""`haid submit` — opt-in: push your summary row to the community benchmark (ADR-0005 v1).

This is the only path that leaves the machine, and it leaves ONLY the summary-only
benchmark row (benchmark.build_submission — leak-guarded, no logs/diffs/paths). Identity
is the authenticated GitHub PR author; there is no local signature in v1.

The flow, deliberately split so the side-effect-free parts are unit-testable:
  build_submission  -> the public row (pure)
  render_public_preview -> exactly what becomes public + permanent (pure)
  write_entry       -> benchmark/entries/<username>.json in a benchmark-repo checkout (fs)
  pr_commands       -> the git+gh sequence that opens the validated PR (pure: returns argv)

`haid submit` prints the preview, requires confirmation (--yes or a TTY), writes the entry,
and unless --dry-run runs pr_commands to open the PR. The repo-side GitHub Action validates
and auto-merges; direct writes to the log are never accepted.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from . import benchmark

# The benchmark is its OWN data-only repo (no application code, no release secrets), so a
# bad merge there can't reach the package or PyPI. Rows live at entries/<user>.json.
BENCHMARK_REPO = "dv-hart/haid-benchmark"
ENTRIES_DIR = ("entries",)
REPO_MARKER = ".haid-benchmark-repo"     # sentinel file at the data-repo root


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
    dest.write_text(json.dumps(payload, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return dest


def pr_commands(username: str, project: str) -> list[list[str]]:
    """The git + gh sequence that opens the validated submission PR. Pure (returns argv);
    the caller runs them. One file is touched: benchmark/entries/<username>.json."""
    branch = f"benchmark/{username}"
    rel = entry_relpath(username)
    title = f"benchmark: {username} — {project}"
    body = ("Self-reported HAID benchmark row (ADR-0005 v1). Summary statistics only.\n"
            "Validated by the benchmark-validate workflow; auto-merges on pass.")
    return [
        ["git", "checkout", "-B", branch],
        ["git", "add", rel],
        ["git", "commit", "-m", title],
        ["git", "push", "-u", "origin", branch],
        ["gh", "pr", "create", "--repo", BENCHMARK_REPO, "--head", branch,
         "--title", title, "--body", body],
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
