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


@dataclass
class SessionTagJob:
    """One agent job: a whole session BRANCH's transcript + the user messages to label in it.

    R1 (one agent per session branch): the agent reads the branch top-to-bottom ONCE and
    labels every marked user message in order, so each message's causal context is simply the
    transcript above it — no per-message context skeleton is re-embedded (that redundancy is
    what made the per-message manifest balloon). Targets are deduped window-wide by uuid: a
    shared planning prefix is a target in the FIRST branch that owns it (active is walked
    first) and shown as plain context in the rest. The union of all jobs' `targets` is exactly
    the set `extract_window_messages` produces — same branch walk, same uuid dedup."""
    session_id: str
    timeline: str                 # "active" or "rewind:<short-uuid>"
    transcript: str               # rendered branch conversation, target messages marked
    targets: list[str]            # uuids to label in THIS job, in transcript order
    target_refs: list[str]        # short window-unique handle per target (parallel to targets);
                                  # the agent echoes the REF, the CLI expands it back to the uuid


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


# The inline marker the agent looks for: every marked USER line gets one label, echoing its
# REF (a short window-unique handle), not the full uuid — a 6-char copy, not a 36-char one.
_TARGET_MARK = ">>> CLASSIFY THIS MESSAGE — ref: {ref} <<<"
_REF_MIN_LEN = 6                       # last-N chars of the uuid; grows only to break a tie


def _compute_refs(uuids: list[str]) -> dict[str, str]:
    """Map each uuid to a short, WINDOW-UNIQUE handle so the agent never copies a 36-char id.

    The handle is the uuid's last N chars (N starts at 6); if any two uuids collide at that
    length the whole window steps up uniformly until they don't. uuids are unique, so the
    full-length map (N = longest) is always a valid fallback."""
    longest = max((len(u) for u in uuids), default=0)
    for length in range(_REF_MIN_LEN, longest + 1):
        refs = {u: u[-length:] for u in uuids}
        if len(set(refs.values())) == len(uuids):
            return refs
    return {u: u for u in uuids}       # all uuids shorter than _REF_MIN_LEN (test ids); unique


def extract_session_jobs(sessions) -> list[SessionTagJob]:
    """Group the window into one job per session branch (active + each rewind).

    Mirrors `extract_window_messages`' branch walk and uuid dedup exactly — sessions sorted by
    first timestamp, `forest.timelines()` active-first, dedup by uuid — but emits a per-branch
    transcript with its target user messages marked, instead of per-message bounded contexts.
    A branch whose user messages were all already owned by an earlier branch yields no job.

    Two passes: walk once to collect each branch's render entries + the window's target uuids,
    compute window-unique refs from that full set, then render each transcript with its refs."""
    def first_ts(s):
        ts = [r.timestamp for r in s.parse.records if r.timestamp]
        return min(ts) if ts else ""

    seen: set[str] = set()
    # raw[i] = (sid, label, entries, targets); entries are deferred render instructions so the
    # ref marker can be filled in once the whole window's target set is known.
    raw: list[tuple[str, str, list[tuple], list[str]]] = []

    for s in sorted(sessions, key=first_ts):
        sid = _sid(s.path)
        fr = s.forest
        for tl in fr.timelines():              # active first, then each rewind
            entries: list[tuple] = [("meta", f"— session {sid} ({tl.label}) —")]
            targets: list[str] = []
            for uuid in tl.node_uuids:          # root → leaf == chronological on this branch
                r = fr.by_uuid.get(uuid)
                if r is None:
                    continue
                if r.is_user_prompt():
                    text = r.text().strip()
                    if uuid not in seen:        # this branch owns it → mark it for labeling
                        seen.add(uuid)
                        targets.append(uuid)
                        entries.append(("target", uuid, text))
                    else:                       # shared prefix, already owned → context only
                        entries.append(("user", text))
                elif r.type == "assistant":
                    reply = r.text().strip()
                    if reply:                    # final-text reply only (no thinking/tools)
                        entries.append(("agent", _truncate(reply, _AGENT_REPLY_CAP)))
            if targets:
                raw.append((sid, tl.label, entries, targets))

    refs = _compute_refs([u for _, _, _, targets in raw for u in targets])

    jobs: list[SessionTagJob] = []
    for sid, label, entries, targets in raw:
        lines: list[str] = []
        for e in entries:
            if e[0] == "meta":
                lines.append(e[1])
            elif e[0] == "user":
                lines.append(f"USER: {e[1]}")
            elif e[0] == "agent":
                lines.append(f"AGENT: {e[1]}")
            else:                                # ("target", uuid, text)
                lines.append(f"USER: {e[2]}  {_TARGET_MARK.format(ref=refs[e[1]])}")
        jobs.append(SessionTagJob(session_id=sid, timeline=label,
                                  transcript="\n".join(lines), targets=targets,
                                  target_refs=[refs[u] for u in targets]))
    return jobs
