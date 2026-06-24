"""Step 4: score each episode, and turn the window into a DISTRIBUTION of episode scores.

This is the join the whole tool was built toward. Given the episodes (groups of whole sessions,
step 3), it produces per episode:

  - **episode-scope waste metrics** — `metrics.run_episodes` (the 4 rules over the episode's
    session sub-stream);
  - **a reconstructed diff + cost** — `bridge.episode_inputs` over the episode's session subset
    (episode-relative baseline + clean summed cost, because we never cut below a session);
  - **difficulty + cleanliness placement** → `achievement` and `value` (the existing scoring fold).

The window is then the **distribution** of those per-episode placements, not one blended number —
so a project's scaffolding episodes (T0–T1) don't bury the critical 5% (T3–T4); see
[scoring-rubric.md §grain](../../docs/scoring-rubric.md).

The model judgment (placement) is delegated to backends exactly as the rest of the stack does:
`backend_for(axis, subject_id)` returns a `compare.Backend` per episode/axis (a HarnessBackend
for the live host-agent path, or any deterministic stub for tests/CI). Under the live file-handoff
path a placement raises `PendingComparisons`; we collect every episode's manifests so the skill can
run them all in one batch and re-invoke. Stdlib only here; no in-process model call.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from ..metrics import run_episodes
from ..scoring import value as _value
from ..scoring import volume as _volume
from ..scoring.compare import Backend, PendingComparisons
from ..scoring.placement import PlacementResult, place
from .model import Episode

# A factory the caller supplies: (axis, subject_id) -> a comparison Backend for that placement.
BackendFor = Callable[[str, str], Backend]


@dataclass
class EpisodeScore:
    """One episode's full scorecard. Every component is kept (never collapsed) so the diagnosis
    router can key off WHICH term is notable, not just the scalar."""
    episode: Episode
    has_artifact: bool                         # produced a non-empty reconstructed diff
    bridge: object                             # bridge.BridgeResult (diff + cost + caveats)
    metrics: dict = field(default_factory=dict)        # {metric_name: MetricResult} @ episode scope
    difficulty: PlacementResult | None = None
    cleanliness: PlacementResult | None = None
    achievement: object | None = None          # value.AchievementResult
    value: object | None = None                # value.ValueResult
    pending: list = field(default_factory=list)        # manifest paths if placement deferred

    @property
    def id(self) -> str:
        return self.episode.id

    @property
    def value_scalar(self) -> float | None:
        return self.value.value if self.value else None

    @property
    def normalized_tokens(self) -> float:
        return getattr(self.bridge.cost, "normalized_tokens", 0.0)


@dataclass
class WindowDistribution:
    """The window as a distribution of episode scores — the Step-4 hand-off to the report
    compositor and Phase-3 attribution."""
    label: str
    scores: list[EpisodeScore] = field(default_factory=list)

    @property
    def pending(self) -> list:
        return [p for s in self.scores for p in s.pending]

    @property
    def scored(self) -> list[EpisodeScore]:
        return [s for s in self.scores if s.value is not None]

    def to_json(self) -> dict:
        return {
            "schema_version": "1.0", "kind": "episode_scores", "window": self.label,
            "n_episodes": len(self.scores),
            "pending_placements": len(self.pending),
            "window_score": self._window_score(),
            "episodes": [self._episode_json(s) for s in self.scores],
        }

    def _window_score(self) -> dict:
        """The single window-level score: achievement and cost summed across scored episodes,
        folded into one value ratio (achievement_total / normalized_tokens_total). This is the
        number a user tracks over time and the figure the opt-in leaderboard ranks."""
        scored = [s for s in self.scored if s.achievement is not None]
        ach_total = sum(s.achievement.achievement for s in scored)
        tok_total = sum(s.normalized_tokens for s in scored)
        rungs = [s.difficulty.rung for s in scored if s.difficulty is not None]
        return {
            "n_scored": len(scored),
            "achievement_total": round(ach_total, 4),
            "normalized_tokens_total": round(tok_total, 1),
            "value": (round(ach_total / tok_total, 6) if tok_total > 0 else None),
            "difficulty_ceiling": round(max(rungs), 2) if rungs else None,
        }

    @staticmethod
    def _episode_json(s: EpisodeScore) -> dict:
        out = {
            "id": s.id, "title": s.episode.title, "session_ids": s.episode.session_ids,
            "n_sessions": s.episode.n_sessions, "has_artifact": s.has_artifact,
            "normalized_tokens": round(s.normalized_tokens, 1),
            "metrics": {name: {"token_rate": round(m.token_rate, 4), "count": m.count}
                        for name, m in s.metrics.items()},
            "caveats": list(s.bridge.caveats),
        }
        if s.difficulty:
            out["difficulty"] = {"rung": round(s.difficulty.rung, 2),
                                 "percentile": round(s.difficulty.percentile, 3)}
        if s.cleanliness:
            out["cleanliness"] = {"percentile": round(s.cleanliness.percentile, 3)}
        if s.achievement:
            out["achievement"] = round(s.achievement.achievement, 4)
        if s.value:
            out["value"] = (None if s.value.value != s.value.value      # nan → null
                            else round(s.value.value, 6))
        if s.pending:
            out["pending_placements"] = list(s.pending)
        return out

    def render(self) -> str:
        head = f"# episode scores — {self.label}" if self.label else "# episode scores"
        lines = [head, ""]
        scored = sorted(self.scored, key=lambda s: (s.value_scalar or 0), reverse=True)
        if scored:
            lines.append("## scored episodes (by value)")
            for s in scored:
                v = s.value_scalar
                vs = "n/a" if v is None or v != v else f"{v:.4g}"
                lines.append(f"  {s.id}: value={vs}  achievement={s.achievement.achievement:.3g}"
                             f"  (D rung={s.difficulty.rung:.1f}, C p={s.cleanliness.percentile:.2f},"
                             f" {s.normalized_tokens:.0f} nTok)  — {s.episode.title[:50]}")
        no_art = [s for s in self.scores if not s.has_artifact]
        if no_art:
            lines.append("\n## no code artifact (not scored)")
            for s in no_art:
                lines.append(f"  {s.id}: {s.episode.title[:60]}")
        if self.pending:
            lines.append(f"\n## {len(self.pending)} placement(s) pending — run the manifests, "
                         "then re-run")
        return "\n".join(lines).rstrip()


def _stem(path: str) -> str:
    return Path(path).stem[:8]


def score_episodes(view, sessions, episodes, backend_for: BackendFor, *, samples: int = 1,
                   alpha: float = _value.DEFAULT_ALPHA, top_ratio: float = _value.DEFAULT_TOP_RATIO,
                   gamma: float = _value.DEFAULT_GAMMA, floor: float = _value.DEFAULT_FLOOR,
                   label: str = "") -> WindowDistribution:
    """Score every episode → a WindowDistribution.

    `backend_for(axis, subject_id)` supplies the comparison backend per placement. A placement
    that defers (live file-handoff) raises `PendingComparisons`; it is caught and its manifest
    recorded so all episodes' manifests surface together."""
    from ..bridge import episode_inputs

    emetrics = run_episodes(view, episodes)
    by_id = {_stem(s.path): s for s in sessions}
    scores: list[EpisodeScore] = []

    for ep in episodes:
        members = [by_id[sid] for sid in ep.session_ids if sid in by_id]
        br = episode_inputs(members)
        mets = emetrics.get(ep.id, {})
        diff = br.diff.strip()

        if not diff:                                  # planning-only / no code change
            scores.append(EpisodeScore(ep, has_artifact=False, bridge=br, metrics=mets))
            continue

        pending: list[str] = []
        placements: dict[str, PlacementResult] = {}
        for axis in ("difficulty", "cleanliness"):
            try:
                placements[axis] = place(diff, axis, backend_for(axis, ep.id),
                                         samples=samples, subject_id=ep.id)
            except PendingComparisons as p:
                pending.append(p.manifest_path)

        dpl, cpl = placements.get("difficulty"), placements.get("cleanliness")
        if pending or dpl is None or cpl is None:
            scores.append(EpisodeScore(ep, has_artifact=True, bridge=br, metrics=mets,
                                       difficulty=dpl, cleanliness=cpl, pending=pending))
            continue

        vol = _volume.measure(diff)
        ach = _value.achievement(vol, dpl, cpl, alpha=alpha, top_ratio=top_ratio,
                                 gamma=gamma, floor=floor)
        val = _value.value(ach, br.cost)
        scores.append(EpisodeScore(ep, has_artifact=True, bridge=br, metrics=mets,
                                   difficulty=dpl, cleanliness=cpl, achievement=ach, value=val))

    return WindowDistribution(label=label, scores=scores)
