"""Guard: the haid version is declared in four places — keep them in lockstep.

The PyPI package version (pyproject.toml `[project].version` + src/haid/__init__.py
`__version__`) and the Claude Code plugin version (.claude-plugin/plugin.json +
marketplace.json) are SEPARATE tracks that must agree. If they drift, `/plugin update`
reports a stale version while pip serves a newer one — exactly what happened when 0.0.4/0.0.5
bumped only the Python files and the plugin stayed at 0.0.3. On a tagged release, all four
must also equal the tag.

  python scripts/check_versions.py                 # do all declarations agree?
  python scripts/check_versions.py --expect 0.0.5  # ...and equal this (the release tag)?
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def _pyproject_version() -> str:
    # regex (not tomllib) so the check runs on 3.10 too; `version =` appears once, in [project]
    m = re.search(r'(?m)^version\s*=\s*"([^"]+)"', _read("pyproject.toml"))
    if not m:
        raise ValueError("no [project].version in pyproject.toml")
    return m.group(1)


def _init_version() -> str:
    m = re.search(r'__version__\s*=\s*"([^"]+)"', _read("src/haid/__init__.py"))
    if not m:
        raise ValueError("no __version__ in src/haid/__init__.py")
    return m.group(1)


def collect() -> dict:
    """label -> declared version, across every file that pins it."""
    out = {
        "pyproject.toml": _pyproject_version(),
        "src/haid/__init__.py": _init_version(),
        ".claude-plugin/plugin.json": json.loads(_read(".claude-plugin/plugin.json"))["version"],
    }
    mk = json.loads(_read(".claude-plugin/marketplace.json"))
    for p in mk.get("plugins", []):
        out[f".claude-plugin/marketplace.json[{p.get('name')}]"] = p.get("version")
    return out


def _table(vs: dict) -> str:
    w = max(len(k) for k in vs)
    return "\n".join(f"      {k.ljust(w)}  {v}" for k, v in vs.items())


def check(expect: str | None = None) -> list[str]:
    vs = collect()
    problems = []
    if len(set(vs.values())) > 1:
        problems.append("versions disagree across files:\n" + _table(vs))
    if expect is not None:
        off = {k: v for k, v in vs.items() if v != expect}
        if off:
            problems.append(f"these do not equal the expected version {expect!r}:\n" + _table(off))
    return problems


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--expect", help="also require every declaration equals this (e.g. a tag)")
    args = ap.parse_args(argv)

    problems = check(args.expect)
    if problems:
        print("version guard FAILED:", file=sys.stderr)
        for p in problems:
            print("  - " + p, file=sys.stderr)
        print("\nFix: set the SAME version in pyproject.toml, src/haid/__init__.py, and\n"
              ".claude-plugin/{plugin,marketplace}.json - the PyPI and plugin tracks move\n"
              "together. On a release, that version must also match the git tag.",
              file=sys.stderr)
        return 1
    vs = collect()
    print(f"version guard OK: all {len(vs)} declarations = {next(iter(set(vs.values())))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
