#!/usr/bin/env python3
"""Split haid `score` manifests into one prompt file per pairwise comparison.

`haid score --backend harness` pends with one manifest per episode/axis
(`<job-dir>/<episode>_<axis>.job.json`), each holding a `comparisons[]` array of
fully-built pairwise prompts (diffs inlined). Holding all of those prompts in the
orchestrator's context — or marshalling them through a Workflow's `args` — is what blows up
on a real window (~28 manifests x ~10 comparisons = ~280 large prompts).

This splitter does the heavy I/O mechanically, entirely outside any model's context: it
writes each comparison's `prompt` to `<job-dir>/score_split/<manifest>__<k>.txt` and prints
a tiny index to stdout for the `haid-judge` workflow to fan out over. The orchestrator only
ever handles the small index; the diffs live on disk and are read by the spawned judges.

stdout (UTF-8, no BOM) is the exact `args` object for the haid-judge workflow:
  {"base": "<job-dir>/score_split",
   "manifests": [{"manifest": "<stem>", "n": <count>, "fingerprint": "<fp>"}, ...]}
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys

_SUFFIX = ".job.json"


def is_score_manifest(d: object) -> bool:
    """Score manifests carry inlined pairwise comparisons + the subject diff + a fingerprint;
    tag/episodes/why/compose manifests have none of that signature."""
    return (
        isinstance(d, dict)
        and "comparisons" in d
        and "subject" in d
        and "fingerprint" in d
    )


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Split haid score manifests into per-comparison prompt files."
    )
    ap.add_argument(
        "--job-dir", default="out/jobs",
        help="dir holding *.job.json (default: out/jobs)",
    )
    ap.add_argument(
        "manifests", nargs="*",
        help="explicit manifest paths (default: auto-discover score manifests in --job-dir)",
    )
    args = ap.parse_args()

    paths = args.manifests or sorted(glob.glob(os.path.join(args.job_dir, "*" + _SUFFIX)))
    out_dir = os.path.join(args.job_dir, "score_split")
    os.makedirs(out_dir, exist_ok=True)
    # Clear stale splits so a regenerated (or smaller) manifest can't leave orphan files that
    # the workflow would otherwise judge as if they were current.
    for stale in glob.glob(os.path.join(out_dir, "*.txt")):
        os.remove(stale)

    index = []
    for p in paths:
        try:
            with open(p, encoding="utf-8") as fh:
                d = json.load(fh)
        except (OSError, json.JSONDecodeError) as e:
            print(f"skip {p}: {e}", file=sys.stderr)
            continue
        if not is_score_manifest(d):
            continue
        name = os.path.basename(p)
        stem = name[: -len(_SUFFIX)] if name.endswith(_SUFFIX) else os.path.splitext(name)[0]
        comps = d["comparisons"]
        for k, c in enumerate(comps):
            with open(os.path.join(out_dir, f"{stem}__{k}.txt"), "w", encoding="utf-8") as f:
                f.write(c["prompt"])
        index.append({"manifest": stem, "n": len(comps), "fingerprint": d["fingerprint"]})

    if not index:
        print(
            f"no score manifests found in {args.job_dir} "
            f"(inspected {len(paths)} *{_SUFFIX})",
            file=sys.stderr,
        )
        return 1

    total = sum(m["n"] for m in index)
    print(
        f"split {total} comparison(s) from {len(index)} manifest(s) into {out_dir}",
        file=sys.stderr,
    )
    # Forward-slash base so the path strings work whether the judges run on Windows or WSL.
    json.dump(
        {"base": out_dir.replace(os.sep, "/"), "manifests": index},
        sys.stdout,
        ensure_ascii=False,
    )
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
