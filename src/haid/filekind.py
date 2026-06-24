"""File-kind classification for the scorer.

Two jobs:
  - `file_priority` — code-files-first ordering, so a session diff is reassembled the
    same way the reference anchors were (placement parity).
  - `file_kind` + `KIND_WEIGHT` — weighted kinds for the volume measure: hand-written
    logic counts more than config, generated/lockfile content counts ~nothing, tests are
    tracked but down-weighted (they are achievement, but not product logic).

Ported from the calibration harness's filekind helper (now on the `archive/experiments`
branch) and extended with the weight tiers and test detection the volume measure needs.
Kept stdlib-only.
"""

from __future__ import annotations

CODE_EXT = {".rs", ".go", ".py", ".ts", ".tsx", ".js", ".jsx", ".c", ".h", ".hpp",
            ".cc", ".cpp", ".cxx", ".zig", ".java", ".kt", ".rb", ".cs", ".swift",
            ".scala", ".m", ".mm", ".ml", ".hs", ".ex", ".exs", ".php", ".lua",
            ".sh", ".pl", ".r", ".jl", ".dart", ".sql", ".proto", ".cu", ".vue"}
CONFIG_EXT = {".toml", ".yaml", ".yml", ".json", ".ini", ".cfg", ".conf", ".xml",
              ".gradle", ".cmake", ".mk", ".tf", ".dockerfile", ".env", ".properties"}
DOCGEN_EXT = {".md", ".markdown", ".rst", ".txt", ".adoc", ".lock", ".sum", ".snap",
              ".golden", ".svg", ".png", ".jpg", ".jpeg", ".gif", ".csv", ".html",
              ".min.js", ".map"}

# Lockfiles / vendored / generated trees contribute ~no achievement volume.
GENERATED_NAMES = {"package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock",
                   "cargo.lock", "go.sum", "composer.lock", "gemfile.lock",
                   "flake.lock"}
GENERATED_DIR_HINTS = ("/dist/", "/build/", "/vendor/", "/node_modules/",
                       "/generated/", "/__generated__/", "/.min.", "/migrations/")

# Volume weight per kind (multiplies surviving added LOC).
KIND_WEIGHT = {
    "logic": 1.0,       # hand-written implementation
    "config": 0.4,      # config/markup/glue
    "test": 0.5,        # real work, but not product logic
    "docs": 0.1,        # prose
    "generated": 0.0,   # lockfiles, vendored, build output
    "unknown": 0.6,     # unclassified — credited cautiously below logic
}


def _ext(path: str) -> str:
    tail = path.rsplit("/", 1)[-1].lower()
    return "." + tail.rsplit(".", 1)[-1] if "." in tail else ""


def _basename(path: str) -> str:
    return path.rsplit("/", 1)[-1].lower()


def file_priority(path: str) -> int:
    """0=code, 1=config, 2=unknown, 3=docs/generated (lower shown first)."""
    p = path.lower()
    ext = _ext(p)
    if ext in CODE_EXT:
        return 0
    if ext in CONFIG_EXT:
        return 1
    if ext in DOCGEN_EXT or p.startswith("docs/") or "/docs/" in p or "/examples/" in p:
        return 3
    return 2


def is_test(path: str) -> bool:
    p = path.lower()
    base = _basename(p)
    return (
        "/test/" in p or "/tests/" in p or "/__tests__/" in p or "/spec/" in p
        or base.startswith("test_") or base.startswith("test-")
        or "_test." in base or ".test." in base or ".spec." in base
        or base.endswith("_test.go") or base.endswith("test.py")
    )


def is_generated(path: str) -> bool:
    p = path.lower()
    if _basename(p) in GENERATED_NAMES:
        return True
    if _ext(p) in {".lock", ".sum", ".min.js", ".map", ".snap", ".golden"}:
        return True
    return any(h in p for h in GENERATED_DIR_HINTS)


def file_kind(path: str) -> str:
    """Weighted kind for the volume measure: logic|config|test|docs|generated|unknown.

    Order matters: generated and test classifications override the extension tier
    (a generated .json is not 'config'; a test .py is not 'logic')."""
    if is_generated(path):
        return "generated"
    if is_test(path):
        return "test"
    prio = file_priority(path)
    return {0: "logic", 1: "config", 3: "docs"}.get(prio, "unknown")


def is_code(path: str) -> bool:
    return file_priority(path) == 0
