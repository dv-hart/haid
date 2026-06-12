"""Candidate manifest: JSONL records, deduped by full_name.

One reviewable line per candidate repo. This is the Pass-1 deliverable — the human
eyeballs coverage of the plane here before any Pass-2 diff extraction
(docs/calibration-experiment.md §11). Output dir /out/ is gitignored.
"""

from __future__ import annotations

import json
import os
from collections import Counter
from dataclasses import asdict, dataclass, field


@dataclass
class Candidate:
    full_name: str
    source_channel: str                  # "github_search" | "show_hn"
    population: str = "oracle"           # "oracle" (recent) | "validator" (reviewed)
    html_url: str | None = None
    description: str | None = None
    language: str | None = None
    stars: int | None = None
    forks: int | None = None
    open_issues: int | None = None
    license: str | None = None
    created_at: str | None = None
    pushed_at: str | None = None
    size_kb: int | None = None
    topics: list[str] = field(default_factory=list)
    is_fork: bool | None = None
    archived: bool | None = None
    has_issues: bool | None = None
    # cheap-proxy plane placement (priors only — never labels)
    difficulty_prior: str | None = None
    volume_prior: str | None = None
    star_bucket: str | None = None
    # Show HN provenance (when applicable)
    hn_points: int | None = None
    hn_comments: int | None = None
    hn_url: str | None = None
    hn_title: str | None = None


def from_repo_json(repo: dict, *, source_channel: str) -> Candidate:
    """Build a Candidate from a GitHub repo API object."""
    lic = repo.get("license") or {}
    return Candidate(
        full_name=repo["full_name"],
        source_channel=source_channel,
        html_url=repo.get("html_url"),
        description=repo.get("description"),
        language=repo.get("language"),
        stars=repo.get("stargazers_count"),
        forks=repo.get("forks_count"),
        open_issues=repo.get("open_issues_count"),
        license=(lic.get("spdx_id") if isinstance(lic, dict) else None),
        created_at=repo.get("created_at"),
        pushed_at=repo.get("pushed_at"),
        size_kb=repo.get("size"),
        topics=repo.get("topics") or [],
        is_fork=repo.get("fork"),
        archived=repo.get("archived"),
        has_issues=repo.get("has_issues"),
    )


class Manifest:
    """Append-only JSONL writer with in-memory dedup by full_name."""

    def __init__(self, path: str):
        self.path = path
        self.seen: set[str] = set()
        self.records: list[Candidate] = []
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self._load_existing()

    def _load_existing(self) -> None:
        if not os.path.exists(self.path):
            return
        with open(self.path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    self.seen.add(rec["full_name"])
                except (json.JSONDecodeError, KeyError):
                    continue

    def has(self, full_name: str) -> bool:
        return full_name in self.seen

    def add(self, cand: Candidate) -> bool:
        """Append a candidate; returns False if it was a duplicate."""
        if cand.full_name in self.seen:
            return False
        self.seen.add(cand.full_name)
        self.records.append(cand)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(cand), ensure_ascii=False) + "\n")
        return True

    # -- coverage reporting -------------------------------------------------------
    def coverage_table(self) -> str:
        """ASCII difficulty x volume coverage grid over this run's added records."""
        diffs = ["low", "mid", "high"]
        vols = ["small", "mid", "large", "unknown"]
        counts: Counter[tuple[str, str]] = Counter()
        for r in self.records:
            counts[(r.difficulty_prior or "?", r.volume_prior or "?")] += 1

        header = "difficulty \\ volume | " + " | ".join(f"{v:>7}" for v in vols)
        lines = [header, "-" * len(header)]
        for d in diffs:
            row = " | ".join(f"{counts[(d, v)]:>7}" for v in vols)
            lines.append(f"{d:>19} | {row}")
        lines.append(f"\nadded this run: {len(self.records)} | total in manifest: {len(self.seen)}")
        return "\n".join(lines)

    def channel_breakdown(self) -> str:
        c = Counter(r.source_channel for r in self.records)
        return ", ".join(f"{k}={v}" for k, v in sorted(c.items())) or "(none)"
