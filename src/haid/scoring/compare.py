"""Pairwise comparison backends — the model-judgment boundary.

Placement needs one judgment per anchor: "is the session diff MORE difficult than this
anchor?" (difficulty is the only pairwise axis — cleanliness is now counted defect
detection, see scoring/defects.py). That judgment is the ONLY part of scoring a model
performs, and HAID never makes an in-process API call for it. Two backends implement the
same interface:

  - ReplayBackend  — answers from saved calibration verdicts. No model. Used to prove the
                     runtime placement code reproduces the validated experiment exactly.
  - HarnessBackend — delegates each comparison to the host agent (Claude Code subagents)
                     via a job manifest the skill orchestrates. This is the live path;
                     future codex / `gh` variants swap only the `runner`, not this module.

The comparison is BLIND and symmetric: given two diffs, which is MORE <axis>? The
codified prompts below are the hardened wording from the calibration playbook
(docs/axis-calibration-playbook.md §3): ignore size, ignore surface sophistication,
judge relative to what the task requires.

COUNTERBALANCING + INTEGRITY (the agent-orchestration contract): the subject's side
(Diff A vs Diff B) is flipped per comparison by a deterministic hash, so position bias
averages out. The flip is computed here on BOTH passes (manifest emission and verdict
read-back) and is never written into the manifest — the orchestrator only sees opaque
prompts, so it cannot leak which side is the subject nor mis-apply the un-flip. The
manifest carries a `fingerprint` the verdicts file must echo; a missing/mismatched
fingerprint, a wrong winner count, or a winner outside A/B/tie raises instead of
silently mis-scoring.
"""

from __future__ import annotations

import hashlib
import json
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Callable, Literal

Winner = Literal["subject", "anchor", "tie"]

# --- codified axis questions (docs/axis-calibration-playbook.md §3) ----------------
_DIFFICULTY_Q = (
    "ONE axis only: DIFFICULTY = skill rarity. Ask: what fraction of working engineers "
    "could produce THIS change correctly? A change only a few could get right is HIGH; "
    "one almost anyone could is LOW. IGNORE size (a large change is not automatically "
    "hard) and IGNORE surface sophistication (fancy-looking code is not automatically "
    "hard). Judge difficulty relative to what the task actually requires.")
# NOTE: cleanliness is no longer a pairwise axis — it is counted defect detection
# (scoring/defects.py + scoring/detect.py). This module is difficulty-only.
AXIS_QUESTION = {"difficulty": _DIFFICULTY_Q}
# what "winner" means per axis (for human-readable manifests)
MORE_MEANS = {"difficulty": "harder to produce correctly"}

_PREAMBLE = (
    "You are a senior staff engineer judging two anonymized code-change diffs (A and B) "
    "on ONE axis only. Identifiers may be anonymized (PROJECT/OWNER); diffs may be "
    "truncated to show code first.")

# structured-output contract the host agent must satisfy per comparison.
# `reason` is listed FIRST (before `winner`) on purpose: a decoder emits fields left-to-right, so
# tokens after `winner` cannot influence it — an answer-first schema makes the justification
# post-hoc and measurably degrades the judgment (forced JSON answer-first costs ~10-30% reasoning;
# reasoning-first recovers it). Putting `reason` first turns it into a compact chain-of-thought that
# conditions `winner`, so the separate free-text narration can be dropped (big output-token saving).
# Field NAMES are unchanged — the workflow folds `winner` and the read-back ignores `reason`.
VERDICT_SCHEMA = {
    "type": "object",
    "properties": {
        "reason": {"type": "string",
                   "description": "One or two terse sentences naming the deciding factor; do NOT "
                                  "restate the diff."},
        "winner": {"type": "string", "enum": ["A", "B", "tie"]},
    },
    "required": ["reason", "winner"],
    "additionalProperties": False,
}


@dataclass(frozen=True)
class CompareItem:
    """A diff to compare. `id` lets ReplayBackend look up a saved verdict; the live
    backend uses `diff` text. `id` is None for a fresh session diff."""
    diff: str
    id: str | None = None


def build_pair_prompt(axis: str, diff_a: str, diff_b: str) -> str:
    """The blind A-vs-B prompt for a single comparison (used by the live backend)."""
    q = AXIS_QUESTION[axis]
    return (f"{_PREAMBLE}\n\n{q}\n\nDecide which diff is MORE {axis} "
            f"({MORE_MEANS[axis]}).\n\n--- Diff A ---\n{diff_a}\n\n--- Diff B ---\n{diff_b}"
            "\n\nRespond ONLY via structured output: first `reason` — one or two terse sentences "
            "naming the deciding factor, never restating the diff — then `winner` = \"A\" | \"B\" "
            "| \"tie\" (the diff that is MORE " + axis + "). Write NO analysis or prose in your "
            "message text; put all (terse) reasoning inside the `reason` field and emit just the "
            "tool call.")


class Backend(ABC):
    """Resolve, for one subject diff, whether it is MORE <axis> than each anchor."""

    @abstractmethod
    def compare_batch(self, subject: CompareItem, anchors: list[CompareItem],
                      axis: str) -> list[Winner]:
        """Return one Winner per anchor: 'subject' if the subject is MORE <axis> than the
        anchor, 'anchor' if less, 'tie' if equivalent."""
        raise NotImplementedError


# --- ReplayBackend: saved verdicts, no model ---------------------------------------
class ReplayBackend(Backend):
    """Answer comparisons from saved calibration verdicts.

    Accepts the two on-disk shapes: placement verdicts ({holdout, anchor, winner}) and
    dense pair verdicts ({a, b, winner}). `winner` is an id or 'tie'. Lookups are strict:
    a missing pair raises, so validation surfaces coverage gaps instead of hiding them.
    """

    def __init__(self, verdicts: dict[tuple[str, str], str]):
        self._v = verdicts

    @classmethod
    def from_files(cls, *paths: str) -> "ReplayBackend":
        v: dict[tuple[str, str], str] = {}
        for path in paths:
            data = json.load(open(path, encoding="utf-8"))
            rows = data["placements"] if "placements" in data else data["verdicts"]
            for r in rows:
                if "holdout" in r:
                    a, b = r["holdout"], r["anchor"]
                else:
                    a, b = r["a"], r["b"]
                v[(a, b)] = r["winner"]
        return cls(v)

    def _winner_id(self, x: str, y: str) -> str:
        if (x, y) in self._v:
            return self._v[(x, y)]
        if (y, x) in self._v:
            return self._v[(y, x)]
        raise KeyError(f"no saved verdict for pair ({x}, {y})")

    def compare_batch(self, subject: CompareItem, anchors: list[CompareItem],
                      axis: str) -> list[Winner]:
        if subject.id is None:
            raise ValueError("ReplayBackend requires subject.id (a known unit id)")
        out: list[Winner] = []
        for anc in anchors:
            w = self._winner_id(subject.id, anc.id)
            out.append("subject" if w == subject.id
                       else "anchor" if w == anc.id else "tie")
        return out


# --- counterbalancing: deterministic per-comparison side flip -----------------------
def _flip(axis: str, subject_diff: str, anchor_id: str | None, index: int) -> bool:
    """True → the subject is presented as Diff B (anchor as A) for this comparison.

    Deterministic by construction: the file-handoff cycle rebuilds the manifest on the
    read-back run, so a true-random flip would desync from the answers the orchestrator
    already collected. A content hash gives a balanced, position-uncorrelated pattern
    that both passes recompute identically. (Python's builtin hash() is salted per
    process — hashlib only.)"""
    key = f"{axis}\x00{anchor_id or ''}\x00{index}\x00{subject_diff}"
    return bool(hashlib.sha256(key.encode("utf-8")).digest()[0] & 1)


def _fingerprint(axis: str, subject: CompareItem, anchors: list[CompareItem]) -> str:
    """Identity of one emitted manifest: axis + subject + anchor set + flip pattern.

    The verdicts file must echo it, which pins the answers to the exact manifest (and
    flip pattern) they were produced from."""
    h = hashlib.sha256()
    h.update(axis.encode("utf-8"))
    h.update(subject.diff.encode("utf-8"))
    for i, a in enumerate(anchors):
        h.update(f"\x00{a.id or ''}\x00{int(_flip(axis, subject.diff, a.id, i))}"
                 .encode("utf-8"))
    return h.hexdigest()[:16]


# --- HarnessBackend: delegate to host-agent subagents ------------------------------
class PendingComparisons(Exception):
    """Raised by HarnessBackend (file-handoff mode) when verdicts aren't ready yet.

    Carries the manifest path the skill should run subagents over, then re-invoke."""

    def __init__(self, manifest_path: str, n_jobs: int):
        super().__init__(f"{n_jobs} comparisons pending — run subagents over "
                         f"{manifest_path}, write verdicts, then re-run")
        self.manifest_path = manifest_path
        self.n_jobs = n_jobs


# a runner is the agent-orchestration layer: given the job manifest it returns, per
# anchor, "A"/"B"/"tie" (A = subject). Injected by the skill; absent in pure Python.
Runner = Callable[[dict], list[str]]


class HarnessBackend(Backend):
    """Delegate comparisons to the host agent.

    Two modes:
      - runner injected → call it synchronously (the skill provides a subagent runner).
      - no runner → file handoff: write the job manifest; if a verdicts file already sits
        beside it, read that; otherwise raise PendingComparisons for the skill to fulfill.
    The manifest carries the subject diff, each anchor's diff, the axis prompt and the
    structured-output schema, so the orchestration layer needs no scoring knowledge.
    """

    def __init__(self, job_dir: str, runner: Runner | None = None,
                 job_name: str = "placement"):
        self.job_dir = job_dir
        self.runner = runner
        self.job_name = job_name

    def _manifest(self, subject: CompareItem, anchors: list[CompareItem],
                  axis: str) -> dict:
        # The flip is applied to the PROMPT only — never recorded in a comparison entry,
        # so the orchestrator cannot reveal (or need to track) which side is the subject.
        comparisons = []
        for i, a in enumerate(anchors):
            if _flip(axis, subject.diff, a.id, i):
                prompt = build_pair_prompt(axis, a.diff, subject.diff)
            else:
                prompt = build_pair_prompt(axis, subject.diff, a.diff)
            comparisons.append({"anchor_id": a.id, "prompt": prompt})
        fp = _fingerprint(axis, subject, anchors)
        return {
            "axis": axis,
            "more_means": MORE_MEANS[axis],
            "schema": VERDICT_SCHEMA,
            "fingerprint": fp,
            "verdicts_format": {"fingerprint": fp,
                                "winners": ['"A"|"B"|"tie" per comparison, in order']},
            "subject": {"id": subject.id, "diff": subject.diff},
            "comparisons": comparisons,
        }

    def _resolve(self, raw: list[str], subject: CompareItem,
                 anchors: list[CompareItem], axis: str) -> list[Winner]:
        """Validate raw A/B/tie answers and un-flip them back to subject/anchor."""
        if len(raw) != len(anchors):
            raise ValueError(f"{self.job_name}: expected {len(anchors)} winners, "
                             f"got {len(raw)}")
        out: list[Winner] = []
        for i, (w, a) in enumerate(zip(raw, anchors)):
            if w not in ("A", "B", "tie"):
                raise ValueError(f"{self.job_name}: winner #{i} is {w!r} "
                                 '(must be "A", "B", or "tie")')
            if w == "tie":
                out.append("tie")
            else:
                subject_is_a = not _flip(axis, subject.diff, a.id, i)
                out.append("subject" if (w == "A") == subject_is_a else "anchor")
        return out

    def compare_batch(self, subject: CompareItem, anchors: list[CompareItem],
                      axis: str) -> list[Winner]:
        manifest = self._manifest(subject, anchors, axis)
        if self.runner is not None:
            return self._resolve(list(self.runner(manifest)), subject, anchors, axis)

        os.makedirs(self.job_dir, exist_ok=True)
        mpath = os.path.join(self.job_dir, f"{self.job_name}.job.json")
        vpath = os.path.join(self.job_dir, f"{self.job_name}.verdicts.json")
        if os.path.exists(vpath):
            data = json.load(open(vpath, encoding="utf-8"))
            if data.get("fingerprint") != manifest["fingerprint"]:
                raise ValueError(
                    f"{vpath}: fingerprint {data.get('fingerprint')!r} does not match "
                    f"manifest {manifest['fingerprint']!r} — stale verdicts (answers "
                    "from an older manifest/flip pattern). Delete it and re-run the "
                    "comparisons from the regenerated manifest.")
            return self._resolve(data["winners"], subject, anchors, axis)
        json.dump(manifest, open(mpath, "w", encoding="utf-8"), indent=1)
        raise PendingComparisons(mpath, len(anchors))
