"""Typed view over one Claude Code transcript record + content classification.

The on-disk JSONL format is undocumented and drifts across versions, so this layer is
deliberately tolerant: it wraps the raw dict, lifts the envelope fields HAID relies on,
and exposes *classification helpers* (is this a real user instruction? a tool result?
slash-command noise?) rather than forcing every record into a rigid schema. Unknown
record shapes are surfaced loudly by the parser (see parse.py), never silently dropped.

Grounded in the verified format reference (docs/claude-code-data-format.md) and a scan
of 65 real transcripts (10 HAID + 55 boxBot). Key facts encoded here:
  - Tool results ride on `user` records (carry `toolUseResult`; paired to their call by
    the `tool_result` block's `tool_use_id`, not a top-level field); there is no
    top-level tool_result record type.
  - Metadata records (ai-title/custom-title/last-prompt/mode/queue-operation) carry no
    `uuid` and are not part of the conversation tree.
  - Slash-/local-command synthetic records wrap their content in <command-name> /
    <local-command-stdout> / <local-command-caveat> etc. They look like user prompts but
    are NOT instructions — a false-positive trap for rewind/instruction detection.

Stdlib only.
"""

from __future__ import annotations

from dataclasses import dataclass

# Record `type` values seen across the real corpus (65 transcripts). Anything outside
# this set is flagged by the parser as an unknown shape (drift), not dropped.
KNOWN_TYPES = {
    "user", "assistant", "system",
    "ai-title", "custom-title", "last-prompt", "mode", "queue-operation",
    "permission-mode", "pr-link", "agent-name", "file-history-snapshot",
    "attachment",
}

# Metadata records carry no uuid and sit outside the conversation tree. (file-history-
# snapshot is CC's rewind-checkpoint state; pr-link carries prNumber/prUrl for later
# git/PR grouping — both uuid-less, so neither enters the tree.)
METADATA_TYPES = {
    "ai-title", "custom-title", "last-prompt", "mode", "queue-operation",
    "permission-mode", "pr-link", "agent-name", "file-history-snapshot",
}

# Synthetic-content wrappers: content that *looks* like a user prompt but is really
# slash-command / local-command / bash-mode / injected-notification machinery. Matched
# against the stripped leading text. (Each was an observed false-positive in the corpus.)
_COMMAND_NOISE_PREFIXES = (
    "<command-name>", "<command-message>", "<command-args>",
    "<local-command-stdout>", "<local-command-stderr>", "<local-command-caveat>",
    "<bash-input>", "<bash-stdout>", "<bash-stderr>",
    "<task-notification>",
)

_INTERRUPT_MARKER = "[Request interrupted by user]"


@dataclass
class Record:
    """One transcript line, with the envelope fields HAID threads on lifted out.

    `raw` retains the full dict so nothing is lost; everything else is convenience."""

    raw: dict
    type: str
    uuid: str | None
    parent_uuid: str | None
    timestamp: str | None          # ISO-8601 UTC ("...Z"); lexicographic sort == chronological
    session_id: str | None
    is_sidechain: bool
    agent_id: str | None
    role: str | None               # message.role for user/assistant records

    # --- content helpers -------------------------------------------------------------

    @property
    def content(self):
        return (self.raw.get("message") or {}).get("content")

    def text(self) -> str:
        """Concatenated text-block content (empty for tool-only / non-message records)."""
        c = self.content
        if isinstance(c, str):
            return c
        if isinstance(c, list):
            return " ".join(
                b["text"] for b in c
                if isinstance(b, dict) and b.get("type") == "text" and "text" in b
            )
        return ""

    def has_tool_result(self) -> bool:
        c = self.content
        return isinstance(c, list) and any(
            isinstance(b, dict) and b.get("type") == "tool_result" for b in c
        )

    def has_tool_use(self) -> bool:
        c = self.content
        return isinstance(c, list) and any(
            isinstance(b, dict) and b.get("type") == "tool_use" for b in c
        )

    def is_metadata(self) -> bool:
        return self.type in METADATA_TYPES

    def is_threaded(self) -> bool:
        """Part of the conversation tree (has a uuid)."""
        return self.uuid is not None

    def is_interrupt(self) -> bool:
        c = self.content
        return isinstance(c, str) and c.strip().startswith(_INTERRUPT_MARKER)

    def is_command_noise(self) -> bool:
        """Slash-/local-command synthetic content masquerading as a user prompt."""
        t = self.text().strip()
        return t.startswith(_COMMAND_NOISE_PREFIXES)

    def is_user_prompt(self) -> bool:
        """A genuine user instruction: main-thread user text that is NOT a tool result,
        NOT an interrupt marker, and NOT slash-command noise. This is the signal used to
        detect rewinds and (later) to anchor instructions/episodes."""
        if self.type != "user" or self.is_sidechain:
            return False
        if self.has_tool_result():
            return False
        if not self.text().strip():
            return False
        if self.is_interrupt() or self.is_command_noise():
            return False
        return True


def from_dict(raw: dict) -> Record:
    """Lift a raw JSONL object into a Record. Never raises on missing fields."""
    msg = raw.get("message") or {}
    return Record(
        raw=raw,
        type=raw.get("type", "~unknown"),
        uuid=raw.get("uuid"),
        parent_uuid=raw.get("parentUuid"),
        timestamp=raw.get("timestamp"),
        session_id=raw.get("sessionId"),
        is_sidechain=bool(raw.get("isSidechain")),
        agent_id=raw.get("agentId"),
        role=msg.get("role") if isinstance(msg, dict) else None,
    )


def is_known_shape(raw: dict) -> bool:
    """True if the record's `type` is one we recognize. Used by the parser to count and
    surface drift rather than silently dropping unfamiliar records."""
    return raw.get("type") in KNOWN_TYPES
