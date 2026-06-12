"""SQLite parsed-transcript cache (ADR-0002 hybrid: persist parse, build graph in memory).

Multi-session is the default, so re-parsing every transcript on every run is wasteful —
especially the multi-MB ones. This caches the parse output keyed by (path, content-hash):
unchanged files return instantly; changed files re-parse and overwrite.

Zero user friction: `sqlite3` is in the Python stdlib (no install, no server) and the DB is
a single file under `~/.haid/` by default. Actively-written sessions (partial trailing
line) are NOT cached — they would go stale within the same run.

NOTE: full-file content-hash keying re-parses a growing active session in full each run.
Incremental "parse only appended bytes" is a later refinement (plans/phase1-build.md Step 1).
Stdlib only.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from pathlib import Path

from .parse import ParseResult
from . import records as rec

_SCHEMA = """
CREATE TABLE IF NOT EXISTS parsed_sessions (
    path        TEXT NOT NULL,
    file_hash   TEXT NOT NULL,
    payload     TEXT NOT NULL,   -- JSON: {records:[raw...], n_lines, n_parse_errors, unknown_types}
    cached_at   REAL NOT NULL,
    PRIMARY KEY (path, file_hash)
);
"""


def default_db_path() -> Path:
    root = Path(os.path.expanduser("~")) / ".haid"
    root.mkdir(parents=True, exist_ok=True)
    return root / "cache.db"


def file_hash(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _connect(db_path: str | Path | None) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path) if db_path else str(default_db_path()))
    conn.execute(_SCHEMA)
    return conn


def _serialize(res: ParseResult) -> str:
    return json.dumps({
        "records": [r.raw for r in res.records],
        "n_lines": res.n_lines,
        "n_parse_errors": res.n_parse_errors,
        "unknown_types": dict(res.unknown_types),
    })


def _deserialize(path: str, payload: str) -> ParseResult:
    from collections import Counter
    d = json.loads(payload)
    res = ParseResult(path=path)
    res.records = [rec.from_dict(o) for o in d["records"]]
    res.n_lines = d.get("n_lines", 0)
    res.n_parse_errors = d.get("n_parse_errors", 0)
    res.unknown_types = Counter(d.get("unknown_types", {}))
    res.had_partial_tail = False  # cached entries are never partial (see below)
    return res


def load_or_parse(path: str | Path, db_path: str | Path | None = None,
                  use_cache: bool = True) -> tuple[ParseResult, bool]:
    """Return (ParseResult, was_cache_hit). Parses + stores on miss; skips cache for
    actively-written (partial-tail) sessions."""
    from .parse import parse_file
    path = str(path)
    if not use_cache:
        return parse_file(path), False
    h = file_hash(path)
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT payload FROM parsed_sessions WHERE path=? AND file_hash=?", (path, h)
        ).fetchone()
        if row:
            return _deserialize(path, row[0]), True
        res = parse_file(path)
        if not res.had_partial_tail:   # don't cache a session still being written
            import time
            conn.execute(
                "INSERT OR REPLACE INTO parsed_sessions VALUES (?,?,?,?)",
                (path, h, _serialize(res), time.time()),
            )
            conn.commit()
        return res, False
    finally:
        conn.close()
