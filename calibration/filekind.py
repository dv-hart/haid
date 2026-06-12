"""File-kind classification, shared by extraction (code-substance filter) and
blinding (code-first ordering).

Rubric basis: hand-written logic > config > generated/docs. A *code*-difficulty
oracle should judge implementation, so doc/RFC-only PRs are filtered out upstream and
docs are shown last when they do appear.
"""

from __future__ import annotations

CODE_EXT = {".rs", ".go", ".py", ".ts", ".tsx", ".js", ".jsx", ".c", ".h", ".hpp",
            ".cc", ".cpp", ".cxx", ".zig", ".java", ".kt", ".rb", ".cs", ".swift",
            ".scala", ".m", ".mm", ".ml", ".hs", ".ex", ".exs", ".php", ".lua",
            ".sh", ".pl", ".r", ".jl", ".dart", ".sql", ".proto", ".cu", ".vue"}
CONFIG_EXT = {".toml", ".yaml", ".yml", ".json", ".ini", ".cfg", ".conf", ".xml",
              ".gradle", ".cmake", ".mk", ".tf", ".dockerfile"}
DOCGEN_EXT = {".md", ".markdown", ".rst", ".txt", ".adoc", ".lock", ".sum", ".snap",
              ".golden", ".svg", ".png", ".jpg", ".csv", ".html", ".min.js"}


def _ext(path: str) -> str:
    tail = path.rsplit("/", 1)[-1].lower()
    return "." + tail.rsplit(".", 1)[-1] if "." in tail else ""


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


def is_code(path: str) -> bool:
    return file_priority(path) == 0
