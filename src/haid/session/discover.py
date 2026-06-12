"""Locate a project's transcript files from its working directory.

Claude Code stores sessions under `~/.claude/projects/<encoded-project-path>/`, where the
encoding replaces path separators and the drive colon with dashes:
  C:\\Users\\jhart\\Documents\\software\\HAID  ->  C--Users-jhart-Documents-software-HAID
  /home/jhart/software/boxBot                  ->  -home-jhart-software-boxBot

This lets `haid report` default to "recent sessions for the current project" with no args.
The projects root is overridable (tests, and WSL sessions live under a different HOME than
the Windows host). Stdlib only.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

_SEP = re.compile(r"[/\\:]")


def encode_project_path(project_path: str) -> str:
    """Encode an absolute project path the way Claude Code names its transcript dir."""
    return _SEP.sub("-", project_path)


def default_projects_root() -> Path:
    return Path(os.path.expanduser("~")) / ".claude" / "projects"


def project_dir(project_path: str, projects_root: str | os.PathLike | None = None) -> Path:
    root = Path(projects_root) if projects_root else default_projects_root()
    return root / encode_project_path(project_path)


def find_sessions(project_path: str, projects_root: str | os.PathLike | None = None,
                  since: str | None = None) -> list[Path]:
    """Transcript files for a project, newest first. `since` filters by mtime ISO date."""
    d = project_dir(project_path, projects_root)
    if not d.is_dir():
        return []
    files = sorted(d.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    if since:
        cutoff = _mtime_cutoff(since)
        files = [p for p in files if p.stat().st_mtime >= cutoff]
    return files


def _mtime_cutoff(since: str) -> float:
    from datetime import datetime
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(since, fmt).timestamp()
        except ValueError:
            continue
    raise ValueError(f"unrecognized --since date: {since!r} (use YYYY-MM-DD)")
