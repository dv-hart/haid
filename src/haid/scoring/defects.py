"""Cleanliness as a counted defect profile — NOT a pairwise-ladder percentile.

WHY THIS REPLACES THE LADDER (decided with the maintainer, 2026-06-27):

Cleanliness is not an ordinal scalar. Two diffs can be *differently* dirty (one ships
dead code, the other duplicates a block) with no true fact about which is "cleaner". The
old pairwise placement forced that non-existent total order onto a ladder; the validation
showed every cleanliness placement was non-monotonic (58 ordering inversions across 11
episodes, 0/11 coherent) while the *same* machinery on difficulty stayed coherent (5/11).
The inversions weren't noise — they were the signature of an ordinal instrument measuring
a non-ordinal quantity. A better ladder cannot fix a category error.

So cleanliness is measured the way professionals actually read code: by COUNTING discrete,
falsifiable defects, not by eliciting a holistic rank. The contract:

  - A CLOSED taxonomy of defect classes, each with severity FIXED here (severe | minor).
    The judge classifies and locates; it NEVER opines on severity — severity is a lookup,
    not a judgment, which is what keeps the measure consistent run-over-run.
  - Every finding must carry a `locator` (a verbatim snippet from the diff) so a second
    pass can confirm or refute it. Evidence-bearing judgments are far more reliable than a
    1-10 vibe, and they make the adversarial-verify pass trivial.
  - Each defect counts as ONE instance (severity = its class), so the judge never has to
    decide "cite the line or the whole function?" — that line-span judgment was a hidden
    +-5x noise source. Magnitude lives in the class, not the footprint.
  - An "other" channel surfaces novel slop for coaching, but carries NO score weight, so a
    single judge's free-text label can never swing the number.

The COUNTS this module produces feed value.execution_factor(): cleanliness becomes a
bounded penalty on achievement (orthogonal to difficulty — "you made it hard, but did you
make it professionally?"), never the squared reward multiplier it used to be.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# --- the closed taxonomy: class id -> (severity, one-line operational definition) -------
# SEVERE = a professionalism failure a senior reviewer would block on. These are the only
# classes that move the score. MINOR = coaching color (weight 0). The definitions are the
# wording the judge keys off; keep them operational (pointable), not aspirational.
SEVERE = "severe"
MINOR = "minor"

DEFECT_CLASSES: dict[str, dict] = {
    # ---- severe: pointable, high inter-rater agreement, "did you do it professionally" ----
    "reinvents_primitive": {
        "severity": SEVERE,
        "desc": "Hand-rolls a well-known standard primitive instead of using the obvious "
                "library/stdlib facility — e.g. manual date/time string parsing, hand-written "
                "JSON/CSV parsing, a bespoke arg parser, a hand-rolled retry/backoff loop, "
                "manual deep-copy. The give-away that the simpler professional path was missed.",
    },
    "dead_code": {
        "severity": SEVERE,
        "desc": "Commits dead weight: large commented-out code blocks, unreachable branches, "
                "or functions/vars added in this diff that are never used.",
    },
    "comment_contradiction": {
        "severity": SEVERE,
        "desc": "A comment or docstring that contradicts, misdescribes, or lies about what the "
                "adjacent code actually does (stale or wrong, not merely sparse).",
    },
    "copy_paste_duplication": {
        "severity": SEVERE,
        "desc": "A substantial block (roughly a function's worth) is duplicated near-verbatim "
                "within the diff instead of being factored — a real DRY violation, not two "
                "lines that happen to rhyme.",
    },
    "error_swallowing": {
        "severity": SEVERE,
        "desc": "Silently discards failures: bare except/catch that swallows, an ignored error "
                "return, an empty catch — turning a real failure into invisible wrong behavior.",
    },
    "debug_artifact": {
        "severity": SEVERE,
        "desc": "Leftover scaffolding shipped in the change: stray debug print/console.log, "
                "commented 'TODO: remove', temporary logging, hardcoded test values.",
    },
    # ---- minor: real but coaching-only; severity lookup gives them weight 0 ---------------
    "verbosity": {
        "severity": MINOR,
        "desc": "Needlessly long-hand where a concise, idiomatic form exists (10 lines for a "
                "1-line job), without rising to a severe defect.",
    },
    "naming": {
        "severity": MINOR,
        "desc": "Unclear, misleading, or inconsistent naming.",
    },
    "mild_overabstraction": {
        "severity": MINOR,
        "desc": "Slightly more indirection/configurability than the task needs (a layer that "
                "earns nothing here) — short of the severe reinvention/duplication classes.",
    },
    "missing_comment": {
        "severity": MINOR,
        "desc": "A genuinely tricky spot left with no explanation a maintainer would want.",
    },
}

# The free-text escape hatch. Surfaced for coaching, NEVER counted toward the score, so a
# novel label a single judge invents cannot move the number (keeps the measure consistent).
OTHER = "other"

SEVERE_CLASSES = tuple(k for k, v in DEFECT_CLASSES.items() if v["severity"] == SEVERE)
MINOR_CLASSES = tuple(k for k, v in DEFECT_CLASSES.items() if v["severity"] == MINOR)


def severity_of(defect_class: str) -> str:
    """Severity is a TABLE LOOKUP, never the judge's opinion. Unknown class -> 'other'
    (weight 0). This single function is why the measure is consistent: tune severity here,
    in one place, and every diff gets the same weight for the same class."""
    spec = DEFECT_CLASSES.get(defect_class)
    return spec["severity"] if spec else OTHER


# --- structured-output contract the judge must satisfy (one pass over the whole diff) ----
# A flat list of findings. class is a CLOSED enum (+ "other"); locator pins the evidence;
# note is the one-line justification. No severity field — we derive it via severity_of().
DEFECT_SCHEMA = {
    "type": "object",
    "properties": {
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "defect_class": {"type": "string",
                                     "enum": list(DEFECT_CLASSES.keys()) + [OTHER]},
                    "locator": {"type": "string",
                                "description": "A short verbatim snippet from the diff "
                                               "(or a file:line) pinpointing the instance, "
                                               "so the finding can be verified or refuted."},
                    "note": {"type": "string",
                             "description": "One sentence: why this is the named defect."},
                },
                "required": ["defect_class", "locator", "note"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["findings"],
    "additionalProperties": False,
}

_PREAMBLE = (
    "You are a senior staff engineer reviewing ONE anonymized code-change diff for "
    "professional-grade execution. You are NOT rating it on a scale and NOT comparing it to "
    "anything — you are CATALOGUING concrete defects, each pinned to evidence in the diff. "
    "Identifiers may be anonymized; the diff may be truncated to show code first.")

_RULES = (
    "Rules:\n"
    "- Report ONLY instances you can point to. Every finding needs a `locator`: a short "
    "verbatim snippet copied from the diff (or file:line). If you cannot quote it, do not "
    "report it.\n"
    "- One finding per distinct instance. Do NOT inflate one defect into several, and do "
    "NOT widen a single bad line into a 'whole function' finding — count the instance once.\n"
    "- Judge ONLY the lines this diff adds or changes; do not flag pre-existing code shown "
    "for context.\n"
    "- Ignore raw size and surface style/formatting. A large change that genuinely needs to "
    "be large is fine. You are looking for the defects below, not for length.\n"
    "- Pick the SINGLE best-fitting class for each instance. Use \"other\" ONLY for a clear, "
    "serious professionalism defect that fits none of the named classes; describe it in "
    "`note`. When unsure whether something is a defect at all, leave it out.")


def _catalog_block() -> str:
    sev = "\n".join(f"  - {k}: {DEFECT_CLASSES[k]['desc']}" for k in SEVERE_CLASSES)
    minr = "\n".join(f"  - {k}: {DEFECT_CLASSES[k]['desc']}" for k in MINOR_CLASSES)
    return (f"SEVERE defect classes (professionalism failures a reviewer would block on):\n"
            f"{sev}\n\n"
            f"MINOR defect classes (report for coaching; lower stakes):\n{minr}")


def build_defect_prompt(diff: str) -> str:
    """The single-diff defect-cataloguing prompt (the live judge's whole job)."""
    return (f"{_PREAMBLE}\n\n{_catalog_block()}\n\n{_RULES}\n\n"
            "Respond ONLY via structured output: a `findings` list, each with "
            "`defect_class`, a verbatim `locator`, and a one-sentence `note`.\n\n"
            f"--- Diff ---\n{diff}")


@dataclass(frozen=True)
class DefectResult:
    """One diff's cleanliness scorecard: counted instances by severity, evidence kept.

    `changed_lines` is the denominator for the density penalty (added lines from volume),
    carried here so value.execution_factor() needs nothing else."""
    findings: list = field(default_factory=list)    # [{defect_class, locator, note}, ...]
    severe_count: int = 0                           # the ONLY count that moves the score
    minor_count: int = 0                            # coaching color
    other_count: int = 0                            # surfaced, never scored
    changed_lines: int = 0

    @classmethod
    def from_findings(cls, findings: list, changed_lines: int) -> "DefectResult":
        """Build from raw judge findings — severity assigned by LOOKUP, not trusted from
        the judge. `findings` is the validated DEFECT_SCHEMA `findings` list."""
        severe = minor = other = 0
        for f in findings:
            s = severity_of(f.get("defect_class", OTHER))
            if s == SEVERE:
                severe += 1
            elif s == MINOR:
                minor += 1
            else:
                other += 1
        return cls(findings=list(findings), severe_count=severe, minor_count=minor,
                   other_count=other, changed_lines=int(changed_lines))

    def severe_findings(self) -> list:
        """The findings that actually move the score (severity == severe, by lookup) — the
        ones the verify pass re-checks."""
        return [f for f in self.findings
                if severity_of(f.get("defect_class", OTHER)) == SEVERE]

    def by_class(self) -> dict:
        """Per-class instance counts (for the coaching profile, e.g. 'dead_code x3')."""
        out: dict[str, int] = {}
        for f in self.findings:
            c = f.get("defect_class", OTHER)
            out[c] = out.get(c, 0) + 1
        return out

    def summary(self) -> str:
        prof = ", ".join(f"{c}x{n}" for c, n in sorted(self.by_class().items())) or "none"
        return (f"severe={self.severe_count} minor={self.minor_count} other={self.other_count}"
                f" over {self.changed_lines} changed lines  [{prof}]")


# --- verify pass: an adversarial refuter per severe finding ------------------------------
# Detection alone has a "find-one" bias and a single false-positive severe defect now bites
# real points (sqrt model). So every SEVERE finding is independently re-checked by a skeptic
# PROMPTED TO REFUTE, defaulting to refuted when not clearly demonstrable. Only survivors
# count. Minors/others are never verified (they don't score).
VERIFY_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["confirmed", "refuted"]},
        "reason": {"type": "string"},
    },
    "required": ["verdict", "reason"],
    "additionalProperties": False,
}

_VERIFY_PREAMBLE = (
    "You are a skeptical senior reviewer double-checking ONE claimed code defect. Your job is "
    "to REFUTE it unless the diff clearly demonstrates it. A claim that is borderline, "
    "stylistic, a matter of taste, intentional-and-documented, or that you cannot confirm from "
    "the evidence shown should be REFUTED. Only 'confirmed' if it is unambiguously the named "
    "defect in the added/changed code.")


def build_verify_prompt(finding: dict, diff: str) -> str:
    """Per-finding refutation prompt. `finding` is one DEFECT_SCHEMA finding."""
    cls = finding.get("defect_class", OTHER)
    spec = DEFECT_CLASSES.get(cls)
    definition = spec["desc"] if spec else "(unlisted defect)"
    return (f"{_VERIFY_PREAMBLE}\n\n"
            f"Claimed defect class: {cls}\nClass means: {definition}\n"
            f"Locator (verbatim from the diff): {finding.get('locator','')}\n"
            f"Claimed reason: {finding.get('note','')}\n\n"
            "Decide: is this REALLY an instance of that defect class in the added/changed code? "
            "Default to 'refuted' if it is not clearly demonstrable.\n\n"
            f"--- Diff ---\n{diff}\n\n"
            "Respond ONLY via structured output: verdict = \"confirmed\" | \"refuted\", plus reason.")


def apply_verify(result: DefectResult, verdicts: list) -> DefectResult:
    """Drop refuted severe findings, then rebuild the counts (severity re-derived by lookup).

    `verdicts` is one VERIFY verdict per SEVERE finding, IN ORDER (i.e. aligned to
    result.severe_findings()). Minors/others pass through untouched."""
    severe_idx = [i for i, f in enumerate(result.findings)
                  if severity_of(f.get("defect_class", OTHER)) == SEVERE]
    if len(verdicts) != len(severe_idx):
        raise ValueError(f"verify: expected {len(severe_idx)} verdicts "
                         f"(one per severe finding), got {len(verdicts)}")
    refuted_positions = {severe_idx[j] for j, v in enumerate(verdicts)
                         if str(v.get("verdict", "")).lower() == "refuted"}
    kept = [f for i, f in enumerate(result.findings) if i not in refuted_positions]
    return DefectResult.from_findings(kept, result.changed_lines)
