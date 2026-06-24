"""Volume measure: fixture unit tests for the kind weighting.

Hand-built diffs exercise the kind weighting (lockfile bump ~0; logic high; tests
down-weighted). The volume ⊥ difficulty orthogonality check lived here too, but it
depends on the calibration corpus and Bradley-Terry fit — it now lives with the
calibration harness on the `archive/experiments` branch.

Run: PYTHONPATH=src python tests/scoring/test_volume.py   (or pytest tests/scoring/)
"""

from __future__ import annotations

import os
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(_ROOT, "src"))

from haid.scoring import volume

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


if __name__ == "__main__":
    test_lockfile_is_zero_volume()
    test_logic_outweighs_tests()
    print("\nALL VOLUME TESTS PASSED")
