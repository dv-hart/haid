# `haid metrics` output schema — the Phase-1 → Phase-2/3 hand-off contract

> **Status: BUILT (2026-06-07).** Emitted by `haid metrics` — `src/haid/metrics/json_out.py`
> (this JSON) + `src/haid/metrics/view.py` (the Markdown view, rendered from the same dict).
> It is the **machine-readable hand-off** to the later "why" passes (intent-tagging in Phase 2,
> error-attribution / `why()` in Phase 3). The Markdown view is the maintainer's eyeball/DoD
> surface; this JSON is the contract. See [phase1-build.md](../plans/phase1-build.md) Step 5.

## What this is (and is not)

`haid metrics` produces the **measured substrate** — *where* and *how much*, never *why* or
*what to fix*. The JSON exists so a downstream subagent can take a finding, **re-open the
exact spot in the transcript**, and reason about cause. Four properties follow:

1. **Pointers, not dumps.** Each finding carries stable keys — `session_id` → `file_id` →
   `calls[].tool_use_id`/`turn_id` → `line_span`. The JSON *locates*; it does not re-embed
   transcript text. The consumer has the transcript already.
2. **Pure measurement.** No remedy, no "this suggests…" — interpretation is the consumer's job.
3. **Two orthogonal axes: metric × scope.** Not a long named-metric list.
4. **No silent caps, and versioned.** Everything skipped/limited is in `caps`; the contract
   carries `schema_version`.

## The core model: metric × scope

Two independent axes, kept orthogonal:

- **metric** ∈ `{rereads, retries, retouched, unused_context}` — *what kind* of waste.
  **One rule each.** Scope is **never** encoded in the name (no `cross_session_rereads`).
- **scope** ∈ `{session, episode, window}` — **the memory window the one rule runs over**,
  nothing more. `session` = memory resets per session; `episode` = memory resets per episode
  (the unit-of-work / PR-proxy grain, [agent-analysis.md §1](../plans/agent-analysis.md));
  `window` = memory persists across the whole window. Extensible further: `pr` with git
  (Phase 4), `all_time` later.

  **Availability differs by determinism.** `session` and `window` are **deterministic** —
  computable with no model (the built Phase-1 substrate). **`episode` is an enrichment**: it
  only exists once the model-in-the-loop **episode segmentation** has run (runtime step 4, see
  [agent-analysis.md → Runtime pipeline](../plans/agent-analysis.md)). Same `_core` rule, folded
  over each episode sub-stream (`iter_episodes`). Consequence: the `episode`-scope **baseline**
  is heavier to build than session/window — it needs the baseline corpus *segmented* first
  (a model pass), not just parsed.

There is **one rule per metric, applied identically at every scope.** For rereads that rule
is *"read tokens covering content already read, with no edit since"* (`metric_defs[m].rule`).
The only thing scope changes is how far back "already" reaches. So **the cross-session
signals fall out for free, with no second rule**: `rereads @ window` is the re-establishment
tax (you re-read, in a later session, content seen earlier in the window); `retouched @
window` is cross-session rework. No separate metric, no scope-specific rule.

### Same rule, longer memory → a baseline per scope

Because the rule is identical and only the memory length differs, a wider scope generally
*sees more* (window catches the cross-session repeats a session forgets) — though for
`unused_context` window can see *less* (a read edited in a later session is credited at window
scope). Either way a window rate is **not** the arithmetic sum of its session rows, and the
two are not directly comparable — which is exactly why each `(metric, scope)` keeps **its own
baseline**: a window-scope rate must be placed against a window-scope population, never a
session one.

The one thing that is **not** a rule difference: memory accumulates along the **active
conversation path** (the `active_stream` we already build), so abandoned rewind branches
never manufacture phantom re-reads. That is data selection, applied the same at every scope.

## Design decisions (locked 2026-06-07)

- **Tidy `measurements` table** (`metric × scope × unit` rows) + **flat scope-tagged
  `instances`** + **`metric_defs`** for per-metric prose. Filter/group by any axis; no
  per-metric name proliferation; no redundant `detection_scope` field.
- **Baseline lives per measurement row** — each `(metric, scope)` has its own population
  (session-scope and window-scope rates differ systematically, e.g. retouched ~3.4% vs ~6.4%).
- **JSON carries ALL instances**; top-N is a Markdown-only concern (`caps.instances_truncated`).
- **Missing baselines are `{percentile: null, note: …}`**, never faked — for any
  `(metric, scope)` the bootstrap has no sample for (`caps.baseline.missing` lists them).
- **No `value`/`cost` block.** This is the waste substrate; the value score composes at the
  *report* level via the window→diff/usage bridge ([src/haid/bridge/](../src/haid/bridge/),
  now built — `haid value --project/--session`).
- **refs carry `turn_ids` (paired with `tool_use_id` in `calls[]`) but no snippets** — the
  anchoring turn is free from `ToolCall.turn_id`; embedding transcript text is not.
- **Stable instance `id`** = `"<metric>/<scope>/<rank>"`, ranked by `token_weight` desc
  (ties → `file_id`, then first `tool_use_id`). Stable for a given input; not durable cross-run.

## Annotated example

```jsonc
{
  "schema_version": "1.0",
  "kind": "metrics",
  "haid_version": "0.1.0",
  "generated_at": "2026-06-07T12:00:00Z",

  "window": {
    "label": "boxBot — last 30d (35 sessions)",
    "project_path": "/home/jhart/software/boxBot",
    "days": 30,
    "n_sessions": 35,
    "sessions": [
      { "id": "a1b2c3d4", "path": ".../a1b2c3d4-….jsonl",
        "first_ts": "2026-05-10T09:12:00Z",
        "timelines": ["a1b2c3d4", "a1b2c3d4:rewind:1d4c7019"] }
    ]
  },

  "scopes": ["session", "window"],

  "metric_defs": {                                  // per-metric facts; ONE rule each, applied at every scope
    "rereads": {
      "token_denom_label": "total read tokens",
      "rule": "read tokens covering content already read, with no edit since; scope = how far back 'already' reaches",
      "carve_out": "A re-read after an edit/write to that file is legitimate (the file changed) and is excluded by construction. 'edit/write' = is_write(tc): native Edit/Write OR a Bash shell write (sed -i / > / tee).",
      "notes": ["One rule at any scope; scope only sets memory length.", "Reads = is_read(tc): native Read AND Bash shell reads (cat / sed -n / head); both numerator and denominator include them.", "…"]
    },
    "retries": {
      "token_denom_label": "total tool-attempt tokens",
      "rule": "tokens of the 2nd+ attempt of a signature that already failed, no success/change between",
      "carve_out": "One failure then a successful retry is healthy; only the same signature failing >=2x counts.",
      "notes": []
    },
    "retouched": {
      "token_denom_label": "total authored tokens (NATIVE edits + writes only)",
      "rule": "tokens rewriting a line the agent itself produced earlier within the memory window",
      "carve_out": "Editing pre-existing code is normal and excluded; only rewriting own output counts.",
      "notes": ["Tracked across sessions at window scope; rework compounds.", "Content-based: a Bash shell write (sed -i / >) has no recoverable old/new lines, so it neither contributes authored tokens nor counts as rework — it is invisible to THIS metric by design (it still clears rereads and grants unused-context credit).", "…"]
    },
    "unused_context": {
      "token_denom_label": "total read tokens",
      "rule": "tokens of a large read of a file never edited within the memory window",
      "carve_out": "Reading to understand is legitimate; flags only large unedited reads. 'read' = is_read(tc) and 'edited' = is_write(tc), so shell reads/writes participate.",
      "notes": ["Softest signal; window scope can see LESS (a later-session edit earns credit).", "A file edited only via a Bash shell write (sed -i) is credited as used, not flagged.", "…"]
    }
  },

  "measurements": [
    { "metric": "rereads", "scope": "window", "unit_id": "window",
      "count": 3, "denominator": 120, "token_weight": 26000, "total_tokens": 310000,
      "rate": 0.025, "token_rate": 0.084,
      "baseline": { "percentile": 50, "median": 0.153, "n": 6, "band": "around normal",
                    "source": "single-author bootstrap (window-scope)" } },

    { "metric": "rereads", "scope": "session", "unit_id": "a1b2c3d4",
      "count": 2, "denominator": 40, "token_weight": 900, "total_tokens": 52000,
      "rate": 0.05, "token_rate": 0.017,
      "baseline": { "percentile": 46, "median": 0.017, "n": 81, "band": "around normal",
                    "source": "single-author bootstrap (session-scope)" } },

    { "metric": "retouched", "scope": "window", "unit_id": "window",
      "count": 41, "denominator": 488, "token_weight": 35400, "total_tokens": 421000,
      "rate": 0.084, "token_rate": 0.084,
      "baseline": { "percentile": 82, "median": 0.064, "n": 13, "band": "above normal",
                    "source": "single-author bootstrap (window-scope)" } }
    // …one row per (metric, scope, unit)
  ],

  "instances": [
    { "id": "rereads/window/1", "metric": "rereads", "scope": "window",
      "session_id": "b2c3d4e5", "timeline": "b2c3d4e5",
      "detail": "src/config.ts lines 1-40 re-read (100% already in context, no edit since)",
      "token_weight": 14000,
      "refs": {
        "file_id": "src/config.ts",
        "session_ids": ["b2c3d4e5"],
        "calls": [ { "tool_use_id": "toolu_…", "turn_id": "…", "session_id": "b2c3d4e5" } ],
        "line_span": [1, 40]
      } },
      // window scope: the re-read here in b2c3d4e5 of content first read in an EARLIER session.
      // session_id is the session the re-read occurred in (not null); the earlier session that
      // established the content is not yet linked in refs (a noted future enrichment).

    { "id": "retouched/session/1", "metric": "retouched", "scope": "session",
      "session_id": "a1b2c3d4", "timeline": "a1b2c3d4",
      "detail": "src/auth.ts: edit rewrites 6 line(s) written earlier in this session",
      "token_weight": 480,
      "refs": {
        "file_id": "src/auth.ts",
        "session_ids": ["a1b2c3d4"],
        "calls": [ { "tool_use_id": "toolu_01ABC…", "turn_id": "f3a9c1d2-…", "session_id": "a1b2c3d4" } ],
        "line_span": null,
        "sample_lines": ["export async function authenticate(", "…"]
      } }
  ],

  "caps": {
    "notes": ["a1b2c3d4: 2 subagents un-stitched (null toolUseId)"],
    "baseline": { "source": "single-author bootstrap (placeholder until community benchmark)",
                  "have": ["<metric>@<scope> with a population sample…"],
                  "missing": ["…any (metric, scope) with no sample yet — placement omitted, never faked"] },
    "limits": [
      "Line lineage is within-session only (cross-session lineage is Phase 4).",
      "Token weights are per-artifact byte/4 counts (right granularity for same-kind ratios; cost.py's normalized tokens are per-message and can't be attributed to one read — they enter at the waste→value reconciliation, not here).",
      "Window scope sees cross-session repeats a session forgets — placed against a window-scope baseline, not comparable to session rates.",
      "Bash shell IO is counted (is_read / is_write), but a shell WRITE has result_bytes~=0 (a sed -i / > emits no stdout — the bytes went to the file, not context), so its token weight is ~0. That is honest authoring cost, NOT change magnitude; reconcile with git for the size of a shell edit."
    ],
    "instances_truncated": false
  }
}
```

## Field reference

### Top level
| Field | Type | Notes |
|-------|------|-------|
| `schema_version` | string | Bump on any breaking change. |
| `kind` | `"metrics"` | Distinguishes the substrate from a future composed `"report"`. |
| `haid_version` | string | Tool version. |
| `generated_at` | ISO-8601 | Stamped by the CLI. |
| `window` | object | Window provenance (see below). |
| `scopes` | string[] | The scopes present in this run — `["session","window"]` deterministic-only; `["session","episode","window"]` once episode segmentation has run. |
| `metric_defs` | object | Per-metric, scope-independent facts (keyed by metric name). |
| `measurements` | array | The `metric × scope × unit` table. |
| `instances` | array | Findings, tagged with `metric` + `scope` + unit. |
| `caps` | object | No-silent-caps disclosures. |

### `window`
`label`, `project_path`, `days` (null if an explicit session list was used), `n_sessions`,
`sessions[]` of `{ id, path, first_ts, timelines[] }`. `id` is the 8-char stem used as
`session_id`/`unit_id` everywhere else. **Once episode segmentation has run**, this also carries
`episodes[]` of `{ id, purpose, sessions[], first_ts, last_ts }` (the unit referenced by
`episode`-scope `unit_id`s) — added at runtime step 4, see
[agent-analysis.md](../plans/agent-analysis.md).

### `metric_defs[name]`
`token_denom_label`, `rule` (the single detection rule, applied at every scope), `carve_out`,
`notes[]`. There is no per-scope rule — scope only sets the memory window.

### `measurements[]` (one row per metric × scope × unit)
| Field | Type | Notes |
|-------|------|-------|
| `metric` | string | `rereads` \| `retries` \| `retouched` \| `unused_context`. |
| `scope` | string | `session` \| `episode` \| `window` (\| `pr`, `all_time` later). |
| `unit_id` | string | `"window"` for window scope; the `session_id` for session scope; the `episode_id` for episode scope. |
| `count`, `denominator` | int | Flagged instances / opportunities at this scope+unit. |
| `token_weight`, `total_tokens` | int | Wasted vs total tokens of this kind. |
| `rate`, `token_rate` | float | `count/denominator`, `token_weight/total_tokens`. |
| `baseline` | object | `{percentile, median, n, band, source}` or `{percentile: null, note}`. |

`band` ∈ {`far above normal`, `above normal`, `around normal`, `below normal`}.

### `instances[]`
| Field | Type | Notes |
|-------|------|-------|
| `id` | string | `"<metric>/<scope>/<rank>"`, stable for a given input. |
| `metric`, `scope` | string | Which measurement family this finding belongs to. |
| `session_id` | string \| null | The session the finding *occurred* in (e.g. for a window-scope re-read, the session where the re-read happened). `null` only when the finding isn't tied to one session — `unused_context` (window-aggregate) and a `retries` signature spanning sessions. |
| `timeline` | string | Timeline label, or `"window"`. |
| `detail` | string | Objective, human-readable measurement. |
| `token_weight` | int | This finding's wasted-token estimate (byte/4). |
| `refs` | object | The pointers (below). |

`refs`: `file_id`, `session_ids[]`, `calls[]` of `{ tool_use_id, turn_id, session_id }`, and
optional metric-specific keys — `line_span` ([start,end] or null), `sample_lines[]`
(retouched), `signature` (retries), `n_sessions` (only when a finding's calls span >1 session,
e.g. a cross-session `retries`). *Note: a window-scope re-read does not yet link the earlier
session that first established the content — a noted future enrichment.*

## Markdown view

Derived from the same data: window headline (window-scope rows sorted by baseline
percentile), per-metric sections with top-N ranked instances (the only place top-N applies),
a per-session table (the session-scope rows), and a "Limits & caps" footer mirroring `caps`.
Pure measurement — no remedy lines.

## Related
- Metric definitions: [detectors.md](detectors.md).
- Build step + scope doctrine: [phase1-build.md](../plans/phase1-build.md) Step 5 + §0.5.
- The why-passes that consume this: roadmap [Phase 2 / Phase 3](../plans/roadmap.md).
