"""Sampling configuration for the Pass-1 harvester.

These are *priors and targets* for stratified discovery, not labels. The oracle
(docs/calibration-experiment.md §4) produces the real difficulty/originality/
cleanliness rankings; here we only ensure the candidate pool *covers* the plane and
is sampled off the popularity axis (§3c).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date, timedelta


def load_dotenv() -> str | None:
    """Load KEY=VALUE pairs from a `.env` file into os.environ (no dependency).

    Looks in the current working directory and the repo root (parent of this
    package). Does NOT override variables already set in the real environment.
    Returns the path it loaded, or None. `.env` is gitignored — keep tokens there.
    """
    candidates = [
        os.path.join(os.getcwd(), ".env"),
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"),
    ]
    seen: set[str] = set()
    for path in candidates:
        path = os.path.abspath(path)
        if path in seen or not os.path.isfile(path):
            continue
        seen.add(path)
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                key, val = key.strip(), val.strip().strip('"').strip("'")
                if key and key not in os.environ:   # real env wins
                    os.environ[key] = val
        return path
    return None


# --- Star buckets (§3c) ----------------------------------------------------------
# Sample ACROSS tiers, not stars-descending. `None` upper bound = open-ended top.
StarBucket = tuple[int, "int | None"]
STAR_BUCKETS: list[StarBucket] = [
    (0, 10),
    (10, 50),
    (50, 200),
    (200, 1000),
    (1000, None),
]

# Validator population (§3b / §4b): established, actively-maintained repos likely to
# carry genuine multi-person review. Recency is irrelevant here — review signals are
# contamination-immune — so we filter on `pushed:` activity + high stars over a wide
# window. (Blinding still applies at oracle time for the famous ones.)
VALIDATOR_STAR_BUCKETS: list[StarBucket] = [
    (500, 2000),
    (2000, 10000),
    (10000, None),
]


def bucket_label(bucket: StarBucket) -> str:
    lo, hi = bucket
    return f"{lo}-{hi}" if hi is not None else f"{lo}+"


def bucket_qualifier(bucket: StarBucket) -> str:
    """GitHub search `stars:` qualifier for a bucket."""
    lo, hi = bucket
    return f"stars:{lo}..{hi}" if hi is not None else f"stars:>={lo}"


# --- Languages, grouped by a difficulty PRIOR (§3e) ------------------------------
# The prior is only a coarse cell-placement hint. We deliberately span languages
# *within* each difficulty tier so the rubric can't learn "Rust = hard": include
# easy Rust and hard Python by sampling every language across every star bucket.
LANG_DIFFICULTY_PRIOR: dict[str, str] = {
    # systems / specialized — lean high-difficulty
    "Rust": "high",
    "C": "high",
    "C++": "high",
    "Zig": "high",
    "Cuda": "high",
    "Assembly": "high",
    "Haskell": "high",
    "OCaml": "high",
    # general-purpose breadth — lean mid (could be anything)
    "Go": "mid",
    "Java": "mid",
    "Python": "mid",
    "TypeScript": "mid",
    "JavaScript": "mid",
}

DEFAULT_LANGUAGES: list[str] = list(LANG_DIFFICULTY_PRIOR.keys())


# --- Topic keyword priors (§3e) --------------------------------------------------
# Repo topics / name hints that bump the difficulty prior up or down. Matched
# case-insensitively against topics + name + description.
HIGH_DIFFICULTY_HINTS: frozenset[str] = frozenset({
    "lock-free", "lockfree", "allocator", "parser", "compiler", "interpreter",
    "simd", "crypto", "cryptography", "zero-knowledge", "consensus", "raft",
    "database", "storage-engine", "kernel", "ebpf", "concurrency", "async-runtime",
    "garbage-collector", "jit", "wasm-runtime", "numerics", "linear-algebra",
    "tensor", "inference-engine", "query-engine", "regex-engine", "b-tree",
})
LOW_DIFFICULTY_HINTS: frozenset[str] = frozenset({
    "todo", "todo-app", "crud", "boilerplate", "starter", "starter-kit",
    "template", "scaffold", "example", "tutorial", "hello-world", "portfolio",
    "landing-page", "wrapper", "cli-wrapper", "config", "dotfiles",
})


@dataclass
class HarvestConfig:
    """All knobs for a Pass-1 run."""

    created_since: str                       # ISO date bound for `date_field`
    languages: list[str] = field(default_factory=lambda: list(DEFAULT_LANGUAGES))
    star_buckets: list[StarBucket] = field(default_factory=lambda: list(STAR_BUCKETS))
    per_cell: int = 20                       # candidates per (language x star bucket)
    channels: list[str] = field(default_factory=lambda: ["github", "showhn"])
    population: str = "oracle"               # "oracle" (recent, blinded) | "validator"
    date_field: str = "created"              # "created" (anti-contam) | "pushed" (active)
    # search hygiene qualifiers always applied
    require_not_fork: bool = True
    require_not_archived: bool = True
    sort: str = "updated"                    # favor active repos within a bucket
    order: str = "desc"


def default_created_since(months: int = 3) -> str:
    """ISO date `months` back from today (anti-contamination window, §3 recency knob)."""
    return (date.today() - timedelta(days=30 * months)).isoformat()
