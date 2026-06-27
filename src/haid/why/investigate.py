"""Investigation backends — the model-judgment boundary for the why-pass.

Same contract as scoring/compare.py and intent/classify.py: HAID never makes an
in-process API call. A manifest carries one self-contained investigation prompt per
anchor plus the structured-output schema; the host agent runs one tool-using subagent
per job (recommended tier: sonnet — multi-step tool use over big transcripts, not
frontier reasoning; user-overridable, and a codex/gh runner maps it to its own tier).

Read-back is STRICT (lesson from the live classifier run, where an invalid enum was
silently accepted): every note is validated against the schema's required keys, flag
vocabulary, and confidence enum before it enters the report.
"""

from __future__ import annotations

import json
import os
from abc import ABC, abstractmethod
from typing import Callable

from .anchors import WhyAnchor
from .prompts import (BUG_NOTE_SCHEMA, CAUSE_CLASSES, CONFIDENCES, FLAGS, HOLDINGS,
                      MISTAKE_KINDS, NOTE_SCHEMA, ORIGINS, RECOMMENDED_MODEL,
                      build_anchor_prompt, build_bug_prompt)

Note = dict
_REQUIRED = tuple(NOTE_SCHEMA["required"])
_BUG_REQUIRED = tuple(BUG_NOTE_SCHEMA["required"])
BUG_METRIC = "bugfix"


def validate_note(note: dict, anchor_id: str) -> Note:
    """Strict note validation — raise on any contract violation, never coerce."""
    missing = [k for k in _REQUIRED if k not in note]
    if missing:
        raise ValueError(f"why note for {anchor_id}: missing keys {missing}")
    bad = [f for f in note["flags"] if f not in FLAGS]
    if bad:
        raise ValueError(f"why note for {anchor_id}: unknown flags {bad} "
                         f"(must be from prompts.FLAGS)")
    if note["confidence"] not in CONFIDENCES:
        raise ValueError(f"why note for {anchor_id}: confidence "
                         f"{note['confidence']!r} not in {CONFIDENCES}")
    tok = note["estimated_avoidable_tokens"]
    if tok is not None and not isinstance(tok, int):
        raise ValueError(f"why note for {anchor_id}: estimated_avoidable_tokens must be "
                         "int or null")
    return {k: note[k] for k in _REQUIRED}


def validate_bug_note(note: dict, anchor_id: str) -> Note:
    """Strict bug-attribution note validation — enum-check every controlled field."""
    missing = [k for k in _BUG_REQUIRED if k not in note]
    if missing:
        raise ValueError(f"bug note for {anchor_id}: missing keys {missing}")
    checks = {"cause_class": CAUSE_CLASSES, "origin": ORIGINS, "holding": HOLDINGS,
              "confidence": CONFIDENCES, "scope": ("same_episode", "cross_episode", "unknown")}
    for key, allowed in checks.items():
        if note[key] not in allowed:
            raise ValueError(f"bug note for {anchor_id}: {key}={note[key]!r} not in {allowed}")
    mk = note["mistake_kind"]
    if mk is not None and mk not in MISTAKE_KINDS:
        raise ValueError(f"bug note for {anchor_id}: mistake_kind {mk!r} not in {MISTAKE_KINDS}")
    if note["cause_class"] == "agent" and mk is None:
        raise ValueError(f"bug note for {anchor_id}: cause_class=agent requires a mistake_kind")
    tok = note["estimated_rework_tokens"]
    if tok is not None and not isinstance(tok, int):
        raise ValueError(f"bug note for {anchor_id}: estimated_rework_tokens must be int/null")
    return {k: note[k] for k in _BUG_REQUIRED}


def validate_for_anchor(note: dict, anchor: WhyAnchor) -> Note:
    """Dispatch validation by anchor kind (bug anchors carry a different contract)."""
    if anchor.metric == BUG_METRIC:
        return validate_bug_note(note, anchor.id)
    return validate_note(note, anchor.id)


def _prompt_for(anchor: WhyAnchor, **kw) -> str:
    return (build_bug_prompt if anchor.metric == BUG_METRIC else build_anchor_prompt)(anchor, **kw)


def _schema_for(anchor: WhyAnchor) -> dict:
    return BUG_NOTE_SCHEMA if anchor.metric == BUG_METRIC else NOTE_SCHEMA


class WhyBackend(ABC):
    @abstractmethod
    def investigate_batch(self, anchors: list[WhyAnchor], *, transcript_dir: str,
                          project_path: str, all_session_ids: list[str]) -> list[Note]:
        """Return one validated Note per anchor, in order."""
        raise NotImplementedError


class ReplayBackend(WhyBackend):
    """Saved notes keyed by anchor id — deterministic tests/CI only."""

    def __init__(self, notes: dict[str, Note]):
        self._notes = notes

    @classmethod
    def from_files(cls, *paths: str) -> "ReplayBackend":
        notes: dict[str, Note] = {}
        for path in paths:
            data = json.load(open(path, encoding="utf-8"))
            rows = data["notes"] if isinstance(data, dict) and "notes" in data else data
            for r in rows:
                notes[r["anchor_id"]] = r
        return cls(notes)

    def investigate_batch(self, anchors, *, transcript_dir, project_path,
                          all_session_ids):
        out = []
        for a in anchors:
            if a.id not in self._notes:
                raise KeyError(f"no saved why note for anchor {a.id}")
            out.append(validate_for_anchor(self._notes[a.id], a))
        return out


class PendingInvestigations(Exception):
    """Raised by HarnessBackend (file-handoff mode) when notes aren't ready yet."""

    def __init__(self, manifest_path: str, n_jobs: int):
        super().__init__(f"{n_jobs} investigations pending — run one subagent per job in "
                         f"{manifest_path}, write notes, then re-run")
        self.manifest_path = manifest_path
        self.n_jobs = n_jobs


# A runner returns one raw note dict per job, in order (a workflow fanning agents, or a
# future codex/gh orchestrator). Injected by the skill; absent in pure Python.
Runner = Callable[[dict], list[Note]]


class HarnessBackend(WhyBackend):
    """Delegate investigations to the host agent (runner or file handoff)."""

    def __init__(self, job_dir: str, runner: Runner | None = None,
                 job_name: str = "why", model: str = RECOMMENDED_MODEL):
        self.job_dir = job_dir
        self.runner = runner
        self.job_name = job_name
        self.model = model

    def _manifest(self, anchors, *, transcript_dir, project_path, all_session_ids):
        # Per-job `schema`: a bug anchor carries the bug-attribution contract, a waste anchor
        # the why-note contract. The top-level `schema` stays the waste default for any reader
        # that predates per-job schemas; the runner/skill should attach each job's own.
        return {
            "task": "why_pass",
            "recommended_model": self.model,
            "schema": NOTE_SCHEMA,
            "jobs": [{"anchor_id": a.id, "metric": a.metric,
                      "token_weight": a.token_weight,
                      "schema": _schema_for(a),
                      "prompt": _prompt_for(
                          a, transcript_dir=transcript_dir, project_path=project_path,
                          all_session_ids=all_session_ids)}
                     for a in anchors],
        }

    def investigate_batch(self, anchors, *, transcript_dir, project_path,
                          all_session_ids):
        manifest = self._manifest(anchors, transcript_dir=transcript_dir,
                                  project_path=project_path,
                                  all_session_ids=all_session_ids)
        if self.runner is not None:
            raw = list(self.runner(manifest))
            if len(raw) != len(anchors):
                raise ValueError(f"why runner returned {len(raw)} notes for "
                                 f"{len(anchors)} anchors")
            return [validate_for_anchor(n, a) for n, a in zip(raw, anchors)]

        os.makedirs(self.job_dir, exist_ok=True)
        mpath = os.path.join(self.job_dir, f"{self.job_name}.job.json")
        npath = os.path.join(self.job_dir, f"{self.job_name}.notes.json")
        if os.path.exists(npath):
            data = json.load(open(npath, encoding="utf-8"))
            rows = data["notes"] if isinstance(data, dict) and "notes" in data else data
            by_id = {r["anchor_id"]: r for r in rows}
            missing = [a.id for a in anchors if a.id not in by_id]
            if missing:
                raise ValueError(f"{npath}: missing notes for anchors {missing}")
            return [validate_for_anchor(by_id[a.id], a) for a in anchors]
        json.dump(manifest, open(mpath, "w", encoding="utf-8"), indent=1)
        raise PendingInvestigations(mpath, len(anchors))
