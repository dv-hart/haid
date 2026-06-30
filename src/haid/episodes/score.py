"""Step 4: score each episode, and turn the window into a DISTRIBUTION of episode scores.

This is the join the whole tool was built toward. Given the episodes (groups of whole sessions,
step 3), it produces per episode:

  - **episode-scope waste metrics** — `metrics.run_episodes` (the 4 rules over the episode's
    session sub-stream);
  - **a reconstructed diff + cost** — `bridge.episode_inputs` over the episode's session subset
    (episode-relative baseline + clean summed cost, because we never cut below a session);
  - **difficulty placement + cleanliness defect-detection (detect→verify)** → `achievement` and `value`.

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
from ..scoring.compare import PendingComparisons
from ..scoring.detect import PendingDetection
from ..scoring.placement import PlacementResult, place
from .model import Episode

# A factory the caller supplies: (axis, subject_id) -> a backend for that axis. For "difficulty"
# it returns a compare.Backend (pairwise placement); for "cleanliness" a detect.DetectBackend.
BackendFor = Callable[[str, str], object]


@dataclass
class EpisodeScore:
    """One episode's full scorecard. Every component is kept (never collapsed) so the diagnosis
    router can key off WHICH term is notable, not just the scalar."""
    episode: Episode
    has_artifact: bool                         # produced a non-empty reconstructed diff
    bridge: object                             # bridge.BridgeResult (diff + cost + caveats)
    metrics: dict = field(default_factory=dict)        # {metric_name: MetricResult} @ episode scope
    difficulty: PlacementResult | None = None
    cleanliness: object | None = None          # scoring.defects.DefectResult (post-verify)
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
        v = _value.value_ratio(ach_total, tok_total)
        return {
            "n_scored": len(scored),
            "achievement_total": round(ach_total, 4),
            "normalized_tokens_total": round(tok_total, 1),
            "value": (None if v != v else round(v, 6)),
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
        if s.cleanliness is not None:
            c = s.cleanliness
            out["cleanliness"] = {
                "severe_count": c.severe_count,
                "minor_count": c.minor_count,
                "other_count": c.other_count,
                "changed_lines": c.changed_lines,
                "by_class": c.by_class(),
                # the execution multiplier (from achievement when scored); counts only —
                # the raw findings (verbatim diff snippets) are kept OUT of scores.json.
                "execution_C": (round(s.achievement.cleanliness_C, 4)
                                if s.achievement else None),
            }
        if s.achievement:
            a = s.achievement
            out["achievement"] = round(a.achievement, 4)
            # components kept so the benchmark row can show what drove achievement
            out["achievement_components"] = {
                "volume_loc": round(a.volume_loc, 2),
                "volume_term": round(a.volume_term, 4),
                "difficulty_D": round(a.difficulty_D, 4),
                "cleanliness_C": round(a.cleanliness_C, 4),
                "bugfix_term": round(a.bugfix_term, 4),
                "n_cured_bugs": a.n_cured_bugs,
            }
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
                             f"  (D rung={s.difficulty.rung:.1f}, "
                             f"{s.cleanliness.severe_count} severe/{s.cleanliness.changed_lines}LOC,"
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
                   k_defect: float = _value.DEFAULT_K_DEFECT,
                   loc_floor: int = _value.DEFAULT_LOC_FLOOR,
                   exec_floor: float = _value.DEFAULT_EXEC_FLOOR,
                   tagged=None, cured_eligible=None,
                   label: str = "") -> WindowDistribution:
    """Score every episode → a WindowDistribution.

    `backend_for("difficulty", id)` supplies a compare.Backend (pairwise placement);
    `backend_for("cleanliness", id)` a detect.DetectBackend (detect → verify). A leg that
    defers under the live file-handoff path raises `PendingComparisons` / `PendingDetection`;
    each is caught and its manifest recorded so all episodes' manifests surface together.

    `tagged` (the haid-tag output) turns on the bug-fix reward: each episode's fix spans are
    placed on the difficulty ladder and folded into achievement as cured inherited bugs
    (scoring/bugfix.py; docs/plans/bugfix-reward.md). `cured_eligible(bug_id)->bool` applies the
    attribution gate (the caller wires it from bug notes); omitted ⇒ all cures count (the gate is
    off). Cure placements defer like the other legs and land in the same pending list."""
    from ..bridge import episode_inputs
    from ..scoring import bugfix as _bugfix

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

        vol = _volume.measure(diff)                   # measured first: changed_lines feeds detect
        pending: list[str] = []

        dpl = None
        try:
            dpl = place(diff, "difficulty", backend_for("difficulty", ep.id),
                        samples=samples, subject_id=ep.id)
        except PendingComparisons as p:
            pending.append(p.manifest_path)

        defects = None
        try:
            defects = backend_for("cleanliness", ep.id).detect(diff, vol.raw_added)
        except PendingDetection as p:
            pending.append(p.manifest_path)

        # bug-fix reward: place this episode's eligible fix spans (cured inherited bugs).
        cured = []
        if tagged is not None:
            ep_sids = set(ep.session_ids)
            ep_tags = [t for t in tagged if t.session_id in ep_sids]
            cands = _bugfix.collect_candidates(members, ep_tags)
            cured, cpending = _bugfix.resolve_cured(cands, backend_for,
                                                    eligible=cured_eligible, samples=samples)
            pending += cpending

        if pending or dpl is None or defects is None:
            scores.append(EpisodeScore(ep, has_artifact=True, bridge=br, metrics=mets,
                                       difficulty=dpl, cleanliness=defects, pending=pending))
            continue

        ach = _value.achievement(vol, dpl, defects, alpha=alpha, top_ratio=top_ratio,
                                 k_defect=k_defect, loc_floor=loc_floor, exec_floor=exec_floor,
                                 cured_bugs=cured)
        val = _value.value(ach, br.cost)
        scores.append(EpisodeScore(ep, has_artifact=True, bridge=br, metrics=mets,
                                   difficulty=dpl, cleanliness=defects, achievement=ach, value=val))

    return WindowDistribution(label=label, scores=scores)
