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
    Action gate can verify integrity. Signing (local key, pseudonymous entry ownership)
    is the `haid submit` step (Phase 5) — out of scope here; the payload carries
    signature: null until then.
"""

from __future__ import annotations

import hashlib
import json
from importlib import resources

SCHEMA_VERSION = "1.0"
# exact forbidden key names + the "path" substring; NOT bare "diff" (difficulty_rungs is fine)
_FORBIDDEN_KEYS = frozenset({"session_ids", "session_id", "title", "caveats", "caveat",
                             "diff", "diffs"})
_FORBIDDEN_SUBSTR = ("path",)


def ladder_versions() -> dict:
    """Hash of each shipped anchor ladder — placements are only comparable per-version."""
    out = {}
    for axis in ("difficulty", "cleanliness"):
        raw = resources.files("haid.data").joinpath(f"{axis}_anchors.json").read_bytes()
        out[axis] = hashlib.sha256(raw).hexdigest()[:16]
    return out


def _stats(xs: list[float]) -> dict:
    if not xs:
        return {"n": 0}
    xs = sorted(xs)
    return {"n": len(xs), "min": xs[0], "max": xs[-1],
            "median": xs[len(xs) // 2], "total": round(sum(xs), 6)}


def build_submission(scores_doc: dict, *, github_username: str, project: str,
                     generated_at: str) -> dict:
    """The v1 self-reported payload from a `haid score --json` document."""
    if not github_username or not project:
        raise ValueError("submission needs github_username and project")
    eps = scores_doc.get("episodes", [])
    scored = [e for e in eps if e.get("value") is not None]

    payload = {
        "schema_version": SCHEMA_VERSION,
        "kind": "haid_benchmark_submission",
        "github_username": github_username,
        "project": project,
        "generated_at": generated_at,
        "ladder_versions": ladder_versions(),
        "window": {
            "n_episodes": len(eps),
            "n_scored": len(scored),
            "n_no_artifact": sum(1 for e in eps if not e.get("has_artifact", True)),
        },
        "scores": {
            "value": _stats([e["value"] for e in scored]),
            "achievement": _stats([e["achievement"] for e in scored
                                   if e.get("achievement") is not None]),
            "difficulty_rungs": sorted(e["difficulty"]["rung"] for e in scored
                                       if e.get("difficulty")),
            "cleanliness_percentiles": sorted(
                round(e["cleanliness"]["percentile"], 3) for e in scored
                if e.get("cleanliness")),
            "normalized_tokens_total": round(
                sum(e.get("normalized_tokens", 0) for e in eps), 1),
        },
        "self_reported": True,
        "signature": None,          # `haid submit` (Phase 5) signs; plausibility-gate only
    }
    payload["content_hash"] = content_hash(payload)
    assert_no_leaks(payload)
    return payload


def content_hash(payload: dict) -> str:
    """sha256 over the canonical JSON of everything except the hash/signature fields."""
    body = {k: v for k, v in payload.items() if k not in ("content_hash", "signature")}
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
