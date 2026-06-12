"""Volume measure: fixture unit tests + the orthogonality sanity check.

  - Hand-built diffs exercise the kind weighting (lockfile bump ~0; logic high; tests
    down-weighted).
  - Across the 55 blinded calibration units, weighted volume must stay roughly
    DECOUPLED from difficulty (the design requires volume ⊥ difficulty so the combined
    achievement score doesn't double-count). We assert |Spearman| is low.

Run: PYTHONPATH=src python tests/scoring/test_volume.py   (or pytest tests/scoring/)
"""

from __future__ import annotations

import json
import math
import os
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(_ROOT, "src"))
sys.path.insert(0, _ROOT)

from calibration.bt_h5 import fit_bradley_terry, spearman
from haid.scoring import volume

BLINDED = "out/blinded"

_LOCKFILE_DIFF = """diff --git a/package-lock.json b/package-lock.json
--- a/package-lock.json
+++ b/package-lock.json
@@ -1,3 +1,3 @@
-  "version": "1.0.0",
+  "version": "1.0.1",
+  "integrity": "sha512-aaaaaa",
"""

_LOGIC_DIFF = """diff --git a/src/engine.py b/src/engine.py
--- a/src/engine.py
+++ b/src/engine.py
@@ -1,2 +1,8 @@
 import os
+def retry(fn, n):
+    for i in range(n):
+        try:
+            return fn()
+        except OSError:
+            continue
+    raise RuntimeError("exhausted")
"""

_TEST_DIFF = """diff --git a/tests/test_engine.py b/tests/test_engine.py
--- a/tests/test_engine.py
+++ b/tests/test_engine.py
@@ -0,0 +1,3 @@
+def test_retry():
+    assert retry(lambda: 1, 3) == 1
+    assert True
"""


def test_lockfile_is_zero_volume():
    r = volume.measure(_LOCKFILE_DIFF)
    print("[lockfile]", r.summary())
    assert r.weighted_loc == 0.0
    assert r.by_kind["generated"]["added"] == 2


def test_logic_outweighs_tests():
    logic = volume.measure(_LOGIC_DIFF)
    tests = volume.measure(_TEST_DIFF)
    print("[logic]", logic.summary())
    print("[test ]", tests.summary())
    assert logic.weighted_loc > tests.weighted_loc      # logic weight 1.0 > test 0.5
    assert logic.functions_added >= 1
    assert tests.tests_touched == 1 and tests.functions_added == 0


def test_volume_orthogonal_to_difficulty():
    """Weighted volume should not track difficulty (design: separate axes)."""
    # use the full 55-unit Opus sort as the difficulty reference
    full = json.load(open("out/ladder_verdicts.json", encoding="utf-8"))["verdicts"]
    ids = sorted({u[:-5] for u in os.listdir(BLINDED) if u.endswith(".diff")})
    strength = fit_bradley_terry(ids, full)
    diff_latent = [math.log(strength[i]) for i in ids]
    vols = [volume.measure_file(os.path.join(BLINDED, f"{i}.diff")).weighted_loc
            for i in ids]
    rho = spearman(vols, diff_latent)
    print(f"[orthogonality] Spearman(weighted_volume, difficulty) = {rho:+.3f} "
          f"(n={len(ids)})")
    # volume is a size measure on the (capped) diff; difficulty is size-decoupled.
    # Anything well under a strong correlation confirms they are distinct axes.
    assert abs(rho) < 0.5, rho


if __name__ == "__main__":
    test_lockfile_is_zero_volume()
    test_logic_outweighs_tests()
    test_volume_orthogonal_to_difficulty()
    print("\nALL VOLUME TESTS PASSED")
