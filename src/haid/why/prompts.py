"""Codified why-pass investigation prompts — the prompts ARE the product.

Wording validated live (2026-06-09, c7-connector ×4 + boxBot ×4). Lessons baked in:
  - MANDATORY anchor self-audit: agents repeatedly found real detector overstatements
    (a window-scope reread rendered as "already in context", a retry whose 2nd attempt
    had a different error after an environment change) — auditing is part of the job.
    (Both specific cases were fixed in the detectors on 2026-06-09 — cross-session
    rereads now render "already read earlier in window", retries require matching error
    output — but the audit mandate stands.)
  - SCOPE SEMANTICS per metric must be stated, or agents "refute" intended behavior
    (re_touched deliberately spans sessions in the window).
  - Flag definitions must be scoped precisely (no_user_trigger judges the flagged ACTION,
    not its parent task) and split factual-error ("detector_overstates") from
    justified-action ("legitimate_by_context" / "earned_iteration").
  - Trust rule: earned iteration must come back as NOT waste, plainly.
  - estimated_avoidable_tokens + basis gives the report compositor a ranking handle.
"""

from __future__ import annotations

from .anchors import WhyAnchor

# Recommended judgment tier for investigation agents: needs sustained multi-step tool use
# over large transcripts, not frontier reasoning. Overridable by the user (CLI --model);
# any runner (Claude Code workflow, plain subagents, future codex/gh) may map it to its
# own equivalent tier.
RECOMMENDED_MODEL = "sonnet"

CONFIDENCES = ("high", "medium", "low")

# Non-exclusive observable FLAGS — facts, not verdicts (agent-analysis.md §3).
FLAGS = {
    "correction_preceded": "corrective user feedback came BEFORE the flagged action and "
                           "the action responds to it",
    "no_user_trigger": "the flagged action ITSELF had no preceding user input or external "
                       "event (failed run, new data) making it necessary — even if the "
                       "parent task was user-requested",
    "recurred_across_sessions": "the same file/symptom appears in 2+ sessions in the window",
    "central_file_many_sessions": "the file is architecturally central, touched across "
                                  "many sessions",
    "co_churns_with_tests": "the rework tracked test failures/runs",
    "earned_iteration": "the action was justified — difficulty, new information, user "
                        "redirection, or prudent verification",
    "legitimate_by_context": "the anchor is factually correct but the action was "
                             "structurally required (e.g. a read immediately preceding an "
                             "edit to that file)",
    "fix_did_not_hold": "evidence an earlier fix's symptom returned",
    "different_root_cause": "a suspected recurrence was actually a different problem",
    "environment_flaky": "a failure was external/transient",
    "error_message_ignored": "a retry did not address the prior attempt's error",
    "resolved_after_approach_change": "success came only when the approach changed",
    "detector_overstates": "your audit found the anchor's factual claim partially wrong",
}

# What each metric's window-scope instance actually claims — stated so the agent audits
# the right thing instead of refuting intended cross-session behavior.
SCOPE_SEMANTICS = {
    "rereads": "Window scope: 'already read' spans the window's chronological stream "
               "ACROSS sessions. A flagged read may be the FIRST read in its own session — "
               "that is the cross-session re-establishment tax, not an intra-session "
               "repeat. Re-reads after an edit to the file are already excluded, and so is "
               "a session's first read of a file it goes on to edit (the harness's "
               "Read-before-Edit requirement).",
    "retouched": "Window scope: 'written earlier' spans the window ACROSS sessions — the "
                 "rewritten lines may come from a PREVIOUS session. That cross-session "
                 "rework is deliberately in scope (rework compounds); it is not a detector "
                 "error. Editing pre-existing (non-agent) code is already excluded.",
    "retries": "Same command signature failed 2+ times with no success between, AND the "
               "later attempt's error output matches/overlaps the earlier one's (a later "
               "attempt failing differently is excluded as adaptation). Still audit whether "
               "the matching errors hide a real environment change between attempts.",
    "unused_context": "A large read of a file never edited anywhere in the window. Reading "
                      "to understand is legitimate; this flags possible context bloat, the "
                      "softest signal — calibrate your tone accordingly.",
}

_QUESTIONS = {
    "rereads": (
        "1. At each flagged read, what was the agent about to do, and what did it need "
        "from the file (invocation? flags? a behavioral contract like 'does X happen')?\n"
        "2. How many of the window's sessions read this file — is it a per-session tax?\n"
        "3. Does CLAUDE.md / docs / project memory already cover what keeps being "
        "re-established? What 2-3 lines (or what skill) would make the re-reads "
        "unnecessary — or is re-reading prudent here (e.g. before a production deploy)?"),
    "retouched": (
        "1. What was written first, and what did each rewrite change (tight paraphrase)?\n"
        "2. What happened between write and rewrite: user feedback (quote it), a failed "
        "run/test, new information, or nothing visible?\n"
        "3. Earned or avoidable? If avoidable, what specific process change would have "
        "avoided it (read X first, run the test before writing, design before code)?"),
    "retries": (
        "1. What was each attempt's exact error, and did the later attempt change "
        "anything (command, environment, prerequisites)?\n"
        "2. What finally worked — and was the eventual fix knowable from the first error?\n"
        "3. Does this fight recur across sessions? If so, what one-liner (CLAUDE.md/skill) "
        "would end it?"),
    "unused_context": (
        "1. Why was the file read — what question was the agent answering?\n"
        "2. Did the read inform anything visible (a decision, an edit elsewhere), or was "
        "it speculative context-filling?\n"
        "3. Would a targeted partial read (or an existing doc) have answered the same "
        "question?"),
}

_ROLE = (
    "You are a HAID why-pass analysis agent. HAID's deterministic metrics flagged a WASTE "
    "ANCHOR in a Claude Code project's recent sessions. Your job is to explain WHY it "
    "happened — not to re-detect it. Rules:\n\n"
    "1. AUDIT THE ANCHOR FIRST. Verify its factual claim yourself from the transcript. "
    "The metric's scope semantics are stated below — audit against THOSE, not your "
    "assumptions. If the claim is partially wrong, flag \"detector_overstates\"; if it is "
    "right but the action was structurally required, flag \"legitimate_by_context\". "
    "Auditing the detector is part of your job.\n"
    "2. EVIDENCE DISCIPLINE. Any claim that the user triggered/corrected something needs "
    "an exact quote + timestamp + session id. Hedge anything unevidenced; lower your "
    "confidence.\n"
    "3. TRUST RULE. Iteration, re-reading, or retries that were earned (hard work, new "
    "information, user redirection, prudent verification) are NOT waste — say so plainly. "
    "\"No remedy needed\" is a valid answer.\n"
    "4. FLAGS are non-exclusive observable facts, never verdicts. Apply one only when its "
    "definition fits exactly.\n"
    "5. EXTRACTION DISCIPLINE. Transcripts are multi-MB JSONL — use targeted search "
    "(grep with context, or python extraction); NEVER read a whole .jsonl. User messages "
    "are records with \"type\":\"user\".")

# Structured-output contract for one investigation (runners with schema support enforce
# it; the file-handoff read-back validates against it).
NOTE_SCHEMA = {
    "type": "object",
    "properties": {
        "anchor_audit": {"type": "string"},
        "note": {"type": "string"},
        "flags": {"type": "array", "items": {"type": "string", "enum": sorted(FLAGS)}},
        "evidence": {"type": "array", "items": {
            "type": "object",
            "properties": {"session": {"type": "string"}, "ts": {"type": "string"},
                           "what": {"type": "string"}},
            "required": ["session", "what"], "additionalProperties": False}},
        "remedy": {"type": "string"},
        "estimated_avoidable_tokens": {"type": ["integer", "null"]},
        "avoidable_basis": {"type": "string"},
        "confidence": {"type": "string", "enum": list(CONFIDENCES)},
    },
    "required": ["anchor_audit", "note", "flags", "evidence", "remedy",
                 "estimated_avoidable_tokens", "avoidable_basis", "confidence"],
    "additionalProperties": False,
}


def build_anchor_prompt(anchor: WhyAnchor, *, transcript_dir: str, project_path: str,
                        all_session_ids: list[str]) -> str:
    """The complete, self-contained investigation prompt for one anchor."""
    flag_lines = "\n".join(f'   - "{k}": {v}' for k, v in FLAGS.items())
    sessions = ", ".join(anchor.session_ids) if anchor.session_ids else "(unknown)"
    others = [s for s in all_session_ids if s not in anchor.session_ids]
    qs = _QUESTIONS.get(anchor.metric, _QUESTIONS["retouched"])
    return (
        f"{_ROLE}\n\nFLAG DEFINITIONS:\n{flag_lines}\n\n"
        f"ANCHOR (metric: {anchor.metric}, id: {anchor.id}, ~{anchor.token_weight} tok):\n"
        f"{anchor.detail}\n"
        f"Scope semantics: {SCOPE_SEMANTICS.get(anchor.metric, '')}\n"
        f"Flagged session(s): {sessions}\n\n"
        "INPUTS:\n"
        f"- Transcripts directory: {transcript_dir} — session files are <id>.jsonl; the "
        f"flagged session(s) are the place to start. Other sessions in the window "
        f"({len(others)} more) are in the same directory — use them for cross-session "
        "recurrence checks.\n"
        f"- Project working tree: {project_path} — check CLAUDE.md / docs / project "
        "memory for context that already existed, and read the relevant artifact "
        f"({anchor.file_id or 'see anchor detail'}) as needed.\n\n"
        f"QUESTIONS:\n{qs}\n\n"
        "Reply with ONLY this JSON (no fences, no commentary):\n"
        '{"anchor_audit": "<your independent verification of the anchor claim>", '
        '"note": "<evidence-grounded why, 3-6 sentences citing sessions+timestamps>", '
        '"flags": [<exact flag strings from the definitions above>], '
        '"evidence": [{"session": "<8-char id>", "ts": "<timestamp>", "what": "<quote or '
        'tight paraphrase>"}], '
        '"remedy": "<concrete, pattern-specific, hedged; \'no remedy needed\' is valid>", '
        '"estimated_avoidable_tokens": <int or null>, '
        '"avoidable_basis": "<one line on how you estimated>", '
        '"confidence": "high|medium|low"}')
