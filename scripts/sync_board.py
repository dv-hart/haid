"""Refresh the bundled board snapshot from the data-only benchmark repo — SAFELY.

The package ships src/haid/data/benchmark_board.json so `haid report`/`haid rank` can show
community context with zero network on the default path. This script refreshes it from the
benchmark repo. The hard requirement: **the sync can carry nothing but current benchmark
data** — no code, no free-form fields, even if the benchmark repo were fully compromised.

How that's guaranteed:
  - It only ever does an HTTP GET of a single data file (board.json). It never checks out
    the benchmark repo and never executes anything from it.
  - The downloaded board is run through `benchmark.sanitize_board` (THIS package's trusted
    code): every row must pass the leak guard and recompute its content_hash, and is then
    projected onto a field WHITELIST (known scalars only). Anything else is dropped or, if
    structurally wrong, the whole sync aborts.
  - The result is re-serialized canonically — the bytes written are built here, not copied.

Run locally or from the package repo's sync workflow (which then opens a reviewable PR):
  PYTHONPATH=src python scripts/sync_board.py [BOARD_URL]
"""

from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

from haid.report import benchmark

# the board is a Pages-only artifact in the data repo (never committed to main), so the
# sync reads it from Pages. sanitize_board re-verifies every row, and the result lands in a
# reviewable PR in the package repo — so the source needn't be a pinned git ref.
DEFAULT_URL = "https://dv-hart.github.io/haid-benchmark/board.json"
OUT = Path(__file__).resolve().parent.parent / "src" / "haid" / "data" / "benchmark_board.json"


def fetch(url: str, *, timeout: float = 20.0) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as r:        # noqa: S310 (https)
        return json.loads(r.read().decode("utf-8"))


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    url = argv[0] if argv else DEFAULT_URL
    try:
        board = fetch(url)
        clean = benchmark.sanitize_board(board)     # the security boundary — trusted code
    except benchmark.SubmissionRejected as e:
        print(f"refusing to sync: board failed sanitization: {e}", file=sys.stderr)
        return 1
    except (OSError, ValueError) as e:
        print(f"refusing to sync: could not fetch/parse board: {e}", file=sys.stderr)
        return 1
    OUT.write_text(json.dumps(clean, indent=1) + "\n", encoding="utf-8")
    print(f"synced {clean['n_entries']} rows -> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
