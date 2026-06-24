# Step 4 build — episode-scope metrics + per-episode achievement (session grain)

> **Status: BUILT (2026-06-08), session grain.** Step 4 of the runtime pipeline
> ([agent-analysis.md](agent-analysis.md)). `metrics.run_episodes` + `bridge.episode_inputs` +
> `episodes/score.py` (`score_episodes` → `WindowDistribution`) + `haid score` CLI;
> 7 new tests (195 suite-wide), validated end-to-end on the real HAID window (8 episodes → 6
> scored + 2 no-artifact, a real value distribution). The only piece still gated on the held
> live runs is the **live placement eyeball** (the deterministic stub stands in for the model in
> tests + the dry run). Depends on **episodes at session grain** (step 3) — an episode is a
> **collection of whole sessions**; the session is atomic, so **every token attributes cleanly**.

## Goal

Turn each episode into a scored unit and the window into a **distribution** of those scores, so
the critical 5% isn't buried under a project's scaffolding ([scoring-rubric.md §grain](../docs/scoring-rubric.md)).
Per episode: its waste metrics, its reconstructed diff, its cost, and its placement on the
difficulty/cleanliness ladders → `achievement` and `value`.

## Why session grain makes this easy

Everything reuses code that already takes a *set of sessions* or a *sub-stream*:

| Need | Mechanism (existing) |
|------|----------------------|
| episode-scope metrics | group `view.active_stream` by episode (mirrors `run_sessions`) |
| per-episode diff | `bridge.window_inputs(sub_view, episode_sessions)` — over the episode's sessions only |
| per-episode baseline | falls out: the earliest `originalFile` *within the episode's sessions* = the file's state as it entered the episode (post earlier episodes) |
| per-episode cost | `bridge.extract_cost(episode_sessions)` = sum of the sessions' clean per-context-window costs |
| placement / achievement / value | the existing `scoring.placement` + `scoring.value`, unchanged |

No new slicing engine, no reconstruct-twice, no owner-message mapping — all the message-grain
complexity is gone.

## Build sequence

### 1. Episode-scope metrics (deterministic, no model)
- `metrics.run_episodes(view, episodes)` — mirror `run_sessions`: build `sid → episode_id`, group
  `view.active_stream` by episode, run each `_core(sub, episode_id)`. ~6 lines.
- Add `"episode"` to `metrics.base.SCOPES`; wire episode-scope results into `metrics.json_out`
  (the `scopes` array becomes `["session","episode","window"]` once episodes exist, exactly as
  [metrics-output-schema.md](../docs/metrics-output-schema.md) already specifies; `unit_id` = the
  `episode_id`). Episode-scope **baseline** reuses the window baseline for now, flagged (a true
  episode baseline needs the community corpus — same caveat as the other scopes).
- *Note:* episode-scope `rereads` is the **re-establishment tax within the episode** (a file
  re-read across the episode's sessions) — meaningful, the same way window-scope rereads is the
  cross-session tax. No special handling.

### 2. Per-episode diff + cost (deterministic, no model)
- `bridge.episode_inputs(view, sessions, episode)` — thin wrapper: restrict the view to the
  episode's sessions (an `episode_view` filter on `active_stream` + `timelines` by session
  membership) and call `window_inputs(sub_view, episode_sessions)`. Returns the same
  `BridgeResult` (diff + cost + caveats) the scorer already consumes.
- **Test the cross-episode baseline explicitly:** a file edited in episode 1 (sessions A,B) and
  again in episode 2 (session C) — episode 2's diff must be *its own delta* (against the
  end-of-episode-1 state, which the `originalFile` captured in C provides), not the whole change.
- Empty-diff episodes (planning-only sessions, no writes) are legal — mark them "no artifact",
  don't score them as a failure.

### 3. Per-episode achievement + value, and the distribution (model half; replay-validated now)
- For each episode: `volume.measure(diff)`, place difficulty + cleanliness via the existing
  `scoring.placement` backends (Replay for CI/now, Harness for the live host-agent path),
  `value.achievement(vol, D, C)`, `value.value(ach, cost)`.
- Assemble an `EpisodeScore` per episode `{episode, metrics_by_scope, diff, cost, volume,
  difficulty_placement, cleanliness_placement, achievement, value, caveats}` and a
  `WindowDistribution` (the list + summary stats: the spread of placements, the top episodes by
  value, the scaffolding-vs-core shape). This is the hand-off the report compositor and Phase-3
  attribution read.

### 4. CLI + JSON hand-off + render
- `haid value --by-episode` (or a new `haid score`) emits the per-episode distribution instead of
  one blended window number. JSON for the downstream passes; a Markdown eyeball view.

### 5. Tests + real-data dry run
- Deterministic tests (replay placement backend, synthetic episodes), including the cross-episode
  baseline case and an empty-diff episode.
- Real-data dry run on the HAID window (replay placement) end-to-end: episodes → per-episode
  diff/cost/metrics → distribution.

## What's gated on the held live runs

Only the **live placement eyeball** — does the model place *real* episode diffs sensibly. The
placement stack is already replay-validated (ρ=0.866, no model), so everything builds now; the
live check folds into the same session that validates the classifier and the grouping pass when
runs resume. Same single validation debt, not a new one.

## Risks
- **Garbage-in from grouping:** if step-3 grouping over-merges (a hub file like a shared
  `__init__` chaining unrelated sessions), episode scores blur. Mitigated by the grouping being
  model-driven + auditable; surface the grouping rationale alongside each score.
- **Cross-episode baseline correctness** (§2) — covered by the explicit test.
- **Cleanliness grain** — may want final-artifact (end-state) rather than per-episode; left open
  in [scoring-rubric.md](../docs/scoring-rubric.md), decided separately.
