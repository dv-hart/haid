"""Write the visualizer artifacts: a dev `data.js` and a self-contained HTML.

The self-contained HTML inlines the CSS, the JS, and the data bundle into one file with no
relative-path dependencies, so it opens straight from `file://` (or ships in `out/report/`)
the way the rest of the pipeline ships JSON. Assets are package data under `viz/assets/`,
so they survive `pip install`.
"""

from __future__ import annotations

import json
from importlib import resources
from pathlib import Path

_ASSETS = "haid.viz.assets"


def _asset(name: str) -> str:
    return resources.files(_ASSETS).joinpath(name).read_text(encoding="utf-8")


def data_js(bundle: dict) -> str:
    """The `window.HAID_DATA = {...};` script body (the dev prototype loads this via src)."""
    return "window.HAID_DATA = " + json.dumps(bundle, indent=1) + ";\n"


def self_contained_html(bundle: dict) -> str:
    """bus.html with its <link>/<script src> replaced by inlined CSS, data, and JS."""
    html = _asset("bus.html")
    css = _asset("bus.css")
    js = _asset("bus.js")
    data = data_js(bundle)
    html = html.replace('<link rel="stylesheet" href="bus.css">',
                         f"<style>\n{css}\n</style>")
    html = html.replace('<script src="data.js"></script>',
                        f"<script>\n{data}</script>")
    html = html.replace('<script src="bus.js"></script>',
                        f"<script>\n{js}\n</script>")
    return html


def write_html(bundle: dict, dest: str | Path) -> Path:
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(self_contained_html(bundle), encoding="utf-8")
    return dest


def write_data_js(bundle: dict, dest: str | Path) -> Path:
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(data_js(bundle), encoding="utf-8")
    return dest
