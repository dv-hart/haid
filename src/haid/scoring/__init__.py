"""Relative achievement scoring: place a session diff against fixed reference ladders.

achievement = f(volume[surviving LOC], difficulty, cleanliness)   (originality dropped)

- volume.py    — deterministic weighted surviving-LOC + structural counts (no model).
- placement.py — relative placement of a diff against a locked anchor ladder (per axis).
- compare.py   — pluggable comparison backend (ReplayBackend for validation; the live
                 HarnessBackend delegates each comparison to a host-agent subagent).
- anchors.py   — load the locked anchor ladders + their reference diff texts.
- cost.py      — the denominator: normalized-token cost (relative type/tier weights, no $).

See docs/scoring-rubric.md, docs/difficulty-ladder.md, docs/cleanliness-ladder.md.
"""
