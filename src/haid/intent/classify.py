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
Label = dict      # {"move","work_type","purpose","impl_kind"}
_LABEL_KEYS = ("move", "work_type", "purpose")   # required; strict
_OPT_KEYS = ("impl_kind",)                        # optional; default null if absent


def _label_from_row(r: dict) -> Label:
    """Pull the required keys (strict) plus any optional keys (default null) off a raw row."""
    lab = {k: r[k] for k in _LABEL_KEYS}
    for k in _OPT_KEYS:
        lab[k] = r.get(k)
    return lab


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
                labels[r["uuid"]] = _label_from_row(r)
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
                         f"subagents over {manifest_path}, write their ref-keyed answers to "
                         "<name>.answers.json, then re-run")
        self.manifest_path = manifest_path
        self.n_jobs = n_jobs


# A runner: given the manifest it returns a flat list of label rows (each carrying its ref).
# Injected by the skill (absent in pure Python).
Runner = Callable[[dict], list[Label]]


def _rows_of(data) -> list[dict]:
    """Accept either a bare list or the documented `{"labels": [...]}` wrapper."""
    return data["labels"] if isinstance(data, dict) and "labels" in data else data


class HarnessBackend(ClassifierBackend):
    """Delegate classification to the host agent — one agent per session branch (R1).

    The agents emit short REFS, never full uuids; this backend owns the ref → uuid expansion
    so the model never copies a 36-char id. Two files, two roles:
      - `<name>.answers.json` — the agents' ref-keyed output, written by the host (or runner).
      - `<name>.labels.json`  — the canonical uuid-keyed labels THIS backend authors on
        read-back, consumed unchanged by `score`/`episodes`/ReplayBackend.

    Modes:
      - runner injected → call it synchronously, expand, write canonical.
      - no runner → file handoff: write the manifest; if answers (or a legacy/canonical labels
        file) already sit beside it, read+expand them; otherwise raise PendingClassifications.
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
                      "n_targets": len(j.targets),
                      # self-describing ref↔uuid map; the agent only ever echoes `ref`.
                      "targets": [{"uuid": u, "ref": rf}
                                  for u, rf in zip(j.targets, j.target_refs)],
                      "prompt": taxonomy.build_session_prompt(j.transcript, len(j.targets))}
                     for j in session_jobs],
        }

    def classify_messages(self, session_jobs, messages) -> dict[str, Label]:
        manifest = self._manifest(session_jobs)
        ref_to_uuid = {rf: u for j in session_jobs
                       for u, rf in zip(j.targets, j.target_refs)}
        if self.runner is not None:
            result = self._collect_by_ref(_rows_of(self.runner(manifest)), ref_to_uuid)
            self._write_canonical(result, messages)
            return result

        os.makedirs(self.job_dir, exist_ok=True)
        mpath = os.path.join(self.job_dir, f"{self.job_name}.job.json")
        apath = os.path.join(self.job_dir, f"{self.job_name}.answers.json")
        lpath = os.path.join(self.job_dir, f"{self.job_name}.labels.json")
        if os.path.exists(apath):                       # fresh agent output (ref-keyed)
            result = self._collect_by_ref(_rows_of(json.load(open(apath, encoding="utf-8"))),
                                          ref_to_uuid)
            self._write_canonical(result, messages)
            return result
        if os.path.exists(lpath):                       # resume / legacy: canonical is uuid-keyed
            return self._collect_by_uuid(_rows_of(json.load(open(lpath, encoding="utf-8"))),
                                         {m.uuid for m in messages})
        json.dump(manifest, open(mpath, "w", encoding="utf-8"), indent=1)
        raise PendingClassifications(mpath, len(manifest["jobs"]))

    def _write_canonical(self, result: dict[str, Label], messages) -> None:
        """Author the uuid-keyed labels file downstream steps consume — the only place a full
        uuid is attached, done deterministically from the manifest's own ref↔uuid map."""
        os.makedirs(self.job_dir, exist_ok=True)
        rows = [{"uuid": m.uuid, **result[m.uuid]} for m in messages]
        lpath = os.path.join(self.job_dir, f"{self.job_name}.labels.json")
        json.dump({"labels": rows}, open(lpath, "w", encoding="utf-8"), indent=1)

    @staticmethod
    def _collect_by_ref(rows, ref_to_uuid: dict[str, str]) -> dict[str, Label]:
        """Fold the agents' ref-keyed rows into {uuid: Label}, expanding each ref. Fails loudly
        on any coverage gap — a missing, unknown, or duplicated ref means a session job wasn't
        answered (or was mis-answered) and would silently poison everything downstream."""
        by_ref: dict[str, Label] = {}
        dups: set[str] = set()
        for r in rows:
            ref = r["ref"]
            if ref in by_ref:
                dups.add(ref)
            by_ref[ref] = _label_from_row(r)
        expected = set(ref_to_uuid)
        missing = expected - set(by_ref)
        unknown = set(by_ref) - expected
        if missing or unknown or dups:
            raise ValueError(
                f"tag labels don't match the window: {len(missing)} missing, "
                f"{len(unknown)} unknown, {len(dups)} duplicate ref(s). Re-run the affected "
                "session job(s) so every marked message is labeled exactly once.")
        return {ref_to_uuid[ref]: by_ref[ref] for ref in expected}

    @staticmethod
    def _collect_by_uuid(rows, expected: set[str]) -> dict[str, Label]:
        """Read a canonical uuid-keyed labels file directly (resume / legacy path)."""
        by_uuid = {r["uuid"]: _label_from_row(r) for r in rows}
        missing = expected - set(by_uuid)
        extra = set(by_uuid) - expected
        if missing or extra:
            raise ValueError(
                f"tag labels don't match the window: {len(missing)} missing, "
                f"{len(extra)} unexpected uuid(s) in the canonical labels file.")
        return {u: by_uuid[u] for u in expected}
