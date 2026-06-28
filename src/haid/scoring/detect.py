"""Single-diff defect-detection backends — the cleanliness model-judgment boundary.

The counted-defect replacement for the pairwise cleanliness ladder (see scoring/defects.py
for WHY). Difficulty still uses the pairwise `compare.py` boundary; cleanliness does NOT.
This module mirrors compare.py's shape (Backend ABC, ReplayBackend, HarnessBackend with a
runner or a file-handoff manifest, fingerprint + staleness guard) but for a fundamentally
different judgment:

  - It is SINGLE-DIFF, not pairwise: there are no anchors and no counterbalancing (the two
    things compare.py's flip machinery exists for). The judgment is "catalogue the defects
    in THIS diff", answered by inspection.
  - It is TWO-PHASE: first DETECT (one cataloguing pass over the diff), then VERIFY (one
    adversarial refuter per severe finding). The file-handoff path therefore defers TWICE —
    once for findings, once for verdicts — via PendingDetection(phase=...).

HAID never makes an in-process model call: a HarnessBackend either calls an injected runner
(the skill's subagent fan-out) or writes a job manifest the host agent fulfils.
"""

from __future__ import annotations

import hashlib
import json
import os
from abc import ABC, abstractmethod
from typing import Callable

from .defects import (DEFECT_SCHEMA, VERIFY_SCHEMA, DefectResult, apply_verify,
                      build_defect_prompt, build_verify_prompt)


class DetectBackend(ABC):
    """Resolve, for one diff, its post-verify DefectResult (detect → verify)."""

    @abstractmethod
    def detect(self, diff: str, changed_lines: int) -> DefectResult:
        raise NotImplementedError


# --- ReplayBackend: saved findings + verdicts, no model -----------------------------
class ReplayBackend(DetectBackend):
    """Answer detection from saved findings (+ optional verify verdicts), keyed by subject id.

    `saved` maps subject_id -> {"findings": [...], "verify": [...]} where `verify` is one
    verdict per severe finding, in order (omitted/empty = no verify pass applied). Lookups are
    strict: a missing subject raises, so validation surfaces coverage gaps. The subject id is
    taken from the backend's `subject_id` (set per placement)."""

    def __init__(self, saved: dict, subject_id: str | None = None):
        self._saved = saved
        self.subject_id = subject_id

    @classmethod
    def from_files(cls, *paths: str) -> "ReplayBackend":
        merged: dict = {}
        for path in paths:
            merged.update(json.load(open(path, encoding="utf-8")))
        return cls(merged)

    def for_subject(self, subject_id: str) -> "ReplayBackend":
        return ReplayBackend(self._saved, subject_id=subject_id)

    def detect(self, diff: str, changed_lines: int) -> DefectResult:
        if self.subject_id is None:
            raise ValueError("ReplayBackend requires subject_id (use for_subject)")
        if self.subject_id not in self._saved:
            raise KeyError(f"no saved detection for subject {self.subject_id!r}")
        entry = self._saved[self.subject_id]
        result = DefectResult.from_findings(entry.get("findings", []), changed_lines)
        verdicts = entry.get("verify")
        if verdicts is not None:
            result = apply_verify(result, verdicts)
        return result


# --- HarnessBackend: delegate to host-agent subagents ------------------------------
class PendingDetection(Exception):
    """Raised by HarnessBackend (file-handoff mode) when a phase's answers aren't ready.

    `phase` is "detect" (findings pending) or "verify" (verdicts pending). The skill runs the
    subagents over `manifest_path`, writes the answers beside it, and re-invokes; a re-invoke
    after detect findings land will progress to the verify phase (or finish if 0 severe)."""

    def __init__(self, manifest_path: str, phase: str):
        super().__init__(f"{phase} pending — run subagents over {manifest_path}, "
                         "write answers, then re-run")
        self.manifest_path = manifest_path
        self.phase = phase


# A runner: given a job manifest, returns the detect `findings` list (detect manifest) or the
# `verdicts` list (verify manifest). Injected by the skill; absent in pure Python.
Runner = Callable[[dict], list]


def _fp(*parts: str) -> str:
    h = hashlib.sha256()
    for p in parts:
        h.update(b"\x00")
        h.update(p.encode("utf-8"))
    return h.hexdigest()[:16]


class HarnessBackend(DetectBackend):
    """Delegate detection to the host agent.

    runner injected → run detect then verify synchronously. No runner → file handoff: write the
    detect manifest; when findings land, build the DefectResult; if it has severe findings write
    the verify manifest and (when verdicts land) apply them. Each missing answer file defers via
    PendingDetection so the skill can fulfil it and re-invoke."""

    def __init__(self, job_dir: str, runner: Runner | None = None,
                 job_name: str = "detect"):
        self.job_dir = job_dir
        self.runner = runner
        self.job_name = job_name

    # -- manifests -----------------------------------------------------------------
    def _detect_manifest(self, diff: str, changed_lines: int) -> dict:
        return {
            "task": "detect_defects",
            "schema": DEFECT_SCHEMA,
            "fingerprint": _fp("detect", diff),
            "subject": {"changed_lines": changed_lines},
            "prompt": build_defect_prompt(diff),
        }

    def _verify_manifest(self, diff: str, severe: list) -> dict:
        return {
            "task": "verify_defects",
            "schema": VERIFY_SCHEMA,
            "fingerprint": _fp("verify", *(f"{f.get('defect_class','')}\x00{f.get('locator','')}"
                                           for f in severe)),
            "verifications": [
                {"finding_index": i, "defect_class": f.get("defect_class"),
                 "prompt": build_verify_prompt(f, diff)}
                for i, f in enumerate(severe)
            ],
        }

    # -- resolution ----------------------------------------------------------------
    def detect(self, diff: str, changed_lines: int) -> DefectResult:
        if self.runner is not None:
            findings = list(self.runner(self._detect_manifest(diff, changed_lines)))
            result = DefectResult.from_findings(findings, changed_lines)
            severe = result.severe_findings()
            if not severe:
                return result
            verdicts = list(self.runner(self._verify_manifest(diff, severe)))
            return apply_verify(result, verdicts)
        return self._file_handoff(diff, changed_lines)

    def _file_handoff(self, diff: str, changed_lines: int) -> DefectResult:
        os.makedirs(self.job_dir, exist_ok=True)
        dman = self._detect_manifest(diff, changed_lines)
        dpath = os.path.join(self.job_dir, f"{self.job_name}.detect.job.json")
        fpath = os.path.join(self.job_dir, f"{self.job_name}.detect.findings.json")

        if not os.path.exists(fpath):
            json.dump(dman, open(dpath, "w", encoding="utf-8"), indent=1)
            raise PendingDetection(dpath, phase="detect")

        fdata = json.load(open(fpath, encoding="utf-8"))
        if fdata.get("fingerprint") != dman["fingerprint"]:
            raise ValueError(f"{fpath}: fingerprint {fdata.get('fingerprint')!r} != manifest "
                             f"{dman['fingerprint']!r} — stale findings; delete and re-run.")
        result = DefectResult.from_findings(fdata["findings"], changed_lines)
        severe = result.severe_findings()
        if not severe:
            return result

        vman = self._verify_manifest(diff, severe)
        vpath = os.path.join(self.job_dir, f"{self.job_name}.verify.job.json")
        vrpath = os.path.join(self.job_dir, f"{self.job_name}.verify.verdicts.json")
        if not os.path.exists(vrpath):
            json.dump(vman, open(vpath, "w", encoding="utf-8"), indent=1)
            raise PendingDetection(vpath, phase="verify")

        vdata = json.load(open(vrpath, encoding="utf-8"))
        if vdata.get("fingerprint") != vman["fingerprint"]:
            raise ValueError(f"{vrpath}: fingerprint {vdata.get('fingerprint')!r} != manifest "
                             f"{vman['fingerprint']!r} — stale verdicts; delete and re-run.")
        return apply_verify(result, vdata["verdicts"])
