"""The user-anchored pass, step 2: tag every user message + emit the purpose timeline.

This is the entry point to the model-in-the-loop "why" pass (plans/agent-analysis.md). It
turns a window of real sessions into a compact, structured per-message timeline:

    tag_window(view, sessions, backend) -> [TaggedMessage]

Each message gets two orthogonal labels (move × work-type) and a one-sentence purpose
snapshot. The sequence of snapshots is the purpose timeline that episode segmentation
(step 3) and drift detection read holistically.

The judgment is delegated to a backend (classify.py): ReplayBackend for deterministic tests,
HarnessBackend for the live host-agent path. This module is the deterministic orchestration
around that one model call — it walks every branch, builds each message's bounded context,
and folds the labels back together. Stdlib only.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import taxonomy
from .classify import (ClassifierBackend, ClassifyItem, HarnessBackend,
                       PendingClassifications, ReplayBackend)
from .messages import UserMessage, extract_window_messages

__all__ = ["TaggedMessage", "tag_window", "to_json", "render",
           "ClassifierBackend", "ReplayBackend", "HarnessBackend",
           "PendingClassifications", "ClassifyItem", "UserMessage"]


@dataclass
class TaggedMessage:
    uuid: str
    session_id: str
    timeline: str                  # "active" or "rewind:<short-uuid>" — the branch it's on
    ts: str | None
    index: int
    text: str
    move: str
    work_type: str
    purpose: str


def tag_window(view, sessions, backend: ClassifierBackend) -> list[TaggedMessage]:
    """Tag every user message in the window, across all branches. `view` is accepted for
    symmetry with the metrics API (and future episode use); messages come from `sessions`."""
    messages = extract_window_messages(sessions)

    items = [ClassifyItem(uuid=m.uuid, session_id=m.session_id,
                          prompt=taxonomy.build_message_prompt(m.text, m.context))
             for m in messages]
    labels = backend.classify_batch(items)

    return [TaggedMessage(
                uuid=m.uuid, session_id=m.session_id, timeline=m.timeline, ts=m.ts,
                index=m.index, text=m.text,
                move=lab["move"], work_type=lab["work_type"], purpose=lab["purpose"])
            for m, lab in zip(messages, labels)]


def to_json(tagged: list[TaggedMessage], label: str = "") -> dict:
    """The machine-readable hand-off to episode segmentation (step 3)."""
    return {
        "schema_version": "1.0",
        "kind": "message_tags",
        "window": label,
        "moves": list(taxonomy.MOVES),
        "work_types": list(taxonomy.WORK_TYPES),
        "messages": [
            {"uuid": t.uuid, "session_id": t.session_id, "timeline": t.timeline,
             "ts": t.ts, "index": t.index,
             "move": t.move, "work_type": t.work_type, "purpose": t.purpose,
             "text_preview": (t.text[:140] + "…") if len(t.text) > 140 else t.text}
            for t in tagged
        ],
    }


def render(tagged: list[TaggedMessage], label: str = "") -> str:
    """The eyeball view: the purpose timeline with each message's two-axis label."""
    lines = [f"# message tags — {label}" if label else "# message tags", ""]
    cur_sess = None
    for t in tagged:
        if t.session_id != cur_sess:
            cur_sess = t.session_id
            lines.append(f"\n## session {cur_sess}")
        branch = "" if t.timeline == "active" else f"  [{t.timeline}]"
        lines.append(f"  {t.index:>3}. ({t.move} × {t.work_type}) {t.purpose}{branch}")
    return "\n".join(lines)
