"""Bootstrap the metric baseline distributions — PER SCOPE.

Each metric is reported at two scopes (the memory window the one rule runs over):
  - window  → one token-rate per analysis WINDOW (a project's sessions in a time bucket).
  - session → one token-rate per SESSION.
A wider scope sees more, so the two distributions differ and must be kept separate.

From a single-author corpus we have few natural project-windows, so we bucket each project's
sessions into consecutive time windows. This is a single-author BOOTSTRAP (small N) — a
labeled placeholder until the community benchmark (ADR-0005) supplies real population data.

Usage:
  PYTHONPATH=src python scripts/build_metric_baselines.py [--bucket-days N] "<glob>" ...
"""

from __future__ import annotations

import glob
import json
import os
import sys
from collections import defaultdict

from haid.session.loader import load_session
from haid.window import build_view
from haid import metrics

METRIC_NAMES = list(metrics.METRIC_NAMES)
SCOPES = ("session", "window")
OUT = os.path.join(os.path.dirname(__file__), "..", "src", "haid", "data", "metric_baselines.json")


def _bucket_key(mtime: float, bucket_days: int) -> int:
    return int(mtime // (bucket_days * 86400))


def main(argv: list[str]) -> int:
    bucket_days = 7
    globs = []
    i = 0
    while i < len(argv):
        if argv[i] == "--bucket-days":
            bucket_days = int(argv[i + 1]); i += 2
        else:
            globs.append(argv[i]); i += 1
    files = sorted({f for g in globs for f in glob.glob(g)})
    if not files:
        print("no session files matched", file=sys.stderr)
        return 1

    # Group files into windows: (project dir, time bucket).
    windows: dict[tuple, list[str]] = defaultdict(list)
    for fp in files:
        proj = os.path.basename(os.path.dirname(fp))
        windows[(proj, _bucket_key(os.path.getmtime(fp), bucket_days))].append(fp)

    # samples[scope][metric] = [rate, ...]
    samples: dict[str, dict[str, list[float]]] = {s: {m: [] for m in METRIC_NAMES} for s in SCOPES}
    n_windows = 0
    n_sessions_seen = 0
    for (proj, _), paths in sorted(windows.items()):
        try:
            view = build_view([load_session(p) for p in paths])
            win = metrics.run_window(view)
            per_sess = metrics.run_sessions(view)
        except Exception as e:  # noqa: BLE001
            print(f"  skip window {proj}: {e}", file=sys.stderr)
            continue
        n_windows += 1
        for name, m in win.items():
            if m.total_tokens > 0:
                samples["window"][name].append(round(m.token_rate, 6))
        for sid, ms in per_sess.items():
            n_sessions_seen += 1
            for name, m in ms.items():
                if m.total_tokens > 0:
                    samples["session"][name].append(round(m.token_rate, 6))

    source = (f"bootstrap: {n_windows} windows ({bucket_days}-day buckets), "
              f"{len(files)} sessions, single author — placeholder until community benchmark (ADR-0005)")
    out: dict[str, dict] = {}
    for name in METRIC_NAMES:
        out[name] = {}
        for scope in SCOPES:
            v = sorted(samples[scope][name])
            out[name][scope] = {"rates": v, "n": len(v), "source": f"{source} [{scope} scope]"}

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=2)
    print(f"wrote {os.path.normpath(OUT)} from {n_windows} windows ({len(files)} sessions, "
          f"{n_sessions_seen} session-scope samples)")
    for name in METRIC_NAMES:
        for scope in SCOPES:
            v = sorted(samples[scope][name])
            if v:
                print(f"  {name:20s} [{scope:7s}] n={len(v):3d} "
                      f"median={v[len(v)//2]:.4f} max={max(v):.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
