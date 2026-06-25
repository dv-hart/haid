"""Single-command release: stamp the version into every file that declares it, then commit
and tag — so a release can never half-bump (the drift that left `/plugin` at 0.0.3 while pip
served 0.0.5).

The version lives in four files: pyproject.toml, src/haid/__init__.py, and
.claude-plugin/{plugin,marketplace}.json (the PyPI track + the Claude Code plugin track,
which can't be dynamic because an external tool reads the literal JSON). This script is the
ONLY supported way to change it; scripts/check_versions.py then verifies the result (and runs
in CI, pre-commit, and publish.yml).

  python scripts/release.py 0.0.6              # stamp the 4 files, commit, tag v0.0.6
  python scripts/release.py 0.0.6 --dry-run    # show the changes, touch nothing
  python scripts/release.py 0.0.6 --no-tag     # stamp + commit, but don't create the tag
Then:  git push origin main --follow-tags      # the tag push triggers publish.yml -> PyPI
"""

from __future__ import annotations

import argparse
import importlib.util
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SEMVER = re.compile(r"^\d+\.\d+\.\d+([.-][0-9A-Za-z.]+)?$")

# (path, regex capturing (prefix)(version)(suffix)) — every version literal we stamp
EDITS = [
    ("pyproject.toml", re.compile(r'(?m)^(version\s*=\s*")([^"]+)(")')),
    ("src/haid/__init__.py", re.compile(r'(__version__\s*=\s*")([^"]+)(")')),
    (".claude-plugin/plugin.json", re.compile(r'("version"\s*:\s*")([^"]+)(")')),
    (".claude-plugin/marketplace.json", re.compile(r'("version"\s*:\s*")([^"]+)(")')),
]


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True,
                          check=True).stdout.strip()


def _load_check_versions():
    p = ROOT / "scripts" / "check_versions.py"
    spec = importlib.util.spec_from_file_location("check_versions", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def stamp(version: str, *, dry: bool) -> None:
    for rel, rx in EDITS:
        p = ROOT / rel
        text = p.read_text(encoding="utf-8")
        olds = [m.group(2) for m in rx.finditer(text)]
        if not olds:
            raise SystemExit(f"refusing to release: no version field in {rel}")
        new = rx.sub(lambda m: m.group(1) + version + m.group(3), text)
        arrow = ", ".join(sorted(set(olds)))
        print(f"  {'(dry) ' if dry else ''}{rel:38} {arrow} -> {version}")
        if not dry:
            p.write_text(new, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("version", help="the new version, e.g. 0.0.6")
    ap.add_argument("--dry-run", action="store_true", help="print changes, write nothing")
    ap.add_argument("--no-tag", action="store_true", help="commit but do not create the tag")
    ap.add_argument("--allow-dirty", action="store_true",
                    help="allow a dirty working tree (the release commit won't be clean)")
    ap.add_argument("-m", "--message", help="extra line appended to the commit/tag message")
    args = ap.parse_args(argv)

    v = args.version.lstrip("v")
    if not SEMVER.match(v):
        raise SystemExit(f"not a valid version: {args.version!r}")

    tag = f"v{v}"
    existing = _git("tag", "--list", tag)
    if existing:
        raise SystemExit(f"tag {tag} already exists")

    dirty = _git("status", "--porcelain")
    if dirty and not (args.dry_run or args.allow_dirty):
        raise SystemExit("working tree is dirty; commit/stash first (or pass --allow-dirty)")

    print(f"Release {tag}:")
    stamp(v, dry=args.dry_run)

    # verify the stamp landed everywhere and equals v (the same gate CI/publish run)
    cv = _load_check_versions()
    problems = cv.check(None if args.dry_run else v)
    if problems and not args.dry_run:
        raise SystemExit("version guard failed after stamping:\n" + "\n".join(problems))

    if args.dry_run:
        print("dry run - nothing written, no commit/tag.")
        return 0

    msg = f"Release {tag}" + (f": {args.message}" if args.message else "")
    _git("add", *[rel for rel, _ in EDITS])
    _git("commit", "-m", msg)
    if not args.no_tag:
        _git("tag", "-a", tag, "-m", msg)
        print(f"\nCommitted and tagged {tag}.")
    else:
        print("\nCommitted (no tag).")
    print("Next:  git push origin main --follow-tags   # triggers publish.yml -> PyPI")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
