"""The codified message-classification taxonomy — the two axes, the prompt, the schema.

This is the **product** of the user-anchored pass: the wording here is what the model is
asked, so it is kept in one place, tested, and tunable. Two orthogonal axes
(docs/intent-taxonomy.md), never collapsed:

  - MOVE (axis A) — the message's relationship to the prior turn. Correction lives on its
    OWN axis so it is never filed next to "question"; corrections are ground truth for
    misalignment.
  - WORK_TYPE (axis B) — what is being asked.

Plus a one-sentence PURPOSE snapshot: the declared objective as of this message. The
snapshots form the purpose timeline that episode segmentation (step 3) and drift detection
read holistically.

No model is called here — this module only codifies. The judgment boundary is classify.py.
Stdlib only.
"""

from __future__ import annotations

# --- Axis A: conversational move (relationship to the prior turn) -----------------------
MOVES = ("new_directive", "correction", "re_prompt", "refinement", "approval")

MOVE_DEFS = {
    "new_directive": "Opens a new task or thread (an episode/thread boundary candidate).",
    "correction": ("The agent did the wrong or unwanted thing and is being told to redo or "
                   "change it. GROUND TRUTH for misalignment. This is iteration INSIDE the "
                   "unit of work, NOT a new episode."),
    "re_prompt": ("The same ask restated because it did not land the first time — a weaker "
                  "correction (the user had to repeat themselves)."),
    "refinement": ('"also…", "now add…", "and then…" — builds on work that was FINE. '
                   "Explicitly NOT a correction; same thread; iteration inside the unit."),
    "approval": ('"yes go ahead", "looks good", "thanks", "perfect" — approval or a no-op. '
                 "Keeps acknowledgements out of the work buckets."),
}

# --- Axis B: work type (what is being asked) --------------------------------------------
WORK_TYPES = ("question", "planning", "implementation", "investigation", "meta")

WORK_TYPE_DEFS = {
    "question": "Information only; no artifact expected.",
    "planning": "Produce a decision, design, or plan — not code.",
    "implementation": ("Produce or change artifacts (absorbs generic 'request' and 'bug "
                       "fix'). Use this for feature work, bugfixes, refactors, and chores."),
    "investigation": "Find out WHY; debug. May or may not end in a fix.",
    "meta": "About the session itself (run it, commit, configure) — not the codebase.",
}

# --- The per-message structured-output contract the host agent must satisfy --------------
LABEL_SCHEMA = {
    "type": "object",
    "properties": {
        "move": {"type": "string", "enum": list(MOVES)},
        "work_type": {"type": "string", "enum": list(WORK_TYPES)},
        "purpose": {"type": "string",
                    "description": "One sentence: the current objective as of THIS message."},
    },
    "required": ["move", "work_type", "purpose"],
    "additionalProperties": False,
}

_PREAMBLE = (
    "You are analyzing a Claude Code coaching transcript. Classify ONE user message on two "
    "ORTHOGONAL axes and write a one-sentence purpose snapshot. Work from the conversation "
    "so far (prior user messages + the agent's final text replies) — you are given that "
    "context, not the whole transcript.")

_DISCIPLINE = (
    "Rules:\n"
    "- The two axes are independent: a message is a PAIR, e.g. (correction × implementation) "
    "= 'no, use middleware not a decorator'. Never collapse them.\n"
    "- CORRECTION vs REFINEMENT is the highest-value distinction: a correction means the "
    "prior work was wrong/unwanted; a refinement builds on work that was fine. When the user "
    "is simply adding more, it is a refinement, not a correction.\n"
    "- The purpose snapshot is the DECLARED objective from what the user actually asked "
    "(a trustworthy anchor), in one sentence — e.g. 'Fixing the stale calendar-status "
    "memory.' Not a summary of what the agent did.")


def _enum_block(title: str, defs: dict) -> str:
    lines = [f"{title}:"]
    lines += [f"  - {k}: {v}" for k, v in defs.items()]
    return "\n".join(lines)


def build_message_prompt(message_text: str, context: str) -> str:
    """The classification prompt for a single user message.

    `context` is the bounded conversation skeleton before this message (messages.py). Output
    is constrained to LABEL_SCHEMA by the host agent."""
    return (
        f"{_PREAMBLE}\n\n"
        f"{_enum_block('AXIS A — conversational move', MOVE_DEFS)}\n\n"
        f"{_enum_block('AXIS B — work type', WORK_TYPE_DEFS)}\n\n"
        f"{_DISCIPLINE}\n\n"
        f"--- conversation so far ---\n{context or '(this is the first message)'}\n\n"
        f"--- the user message to classify ---\n{message_text}\n\n"
        "Respond ONLY via structured output: move, work_type, purpose."
    )
