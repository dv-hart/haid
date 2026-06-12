"""Cheap-proxy placement on the difficulty x volume plane (§3e).

PRIORS ONLY. These exist so the candidate pool covers every cell; the Opus oracle
produces the real ranking later. Every field here is named *_prior and must never be
mistaken for a label.
"""

from __future__ import annotations

from . import config


def difficulty_prior(language: str | None, topics: list[str], name: str,
                     description: str | None) -> str:
    """Coarse {low, mid, high} difficulty hint from language + topic/name hints."""
    haystack = " ".join([
        name or "",
        description or "",
        " ".join(topics or []),
    ]).lower()

    if any(h in haystack for h in config.HIGH_DIFFICULTY_HINTS):
        return "high"
    if any(h in haystack for h in config.LOW_DIFFICULTY_HINTS):
        return "low"
    # fall back to the language prior
    return config.LANG_DIFFICULTY_PRIOR.get(language or "", "mid")


def volume_prior(size_kb: int | None) -> str:
    """Coarse {small, mid, large} volume hint from repo size.

    Repo size (KB of checkout) is a rough stand-in at the candidate stage; the real
    volume is measured deterministically on the per-unit diff in Pass-2.
    """
    if size_kb is None:
        return "unknown"
    if size_kb < 500:          # < ~0.5 MB
        return "small"
    if size_kb < 10_000:       # < ~10 MB
        return "mid"
    return "large"


def cell(language: str | None, topics: list[str], name: str,
         description: str | None, size_kb: int | None) -> dict[str, str]:
    """The (difficulty_prior, volume_prior) cell for a candidate."""
    return {
        "difficulty_prior": difficulty_prior(language, topics, name, description),
        "volume_prior": volume_prior(size_kb),
    }
