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
from dataclasses import dataclass
from typing import Callable

from . import taxonomy

# A label is the validated structured output for one message.
Label = dict      # {"move","work_type","purpose"}
_LABEL_KEYS = ("move", "work_type", "purpose")


@dataclass(frozen=True)
class ClassifyItem:
    """One message to classify. `uuid` lets ReplayBackend look up a saved label; the live
    backend uses `prompt` (built from the message + its context + priors)."""
    uuid: str
    session_id: str
    prompt: str


class ClassifierBackend(ABC):
    @abstractmethod
    def classify_batch(self, items: list[ClassifyItem]) -> list[Label]:
        """Return one Label per item, in the same order."""
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

    def classify_batch(self, items: list[ClassifyItem]) -> list[Label]:
        out: list[Label] = []
        for it in items:
            if it.uuid not in self._labels:
                raise KeyError(f"no saved label for message {it.uuid}")
            out.append(self._labels[it.uuid])
        return out


# --- HarnessBackend: delegate to host-agent subagents -----------------------------------
class PendingClassifications(Exception):
    """Raised by HarnessBackend (file-handoff mode) when labels aren't ready yet.

    Carries the manifest path the skill should run subagents over, then re-invoke."""

    def __init__(self, manifest_path: str, n_jobs: int):
        super().__init__(f"{n_jobs} messages to classify — run subagents over "
                         f"{manifest_path}, write labels, then re-run")
        self.manifest_path = manifest_path
        self.n_jobs = n_jobs


# A runner: given the manifest it returns one label dict per job, in order. Injected by the
# skill (absent in pure Python).
Runner = Callable[[dict], list[Label]]


class HarnessBackend(ClassifierBackend):
    """Delegate classification to the host agent.

      - runner injected → call it synchronously.
      - no runner → file handoff: write the manifest; if a labels file already sits beside
        it, read that; otherwise raise PendingClassifications for the skill to fulfill.
    """

    def __init__(self, job_dir: str, runner: Runner | None = None, job_name: str = "tag"):
        self.job_dir = job_dir
        self.runner = runner
        self.job_name = job_name

    def _manifest(self, items: list[ClassifyItem]) -> dict:
        return {
            "task": "classify_messages",
            "schema": taxonomy.LABEL_SCHEMA,
            "jobs": [{"uuid": it.uuid, "session_id": it.session_id, "prompt": it.prompt}
                     for it in items],
        }

    def classify_batch(self, items: list[ClassifyItem]) -> list[Label]:
        manifest = self._manifest(items)
        if self.runner is not None:
            return list(self.runner(manifest))

        os.makedirs(self.job_dir, exist_ok=True)
        mpath = os.path.join(self.job_dir, f"{self.job_name}.job.json")
        lpath = os.path.join(self.job_dir, f"{self.job_name}.labels.json")
        if os.path.exists(lpath):
            data = json.load(open(lpath, encoding="utf-8"))
            rows = data["labels"] if isinstance(data, dict) and "labels" in data else data
            by_uuid = {r["uuid"]: r for r in rows}
            return [{k: by_uuid[it.uuid][k] for k in _LABEL_KEYS} for it in items]
        json.dump(manifest, open(mpath, "w", encoding="utf-8"), indent=1)
        raise PendingClassifications(mpath, len(items))
