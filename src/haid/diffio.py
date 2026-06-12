"""Unified-diff parsing + code-files-first reassembly.

Shared by both scoring inputs:
  - placement needs the diff reassembled CODE-FIRST and length-capped, exactly as the
    reference anchors were prepared (calibration/blind.py), so the judge sees session
    diffs and anchors in the same shape. We do NOT identity-blind a session diff — it is
    the user's own code.
  - volume needs per-file added/removed line counts.

Targets `git diff` output (`diff --git` blocks). Tolerant fallbacks: a single
`---/+++` file with no git header is still parsed; unrecognized leading text is ignored.
Stdlib only.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .filekind import file_priority

DIFF_CAP_CHARS = 16000  # per-diff cap shown to the judge (~4k tokens); matches blind.py

_GIT_SPLIT = re.compile(r"(?m)^(?=diff --git )")
_GIT_PATH = re.compile(r"^diff --git a/(.+?) b/(.+?)\s*$", re.M)
_OLD_PATH = re.compile(r"(?m)^--- (?:a/)?(.+?)\s*$")
_NEW_PATH = re.compile(r"(?m)^\+\+\+ (?:b/)?(.+?)\s*$")


@dataclass
class FileDiff:
    path: str
    added: list[str] = field(default_factory=list)    # added line contents (no '+')
    removed: list[str] = field(default_factory=list)   # removed line contents (no '-')
    is_new: bool = False
    is_delete: bool = False

    @property
    def n_added(self) -> int:
        return len(self.added)

    @property
    def n_removed(self) -> int:
        return len(self.removed)


def _block_path(block: str) -> tuple[str, bool, bool]:
    """Resolve a file block's path + new/delete flags.

    Prefer the +++ (new) path; fall back to the git header or the --- (old) path.
    A /dev/null on either side marks a create/delete."""
    is_new = is_delete = False
    new_m = _NEW_PATH.search(block)
    old_m = _OLD_PATH.search(block)
    new_p = new_m.group(1) if new_m else None
    old_p = old_m.group(1) if old_m else None
    if new_p == "/dev/null":
        is_delete = True
        new_p = None
    if old_p == "/dev/null":
        is_new = True
        old_p = None
    path = new_p or old_p
    if not path:
        gm = _GIT_PATH.search(block)
        path = gm.group(2) if gm else "~unknown"
    return path, is_new, is_delete


def _hunk_lines(block: str) -> tuple[list[str], list[str]]:
    """Added/removed content lines within a block's hunks (excludes ---/+++ headers)."""
    added, removed = [], []
    in_hunk = False
    for line in block.splitlines():
        if line.startswith("@@"):
            in_hunk = True
            continue
        if not in_hunk:
            continue
        if line.startswith("+++") or line.startswith("---"):
            continue
        if line.startswith("+"):
            added.append(line[1:])
        elif line.startswith("-"):
            removed.append(line[1:])
    return added, removed


def parse_diff(text: str) -> list[FileDiff]:
    """Parse a unified diff into per-file added/removed lines."""
    blocks = [b for b in _GIT_SPLIT.split(text) if b.strip()]
    # Fallback: no `diff --git` header but there is a ---/+++ pair → treat as one block.
    if len(blocks) <= 1 and "diff --git " not in text and _NEW_PATH.search(text):
        blocks = [text]
    out: list[FileDiff] = []
    for block in blocks:
        if "@@" not in block:
            continue  # no hunks (pure mode/rename change) → no LOC delta
        path, is_new, is_delete = _block_path(block)
        added, removed = _hunk_lines(block)
        out.append(FileDiff(path=path, added=added, removed=removed,
                            is_new=is_new, is_delete=is_delete))
    return out


def reassemble_code_first(text: str, cap: int = DIFF_CAP_CHARS) -> str:
    """Reorder a diff's file blocks code-first and cap length (placement parity).

    Mirrors calibration/blind.py:prioritize_diff but without identity scrubbing. Keeps
    original order within a priority tier; always includes at least one block; appends a
    note when blocks are dropped for length."""
    parts = [p for p in _GIT_SPLIT.split(text) if p.strip()]
    if len(parts) <= 1:
        return text[:cap]

    def path_of(block: str) -> str:
        m = _GIT_PATH.search(block)
        if m:
            return m.group(2)
        nm = _NEW_PATH.search(block)
        return nm.group(1) if nm else "~"

    ordered = sorted(range(len(parts)),
                     key=lambda i: (file_priority(path_of(parts[i])), i))
    out, used, included = [], 0, 0
    for i in ordered:
        block = parts[i]
        if used + len(block) > cap and included >= 1:
            break
        chunk = block if used + len(block) <= cap else block[:cap - used]
        out.append(chunk)
        used += len(chunk)
        included += 1
    n_dropped = len(parts) - included
    body = "".join(out)
    if n_dropped:
        body += (f"\n\n# [{n_dropped} of {len(parts)} files omitted for length; "
                 "code files shown first]\n")
    return body
