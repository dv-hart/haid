"""Anchor-ladder construction + Haiku-placement analysis (Experiment 2).

The production scoring mechanism: instead of mining an external label, score a diff by
placing it against a fixed ladder of reference diffs whose relative difficulty we trust
(docs/scoring-rubric.md §Calibration; docs/calibration-pilot-1.md §8).

Stage 1 (`select`): from the Opus full-sort, pick N anchor rungs at even rank
percentiles; the rest are holdouts.
Stage 2 (`placement`): after Haiku places each holdout against the ladder, measure
Spearman(Haiku anchored rung, Opus full-sort score) — does the cheap runtime model,
using only the ladder, reproduce the expensive full sort?
"""

from __future__ import annotations

import argparse
import json
import math

from . import bt_h5


def _load_index(path: str = "out/units_blinded.jsonl") -> dict:
    return {u["id"]: u for u in
            (json.loads(l) for l in open(path, encoding="utf-8") if l.strip())}


def _ident(u: dict) -> str:
    return f"{u['repo']}@{(u.get('sha') or '')[:8]}" if u.get("kind") == "commit" \
        else f"{u['repo']}#{u.get('number')}"


def select(verdicts_path: str, n_anchors: int, out_path: str) -> None:
    data = json.load(open(verdicts_path, encoding="utf-8"))
    verdicts = data["verdicts"]
    index = _load_index()
    ids = list(index)
    strength = bt_h5.fit_bradley_terry(ids, verdicts)
    latent = {i: math.log(strength[i]) for i in ids}
    consistency = bt_h5.oracle_consistency(ids, verdicts, strength)

    ranked = sorted(ids, key=lambda i: latent[i])          # easy -> hard
    N = len(ranked)
    # even rank percentiles
    idxs = sorted({round(i * (N - 1) / (n_anchors - 1)) for i in range(n_anchors)})
    anchors = [ranked[i] for i in idxs]
    holdouts = [i for i in ranked if i not in set(anchors)]

    print(f"=== Opus full-sort: {N} units, {len(verdicts)} verdicts, "
          f"consistency {consistency:.1%} ===\n")
    print(f"--- ANCHOR LADDER ({len(anchors)} rungs, easy -> hard) ---")
    for rung, a in enumerate(anchors):
        u = index[a]
        print(f"  rung {rung}: {a}  score={latent[a]:+.2f}  "
              f"[{u.get('difficulty_prior')}/{u.get('kind')}]  {_ident(u)}")

    print(f"\n--- full ranking (easy -> hard), anchors marked * ---")
    aset = set(anchors)
    for rank, i in enumerate(ranked):
        u = index[i]
        mark = "*" if i in aset else " "
        print(f" {mark}{rank:>2} {i} {latent[i]:+.2f} [{u.get('difficulty_prior')}/"
              f"{u.get('kind')}] {_ident(u)}")

    json.dump({
        "anchors": [{"id": a, "rung": r, "score": latent[a]} for r, a in enumerate(anchors)],
        "holdouts": [{"id": h, "score": latent[h]} for h in holdouts],
    }, open(out_path, "w", encoding="utf-8"))
    print(f"\nwrote {len(anchors)} anchors + {len(holdouts)} holdouts -> {out_path}")


def placement(placements_path: str, anchors_path: str) -> None:
    """Analyze Haiku's ladder placements vs the Opus full-sort score."""
    pdata = json.load(open(placements_path, encoding="utf-8"))
    placements = pdata["placements"]                       # [{holdout, anchor, winner}]
    adata = json.load(open(anchors_path, encoding="utf-8"))
    anchor_rung = {a["id"]: a["rung"] for a in adata["anchors"]}
    opus = {h["id"]: h["score"] for h in adata["holdouts"]}
    index = _load_index()

    # Haiku rung for a holdout = number of anchors it was judged HARDER than
    beats: dict[str, int] = {h: 0 for h in opus}
    seen: dict[str, int] = {h: 0 for h in opus}
    for p in placements:
        h, a, w = p["holdout"], p["anchor"], p["winner"]
        if h not in beats or a not in anchor_rung:
            continue
        seen[h] += 1
        if w == h:
            beats[h] += 1

    hold = [h for h in opus if seen.get(h, 0) > 0]
    haiku_rung = [beats[h] for h in hold]
    opus_score = [opus[h] for h in hold]
    rho = bt_h5.spearman(haiku_rung, opus_score)

    print(f"=== Haiku ladder placement vs Opus full-sort ({len(hold)} holdouts) ===")
    print(f"Spearman(Haiku anchored rung, Opus full-sort score) = {rho:+.3f}\n")
    for h in sorted(hold, key=lambda x: opus[x]):
        u = index[h]
        print(f"  {h}  haiku_rung={beats[h]}/{seen[h]}  opus={opus[h]:+.2f}  "
              f"[{u.get('difficulty_prior')}/{u.get('kind')}]  {_ident(u)}")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Anchor-ladder build + placement analysis")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("select")
    s.add_argument("--verdicts", default="out/ladder_verdicts.json")
    s.add_argument("--anchors", type=int, default=9)
    s.add_argument("--out", default="out/ladder_anchors.json")
    pl = sub.add_parser("placement")
    pl.add_argument("--placements", default="out/haiku_placements.json")
    pl.add_argument("--anchors", default="out/ladder_anchors.json")
    args = p.parse_args(argv)

    if args.cmd == "select":
        select(args.verdicts, args.anchors, args.out)
    else:
        placement(args.placements, args.anchors)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
