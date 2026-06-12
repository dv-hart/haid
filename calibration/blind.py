"""Blind the validator diffs before the oracle sees them (docs/calibration-experiment.md §5).

The validator population is famous repos (kubernetes, rust-lang, dfinity...), so the
oracle could recognize them and judge on reputation instead of code. We strip the
obvious identifiers: owner/repo names, URLs, emails, and author/sign-off lines. This
is best-effort, not perfect anonymization (code style can still leak identity) — the
residual risk is accepted and logged; blinding is the primary control, recency the
second (and here recency is waived for the validator population on purpose).

Outputs:
  out/blinded/<anon_id>.diff      — blinded diff text (what the oracle reads)
  out/oracle_input.json           — [{id, diff(capped)}] passed to the Workflow
  out/units_blinded.jsonl         — PRIVATE analysis index: anon_id -> review signals
                                    + real repo (for our eyes only; never shown to oracle)
"""

from __future__ import annotations

import json
import os
import re

from .filekind import file_priority

DIFF_CAP_CHARS = 16000  # per-diff cap shown to the oracle (~4k tokens; logged, not silent)

_URL = re.compile(r"https?://\S+")
_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
_AUTHOR_LINE = re.compile(r"(?im)^(\s*(?:Co-authored-by|Signed-off-by|Author|Reported-by|Reviewed-by)\s*:).*$")


def prioritize_diff(text: str, cap: int) -> tuple[str, int, int]:
    """Reassemble a diff code-first, capped. Returns (text, n_files, n_dropped)."""
    parts = re.split(r"(?m)^(?=diff --git )", text)
    parts = [p for p in parts if p.strip()]
    if len(parts) <= 1:
        return text[:cap], len(parts), 0

    def path_of(block: str) -> str:
        m = re.match(r"diff --git a/(\S+)", block)
        return m.group(1) if m else "~"

    ordered = sorted(parts, key=lambda b: (file_priority(path_of(b)), parts.index(b)))
    out, used, included = [], 0, 0
    for block in ordered:
        if used + len(block) > cap and included >= 1:
            break
        out.append(block if used + len(block) <= cap else block[:cap - used])
        used += len(block)
        included += 1
    return "".join(out), len(parts), len(parts) - included


def _token_variants(name: str) -> set[str]:
    """Identifier variants of a repo/owner name to scrub (case-folded match)."""
    base = name.lower()
    out = {base}
    for sep in ("-", "_", ".", " "):
        out.add(base.replace(sep, ""))
    # also the significant chunks (>=4 chars) so 'azure-service-operator' scrubs 'operator'? no —
    # keep only the whole name + separator-stripped form to avoid over-scrubbing common words.
    return {t for t in out if len(t) >= 3}


def blind_text(text: str, owner: str, repo: str) -> str:
    text = _URL.sub("URL", text)
    text = _EMAIL.sub("EMAIL", text)
    text = _AUTHOR_LINE.sub(r"\1 REDACTED", text)
    # scrub owner/repo identifier tokens, longest first (avoid partial leftovers)
    repl = [(t, "PROJECT") for t in _token_variants(repo)]
    repl += [(t, "OWNER") for t in _token_variants(owner)]
    for tok, sub in sorted(repl, key=lambda x: -len(x[0])):
        text = re.sub(re.escape(tok), sub, text, flags=re.IGNORECASE)
    return text


def run(units_path: str = "out/validator_units.jsonl",
        blinded_dir: str = "out/blinded",
        oracle_input_path: str = "out/oracle_input.json",
        index_path: str = "out/units_blinded.jsonl") -> None:
    os.makedirs(blinded_dir, exist_ok=True)
    units = [json.loads(l) for l in open(units_path, encoding="utf-8") if l.strip()]
    oracle_input = []
    capped = 0
    with open(index_path, "w", encoding="utf-8") as idx:
        for i, u in enumerate(units):
            anon = f"U{i:02d}"
            owner, _, repo = u["repo"].partition("/")
            if not u.get("diff_path") or not os.path.exists(u["diff_path"]):
                print(f"[blind] WARN no diff for {u['repo']}#{u['number']}, skipping")
                continue
            raw = open(u["diff_path"], encoding="utf-8").read()
            blinded = blind_text(raw, owner, repo)
            # code-first, capped view = what the oracle judges (not the alphabetical
            # diff whose leading docs would otherwise eat the budget)
            view, n_files, n_dropped = prioritize_diff(blinded, DIFF_CAP_CHARS)
            if n_dropped:
                view += (f"\n\n# [{n_dropped} of {n_files} files omitted for length; "
                         "code files shown first]\n")
                capped += 1
            with open(os.path.join(blinded_dir, f"{anon}.diff"), "w",
                      encoding="utf-8") as f:
                f.write(view)
            oracle_input.append({"id": anon, "diff": view})
            # private index: keep review signals + real identity for OUR analysis
            idx.write(json.dumps({
                "id": anon,
                "repo": u["repo"], "kind": u.get("kind", "pr"),
                "number": u.get("number"), "sha": u.get("sha"),  # private, not shown to oracle
                "additions": u.get("additions"), "deletions": u.get("deletions"),
                "changed_files": u.get("changed_files"),
                "difficulty_prior": u.get("difficulty_prior"),
                "volume_prior": u.get("volume_prior"),
                "language": u.get("language"),
                "review_signals": u.get("review_signals", {}),
            }, ensure_ascii=False) + "\n")

    with open(oracle_input_path, "w", encoding="utf-8") as f:
        json.dump({"axis": "difficulty", "units": oracle_input}, f, ensure_ascii=False)
    print(f"[blind] {len(oracle_input)} units blinded -> {blinded_dir}/ "
          f"({capped} diffs truncated at {DIFF_CAP_CHARS} chars)")
    print(f"[blind] oracle input: {oracle_input_path} | private index: {index_path}")


if __name__ == "__main__":
    import sys
    run(units_path=sys.argv[1] if len(sys.argv) > 1 else "out/validator_units.jsonl")
