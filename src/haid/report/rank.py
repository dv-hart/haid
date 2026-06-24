"""`haid rank` — see where your scores land against the community distribution.

Viewing requires nothing: the board snapshot ships as package data
(haid/data/benchmark_board.json) and is read locally — no account, no upload. `--refresh`
optionally pulls the live board.json from Pages. Comparability is strict: a row is only
ranked against peers on the SAME anchor ladders AND the same combiner config (ADR-0005),
so we filter the board to the matching bucket before computing percentiles.

The same percentile math feeds the report's "Community benchmark" section (compose.py).
"""

from __future__ import annotations

import json
import urllib.request
from importlib import resources

BOARD_RESOURCE = "benchmark_board.json"
# live snapshot from the data-only benchmark repo's Pages site (`haid rank --refresh`)
BOARD_URL = "https://dv-hart.github.io/haid-benchmark/board.json"
# the axes a row is ranked on; higher is better for all of these
RANK_AXES = ("value_overall", "achievement_total", "difficulty_rung_median",
             "cleanliness_pct_median")


def shipped_board() -> dict:
    """The board snapshot bundled with the package (empty rows if none shipped yet)."""
    try:
        raw = resources.files("haid.data").joinpath(BOARD_RESOURCE).read_text("utf-8")
    except (FileNotFoundError, ModuleNotFoundError):
        return {"schema_version": "1.1", "rows": []}
    return json.loads(raw)


def load_board(path: str) -> dict:
    return json.loads(open(path, encoding="utf-8").read())


def fetch_board(url: str, *, timeout: float = 10.0) -> dict:
    """Pull the live board.json (Pages) — the only network call, and only on --refresh."""
    with urllib.request.urlopen(url, timeout=timeout) as r:        # noqa: S310 (https url)
        return json.loads(r.read().decode("utf-8"))


def comparable_rows(board: dict, payload: dict) -> list[dict]:
    """Rows on the same ladders + combiner config as `payload` (excluding the same user)."""
    return [r for r in board.get("rows", [])
            if r.get("ladder_versions") == payload.get("ladder_versions")
            and r.get("combiner_config_hash") == payload.get("combiner_config_hash")
            and r.get("github_username") != payload.get("github_username")]


def percentile(values: list[float], x: float) -> float:
    """Fraction of `values` <= x, in [0,1]. Empty -> nan."""
    vs = [v for v in values if v is not None]
    if not vs:
        return float("nan")
    return sum(1 for v in vs if v <= x) / len(vs)


def rank_against(board: dict, payload: dict) -> dict:
    """Per-axis percentile of `payload` among comparable peers (peers exclude self)."""
    peers = comparable_rows(board, payload)
    incomparable = len(board.get("rows", [])) - len(peers) \
        - sum(1 for r in board.get("rows", [])
              if r.get("github_username") == payload.get("github_username"))
    out = {"n_peers": len(peers), "n_incomparable": max(incomparable, 0), "axes": {}}
    for axis in RANK_AXES:
        mine = payload.get(axis)
        if mine is None:
            continue
        peer_vals = [r.get(axis) for r in peers]
        # include self so a lone submitter sees 1.0, not nan
        pct = percentile(peer_vals + [mine], mine)
        out["axes"][axis] = {"you": mine, "percentile": round(pct, 3),
                             "n": len([v for v in peer_vals if v is not None]) + 1}
    return out


_LABELS = {"value_overall": "overall score", "achievement_total": "achievement",
           "difficulty_rung_median": "difficulty", "cleanliness_pct_median": "cleanliness"}


def render_rank(ranking: dict, payload: dict) -> str:
    """Standalone `haid rank` view."""
    L = [f"# Community benchmark — {payload['github_username']} / {payload['project']}", ""]
    n = ranking["n_peers"]
    if n == 0:
        L.append("No comparable peers on your ladder+combiner version yet — you'd be the "
                 "first entry in this bucket.")
    else:
        L.append(f"Ranked against {n} comparable "
                 f"{'entry' if n == 1 else 'entries'} (same ladders + combiner):")
    L.append("")
    for axis, a in ranking["axes"].items():
        pc = a["percentile"]
        pct = "n/a" if pc != pc else f"p{round(pc * 100):d}"
        L.append(f"  {_LABELS.get(axis, axis).ljust(14)} {a['you']!s:>10}   {pct}")
    if ranking["n_incomparable"]:
        L.append(f"\n  ({ranking['n_incomparable']} board entries on a different ladder/"
                 "combiner version were excluded — scores aren't comparable across versions.)")
    L.append("\n_Self-reported community board. Viewing uploads nothing; run `haid submit` "
             "to add your own row._")
    return "\n".join(L)
