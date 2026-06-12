"""Node/edge taxonomy for the session graph (L0 spine + L1 action/IO).

The single data structure the whole tool is built on. This is the deterministic Tier-1
skeleton: every field comes from a literal in the transcript (see
docs/session-graph-design.md). No model, no inference here.

Grains (per the design's granularity verdict): Turn (the spine), ToolCall (the primary
analysis grain), File (the cross-session spine). Region is derived and lazy — only
materialized where an Edit/Write touches lines. Episodes/Instructions are Phase 2.

Stdlib only.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Turn:
    """A conversation record on the spine (assistant/user/tool-result-bearing user)."""
    id: str                       # record uuid
    role: str                     # "user" | "assistant"
    parent_uuid: str | None
    ts: str | None
    is_tool_result: bool = False  # a user record carrying toolUseResult
    agent_id: str | None = None


@dataclass
class ToolCall:
    """The primary analysis grain: one tool_use + its paired result."""
    id: str                       # tool_use id (toolu_…)
    tool: str                     # Read/Edit/Write/MultiEdit/Bash/Grep/Glob/Agent/...
    turn_id: str                  # the assistant turn uuid that issued it
    ts: str | None
    params: dict                  # raw tool_use.input
    status: str = "unknown"       # ok | error | unknown  (Bash refined in Step 3, Tier 2)
    error_text: str | None = None # the tool_result block's text when is_error (loop detection)
    result_bytes: int = 0         # proxy for context/result cost
    signature: tuple | None = None
    target_file_id: str | None = None
    read_span: tuple[int, int] | None = None   # (start_line, end_line) for Reads
    derived_read: bool = False    # a Bash command parsed as a single-file read (cat/sed/head)
    derived_write: bool = False   # a Bash command parsed as a single-file write (sed -i/>/tee)
    write_op: str | None = None   # "edit" | "overwrite" | "append" for a derived write
    write_content: str | None = None  # recovered content of a derived write (heredoc only)


_NATIVE_WRITE = {"Edit", "MultiEdit", "Write"}


def is_read(tc: "ToolCall") -> bool:
    """True for native Read calls AND Bash commands parsed as a single-file read
    (cat / sed -n / head / tail; see graph/bash_read.py). The one predicate every
    read-accounting consumer gates on, so shell reads are counted exactly like Reads.
    Keeping tool=="Bash" (rather than reclassifying) preserves per-tool counts, Bash
    retry signatures, and the sed -i-is-not-a-read distinction."""
    return tc.tool == "Read" or tc.derived_read


def is_write(tc: "ToolCall") -> bool:
    """True for native Edit/MultiEdit/Write AND Bash commands parsed as a single-file
    write (sed -i / redirection / tee / cp / mv; see graph/bash_write.py). The predicate
    that says 'this call modified file content' — used to clear the re-read seen-ranges
    (a re-read after a shell edit is legitimate) and to credit unused-context. NOTE: a
    derived write carries no recoverable content, so it does NOT feed the content-based
    rework metric (retouched), which still keys on native Edit/Write old/new strings."""
    return tc.tool in _NATIVE_WRITE or tc.derived_write


@dataclass
class File:
    """Cross-session spine node. id is repo-relative so sessions share one node."""
    id: str                       # repo-relative path (falls back to absolute)
    path: str                     # as seen in the transcript
    exists_at_end: bool = True


@dataclass
class Region:
    """A touched line-span, materialized lazily on Edit/Write. Identity = anchor hash
    (content-based), NOT the line numbers, which drift."""
    id: str                       # file_id + ":" + anchor_hash
    file_id: str
    anchor_hash: str
    current_span: tuple[int, int] | None = None   # (start, end) projection on a version


@dataclass
class Edge:
    src: str
    dst: str
    type: str                     # responds-to | reads | produces | edits
    ts: str | None = None
    attrs: dict = field(default_factory=dict)


@dataclass
class SessionGraph:
    session_id: str | None
    cwd: str | None = None
    turns: dict[str, Turn] = field(default_factory=dict)
    toolcalls: dict[str, ToolCall] = field(default_factory=dict)
    files: dict[str, File] = field(default_factory=dict)
    regions: dict[str, Region] = field(default_factory=dict)
    edges: list[Edge] = field(default_factory=list)
    unpaired_results: int = 0     # tool results with no matching call (drift/overflow)

    # --- convenience views --------------------------------------------------------------

    def edges_of(self, type: str) -> list[Edge]:
        return [e for e in self.edges if e.type == type]

    def toolcalls_of(self, tool: str) -> list[ToolCall]:
        return [tc for tc in self.toolcalls.values() if tc.tool == tool]

    def summary(self) -> dict:
        from collections import Counter
        by_tool = Counter(tc.tool for tc in self.toolcalls.values())
        by_edge = Counter(e.type for e in self.edges)
        return {
            "turns": len(self.turns),
            "toolcalls": len(self.toolcalls),
            "toolcalls_by_tool": dict(by_tool),
            "files": len(self.files),
            "regions": len(self.regions),
            "edges_by_type": dict(by_edge),
            "unpaired_results": self.unpaired_results,
        }
