#!/usr/bin/env python3
"""Split the haid `tag` job manifest into one prompt file per session branch.

`haid tag --backend harness` pends with `out/jobs/tag.job.json`: `jobs[]`, one per session
branch, each carrying that branch's whole transcript inlined in `prompt`. Holding all those
transcripts in the orchestrator's context blows up on a real window (the ~800KB-too-big-to-relay
failure this design exists to avoid). This splitter does the heavy I/O mechanically, outside any
model's context — exactly like `split_score_manifests.py` does for scoring:

  - writes each job's `prompt` to `<job-dir>/tag_split/<stem>.txt`
  - prints a tiny index for the `haid-tag` workflow to fan out over

The agent only ever echoes the short `ref` printed in each CLASSIFY marker; the full uuid stays
in the manifest (`targets[].{uuid,ref}`) and is reattached by `aggregate_tag_answers.py` +
the haid CLI. So no model ever copies a 36-char id.

`job_id` is the canonical `<session_id>::<timeline>` (unique per branch); `stem` is that with
filename-hostile chars folded to `-`. stdout (UTF-8, no BOM) is the exact `args` object for the
workflow:
  {"base": "<job-dir>/tag_split",
   "schema": {...the labels-array schema...},
   "jobs": [{"job_id": "...", "n_targets": N, "path": "<base>/<stem>.txt"}, ...]}
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys


def _job_id(job: dict) -> str:
    """Canonical per-branch id — matches the one aggregate_tag_answers.py derives."""
    return f'{job["session_id"]}::{job["timeline"]}'


def _stem(job_id: str) -> str:
    # `<sid>::<timeline>` → `<sid>__<timeline>`, then fold any remaining filename-hostile chars.
    return re.sub(r"[^A-Za-z0-9._-]", "-", job_id.replace("::", "__"))


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Split the haid tag manifest into one transcript-prompt file per branch.")
    ap.add_argument("--job-dir", default="out/jobs",
                    help="dir holding tag.job.json (default: out/jobs)")
    ap.add_argument("--manifest", default=None,
                    help="explicit manifest path (default: <job-dir>/tag.job.json)")
    args = ap.parse_args()

    mpath = args.manifest or os.path.join(args.job_dir, "tag.job.json")
    try:
        with open(mpath, encoding="utf-8") as fh:
            manifest = json.load(fh)
    except (OSError, json.JSONDecodeError) as e:
        print(f"cannot read tag manifest {mpath}: {e}", file=sys.stderr)
        return 1
    if manifest.get("task") != "classify_messages" or "jobs" not in manifest:
        print(f"{mpath} is not a tag manifest (task != classify_messages)", file=sys.stderr)
        return 1

    out_dir = os.path.join(args.job_dir, "tag_split")
    os.makedirs(out_dir, exist_ok=True)
    # Clear stale splits so a regenerated (or smaller) window can't leave orphan prompt files.
    for stale in os.listdir(out_dir):
        if stale.endswith(".txt"):
            os.remove(os.path.join(out_dir, stale))

    index = []
    for job in manifest["jobs"]:
        jid = _job_id(job)
        path = os.path.join(out_dir, f"{_stem(jid)}.txt")
        with open(path, "w", encoding="utf-8") as f:
            f.write(job["prompt"])
        index.append({"job_id": jid, "n_targets": job["n_targets"],
                      "path": path.replace(os.sep, "/")})

    if not index:
        print(f"no jobs in {mpath}", file=sys.stderr)
        return 1

    total = sum(j["n_targets"] for j in index)
    print(f"split {len(index)} branch job(s) ({total} message(s) to label) into {out_dir}",
          file=sys.stderr)
    # Forward-slash base so paths work whether the workflow's agents run on Windows or WSL.
    json.dump({"base": out_dir.replace(os.sep, "/"),
               "schema": manifest["schema"], "jobs": index},
              sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
