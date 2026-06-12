"""HAID calibration corpus harvester.

Pass-1 instrument for the calibration experiment (docs/calibration-experiment.md):
discover candidate OSS repositories *off* the popularity axis, place each with cheap
proxies on the difficulty x volume plane, and emit a reviewable manifest. Pass-2
(per-unit diff + review-signal extraction) is a separate step run only on accepted
candidates.

Zero third-party dependencies — stdlib urllib only (no `gh`, no `requests`).
"""

__all__ = ["config", "github", "hn", "classify", "manifest"]
