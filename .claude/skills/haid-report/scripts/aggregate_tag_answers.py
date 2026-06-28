#!/usr/bin/env python3
"""Fold the haid-tag workflow's per-branch output into one validated `tag.answers.json`.

The `haid-tag` workflow returns labels grouped by branch (each `{job_id, n_targets, complete,
labels}`). This script is the programmatic concatenation step — it removes the host from ever
hand-assembling the answers file (the transcription slip this whole design exists to kill). It
validates every branch against the manifest BEFORE writing, so problems fail here with a named
job rather than vaguely at the haid read-back:

  - every branch is present and `complete`
  - each branch returns exactly its `n_targets` labels
  - the refs returned are exactly that branch's expected refs — no missing, unknown, or duplicate
  - every label's `move` / `work_type` / `impl_kind` is a valid enum value (read from the
    manifest's own schema, so this can never drift from haid)

On success it writes the flat `{"labels": [{ref, move, work_type, impl_kind, purpose}, ...]}` that
`haid tag` reads back (it expands ref → uuid and authors `tag.labels.json`). On any failure it
prints every problem and exits non-zero, writing nothing.

  python aggregate_tag_answers.py --job-dir out/jobs --answers out/jobs/tag.raw.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys

_REQUIRED = ("ref", "move", "work_type", "purpose")


def _job_id(job: dict) -> str:
    """Canonical per-branch id — matches split_tag_manifest.py's."""
    return f'{job["session_id"]}::{job["timeline"]}'


def _enums(schema: dict) -> dict:
    """Pull the per-field enum sets straight from the manifest schema (no drift from haid)."""
    props = schema["properties"]["labels"]["items"]["properties"]
    return {"move": set(props["move"]["enum"]),
            "work_type": set(props["work_type"]["enum"]),
            "impl_kind": set(props["impl_kind"]["enum"])}      # already includes null


def _validate_branch(job: dict, ans: dict | None, enums: dict, errs: list[str]) -> list[dict]:
    """Validate one branch's labels against its manifest job; collect errors, return its rows."""
    jid = _job_id(job)
    if ans is None:
        errs.append(f"{jid}: no answer returned for this branch")
        return []
    if not ans.get("complete"):
        errs.append(f"{jid}: branch marked incomplete (a tagging agent died or returned a "
                    "wrong-count/unparseable reply) — re-run this branch")
        return []

    expected_refs = [t["ref"] for t in job["targets"]]
    labels = ans.get("labels") or []
    if len(labels) != job["n_targets"]:
        errs.append(f"{jid}: expected {job['n_targets']} label(s), got {len(labels)}")
        return []

    by_ref: dict[str, dict] = {}
    for i, lab in enumerate(labels):
        missing_keys = [k for k in _REQUIRED if k not in lab]
        if missing_keys:
            errs.append(f"{jid}: label #{i} missing key(s) {missing_keys}")
            continue
        ref = lab["ref"]
        if ref in by_ref:
            errs.append(f"{jid}: duplicate ref {ref!r}")
        by_ref[ref] = lab
        if lab["move"] not in enums["move"]:
            errs.append(f"{jid}: ref {ref!r} bad move {lab['move']!r}")
        if lab["work_type"] not in enums["work_type"]:
            errs.append(f"{jid}: ref {ref!r} bad work_type {lab['work_type']!r}")
        if lab.get("impl_kind") not in enums["impl_kind"]:
            errs.append(f"{jid}: ref {ref!r} bad impl_kind {lab.get('impl_kind')!r}")
        if not isinstance(lab["purpose"], str) or not lab["purpose"].strip():
            errs.append(f"{jid}: ref {ref!r} empty purpose")

    want = set(expected_refs)
    missing = want - set(by_ref)
    unknown = set(by_ref) - want
    if missing:
        errs.append(f"{jid}: missing ref(s) {sorted(missing)}")
    if unknown:
        errs.append(f"{jid}: unknown ref(s) {sorted(unknown)} not in this branch")
    if missing or unknown:
        return []

    # Emit in the manifest's target order, keeping only the schema fields (drop any stray keys).
    rows = []
    for ref in expected_refs:
        lab = by_ref[ref]
        rows.append({"ref": ref, "move": lab["move"], "work_type": lab["work_type"],
                     "impl_kind": lab.get("impl_kind"), "purpose": lab["purpose"]})
    return rows


def _raw_groups(data) -> list[dict]:
    """Accept the workflow's bare list, or a {'jobs': [...]} / {'answers': [...]} wrapper."""
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for k in ("jobs", "answers", "groups"):
            if isinstance(data.get(k), list):
                return data[k]
    raise ValueError("answers file is not a list of branch groups")


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Validate + concatenate haid-tag workflow output into tag.answers.json.")
    ap.add_argument("--job-dir", default="out/jobs", help="dir holding tag.job.json (default: out/jobs)")
    ap.add_argument("--manifest", default=None, help="explicit manifest (default: <job-dir>/tag.job.json)")
    ap.add_argument("--answers", required=True, help="the haid-tag workflow's returned output (JSON)")
    ap.add_argument("--out", default=None, help="output path (default: <job-dir>/tag.answers.json)")
    args = ap.parse_args()

    mpath = args.manifest or os.path.join(args.job_dir, "tag.job.json")
    out_path = args.out or os.path.join(args.job_dir, "tag.answers.json")
    try:
        manifest = json.load(open(mpath, encoding="utf-8"))
        raw = _raw_groups(json.load(open(args.answers, encoding="utf-8")))
    except (OSError, json.JSONDecodeError, ValueError) as e:
        print(f"aggregate failed: {e}", file=sys.stderr)
        return 1

    enums = _enums(manifest["schema"])
    by_id = {g.get("job_id"): g for g in raw}
    seen_ids = set(by_id)
    expected_ids = {_job_id(j) for j in manifest["jobs"]}
    errs: list[str] = []
    if seen_ids - expected_ids:
        errs.append(f"answers contain unknown branch(es): {sorted(seen_ids - expected_ids)}")

    rows: list[dict] = []
    for job in manifest["jobs"]:
        rows.extend(_validate_branch(job, by_id.get(_job_id(job)), enums, errs))

    if errs:
        print(f"{len(errs)} problem(s) — wrote nothing:", file=sys.stderr)
        for e in errs:
            print(f"  - {e}", file=sys.stderr)
        return 1

    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    json.dump({"labels": rows}, open(out_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"aggregated {len(rows)} label(s) from {len(manifest['jobs'])} branch(es) -> {out_path}",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
