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
    "implementation": ("Produce or change artifacts. Use this for feature work, bugfixes, "
                       "refactors, and chores — then set impl_kind to say WHICH."),
    "investigation": "Find out WHY; debug. May or may not end in a fix.",
    "meta": "About the session itself (run it, commit, configure) — not the codebase.",
}

# --- Axis B refinement: the KIND of implementation (the bug-attribution discriminator) ---
# Only meaningful when work_type == "implementation"; null otherwise. `bugfix` is the
# load-bearing value: it (and a `correction` move) is what seeds the bug-source-attribution
# pass (docs/detectors.md "Recurrence / bug attribution"). Without this discriminator a fix
# is invisible — it collapses into generic "implementation" and never gets traced.
IMPL_KINDS = ("feature", "bugfix", "refactor", "chore")

IMPL_KIND_DEFS = {
    "feature": "New capability or behavior the project did not have before.",
    "bugfix": ("Repairing something that was already supposed to work but didn't — the "
               "user reports/implies a defect and asks for it to be made correct."),
    "refactor": "Restructuring existing code without changing its observable behavior.",
    "chore": "Mechanical upkeep — deps, config, formatting, renames, version bumps.",
}

# --- The per-message label shape (one entry of the session array below) ------------------
# `impl_kind` is nullable and only set when work_type == "implementation"; it is intentionally
# NOT in `required` so saved fixtures predating it still validate (the read-back defaults it to
# null). The enum is extended with null so a schema-constrained runner can emit it cleanly.
_IMPL_KIND_PROP = {
    "type": ["string", "null"], "enum": list(IMPL_KINDS) + [None],
    "description": "When work_type=implementation, WHICH kind (feature/bugfix/refactor/chore); "
                   "else null. 'bugfix' is load-bearing — it seeds bug-source attribution.",
}

LABEL_SCHEMA = {
    "type": "object",
    "properties": {
        "move": {"type": "string", "enum": list(MOVES)},
        "work_type": {"type": "string", "enum": list(WORK_TYPES)},
        "impl_kind": _IMPL_KIND_PROP,
        "purpose": {"type": "string",
                    "description": "One sentence: the current objective as of THIS message."},
    },
    "required": ["move", "work_type", "purpose"],
    "additionalProperties": False,
}

# --- The per-session structured-output contract the host agent must satisfy (R1) ----------
# One agent labels a whole branch, returning an array — one entry per marked message, each
# echoing its REF (a short window-unique handle, NOT the 36-char uuid) so the labels fold back
# onto the right messages. The CLI expands ref → uuid deterministically on read-back, so the
# model never copies a full uuid (the costly, error-prone step that caused transcription slips).
SESSION_LABELS_SCHEMA = {
    "type": "object",
    "properties": {
        "labels": {
            "type": "array",
            "description": "One entry per marked message, in the order they appear.",
            "items": {
                "type": "object",
                "properties": {
                    "ref": {"type": "string",
                            "description": "Copy the ref from this message's CLASSIFY marker."},
                    "move": {"type": "string", "enum": list(MOVES)},
                    "work_type": {"type": "string", "enum": list(WORK_TYPES)},
                    "impl_kind": _IMPL_KIND_PROP,
                    "purpose": {"type": "string",
                                "description": "One sentence: the objective as of THIS message."},
                },
                "required": ["ref", "move", "work_type", "purpose"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["labels"],
    "additionalProperties": False,
}

_DISCIPLINE = (
    "Rules:\n"
    "- The two axes are independent: a message is a PAIR, e.g. (correction × implementation) "
    "= 'no, use middleware not a decorator'. Never collapse them.\n"
    "- CORRECTION vs REFINEMENT is the highest-value distinction: a correction means the "
    "prior work was wrong/unwanted; a refinement builds on work that was fine. When the user "
    "is simply adding more, it is a refinement, not a correction.\n"
    "- impl_kind: ONLY when work_type is implementation, set impl_kind to feature / bugfix / "
    "refactor / chore (otherwise null). bugfix = repairing something that was supposed to "
    "work but didn't; feature = new behavior. This distinction is load-bearing — do not "
    "default everything to feature.\n"
    "- The purpose snapshot is the DECLARED objective from what the user actually asked "
    "(a trustworthy anchor), in one sentence — e.g. 'Fixing the stale calendar-status "
    "memory.' Not a summary of what the agent did.")


def _enum_block(title: str, defs: dict) -> str:
    lines = [f"{title}:"]
    lines += [f"  - {k}: {v}" for k, v in defs.items()]
    return "\n".join(lines)


_SESSION_PREAMBLE = (
    "You are analyzing a Claude Code coaching transcript — ONE session branch, top to bottom, "
    "in order. Some USER lines are marked '>>> CLASSIFY THIS MESSAGE — ref: … <<<'. Classify "
    "EACH marked message on two ORTHOGONAL axes and write a one-sentence purpose snapshot. "
    "Unmarked lines are context only — do not emit labels for them.")

_SESSION_CAUSALITY = (
    "Causality matters: judge each marked message by the conversation UP TO AND INCLUDING it "
    "— its move is its relationship to the turn just before it. Do NOT use hindsight; a later "
    "message must never change an earlier message's label. Return one label per marked "
    "message, in the order they appear, each echoing its own short ref exactly (copy it from "
    "the marker — it is a few characters, not a long id).")


def build_session_prompt(transcript: str, n_targets: int) -> str:
    """The classification prompt for one whole session branch (R1).

    `transcript` is the rendered branch with its target USER lines marked (messages.py). The
    agent returns a `labels` array constrained to SESSION_LABELS_SCHEMA — one entry per mark."""
    return (
        f"{_SESSION_PREAMBLE}\n\n"
        f"{_enum_block('AXIS A — conversational move', MOVE_DEFS)}\n\n"
        f"{_enum_block('AXIS B — work type', WORK_TYPE_DEFS)}\n\n"
        f"{_enum_block('AXIS B refinement — impl_kind (only if work_type=implementation)', IMPL_KIND_DEFS)}\n\n"
        f"{_DISCIPLINE}\n\n"
        f"{_SESSION_CAUSALITY}\n\n"
        f"--- the session branch ({n_targets} message(s) marked for classification) ---\n"
        f"{transcript}\n\n"
        "Respond ONLY via structured output: a `labels` array, one object per marked message "
        "(ref, move, work_type, impl_kind, purpose)."
    )
