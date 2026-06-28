#!/usr/bin/env python3
"""Split haid `score` job manifests into one prompt file per model job.

`haid score --backend harness` pends with one manifest per episode per axis. There are now
THREE manifest kinds (difficulty is still pairwise; cleanliness is detect→verify):

  pairwise (difficulty)  `<ep>_difficulty.job.json`        — `comparisons[]` of pairwise prompts
  detect   (cleanliness) `<ep>_detect.detect.job.json`     — a single defect-cataloguing `prompt`
  verify   (cleanliness) `<ep>_detect.verify.job.json`     — `verifications[]` of refuter prompts

Holding all those prompts (diffs inlined) in the orchestrator's context blows up on a real
window. This splitter does the heavy I/O mechanically, outside any model's context: it writes
each job's prompt to `<job-dir>/score_split/<stem>__<k>.txt` and prints a tiny index for the
`haid-judge` workflow to fan out over. The per-kind `schema` objects ride at the TOP LEVEL of the
index (keyed by kind), NOT nested inside each manifest entry: the host model marshals the workflow
`args` and silently drops nested-in-array data, so a per-manifest schema arrives as `undefined` —
which disables structured-output forcing and degrades every judge to free-text (the diagnosed
token-blowup failure). Top-level fields survive marshalling, so the schema stays sourced from haid
(no drift) AND reaches the judge intact. There are only three kinds, so this is at most three entries.

The `stem` is the manifest filename minus `.job.json`, so the answer file the haid backend reads
back is always `<stem>.<answers-suffix>`:
  pairwise -> <stem>.verdicts.json  {"fingerprint", "winners":  [...]}
  detect   -> <stem>.findings.json  {"fingerprint", "findings": [...]}
  verify   -> <stem>.verdicts.json  {"fingerprint", "verdicts": [...]}

stdout (UTF-8, no BOM) is the exact `args` object for the haid-judge workflow:
  {"base": "<job-dir>/score_split",
   "schemas": {"pairwise": {...}, "detect": {...}, "verify": {...}},   # by kind, TOP-LEVEL
   "manifests": [{"manifest": "<stem>", "kind": "...", "n": <count>, "fingerprint": "<fp>"}, ...]}
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys

_SUFFIX = ".job.json"


def classify(d: object):
    """Return (kind, prompts) for a score job manifest, or (None, None) if it isn't one.

    pairwise = inlined comparisons + subject + fingerprint; detect/verify = the haid detect
    backend's two-phase manifests (task field)."""
    if not isinstance(d, dict) or "fingerprint" not in d:
        return None, None
    if "comparisons" in d and "subject" in d:
        return "pairwise", [c["prompt"] for c in d["comparisons"]]
    if d.get("task") == "detect_defects" and "prompt" in d:
        return "detect", [d["prompt"]]
    if d.get("task") == "verify_defects" and "verifications" in d:
        return "verify", [v["prompt"] for v in d["verifications"]]
    return None, None


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Split haid score manifests (pairwise / detect / verify) into prompt files.")
    ap.add_argument("--job-dir", default="out/jobs",
                    help="dir holding *.job.json (default: out/jobs)")
    ap.add_argument("manifests", nargs="*",
                    help="explicit manifest paths (default: auto-discover in --job-dir)")
    args = ap.parse_args()

    paths = args.manifests or sorted(glob.glob(os.path.join(args.job_dir, "*" + _SUFFIX)))
    out_dir = os.path.join(args.job_dir, "score_split")
    os.makedirs(out_dir, exist_ok=True)
    # Clear stale splits so a regenerated (or smaller) manifest can't leave orphan files.
    for stale in glob.glob(os.path.join(out_dir, "*.txt")):
        os.remove(stale)

    index = []
    schemas: dict = {}                                # per-kind, sourced from haid's manifests
    for p in paths:
        try:
            with open(p, encoding="utf-8") as fh:
                d = json.load(fh)
        except (OSError, json.JSONDecodeError) as e:
            print(f"skip {p}: {e}", file=sys.stderr)
            continue
        kind, prompts = classify(d)
        if kind is None:
            continue
        name = os.path.basename(p)
        stem = name[:-len(_SUFFIX)] if name.endswith(_SUFFIX) else os.path.splitext(name)[0]
        for k, prompt in enumerate(prompts):
            with open(os.path.join(out_dir, f"{stem}__{k}.txt"), "w", encoding="utf-8") as f:
                f.write(prompt)
        if d.get("schema") is not None:
            schemas.setdefault(kind, d["schema"])     # one schema per kind, lifted to top level
        index.append({"manifest": stem, "kind": kind, "n": len(prompts),
                      "fingerprint": d["fingerprint"]})

    if not index:
        print(f"no score manifests found in {args.job_dir} "
              f"(inspected {len(paths)} *{_SUFFIX})", file=sys.stderr)
        return 1

    # Structured-output forcing depends on a real schema reaching every judge; a kind with no schema
    # would silently fall back to free-text. Refuse loudly here rather than let that happen downstream.
    missing = sorted({m["kind"] for m in index} - set(schemas))
    if missing:
        print(f"no 'schema' on manifest(s) of kind {missing} — structured-output forcing would be "
              "disabled; aborting (regenerate the manifests with `haid score`)", file=sys.stderr)
        return 1

    total = sum(m["n"] for m in index)
    by_kind = {}
    for m in index:
        by_kind[m["kind"]] = by_kind.get(m["kind"], 0) + 1
    print(f"split {total} job(s) from {len(index)} manifest(s) "
          f"({by_kind}) into {out_dir}", file=sys.stderr)
    # Forward-slash base so the path strings work whether judges run on Windows or WSL.
    json.dump({"base": out_dir.replace(os.sep, "/"), "schemas": schemas, "manifests": index},
              sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
