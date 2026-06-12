"""The report compositor — what + why + scores -> a readable coaching report.

Three layers, strictly separated (agent-analysis.md §6):

  1. DETERMINISTIC findings (this module, no model): join the metrics doc (the what), the
     why-notes (the why), the episode-score distribution, and the message tags; derive
     canonical SYMPTOM KEYS from stated rules; match treatments from the shipped catalog.
     The treatment lookup is mechanical — the model can prioritize and narrate, but it
     cannot invent a remedy that isn't in the catalog.
  2. DETERMINISTIC digest render — the "what/why breakdown" reports a user can read with
     zero model involvement (and CI can snapshot).
  3. The COMPOSITION agent (recommended tier: opus) — ONE holistic job via the same
     manifest/backend pattern as every other model boundary: weave the findings into a
     hedged narrative, credit earned work, rank recommendations by leverage. Strictly
     validated read-back: every recommendation must cite a finding id and a treatment id
     the deterministic layer actually matched.

Trust discipline is encoded here, not hoped for: why-notes flagged earned/legitimate are
CREDITED, never treated; suppression and thresholds are stated rules.
"""

from __future__ import annotations

import json
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Callable

from .treatments import Catalog, load_catalog

RECOMMENDED_MODEL = "opus"

# Why-note flags that mean "this was fine" — the finding is reported as a credit, with no
# treatment attached (docs/treatments.md "load-bearing nuances").
SUPPRESSING_FLAGS = frozenset({"earned_iteration", "legitimate_by_context",
                               "different_root_cause"})

# Stated deterministic thresholds (tunable knobs, but never silent).
MIN_CORRECTIONS = 2          # corrections in window before alignment.corrections fires
MIN_RE_PROMPTS = 2
DRIFT_DIRECTIVES_PER_SESSION = 3   # new_directives in ONE session -> drift.multi_topic
LOW_DIFFICULTY_RUNG = 2.0    # rung <= this AND above-median spend -> cost.model_overkill
LOW_CLEANLINESS_P = 0.35     # placement percentile <= this -> cleanliness.low


@dataclass
class Finding:
    """One deterministic finding: a symptom occurrence + its mechanically-matched treatments."""
    id: str                        # "F1", "F2", ...
    source: str                    # "why_note" | "window_rule"
    symptoms: list                 # canonical symptom keys ([] when suppressed)
    summary: str                   # one-line, evidence-grounded
    evidence: str                  # the why-note's note/audit, or the rule + numbers
    treatments: list = field(default_factory=list)   # [{id, title}] matched from catalog
    suppressed: bool = False       # earned/legitimate -> credit, not coaching
    flags: list = field(default_factory=list)
    avoidable_tokens: int | None = None
    confidence: str = ""


def symptoms_for_note(note: dict) -> list[str]:
    """Canonical symptom keys for one why-note.

    Suppressing flags win UNLESS the investigator also quantified avoidable tokens — a
    mixed verdict ("partly earned, but ~N tok were avoidable") keeps its remedy; only a
    cleanly-earned note is fully credited with no coaching."""
    flags = set(note.get("flags", []))
    avoidable = note.get("estimated_avoidable_tokens") or 0
    if flags & SUPPRESSING_FLAGS and avoidable <= 0:
        return []
    metric = note.get("metric", "")
    out: list[str] = []
    if metric == "rereads":
        out.append("rereads.cross_session" if "recurred_across_sessions" in flags
                   else "rereads.in_context")
    elif metric == "retries":
        out.append("retries.error_ignored")
    elif metric == "retouched":
        # without a user trigger it's self-thrash; with correction_preceded the rework
        # answered the user — alignment owns that signal, not retouch coaching.
        if "no_user_trigger" in flags:
            out.append("retouched.self_thrash")
    elif metric == "unused_context":
        out.append("unused_context.bloat")
    if "fix_did_not_hold" in flags:
        out.append("recurrence.fix_did_not_hold")
    return out


def _note_findings(why_doc: dict, catalog: Catalog, counter) -> list[Finding]:
    out = []
    for n in why_doc.get("notes", []):
        flags = set(n.get("flags", []))
        symptoms = symptoms_for_note(n)
        suppressed = not symptoms and bool(flags & SUPPRESSING_FLAGS)
        treats = catalog.match(symptoms) if symptoms else []
        out.append(Finding(
            id=counter(), source="why_note", symptoms=symptoms,
            summary=f"[{n.get('metric')}] {n.get('detail', '')[:120]}",
            evidence=(n.get("note", "") + (" | audit: " + n["anchor_audit"]
                                           if n.get("anchor_audit") else "")),
            treatments=[{"id": t.id, "title": t.title} for t in treats],
            suppressed=suppressed, flags=sorted(flags),
            avoidable_tokens=n.get("estimated_avoidable_tokens"),
            confidence=n.get("confidence", "")))
    return out


def _window_findings(tags_doc: dict | None, scores_doc: dict | None,
                     catalog: Catalog, counter) -> list[Finding]:
    """Symptom rules over the window aggregates — each threshold stated in the evidence."""
    out: list[Finding] = []

    if tags_doc:
        msgs = tags_doc.get("messages", [])
        n_corr = sum(1 for m in msgs if m.get("move") == "correction")
        n_rep = sum(1 for m in msgs if m.get("move") == "re_prompt")
        if n_corr >= MIN_CORRECTIONS:
            quotes = "; ".join(m.get("purpose", "")[:80] for m in msgs
                               if m.get("move") == "correction")[:300]
            out.append(Finding(
                id=counter(), source="window_rule",
                symptoms=["alignment.corrections"],
                summary=f"{n_corr} user corrections in the window",
                evidence=f"rule: >= {MIN_CORRECTIONS} corrections; purposes: {quotes}",
                treatments=[{"id": t.id, "title": t.title}
                            for t in catalog.match(["alignment.corrections"])]))
        if n_rep >= MIN_RE_PROMPTS:
            out.append(Finding(
                id=counter(), source="window_rule",
                symptoms=["alignment.re_prompts"],
                summary=f"{n_rep} re-prompts (the user had to repeat themselves)",
                evidence=f"rule: >= {MIN_RE_PROMPTS} re_prompt moves in window",
                treatments=[{"id": t.id, "title": t.title}
                            for t in catalog.match(["alignment.re_prompts"])]))
        by_sess: dict[str, int] = {}
        for m in msgs:
            if m.get("move") == "new_directive":
                by_sess[m.get("session_id", "?")] = by_sess.get(m.get("session_id", "?"), 0) + 1
        drifty = {s: c for s, c in by_sess.items() if c >= DRIFT_DIRECTIVES_PER_SESSION}
        if drifty:
            out.append(Finding(
                id=counter(), source="window_rule",
                symptoms=["drift.multi_topic"],
                summary=f"{len(drifty)} session(s) carried {DRIFT_DIRECTIVES_PER_SESSION}+ "
                        "distinct directives",
                evidence=f"rule: >= {DRIFT_DIRECTIVES_PER_SESSION} new_directive moves per "
                         f"session; sessions: {drifty}",
                treatments=[{"id": t.id, "title": t.title}
                            for t in catalog.match(["drift.multi_topic"])]))

    if scores_doc:
        eps = [e for e in scores_doc.get("episodes", []) if e.get("value") is not None]
        toks = sorted(e.get("normalized_tokens", 0) for e in eps)
        median_tok = toks[(len(toks) - 1) // 2] if toks else 0   # lower median: stable at small n
        for e in eps:
            d = e.get("difficulty", {})
            if (d.get("rung") is not None and d["rung"] <= LOW_DIFFICULTY_RUNG
                    and e.get("normalized_tokens", 0) > median_tok):
                out.append(Finding(
                    id=counter(), source="window_rule",
                    symptoms=["cost.model_overkill"],
                    summary=f"episode {e['id']} is low-difficulty (rung {d['rung']:g}) but "
                            f"above-median spend ({e['normalized_tokens']:.0f} nTok)",
                    evidence=f"rule: rung <= {LOW_DIFFICULTY_RUNG:g} and nTok > window "
                             f"median ({median_tok:.0f}); episode: {e.get('title', '')[:60]}",
                    treatments=[{"id": t.id, "title": t.title}
                                for t in catalog.match(["cost.model_overkill"])]))
            c = e.get("cleanliness", {})
            if c.get("percentile") is not None and c["percentile"] <= LOW_CLEANLINESS_P:
                out.append(Finding(
                    id=counter(), source="window_rule",
                    symptoms=["cleanliness.low"],
                    summary=f"episode {e['id']} placed low on cleanliness "
                            f"(p{c['percentile']:.2f})",
                    evidence=f"rule: cleanliness percentile <= {LOW_CLEANLINESS_P}; "
                             f"episode: {e.get('title', '')[:60]}",
                    treatments=[{"id": t.id, "title": t.title}
                                for t in catalog.match(["cleanliness.low"])]))
    return out


def build_findings(*, why_doc: dict | None = None, tags_doc: dict | None = None,
                   scores_doc: dict | None = None,
                   catalog: Catalog | None = None) -> list[Finding]:
    catalog = catalog or load_catalog()
    n = iter(range(1, 1000))
    counter = lambda: f"F{next(n)}"  # noqa: E731
    out: list[Finding] = []
    if why_doc:
        out += _note_findings(why_doc, catalog, counter)
    out += _window_findings(tags_doc, scores_doc, catalog, counter)
    return out


# --- layer 2: the deterministic digest ----------------------------------------------
def digest_json(*, metrics_doc: dict | None, why_doc: dict | None, scores_doc: dict | None,
                tags_doc: dict | None, findings: list[Finding], label: str = "") -> dict:
    """The full deterministic hand-off: everything the composition agent may use."""
    headline = []
    if metrics_doc:
        for m in metrics_doc.get("measurements", []):
            if m.get("scope") == "window":
                b = m.get("baseline", {})
                headline.append({"metric": m["metric"], "token_rate": m["token_rate"],
                                 "percentile": b.get("percentile"), "band": b.get("band")})
    return {
        "schema_version": "1.0", "kind": "haid_report_digest", "window": label,
        "metrics_headline": headline,
        "episodes": (scores_doc or {}).get("episodes", []),
        "findings": [vars(f) for f in findings],
        "n_messages_tagged": len((tags_doc or {}).get("messages", [])),
        "caveats": (metrics_doc or {}).get("caps", {}).get("notes", []),
    }


def render_digest(d: dict) -> str:
    """The deterministic what/why report — readable without any model."""
    L = [f"# HAID report — {d.get('window') or 'window'}", ""]
    if d["metrics_headline"]:
        L.append("## The what — waste metrics vs baseline")
        for m in d["metrics_headline"]:
            band = m.get("band") or "no baseline"
            pct = f"p{m['percentile']:.0f}" if isinstance(m.get("percentile"), (int, float)) else "—"
            L.append(f"  - {m['metric']}: {m['token_rate']*100:.1f}% ({pct}, {band})")
        L.append("")
    eps = [e for e in d.get("episodes", []) if e.get("value") is not None]
    if eps:
        L.append("## Episodes (by value)")
        for e in sorted(eps, key=lambda e: e["value"], reverse=True):
            dd, cc = e.get("difficulty", {}), e.get("cleanliness", {})
            L.append(f"  - {e['id']} · {e.get('title','')[:55]}: value={e['value']:.3g} "
                     f"ach={e.get('achievement','?')} (D rung {dd.get('rung','?')}, "
                     f"C p{cc.get('percentile','?')}, {e.get('normalized_tokens',0):.0f} nTok)")
        L.append("")
    findings = d.get("findings", [])
    active = [f for f in findings if f["symptoms"]]
    earned = [f for f in findings if f["suppressed"]]
    if active:
        L.append("## The why — findings with matched treatments")
        for f in active:
            L.append(f"  {f['id']} [{'/'.join(f['symptoms'])}] {f['summary']}")
            L.append(f"      evidence: {f['evidence'][:240]}")
            if f.get("avoidable_tokens"):
                L.append(f"      avoidable: ~{f['avoidable_tokens']} tok")
            for t in f["treatments"][:3]:
                L.append(f"      -> {t['id']}: {t['title']}")
        L.append("")
    if earned:
        L.append("## Credited — flagged by metrics, cleared by investigation")
        for f in earned:
            L.append(f"  {f['id']} {f['summary']} ({', '.join(f['flags'])})")
        L.append("")
    if d.get("caveats"):
        L.append("## Caveats")
        for c in d["caveats"]:
            L.append(f"  - {c}")
    return "\n".join(L).rstrip()


# --- layer 3: the composition agent (one holistic job, opus-tier) -------------------
COMPOSITION_SCHEMA = {
    "type": "object",
    "properties": {
        "headline": {"type": "string"},
        "wins": {"type": "array", "items": {
            "type": "object",
            "properties": {"what": {"type": "string"}, "evidence": {"type": "string"}},
            "required": ["what", "evidence"], "additionalProperties": False}},
        "recommendations": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "priority": {"type": "integer"},
                "finding_id": {"type": "string"},
                "treatment_id": {"type": "string"},
                "action": {"type": "string"},
                "expected_effect": {"type": "string"},
            },
            "required": ["priority", "finding_id", "treatment_id", "action",
                         "expected_effect"],
            "additionalProperties": False}},
        "watchlist": {"type": "array", "items": {"type": "string"}},
        "hedges": {"type": "string"},
    },
    "required": ["headline", "wins", "recommendations", "watchlist", "hedges"],
    "additionalProperties": False,
}


def build_composition_prompt(digest: dict, catalog: Catalog) -> str:
    treat_ids = sorted({t["id"] for f in digest["findings"] for t in f["treatments"]})
    details = [t for t in catalog.treatments if t.id in treat_ids]
    detail_block = "\n".join(
        f"- {t.id}: {t.treatment} (mechanism: {t.mechanism})"
        + (f" CAVEAT: {t.caveats}" if t.caveats else "")
        for t in details)
    return (
        "You are HAID's report compositor. Below is a deterministic digest of a Claude Code "
        "user's recent work: waste metrics vs baseline, per-episode value scores, "
        "evidence-grounded findings (each with treatments matched from a vetted catalog), and "
        "credited items investigation cleared as earned. Compose the coaching layer.\n\n"
        "Rules (non-negotiable):\n"
        "1. CREDIT FIRST. Lead with what went well — high-difficulty episodes, cleared "
        "findings, below-baseline metrics. Never cry wolf on the hardest work.\n"
        "2. Every recommendation MUST cite a finding_id from the digest and a treatment_id "
        "from that finding's matched list. You may prioritize, combine evidence, and write "
        "the action concretely for THIS user's project — but you may NOT invent remedies "
        "or cite treatments that weren't matched.\n"
        "3. Rank by leverage: avoidable tokens, recurrence, and trust impact — not by how "
        "easy the advice is to give. 3-5 recommendations maximum.\n"
        "4. HEDGE honestly: state what rests on thin evidence (finding confidence, "
        "single-source baselines). Respect every treatment's CAVEAT (e.g. subagents cost "
        "MORE total tokens; don't prescribe planning for one-sentence diffs).\n"
        "5. Watchlist = real-but-not-actionable-yet items (one line each).\n\n"
        "## Digest\n" + json.dumps(digest, indent=1) + "\n\n"
        "## Matched treatment details\n" + detail_block + "\n\n"
        "Respond ONLY via structured output: headline, wins, recommendations "
        "(priority/finding_id/treatment_id/action/expected_effect), watchlist, hedges.")


def validate_composition(comp: dict, findings: list[Finding]) -> dict:
    """Strict: every recommendation must reference a real finding + a matched treatment."""
    for k in COMPOSITION_SCHEMA["required"]:
        if k not in comp:
            raise ValueError(f"composition: missing key {k!r}")
    by_id = {f.id: f for f in findings}
    for r in comp["recommendations"]:
        f = by_id.get(r.get("finding_id"))
        if f is None:
            raise ValueError(f"composition: unknown finding_id {r.get('finding_id')!r}")
        if r.get("treatment_id") not in {t["id"] for t in f.treatments}:
            raise ValueError(
                f"composition: treatment {r.get('treatment_id')!r} was not matched for "
                f"finding {f.id} (matched: {[t['id'] for t in f.treatments]})")
    return comp


class PendingComposition(Exception):
    def __init__(self, manifest_path: str):
        super().__init__(f"composition pending — run one {RECOMMENDED_MODEL}-tier subagent "
                         f"over {manifest_path}, write the composition, then re-run")
        self.manifest_path = manifest_path


Runner = Callable[[dict], dict]


class ComposeBackend(ABC):
    @abstractmethod
    def compose(self, digest: dict, findings: list[Finding], catalog: Catalog) -> dict:
        raise NotImplementedError


class ReplayBackend(ComposeBackend):
    def __init__(self, composition: dict):
        self._c = composition

    @classmethod
    def from_file(cls, path: str) -> "ReplayBackend":
        return cls(json.load(open(path, encoding="utf-8")))

    def compose(self, digest, findings, catalog):
        return validate_composition(self._c, findings)


class HarnessBackend(ComposeBackend):
    """One holistic composition job — manifest/file-handoff like every model boundary."""

    def __init__(self, job_dir: str, runner: Runner | None = None,
                 job_name: str = "compose", model: str = RECOMMENDED_MODEL):
        self.job_dir = job_dir
        self.runner = runner
        self.job_name = job_name
        self.model = model

    def compose(self, digest, findings, catalog):
        manifest = {"task": "compose_report", "recommended_model": self.model,
                    "schema": COMPOSITION_SCHEMA,
                    "prompt": build_composition_prompt(digest, catalog)}
        if self.runner is not None:
            return validate_composition(dict(self.runner(manifest)), findings)
        os.makedirs(self.job_dir, exist_ok=True)
        mpath = os.path.join(self.job_dir, f"{self.job_name}.job.json")
        cpath = os.path.join(self.job_dir, f"{self.job_name}.composition.json")
        if os.path.exists(cpath):
            comp = json.load(open(cpath, encoding="utf-8"))
            return validate_composition(comp, findings)
        json.dump(manifest, open(mpath, "w", encoding="utf-8"), indent=1)
        raise PendingComposition(mpath)


def render_report(digest: dict, comp: dict) -> str:
    """The final user-facing report: composed narrative on top of the deterministic digest."""
    L = [f"# How am I doing? — {digest.get('window') or 'window'}", "",
         comp["headline"], ""]
    if comp["wins"]:
        L.append("## What went well")
        for w in comp["wins"]:
            L.append(f"  - {w['what']}  ({w['evidence']})")
        L.append("")
    if comp["recommendations"]:
        L.append("## Recommendations (by leverage)")
        for r in sorted(comp["recommendations"], key=lambda r: r["priority"]):
            L.append(f"  {r['priority']}. {r['action']}")
            L.append(f"     why: {r['finding_id']} · treatment: {r['treatment_id']} "
                     f"· expected: {r['expected_effect']}")
        L.append("")
    if comp["watchlist"]:
        L.append("## Watchlist")
        for w in comp["watchlist"]:
            L.append(f"  - {w}")
        L.append("")
    if comp["hedges"]:
        L.append(f"## Honest hedges\n{comp['hedges']}")
        L.append("")
    L.append("---\n_Below: the deterministic breakdown this was composed from._\n")
    L.append(render_digest(digest))
    return "\n".join(L)
