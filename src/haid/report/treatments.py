"""The treatment catalog — symptom + why → known remedy.

The report compositor's lookup table: HAID detects a SYMPTOM (a metric pattern), the
why-pass establishes the WHY (flags + note), and this catalog pairs them with a known,
evidence-backed TREATMENT (a CLAUDE.md convention, a skill, a workflow discipline, a
model-tier change, a tool). Shipped as package data (src/haid/data/treatments.json) with
per-entry provenance and verification dates — best practices go stale, so the catalog is
versioned and meant for regular refresh (each entry carries `last_verified`).

Symptom keys are HAID's own vocabulary (not free text), so the compositor can match
mechanically:  metric-derived keys (rereads.cross_session, retries.error_ignored, ...) +
why-flag keys (flags.recurred_across_sessions, ...) + diagnosis keys from the value fold
(cost.model_overkill, cleanliness.low, ...).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from importlib import resources

# Canonical symptom vocabulary — every catalog entry must key on these (validated at
# load). Grows only deliberately; the compositor maps detections onto this set.
SYMPTOM_KEYS = frozenset({
    # metric-derived
    "rereads.cross_session",        # re-establishment tax: same file re-learned per session
    "rereads.in_context",           # redundant re-read within one context
    "retries.error_ignored",        # verbatim retry without addressing the error
    "retouched.self_thrash",        # rewrote own fresh code, no user trigger
    "unused_context.bloat",         # large speculative reads never used
    # alignment / intent-derived
    "alignment.corrections",        # user had to correct the agent
    "alignment.re_prompts",         # user had to repeat the ask
    "drift.multi_topic",            # one session/context carried many purposes
    # cross-session / recurrence
    "recurrence.fix_did_not_hold",  # same symptom re-reported after a fix
    # value-fold / cost diagnoses
    "cost.model_overkill",          # low-difficulty work on an expensive tier
    "cost.cache_dominated",         # spend dominated by context re-reads (long sessions)
    "cleanliness.low",              # messy / over-engineered output
})

MATURITIES = ("official", "community-consensus", "emerging", "contested",
              "validated-in-house")


@dataclass(frozen=True)
class Treatment:
    id: str
    title: str
    symptoms: list                  # subset of SYMPTOM_KEYS
    treatment: str                  # what to actually do (the coaching payload)
    mechanism: str                  # why it works
    applies_to: list                # ["claude-code", "codex", "cursor", "generic", ...]
    maturity: str                   # one of MATURITIES
    sources: list = field(default_factory=list)   # [{title, url, date}]
    last_verified: str = ""
    caveats: str = ""


class Catalog:
    def __init__(self, version: str, last_updated: str, treatments: list[Treatment]):
        self.version = version
        self.last_updated = last_updated
        self.treatments = treatments
        self._by_symptom: dict[str, list[Treatment]] = {}
        for t in treatments:
            for s in t.symptoms:
                self._by_symptom.setdefault(s, []).append(t)

    def match(self, symptoms: list[str]) -> list[Treatment]:
        """Treatments touching ANY given symptom, ranked by overlap then maturity.

        Unknown symptom keys raise — the compositor must speak the canonical vocabulary,
        not free text (a silent no-match would read as "no known treatment").
        """
        unknown = [s for s in symptoms if s not in SYMPTOM_KEYS]
        if unknown:
            raise KeyError(f"unknown symptom keys {unknown} (see treatments.SYMPTOM_KEYS)")
        seen: dict[str, Treatment] = {}
        for s in symptoms:
            for t in self._by_symptom.get(s, []):
                seen[t.id] = t
        rank = {m: i for i, m in enumerate(MATURITIES)}
        return sorted(seen.values(),
                      key=lambda t: (-len(set(t.symptoms) & set(symptoms)),
                                     rank.get(t.maturity, len(MATURITIES)), t.id))


def _validate(entry: dict) -> Treatment:
    t = Treatment(**entry)
    bad = [s for s in t.symptoms if s not in SYMPTOM_KEYS]
    if bad:
        raise ValueError(f"treatment {t.id}: unknown symptom keys {bad}")
    if t.maturity not in MATURITIES:
        raise ValueError(f"treatment {t.id}: maturity {t.maturity!r} not in {MATURITIES}")
    if not t.sources and t.maturity != "validated-in-house":
        raise ValueError(f"treatment {t.id}: external maturity requires sources")
    return t


def load_catalog() -> Catalog:
    """Load the shipped catalog (package data), validating every entry."""
    raw = json.loads(resources.files("haid.data").joinpath("treatments.json")
                     .read_text(encoding="utf-8"))
    treatments = [_validate(e) for e in raw["treatments"]]
    return Catalog(raw["version"], raw["last_updated"], treatments)
