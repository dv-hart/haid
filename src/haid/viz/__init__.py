"""Live visualizer wiring: extract per-session spines, assemble the window bundle, render.

Promotes the `scripts/viz_*` prototype feeders into the package and drives them off the
real pipeline output (the `haid episodes`/`haid score` grouping) instead of a hardcoded
session set. `haid viz` is the CLI entry point.
"""

from __future__ import annotations

from .assemble import assemble_bundle
from .extract import extract_session, session_stem
from .render import data_js, self_contained_html, write_data_js, write_html

__all__ = ["assemble_bundle", "extract_session", "session_stem", "data_js",
           "self_contained_html", "write_data_js", "write_html"]
