"""The community-benchmark submission payload (ADR-0005, v1 self-reported tier).

Builds the SUMMARY-ONLY payload from an episode-score distribution: github username +
project name + key scores. Honesty constraints, enforced here:

  - NEVER logs/diffs/paths: the payload carries aggregate statistics only. Session ids,
    episode titles, file paths, and bridge caveats are all stripped — titles and paths
    leak project content. `assert_no_leaks` is part of the public API and the tests.
  - Ladder-version pinned: scores are only comparable against the same anchor ladders, so
    the payload embeds a hash of the shipped anchor data files.
  - Deterministic + content-hashed: same scores doc -> byte-identical payload (caller
    supplies generated_at), and `content_hash` covers every other field so the GitHub
    Action gate can verify integrity. There is no local signature in v1 (ADR-0005
    amended): identity and pseudonymous continuity come from the authenticated GitHub PR
    author that `haid submit` opens, and the Action cross-checks payload.github_username
    against the PR author — so a separate Ed25519 key would be redundant.
"""

from __future__ import annotations

import hashlib
import json
import math
from importlib import resources
from pathlib import Path

from .. import __version__
from ..scoring import value as _value

SCHEMA_VERSION = "1.2"            # 1.2: cleanliness is counted-defect density, not ladder pct
SUPPORTED_SCHEMA = frozenset({"1.2"})
# exact forbidden key names + the "path" substring; NOT bare "diff" (difficulty_rungs is fine)
_FORBIDDEN_KEYS = frozenset({"session_ids", "session_id", "title", "caveats", "caveat",
                             "diff", "diffs"})
_FORBIDDEN_SUBSTR = ("path",)


def ladder_versions() -> dict:
    """Hash of each shipped anchor ladder — placements are only comparable per-version.

    Line endings are normalized to LF before hashing so the version is identical across OS
    checkouts: a CRLF working tree (Windows) must hash the same as the LF artifact the gate
    installs, or that submitter is falsely rejected as 'stale'. (combiner_config_hash is
    already content-canonical; this gives the ladder hashes the same property.)"""
    out = {}
    for axis in ("difficulty",):    # cleanliness no longer uses an anchor ladder (defect counts)
        raw = resources.files("haid.data").joinpath(f"{axis}_anchors.json").read_bytes()
        raw = raw.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
        out[axis] = hashlib.sha256(raw).hexdigest()[:16]
    return out


def combiner_config_hash() -> str:
    """Hash of the value.py combiner knobs — same ladders + different knobs aren't
    comparable, so the board is bucketed by this too (ADR-0005)."""
    canon = json.dumps(_value.combiner_config(), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()[:16]


def _stats(xs: list[float]) -> dict:
    if not xs:
        return {"n": 0}
    xs = sorted(xs)
    return {"n": len(xs), "min": xs[0], "max": xs[-1],
            "median": xs[len(xs) // 2], "total": round(sum(xs), 6)}


def _median(xs: list[float]) -> float | None:
    if not xs:
        return None
    xs = sorted(xs)
    return round(xs[len(xs) // 2], 4)


def _severe_density(e: dict) -> float:
    """Severe-defect density for one scored episode: severe_count / sqrt(max(LOC, loc_floor)) —
    the same shape execution_factor penalizes. Counts only (privacy-safe; no findings)."""
    c = e.get("cleanliness") or {}
    sev = c.get("severe_count", 0) or 0
    chg = c.get("changed_lines", 0) or 0
    return round(sev / math.sqrt(max(chg, _value.DEFAULT_LOC_FLOOR)), 4)


def build_submission(scores_doc: dict, *, github_username: str, project: str,
                     generated_at: str) -> dict:
    """The v1 self-reported payload from a `haid score --json` document."""
    if not github_username or not project:
        raise ValueError("submission needs github_username and project")
    eps = scores_doc.get("episodes", [])
    scored = [e for e in eps if e.get("value") is not None]

    rungs = sorted(e["difficulty"]["rung"] for e in scored if e.get("difficulty"))
    densities = sorted(_severe_density(e) for e in scored if e.get("cleanliness"))
    achievement_total = round(sum(e["achievement"] for e in scored
                                  if e.get("achievement") is not None), 4)
    volume_loc_total = round(sum(e.get("achievement_components", {}).get("volume_loc", 0)
                                 for e in scored), 2)
    ntok_total = round(sum(e.get("normalized_tokens", 0) for e in eps), 1)
    # the ranked overall score: total work done per (cost_unit) tokens over the whole window
    _vo = _value.value_ratio(achievement_total, ntok_total)
    value_overall = (None if _vo != _vo else round(_vo, 6))

    payload = {
        "schema_version": SCHEMA_VERSION,
        "kind": "haid_benchmark_submission",
        "github_username": github_username,
        "project": project,
        "generated_at": generated_at,
        "tool_version": __version__,
        "ladder_versions": ladder_versions(),
        "combiner_config_hash": combiner_config_hash(),
        "window": {
            "n_episodes": len(eps),
            "n_scored": len(scored),
            "n_no_artifact": sum(1 for e in eps if not e.get("has_artifact", True)),
        },
        # leaderboard-row scalars (one row per user-window) ---------------------------
        "achievement_total": achievement_total,
        "volume_loc_total": volume_loc_total,
        "difficulty_rung_median": _median(rungs),
        "severe_density_median": _median(densities),
        "normalized_tokens_total": ntok_total,
        "value_overall": value_overall,
        # distribution detail (drives the per-axis percentile curves) -----------------
        "scores": {
            "value": _stats([e["value"] for e in scored]),
            "achievement": _stats([e["achievement"] for e in scored
                                   if e.get("achievement") is not None]),
            "difficulty_rungs": rungs,
            "severe_densities": densities,
        },
        "self_reported": True,       # v1 anti-fabrication = the Action's plausibility gate
    }
    payload["content_hash"] = content_hash(payload)
    assert_no_leaks(payload)
    return payload


def content_hash(payload: dict) -> str:
    """sha256 over the canonical JSON of everything except the content_hash itself.

    Identity + continuity come from the authenticated GitHub PR author (ADR-0005 v1, no
    local Ed25519 signature); this hash is the integrity check the validator recomputes."""
    body = {k: v for k, v in payload.items() if k != "content_hash"}
    canon = json.dumps(body, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def assert_no_leaks(payload: dict) -> None:
    """Refuse to emit a payload containing anything that smells like project content."""
    def walk(obj, trail):
        if isinstance(obj, dict):
            for k, v in obj.items():
                lk = k.lower()
                if lk in _FORBIDDEN_KEYS or any(h in lk for h in _FORBIDDEN_SUBSTR):
                    raise ValueError(f"submission payload leak: key {trail}.{k}")
                walk(v, f"{trail}.{k}")
        elif isinstance(obj, list):
            for i, v in enumerate(obj):
                walk(v, f"{trail}[{i}]")
        elif isinstance(obj, str) and ("/" in obj or "\\" in obj) and len(obj) > 40:
            raise ValueError(f"submission payload leak: path-like string at {trail}")
    walk(payload, "$")


def verify(payload: dict) -> bool:
    """Recompute the content hash (the GitHub Action's plausibility check, locally)."""
    return payload.get("content_hash") == content_hash(payload)


# --- the submission gate + the cross-repo snapshot sanitizer -----------------------------
# These live in the package (not the benchmark repo's scripts) so the data-repo validator,
# the cross-repo board sync, and the test-suite all share ONE implementation that cannot
# drift from what `haid submit` produces.

class SubmissionRejected(Exception):
    """An entry failed a gate. The message is safe to surface publicly (no project content)."""


_REQUIRED = ("schema_version", "kind", "github_username", "project", "tool_version",
             "ladder_versions", "combiner_config_hash", "window", "achievement_total",
             "volume_loc_total", "difficulty_rung_median", "severe_density_median",
             "normalized_tokens_total", "value_overall", "scores", "self_reported",
             "content_hash")


def validate_entry(payload: dict, *, expected_user: str, entry_name: str | None = None) -> None:
    """The submission gate (ADR-0005 v1). Raises SubmissionRejected on the first failure.

    Checks, in order: shape -> identity (github_username == expected_user == filename stem)
    -> integrity (content_hash recomputes) -> no project-content leak -> current ladder AND
    combiner version -> numeric plausibility. `expected_user` is the authenticated GitHub PR
    author; `entry_name` is the filename so the file, the field, and the author must agree.
    """
    if not isinstance(payload, dict):
        raise SubmissionRejected("entry is not a JSON object")
    missing = [k for k in _REQUIRED if k not in payload]
    if missing:
        raise SubmissionRejected(f"missing required keys: {', '.join(missing)}")
    if payload["schema_version"] not in SUPPORTED_SCHEMA:
        raise SubmissionRejected(f"unsupported schema_version {payload['schema_version']!r}")
    if payload["kind"] != "haid_benchmark_submission":
        raise SubmissionRejected(f"wrong kind {payload['kind']!r}")
    if payload.get("self_reported") is not True:
        raise SubmissionRejected("self_reported must be true")

    user = payload["github_username"]
    stem = Path(entry_name).stem if entry_name else user
    if not (user == expected_user == stem):
        raise SubmissionRejected(
            f"identity mismatch: github_username={user!r}, filename={stem!r}, "
            f"author={expected_user!r} must all be equal")

    if not verify(payload):
        raise SubmissionRejected("content_hash does not recompute (entry was altered)")
    try:
        assert_no_leaks(payload)
    except ValueError as e:
        raise SubmissionRejected(str(e)) from e

    if payload["ladder_versions"] != ladder_versions():
        raise SubmissionRejected("ladder_versions are stale — re-score with the current "
                                 "shipped anchor ladders and resubmit")
    if payload["combiner_config_hash"] != combiner_config_hash():
        raise SubmissionRejected("combiner_config_hash is stale — re-score with the current "
                                 "combiner config and resubmit")
    _plausible(payload)


def _plausible(p: dict) -> None:
    w = p["window"]
    if not (0 <= w.get("n_scored", 0) <= w.get("n_episodes", 0)):
        raise SubmissionRejected(f"n_scored {w.get('n_scored')} out of range "
                                 f"(n_episodes {w.get('n_episodes')})")
    for k in ("achievement_total", "volume_loc_total", "normalized_tokens_total"):
        if p[k] is not None and p[k] < 0:
            raise SubmissionRejected(f"{k} is negative")
    sd = p["severe_density_median"]
    if sd is not None and sd < 0:
        raise SubmissionRejected(f"severe_density_median {sd} is negative")
    vo, at, nt = p["value_overall"], p["achievement_total"], p["normalized_tokens_total"]
    if vo is not None and nt:
        expected = _value.value_ratio(at, nt)
        if abs(vo - expected) > 1e-4 * max(1.0, abs(expected)):
            raise SubmissionRejected(f"value_overall {vo} != achievement_total/"
                                     f"normalized_tokens ({expected:.6g}) — inconsistent")
    for d in p.get("scores", {}).get("severe_densities", []):
        if d < 0:
            raise SubmissionRejected(f"severe density {d} is negative")


# The WHITELIST the cross-repo board snapshot may carry: known scalar fields only. Anything
# else a board.json row contains is dropped — the snapshot can never carry free-form data,
# let alone anything executable.
_ROW_STR = ("github_username", "project", "schema_version", "tool_version",
            "combiner_config_hash")
_ROW_NUM = ("achievement_total", "volume_loc_total", "difficulty_rung_median",
            "severe_density_median", "normalized_tokens_total", "value_overall")


def project_row(payload: dict) -> dict:
    """Project an already-verified row onto the whitelist of canonical scalar fields,
    type-checking each. Raises SubmissionRejected on a wrong type."""
    row: dict = {}
    for k in _ROW_STR:
        v = payload.get(k)
        if v is not None and not isinstance(v, str):
            raise SubmissionRejected(f"row field {k} must be a string")
        row[k] = v
    for k in _ROW_NUM:
        v = payload.get(k)
        if v is not None and (isinstance(v, bool) or not isinstance(v, (int, float))):
            raise SubmissionRejected(f"row field {k} must be a number")
        row[k] = v
    lv = payload.get("ladder_versions")
    if not isinstance(lv, dict) or not all(
            isinstance(a, str) and isinstance(h, str) for a, h in lv.items()):
        raise SubmissionRejected("ladder_versions must be a {str: str} map")
    row["ladder_versions"] = {a: h for a, h in lv.items()}
    return row


def sanitize_board(board: dict) -> dict:
    """Rebuild a board snapshot from an UNTRUSTED board.json so it can carry ONLY current,
    integrity-verified benchmark scalars and nothing else (the cross-repo sync's guarantee).

    Every row must pass the leak guard and recompute its content_hash; it is then projected
    onto the field whitelist. The package never executes a board — but this makes the
    "only current benchmarks cross the repo boundary" property structural, not incidental.
    """
    if not isinstance(board, dict) or board.get("kind") != "haid_benchmark_board":
        raise SubmissionRejected("not a haid benchmark board")
    rows = board.get("rows")
    if not isinstance(rows, list):
        raise SubmissionRejected("board.rows must be a list")
    clean, seen = [], set()
    for r in rows:
        if not isinstance(r, dict):
            raise SubmissionRejected("each board row must be a JSON object")
        try:
            assert_no_leaks(r)
        except ValueError as e:
            raise SubmissionRejected(str(e)) from e
        if not verify(r):
            raise SubmissionRejected(
                f"board row content_hash does not verify: {r.get('github_username')!r}")
        user = r.get("github_username")
        if user in seen:
            raise SubmissionRejected(f"duplicate board row for {user!r}")
        seen.add(user)
        clean.append(project_row(r))
    clean.sort(key=lambda x: (x.get("value_overall") or -1), reverse=True)
    return {"schema_version": SCHEMA_VERSION, "kind": "haid_benchmark_board",
            "generated_at": board.get("generated_at"), "n_entries": len(clean),
            "rows": clean}
