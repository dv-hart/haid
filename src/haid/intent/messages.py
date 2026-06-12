"""Extract the user messages of a window + build each one's bounded context.

The classifier works the conversation, not the whole transcript: to label message *n* it
sees the prior user messages and the agent's FINAL TEXT replies — never thinking blocks or
tool calls (docs/intent-taxonomy.md "Classifier context discipline"). `Record.text()`
already returns only `type=="text"` blocks (thinking and tool_use are excluded), so it IS
the agent's final reply.

**Walk EVERY branch, not just the active one.** A user commonly does work, REWINDS to an
earlier point, and does different work so the first stretch doesn't cloud context (e.g. step
A → rewind → step B). That abandoned stretch is real work that really cost tokens, so it
must get labels and an episode — otherwise its cost is orphaned (cost counts all branches;
see the bridge). So we walk `forest.timelines()` (active + every rewind), dedup messages by
uuid (the shared planning prefix is ONE message, counted once), and build each message's
context from ITS OWN branch — so a step-B message never sees the step-A context it never had.

This differs from the waste metrics on purpose: metrics scope WITHIN one timeline (a read on
an abandoned branch must not become a phantom re-read of the active branch). The classifier
isn't computing redundancy — it's labeling messages — so it walks all branches. Two
consumers, two scoping rules.

Stdlib only; no model.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

_AGENT_REPLY_CAP = 400      # total chars of each agent reply kept (head + tail)
_SKELETON_ENTRIES = 40      # most-recent entries kept in a message's context


@dataclass
class UserMessage:
    """One genuine user instruction, with the context to classify it."""
    uuid: str
    session_id: str               # 8-char session stem
    timeline: str                 # "active" or "rewind:<short-uuid>" — the branch it's on
    ts: str | None
    text: str
    index: int                    # window-wide chronological position
    context: str = ""             # bounded conversation skeleton BEFORE this message, on its branch


def _sid(path: str) -> str:
    return Path(path).stem[:8]


def _truncate(s: str, n: int) -> str:
    """Collapse whitespace and, if over budget, keep the HEAD and TAIL (agent replies put
    the substance up front and the handoff/questions at the end; the middle is detail)."""
    s = " ".join(s.split())
    if len(s) <= n:
        return s
    half = (n - 1) // 2
    return s[:half] + "…" + s[-half:]


def extract_window_messages(sessions) -> list[UserMessage]:
    """Ordered user messages across the window's branches, each carrying the bounded
    conversation skeleton that precedes it ON ITS BRANCH. Deduped by uuid; returned in
    window-chronological order with a stable `index`."""
    def first_ts(s):
        ts = [r.timestamp for r in s.parse.records if r.timestamp]
        return min(ts) if ts else ""

    seen: set[str] = set()
    collected: list[UserMessage] = []

    for s in sorted(sessions, key=first_ts):
        sid = _sid(s.path)
        fr = s.forest
        for tl in fr.timelines():              # active first, then each rewind
            skeleton: list[tuple[str, str]] = [("meta", f"— session {sid} ({tl.label}) —")]
            for uuid in tl.node_uuids:          # root → leaf == chronological on this branch
                r = fr.by_uuid.get(uuid)
                if r is None:
                    continue
                if r.is_user_prompt():
                    if uuid not in seen:        # shared-prefix messages are emitted once
                        collected.append(UserMessage(
                            uuid=uuid, session_id=sid, timeline=tl.label, ts=r.timestamp,
                            text=r.text().strip(), index=-1,
                            context=_render_skeleton(skeleton)))
                        seen.add(uuid)
                    skeleton.append(("user", r.text().strip()))
                elif r.type == "assistant":
                    reply = r.text().strip()
                    if reply:                    # final-text reply only (no thinking/tools)
                        skeleton.append(("assistant", _truncate(reply, _AGENT_REPLY_CAP)))

    collected.sort(key=lambda m: (m.ts or "", m.uuid))
    for i, m in enumerate(collected):
        m.index = i
    return collected


def _render_skeleton(skeleton: list[tuple[str, str]]) -> str:
    """Render the most-recent entries as a compact transcript for the classifier prompt."""
    tail = skeleton[-_SKELETON_ENTRIES:]
    lines = []
    for role, text in tail:
        if role == "meta":
            lines.append(text)
        elif role == "user":
            lines.append(f"USER: {text}")
        else:
            lines.append(f"AGENT: {text}")
    return "\n".join(lines)
