"""The codified session-grouping pass — the cues, the prompt the agent reads, the schema.

Episode formation is ONE holistic judgment: cluster the window's whole sessions into episodes by
shared component/topic. This module codifies that judgment's product (mirrors intent/taxonomy.py
for the classifier): the deterministic pairwise cues (file overlap, idle gap), the compact
per-session view the agent reads, and the structured-output schema. No model is called here; the
judgment boundary is segment.py.
"""

from __future__ import annotations

# A pair of sessions linked by enough file overlap is a strong same-episode cue; an idle gap
# between them is weak corroboration. Defaults are HYPOTHESES to validate on real windows.
DEFAULT_OVERLAP = 0.2              # Jaccard of touched-file sets ≥ this ⇒ same-component cue
DEFAULT_GAP_SECONDS = 3 * 24 * 60 * 60   # >3 days between sessions ⇒ weak split evidence


def file_overlap(a: set, b: set) -> float | None:
    """Jaccard of two sessions' touched-file sets; None when either is empty (no signal)."""
    if not a or not b:
        return None
    return len(a & b) / len(a | b)


# The structured-output contract the grouping agent must satisfy. Episodes are returned as
# explicit session-id lists (NOT index spans), so the agent can group a component worked across
# non-adjacent sessions (A, then B, then back to A) into one episode.
SEGMENT_SCHEMA = {
    "type": "object",
    "properties": {
        "episodes": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string",
                              "description": "Short component/topic phrase, e.g. 'Episode segmentation'."},
                    "session_ids": {"type": "array", "items": {"type": "string"}, "minItems": 1,
                                    "description": "The 8-char session ids in this episode."},
                    "rationale": {"type": "string",
                                  "description": "Why these sessions are one unit; cite the shared component / files / topic."},
                },
                "required": ["title", "session_ids", "rationale"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["episodes"],
    "additionalProperties": False,
}

_PREAMBLE = (
    "You are grouping a window of Claude Code SESSIONS into EPISODES. An episode is one coherent "
    "unit of work — the git-free equivalent of a pull request — made of one or MORE whole "
    "sessions that worked on a shared component or topic. You are given a compact summary of each "
    "session (its purpose snapshots and the files it touched). Assign every session to an episode.")

_RULES = (
    "Rules:\n"
    "- The SESSION is atomic: assign each session to exactly ONE episode. NEVER split a session — "
    "if a session drifted across topics mid-way, it still stays whole (that drift is a coaching "
    "note, not a reason to divide it).\n"
    "- Group by shared COMPONENT/TOPIC. Sessions that touch the same files/areas or continue the "
    "same thread belong together — even across days (a thread resumed the next morning is ONE "
    "episode). Sessions repeatedly re-reading the same context are a strong same-episode cue.\n"
    "- Do NOT force-merge unrelated sessions just to make bigger episodes. A self-contained task "
    "done in a single session is its own one-session episode — that is normal and fine.\n"
    "- An idle gap between sessions is weak evidence only; shared component beats clock.\n"
    "- Every session id must appear in exactly one episode (cover all, no duplicates).\n"
    "- Give each episode a short component/topic title and a one-line rationale citing the shared "
    "files/topic.")


def _session_block(s, link_note: str) -> str:
    files = sorted(s.file_set)
    shown = ", ".join(files[:6]) + (f"  (+{len(files) - 6} more)" if len(files) > 6 else "")
    when = f"{s.first_ts or '?'} → {s.last_ts or '?'}"
    drift = "  ⚠ multi-directive (possible within-session drift)" if s.drift_flag else ""
    head = f"[{s.session_id}]  {when}  ({s.n_messages} msgs){drift}{link_note}"
    purposes = "\n".join(f"      - {p}" for p in s.purposes[:8]) or "      (no tagged purposes)"
    files_line = f"      files: {shown}" if files else "      files: (none touched)"
    return f"{head}\n{files_line}\n      purposes:\n{purposes}"


def _link_notes(summaries, overlap_threshold: float) -> dict[str, str]:
    """For each session, a short note on file overlap with the previous session (a rendering aid
    so the agent sees the strongest deterministic cue inline)."""
    notes: dict[str, str] = {}
    for i in range(1, len(summaries)):
        ov = file_overlap(summaries[i].file_set, summaries[i - 1].file_set)
        if ov is not None and ov >= overlap_threshold:
            notes[summaries[i].session_id] = f"  <<shares files w/ prev: {ov:.0%}>>"
    return notes


def render_sessions(summaries, overlap_threshold: float = DEFAULT_OVERLAP) -> str:
    notes = _link_notes(summaries, overlap_threshold)
    return "\n\n".join(_session_block(s, notes.get(s.session_id, ""))
                       for s in sorted(summaries, key=lambda x: x.index))


def build_group_prompt(summaries, overlap_threshold: float = DEFAULT_OVERLAP) -> str:
    ids = ", ".join(s.session_id for s in sorted(summaries, key=lambda x: x.index))
    return (
        f"{_PREAMBLE}\n\n"
        f"{_RULES}\n\n"
        f"--- sessions ({len(summaries)}): {ids} ---\n"
        f"{render_sessions(summaries, overlap_threshold)}\n\n"
        "Group every session id into episodes. Respond ONLY via structured output: a list of "
        "episodes, each with title, session_ids, rationale."
    )
