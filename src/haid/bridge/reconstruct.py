"""Reconstruct the net code diff a body of work produced — from the transcript alone.

This is the diff half of the window→(diff, usage) bridge: the join between the real-session
pipeline (session→graph) and the scoring stack (volume/difficulty/cleanliness), which until
now only ever saw calibration diffs. It is **replay-primary, no git** (decision recorded after
measuring the bash-write-to-source gap at ~0–1% across three real projects; see the project
notes). The same data Claude Code's own rewind uses:

  - Edit/MultiEdit  → `originalFile` (full pre-edit content) + exact `oldString`→`newString`.
  - Write           → full `content` (and `originalFile` for overwrites; None on create).
  - Bash heredoc    → recovered `write_content` (see graph/bash_write.parse_heredoc_write).

Two reconstruction modes, picked per file:

  * **buffer (preferred)** — when we have the file's content as it entered the window (the
    earliest captured `originalFile`), we replay every write onto a running string and emit
    `unified_diff(baseline, final)`. This is **net by construction** (a line written then
    rewritten appears once, in final form — exactly what `volume` wants; the churn lives on
    the cost side) and **self-detects gaps**: each edit's `originalFile` must equal our running
    content, so an untracked shell write in between is caught and flagged, never silently wrong.
  * **hunks (fallback)** — Claude Code omits `originalFile` on some edits (e.g. large files),
    so a pre-existing file may have no full baseline anywhere in the window. There we emit the
    edits' `structuredPatch` hunks directly (always present). Correct for the changed lines,
    but flagged: overlapping re-edits of the same lines can double-count (no net dedup).

No silent caps — every shortfall lands in `FileRecon.reasons` and surfaces as a caveat.
Grain-agnostic: `reconstruct()` takes an ordered list of writes, so the caller slices by
window now and by episode later. Stdlib only.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass, field

_NATIVE_EDIT = {"Edit", "MultiEdit"}
_NATIVE_WRITE = {"Write"}


@dataclass
class FileRecon:
    """One file's reconstructed change, with mode and any honesty flags."""
    file_id: str
    mode: str = "buffer"                         # "buffer" | "hunks"
    baseline: str = ""
    final: str = ""
    hunks: list = field(default_factory=list)    # structuredPatch hunks (hunks mode)
    ops: int = 0
    complete: bool = True
    reasons: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.hunks) if self.mode == "hunks" else (self.baseline != self.final)

    def _flag(self, reason: str) -> None:
        self.complete = False
        if reason not in self.reasons:
            self.reasons.append(reason)


@dataclass
class ReconResult:
    diff: str                                   # concatenated git-style unified diff
    files: list[FileRecon]
    caveats: list[str] = field(default_factory=list)
    excluded_external: int = 0                  # writes to paths outside the project tree

    @property
    def incomplete(self) -> list[FileRecon]:
        return [f for f in self.files if not f.complete]


# --- per-tool application ---------------------------------------------------------------

def _seed_baseline(fr: FileRecon, original, baselines: dict, fid: str):
    """Resolve the file's window-entry content for buffer mode, or switch to hunks mode."""
    seed = original if original is not None else baselines.get(fid)
    if seed is not None:
        fr.mode = "buffer"
        fr.baseline = seed
        fr.final = seed
        return True
    return False


def _apply_native_edit(fr: FileRecon, tur: dict, first: bool, baselines: dict) -> None:
    original = tur.get("originalFile")
    if first and not _seed_baseline(fr, original, baselines, fr.file_id):
        fr.mode = "hunks"
        fr._flag("no full baseline captured for this pre-existing file — reconstructed from "
                 "diff hunks (overlapping re-edits may double-count)")
    if fr.mode == "hunks":
        fr.hunks.extend(tur.get("structuredPatch") or [])
        return
    if not first and original is not None and fr.final != original:
        fr._flag("untracked change before an edit (resynced to the file's actual state)")
        fr.final = original

    pairs = tur.get("edits") or [{"old_string": tur.get("oldString", ""),
                                  "new_string": tur.get("newString", ""),
                                  "replace_all": tur.get("replaceAll", False)}]
    for e in pairs:
        old, new = e.get("old_string", ""), e.get("new_string", "")
        if old == "":                            # pure insertion into the buffer
            fr.final = fr.final + new if fr.final and not new.startswith(fr.final) else (fr.final or new)
            continue
        if old not in fr.final:
            fr._flag("edit oldString not found in reconstructed content")
            continue
        fr.final = fr.final.replace(old, new) if e.get("replace_all") else fr.final.replace(old, new, 1)


def _apply_native_write(fr: FileRecon, tur: dict, first: bool, baselines: dict) -> None:
    content = tur.get("content")
    original = tur.get("originalFile")
    sp = tur.get("structuredPatch") or []
    if first:
        if not _seed_baseline(fr, original, baselines, fr.file_id):
            fr.baseline = fr.final = ""          # create (sp empty) or unknown overwrite
            if sp:
                fr._flag("Write overwrote an existing file with no captured baseline")
    elif original is not None and fr.final != original:
        fr._flag("untracked change before a write (resynced to the file's actual state)")
        fr.final = original
    if content is None:
        fr._flag("Write result had no content")
        return
    fr.final = content


def _apply_shell_write(fr: FileRecon, op: str | None, content: str | None, first: bool) -> None:
    # Bash writes carry no originalFile, so a shell write as the FIRST touch leaves the
    # pre-state unknown.
    if first:
        fr.baseline = fr.final = ""
        if op == "append":
            fr._flag("shell append as first write — prior file content is unknown")
    if content is None:
        fr._flag(f"shell {op or 'write'} content unrecoverable (sed -i / plain redirect)")
        return
    if op == "append":
        fr.final = fr.final + content
    else:
        if not first:
            fr._flag("shell overwrite of a tracked file (prior content replaced)")
        fr.final = content


# --- the engine -------------------------------------------------------------------------

def reconstruct(writes, baselines: dict | None = None) -> ReconResult:
    """Reconstruct per-file diffs from an ordered list of writes.

    `writes` is `(file_id, tool, tur, write_op, write_content, derived)` in chronological
    (active-timeline) order. `baselines` maps file_id -> the file's content as it entered the
    window (earliest captured originalFile), used to seed buffer mode when an edit's own
    originalFile is None.
    """
    baselines = baselines or {}
    states: dict[str, FileRecon] = {}
    order: list[str] = []
    for file_id, tool, tur, write_op, write_content, derived in writes:
        fr = states.get(file_id)
        first = fr is None
        if first:
            fr = FileRecon(file_id=file_id)
            states[file_id] = fr
            order.append(file_id)
        fr.ops += 1
        tur = tur or {}
        if derived or tool == "Bash":
            _apply_shell_write(fr, write_op, write_content, first)
        elif tool in _NATIVE_WRITE:
            _apply_native_write(fr, tur, first, baselines)
        elif tool in _NATIVE_EDIT:
            _apply_native_edit(fr, tur, first, baselines)
        else:
            fr._flag(f"unhandled write tool {tool!r}")

    files = [states[fid] for fid in order]
    diff = "".join(_emit(fr) for fr in files if fr.changed)
    return ReconResult(diff=diff, files=files, caveats=_caveats(files))


def _caveats(files: list[FileRecon]) -> list[str]:
    incomplete = [f for f in files if not f.complete]
    if not incomplete:
        return []
    out = [f"{len(incomplete)} of {len(files)} changed file(s) could not be fully reconstructed "
           "from the transcript — the diff may be incomplete for these:"]
    out += [f"  - {fr.file_id}: " + "; ".join(fr.reasons) for fr in incomplete]
    return out


def _emit(fr: FileRecon) -> str:
    if fr.mode == "hunks":
        return _hunks_diff(fr.file_id, fr.hunks)
    return _file_diff(fr.file_id, fr.baseline, fr.final)


def _file_diff(path: str, baseline: str, final: str) -> str:
    """A git-style unified-diff block from full before/after content."""
    is_new = baseline == ""
    is_del = final == "" and baseline != ""
    fromf = "/dev/null" if is_new else f"a/{path}"
    tof = "/dev/null" if is_del else f"b/{path}"
    body = difflib.unified_diff(baseline.splitlines(), final.splitlines(),
                                fromfile=fromf, tofile=tof, lineterm="")
    return f"diff --git a/{path} b/{path}\n" + "\n".join(body) + "\n"


def _hunks_diff(path: str, hunks: list) -> str:
    """A git-style block assembled directly from structuredPatch hunks (fallback mode)."""
    out = [f"diff --git a/{path} b/{path}", f"--- a/{path}", f"+++ b/{path}"]
    for h in hunks:
        if not isinstance(h, dict):
            continue
        out.append(f"@@ -{h.get('oldStart', 0)},{h.get('oldLines', 0)} "
                   f"+{h.get('newStart', 0)},{h.get('newLines', 0)} @@")
        out.extend(h.get("lines") or [])
    return "\n".join(out) + "\n"
