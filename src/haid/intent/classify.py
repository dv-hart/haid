"""Message-classification backends — the model-judgment boundary for the user-anchored pass.

Tagging needs one judgment per user message: its move, work type, and purpose snapshot.
That judgment is the ONLY part of the pass a model performs, and — exactly as the scoring
stack does for pairwise comparison (haid.scoring.compare) — HAID never makes an in-process
API call for it. Two backends implement the same interface:

  - ReplayBackend  — answers from saved fixture labels (keyed by message uuid). No model.
                     Used to test the orchestration deterministically in CI.
  - HarnessBackend — delegates to the host agent (Claude Code subagents) via a job manifest:
                     either an injected `runner` (the skill provides one — e.g. a dynamic
                     workflow that fans out one agent per message), or a file handoff that
                     writes the manifest and raises PendingClassifications for the skill to
                     fulfill and re-invoke. A future codex/`gh` runner swaps only the runner.

The manifest carries each message's codified prompt + the structured-output schema, so the
orchestration layer needs no taxonomy knowledge.
"""

from __future__ import annotations

import json
import os
from abc import ABC, abstractmethod
from typing import Callable

from . import taxonomy
from .messages import SessionTagJob, UserMessage

# A label is the validated structured output for one message.
Label = dict      # {"move","work_type","purpose"}
_LABEL_KEYS = ("move", "work_type", "purpose")


class ClassifierBackend(ABC):
    @abstractmethod
    def classify_messages(self, session_jobs: list[SessionTagJob],
                          messages: list[UserMessage]) -> dict[str, Label]:
        """Return {uuid: Label} covering every message.

        `session_jobs` carry the per-branch transcripts the live backend hands to agents;
        `messages` is the canonical deduped/ordered list (ReplayBackend reads its uuids; the
        HarnessBackend uses it to check that the labels cover the window exactly)."""
        raise NotImplementedError


# --- ReplayBackend: saved labels, no model ----------------------------------------------
class ReplayBackend(ClassifierBackend):
    """Answer classifications from saved fixture labels keyed by message uuid.

    Strict: a missing uuid raises, so a coverage gap surfaces instead of hiding."""

    def __init__(self, labels: dict[str, Label]):
        self._labels = labels

    @classmethod
    def from_files(cls, *paths: str) -> "ReplayBackend":
        labels: dict[str, Label] = {}
        for path in paths:
            data = json.load(open(path, encoding="utf-8"))
            rows = data["labels"] if isinstance(data, dict) and "labels" in data else data
            for r in rows:
                labels[r["uuid"]] = {k: r[k] for k in _LABEL_KEYS}
        return cls(labels)

    def classify_messages(self, session_jobs, messages) -> dict[str, Label]:
        out: dict[str, Label] = {}
        for m in messages:
            if m.uuid not in self._labels:
                raise KeyError(f"no saved label for message {m.uuid}")
            out[m.uuid] = self._labels[m.uuid]
        return out


# --- HarnessBackend: delegate to host-agent subagents -----------------------------------
class PendingClassifications(Exception):
    """Raised by HarnessBackend (file-handoff mode) when labels aren't ready yet.

    Carries the manifest path the skill should run subagents over, then re-invoke."""

    def __init__(self, manifest_path: str, n_jobs: int):
        super().__init__(f"{n_jobs} classification job(s) (one per session branch) — run "
                         f"subagents over {manifest_path}, write labels, then re-run")
        self.manifest_path = manifest_path
        self.n_jobs = n_jobs


# A runner: given the manifest it returns a flat list of label rows (each with its uuid).
# Injected by the skill (absent in pure Python).
Runner = Callable[[dict], list[Label]]


class HarnessBackend(ClassifierBackend):
    """Delegate classification to the host agent — one agent per session branch (R1).

      - runner injected → call it synchronously.
      - no runner → file handoff: write the manifest; if a labels file already sits beside
        it, read that; otherwise raise PendingClassifications for the skill to fulfill.
    """

    def __init__(self, job_dir: str, runner: Runner | None = None, job_name: str = "tag"):
        self.job_dir = job_dir
        self.runner = runner
        self.job_name = job_name

    def _manifest(self, session_jobs: list[SessionTagJob]) -> dict:
        return {
            "task": "classify_messages",
            "schema": taxonomy.SESSION_LABELS_SCHEMA,
            "jobs": [{"session_id": j.session_id, "timeline": j.timeline,
                      "n_targets": len(j.targets), "targets": list(j.targets),
                      "prompt": taxonomy.build_session_prompt(j.transcript, len(j.targets))}
                     for j in session_jobs],
        }

    def classify_messages(self, session_jobs, messages) -> dict[str, Label]:
        manifest = self._manifest(session_jobs)
        expected = {m.uuid for m in messages}
        if self.runner is not None:
            return self._collect(self.runner(manifest), expected)

        os.makedirs(self.job_dir, exist_ok=True)
        mpath = os.path.join(self.job_dir, f"{self.job_name}.job.json")
        lpath = os.path.join(self.job_dir, f"{self.job_name}.labels.json")
        if os.path.exists(lpath):
            data = json.load(open(lpath, encoding="utf-8"))
            rows = data["labels"] if isinstance(data, dict) and "labels" in data else data
            return self._collect(rows, expected)
        json.dump(manifest, open(mpath, "w", encoding="utf-8"), indent=1)
        raise PendingClassifications(mpath, len(manifest["jobs"]))

    @staticmethod
    def _collect(rows, expected: set[str]) -> dict[str, Label]:
        """Fold the agents' label rows into {uuid: Label}, failing loudly on any coverage gap
        — a missing or stray uuid means a session job wasn't answered (or was mis-answered)
        and would silently poison everything downstream."""
        by_uuid = {r["uuid"]: {k: r[k] for k in _LABEL_KEYS} for r in rows}
        missing = expected - set(by_uuid)
        extra = set(by_uuid) - expected
        if missing or extra:
            raise ValueError(
                f"tag labels don't match the window: {len(missing)} missing, "
                f"{len(extra)} unexpected uuid(s). Re-run the affected session job(s) and "
                "rewrite tag.labels.json so every marked message is labeled exactly once.")
        return {u: by_uuid[u] for u in expected}
