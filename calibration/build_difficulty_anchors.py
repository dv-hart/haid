"""Build the canonical difficulty anchor file from the DENSE all-pairs verdicts.

The difficulty ladder's locked order is the *dense* all-pairs comparison
(docs/difficulty-ladder.md), not the older sparse k=3 sort that produced
out/ladder_anchors.json (which mis-placed U13 at rung 7). Cleanliness already has a
canonical out/cleanliness_anchors.json; this produces the difficulty equivalent so the
runtime scorer (src/haid) has a single machine-readable source of truth per axis.

Fit Bradley-Terry over out/anchor_dense_verdicts.json (the 9 anchors, all-pairs,
counterbalanced) -> latent score -> rungs (easy=0 .. hard=N-1). The result is asserted
against the locked rung order in docs/difficulty-ladder.md before writing.

Run from repo root:  python -m calibration.build_difficulty_anchors
"""

from __future__ import annotations

import json
import math

from . import bt_h5

DENSE_VERDICTS = "out/anchor_dense_verdicts.json"
OUT = "out/difficulty_anchors.json"

# Locked rung order from docs/difficulty-ladder.md (dense all-pairs; U13 at rung 5).
# easy -> hard. The build asserts the fitted order reproduces this before writing.
LOCKED_ORDER = ["U37", "U39", "U19", "U11", "U24", "U13", "U10", "U18", "U50"]


def build() -> dict:
    data = json.load(open(DENSE_VERDICTS, encoding="utf-8"))
    ids = list(data["anchors"])
    verdicts = data["verdicts"]

    strength = bt_h5.fit_bradley_terry(ids, verdicts)
    latent = {i: math.log(strength[i]) for i in ids}
    consistency = bt_h5.oracle_consistency(ids, verdicts, strength)

    ranked = sorted(ids, key=lambda i: latent[i])          # easy -> hard
    if ranked != LOCKED_ORDER:
        raise SystemExit(
            "fitted order does not match the locked difficulty-ladder.md order:\n"
            f"  fitted: {ranked}\n  locked: {LOCKED_ORDER}\n"
            "Reconcile before writing — the doc table is the source of truth.")

    anchors = [{"id": i, "rung": rung, "score": latent[i]}
               for rung, i in enumerate(ranked)]
    return {
        "axis": "difficulty",
        "method": f"dense all-pairs ({len(verdicts)} verdicts, "
                  f"{consistency:.1%} consistent)",
        "orientation": "ascending = MORE difficult (rung 0 = easiest, rung 8 = hardest)",
        "source": DENSE_VERDICTS,
        "anchors": anchors,
    }


def main() -> int:
    result = build()
    json.dump(result, open(OUT, "w", encoding="utf-8"), indent=1)
    print(f"=== {result['method']} ===")
    for a in result["anchors"]:
        print(f"  rung {a['rung']}: {a['id']}  score={a['score']:+.3f}")
    print(f"\nwrote {len(result['anchors'])} anchors -> {OUT}")
    print("order matches docs/difficulty-ladder.md locked ladder [OK]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
