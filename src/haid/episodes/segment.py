"""Grouping backends — the model-judgment boundary for session→episode formation.

Episode formation is ONE holistic judgment over the window's sessions (not a per-session
fan-out): cluster whole sessions into episodes by shared component/topic. As the scoring and
classifier stacks do, HAID never makes an in-process API call for it. Three backends share one
interface:

  - HeuristicBackend — NO model. The deterministic baseline: group maximal runs of consecutive
                       sessions linked by file overlap (or, when neither touched files, by
                       temporal proximity). Immediately usable / dogfoodable; the floor the model
                       improves on. (Being adjacency-only, it can't express a component resumed
                       after an interruption — that is exactly what the model adds.)
  - ReplayBackend    — answers from a saved grouping (EpisodeGroups). No model. CI.
  - HarnessBackend   — delegates the single grouping job to the host agent: an injected `runner`
                       or a file handoff that writes the manifest and raises PendingSegmentation.

The manifest carries the codified prompt + schema, so the orchestration layer needs no grouping
knowledge. Stdlib only.
"""

from __future__ import annotations

import json
import os
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Callable

from . import grouping
from .model import EpisodeGroup


def _parse_ts(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


class GroupingBackend(ABC):
    @abstractmethod
    def group(self, summaries) -> list[EpisodeGroup]:
        """Return the window's episodes (a partition of the sessions)."""
        raise NotImplementedError


# --- HeuristicBackend: deterministic baseline, no model ---------------------------------
class HeuristicBackend(GroupingBackend):
    """Maximal contiguous runs of sessions linked by file overlap / temporal proximity."""

    def __init__(self, overlap_threshold: float = grouping.DEFAULT_OVERLAP,
                 gap_seconds: int = grouping.DEFAULT_GAP_SECONDS):
        self.overlap_threshold = overlap_threshold
        self.gap_seconds = gap_seconds

    def _linked(self, prev, cur) -> bool:
        ov = grouping.file_overlap(cur.file_set, prev.file_set)
        if ov is not None:
            return ov >= self.overlap_threshold
        # No file signal on at least one side → fall back to temporal proximity.
        t0, t1 = _parse_ts(prev.last_ts), _parse_ts(cur.first_ts)
        if t0 and t1:
            return (t1 - t0).total_seconds() < self.gap_seconds
        return False

    def group(self, summaries) -> list[EpisodeGroup]:
        ordered = sorted(summaries, key=lambda s: s.index)
        groups: list[list] = []
        for s in ordered:
            if groups and self._linked(groups[-1][-1], s):
                groups[-1].append(s)
            else:
                groups.append([s])
        out = []
        for run in groups:
            title = next((p for s in run for p in s.purposes), f"session {run[0].session_id}")
            out.append(EpisodeGroup(
                title=title, session_ids=[s.session_id for s in run],
                rationale="deterministic: consecutive sessions linked by shared files / proximity"
                          if len(run) > 1 else "deterministic: standalone session"))
        return out


# --- ReplayBackend: saved grouping, no model --------------------------------------------
class ReplayBackend(GroupingBackend):
    """Answer with a saved list of episode groups (test/CI). Validated against the input by the
    orchestration layer (segment_window), so a stale fixture surfaces as an error."""

    def __init__(self, groups: list[EpisodeGroup]):
        self._groups = groups

    @classmethod
    def from_rows(cls, rows: list[dict]) -> "ReplayBackend":
        return cls([EpisodeGroup(title=r.get("title", ""), session_ids=list(r["session_ids"]),
                                 rationale=r.get("rationale", "")) for r in rows])

    @classmethod
    def from_file(cls, path: str) -> "ReplayBackend":
        data = json.load(open(path, encoding="utf-8"))
        rows = data["episodes"] if isinstance(data, dict) and "episodes" in data else data
        return cls.from_rows(rows)

    def group(self, summaries) -> list[EpisodeGroup]:
        return list(self._groups)


# --- HarnessBackend: delegate the single grouping job to the host agent ------------------
class PendingSegmentation(Exception):
    """Raised by HarnessBackend (file-handoff mode) when the grouping isn't ready yet."""

    def __init__(self, manifest_path: str):
        super().__init__(f"run the grouping agent over {manifest_path}, write the grouping, "
                         "then re-run")
        self.manifest_path = manifest_path


# A runner: given the manifest it returns the grouping dict ({"episodes": [...]}). Injected by
# the skill (absent in pure Python).
Runner = Callable[[dict], dict]


class HarnessBackend(GroupingBackend):
    """Delegate the one grouping judgment to the host agent.

      - runner injected → call it synchronously.
      - no runner → file handoff: write the manifest; if a grouping file already sits beside it,
        read that; otherwise raise PendingSegmentation for the skill to fulfill.
    """

    def __init__(self, job_dir: str, runner: Runner | None = None, job_name: str = "episodes",
                 overlap_threshold: float = grouping.DEFAULT_OVERLAP):
        self.job_dir = job_dir
        self.runner = runner
        self.job_name = job_name
        self.overlap_threshold = overlap_threshold

    def _manifest(self, summaries) -> dict:
        return {
            "task": "group_sessions_into_episodes",
            "schema": grouping.SEGMENT_SCHEMA,
            "prompt": grouping.build_group_prompt(summaries, self.overlap_threshold),
        }

    @staticmethod
    def _groups(payload: dict) -> list[EpisodeGroup]:
        rows = payload["episodes"] if isinstance(payload, dict) else payload
        return [EpisodeGroup(title=r.get("title", ""), session_ids=list(r["session_ids"]),
                             rationale=r.get("rationale", "")) for r in rows]

    def group(self, summaries) -> list[EpisodeGroup]:
        manifest = self._manifest(summaries)
        if self.runner is not None:
            return self._groups(self.runner(manifest))

        os.makedirs(self.job_dir, exist_ok=True)
        mpath = os.path.join(self.job_dir, f"{self.job_name}.job.json")
        gpath = os.path.join(self.job_dir, f"{self.job_name}.grouping.json")
        if os.path.exists(gpath):
            return self._groups(json.load(open(gpath, encoding="utf-8")))
        json.dump(manifest, open(mpath, "w", encoding="utf-8"), indent=1)
        raise PendingSegmentation(mpath)
