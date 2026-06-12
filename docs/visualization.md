# Visualization — the time-layered bus diagram

> **Status: design-stage; scheduled as Phase 1.5 (MVP).** Captures the converged model
> from the data-structure discussion. Layout/rendering details (marked ⟳) are for focused
> follow-up. Promoted from the old Phase 4.5 — seeing where tokens go is core to the tool,
> and its dependencies (multi-session window + token-weighted IO graph) are already built.
> See [roadmap.md](../plans/roadmap.md).

> **This is a rendering of the analysis, not a shortcut around it.** Every visual channel
> is fed by the deterministic pipeline that already exists: bus **width = the metric
> token-weights** (`est_tokens(result_bytes)`), the read/edit **edges = the L1 IO graph**
> (reads/produces/edits), the **right-margin cost axis = `scoring/cost.py`**. So
> PARSE→GRAPH→METRICS is a hard **prerequisite** — the diagram has nothing to draw without
> it. The payoff is making that computed analysis legible at a glance, not bypassing it.

> **Shell IO draws too.** The IO edges include **Bash-derived** reads/writes (`is_read`/
> `is_write`; edge `via:"shell"`), not only native Read/Edit/Write. One consequence is
> load-bearing for the width channel and is spelled out in
> [§Derived (shell) writes](#derived-shell-writes): a shell write's token weight is ~0, so
> it must be drawn as a **present-but-minimum-width, marked** edge — never omitted, never
> fattened.

The session graph is rendered as a **layered (Sugiyama-style) diagram with
orthogonal edge routing and edge bundling** — think circuit schematic / subway map
/ Factorio belt bus, not a force-directed hairball. You read it top-to-bottom like
the conversation itself, and file traffic bundles into clean colored buses.

## Core layout

- **Y axis = time** (`timestamp` within agent scope). First message on top; the
  agent and user work downward. This is the L0 spine made visible.
- **Central agent spine** = the vertical column of user messages, assistant turns,
  and tool calls, in time order.
- **File objects live in side gutters**, reached by edges from the spine.

```
   time │  agent spine (center)         file buses (gutters)
    ↓   │
 t0  ───┤ ● user: "build the report"
        │ │
 t1  ───┤ ◆ assistant (think/text)
        │ │
 t2  ───┤ ▣ Read main.py ───────────────┐ (blue, thin)         ┌─[main.py]
        │ │                              └──▶ bus ══════════════┥  (blue)
 t3  ───┤ ▣ Read main.py ───────────────┐                      │
        │ │                              └──▶ ═══════════════════┙ (bus thickens)
 t4  ───┤ ▣ Edit utils.py ◀──────────────────────────────────[utils.py] (orange)
        │ ├──┐ (subagent branches)
 t5  ───┤ │  └ ▣▣▣ sub-spine …
 t6  ───┤ │◀─┘ (rejoins at result)
```

## Encoding rules

| Channel | Encodes | Notes |
|---------|---------|-------|
| **Vertical position** | time | the conversation flows down |
| **Left gutter** | **inbound** — reads / context-in / web fetch | "information coming in" |
| **Right gutter** | **outbound** — writes / edits | "changes going out" |
| **Edge direction** | read vs. write | read: file → spine (inflow); write: spine → file (outflow) |
| **Color** | **file identity** | all traffic to one file shares its color (blue main.py) |
| **Width** | **token volume**, **log-scaled** (see below) | a heavily-read file gets a *fat* bus = "this dominated context" |
| **Bus track** | one vertical lane per file | edges bundle into the lane; only short horizontal stubs leave the spine |

The bundling is the clutter killer: edges don't each draw a long line; they merge
into the target file's bus. Because the same token-weights drive both the metrics and
the bus widths, **the diagram and the metrics agree by construction** — a fat bus to a
file that's never edited is the `unused_context` finding made visible, and a stack of
edges re-touching one file is the `retouched` finding made visible. The diagram
isn't computing these ahead of the metrics; it's rendering the same numbers so they read
at a glance.

### Width scaling (decided)

Token volume spans a huge dynamic range (a ~10-token grep result vs. a 500k
cumulative bus = ~50,000×). Linear width is unusable — the small lines go sub-pixel
or the big ones swamp the canvas. So:

- **Map in the log domain**, rendered as **a few discrete gauge tiers** (e.g.
  hairline / thin / medium / thick / bus / mega-bus) on log-spaced thresholds.
  Discrete tiers read more reliably than continuous width and avoid false precision —
  and match the wire-gauge / belt-tier metaphor.
- **Clamp both ends** — min ≥1–2px so tiny lines stay visible; a max so a monster
  bus doesn't break the layout.
- **Don't let log undersell the monsters.** Log compresses the top end, but the
  whole point of width is "this dominated context." Give the top tier a distinct
  treatment (hatching / brighter + a token-count badge) so a 500k bus is
  unmistakable even with its width capped. Compression buys legibility; the badge
  buys back honesty.
- **Global, fixed scale — not per-diagram.** Anchor the log scale to a fixed
  reference (e.g. the context-window size) so a given width *always* means the same
  token count, in every view. Per-diagram auto-scaling would make the same 200k bus
  look different across sessions and break cross-session comparability (a headline
  feature).
- Same scale applies to both stubs and buses, so a fat bus visibly = the sum of its
  stubs.

### Derived (shell) writes

A write performed through Bash — `sed -i`, `cmd > f`, `>> f`, `| tee f`, `cp`/`mv`
(edge `via:"shell"`, `derived_write` on the ToolCall) — is a real outbound edge but has
**`result_bytes ≈ 0`**, so its `est_tokens` weight is ~0 and the default width rule would
draw it sub-pixel. That zero is *correct, not missing data*: the model spent only the few
tokens of the **command** (`sed -i 's/a/b/' f`), not the tokens to author the file's new
content — the changed bytes went to disk, never into context. A shell write is **leverage**:
a tiny instruction with a potentially large file effect. The diagram must encode that
honestly without making the write vanish:

- **Draw it at the min-width clamp** (the same ≥1–2px floor that keeps any tiny stub
  visible). The write *happened*; never drop the edge just because it's cheap.
- **Mark it as derived**, distinct from an authored write — e.g. a dashed/hatched stub or
  a small glyph (`~`) keyed off `via:"shell"`. This lets the eye separate "the model
  authored N tokens here" (solid, width = real cost) from "the model issued a cheap
  transform" (marked, min width).
- **Do NOT fabricate a width from change magnitude.** Width = authoring/context cost, by
  definition; it is *not* lines-changed. We can't recover a shell write's line count from
  the transcript anyway (that needs git reconciliation — see
  [claude-code-data-format.md](claude-code-data-format.md) gap #1). If a future view wants
  to show change *size*, that is a **separate channel**, not this width.
- **Consequence for the metric overlays:** because a shell write contributes no content,
  it does **not** thicken a `retouched` (rework) bus — that metric is content-based and
  keys on native Edit/Write old/new lines. A shell write *does* clear the re-read
  seen-ranges (so it correctly prevents a false `rereads` finding on a subsequent read)
  and *does* grant `unused_context` credit (the file was used). So a file edited only via
  shell shows a thin marked outbound stub, no rework bus, and no false inbound re-read
  bus — all three agreeing with the metrics by construction.
- **`tee` is the one exception:** `tee` echoes its input to stdout, so `result_bytes` is
  the real content and the bus gets genuine width — also correct, because that content
  did pass through context.

## Margins — running cost (and time)

The two margins are **secondary scales**, not uniform rulers: their gridlines land at
non-uniform Y positions, and that non-uniformity is the payoff — *tick density is a free
heatmap* of where the session spent.

- **Right margin = cumulative normalized tokens** (the cost denominator from
  [cost.py](../src/haid/scoring/cost.py) — *normalized tokens, not dollars*; see
  [scoring-rubric.md](scoring-rubric.md#cost-side--built-2026-06-06-normalized-tokens-never-dollars)).
  Gridlines (e.g. every 50k nTok) fall wherever the session *crossed* that total, so where
  they bunch = an expensive stretch, where they're sparse = a cheap one. You read
  cost-per-step as slope, without computing anything.
- **Left margin = wall time** (later). Same non-uniform treatment. Grey out / collapse long
  idle inter-turn gaps — those are usually the human being away, not slowness, and would
  otherwise dominate the canvas and misread as a slow session.

Both share the **global fixed anchor** the width scale uses (§Width scaling), so a 200k-nTok
session's cost axis is consistently twice a 100k one's — preserving cross-session
comparability.

### v1 scope (decided)

**v1 = the running cumulative normalized-token tally on the right margin only.** That single
element is the obvious design win: total cost by height, expensive regions by tick density.
Keep it monotonic and anchored.

**Deferred to explore/test (NOT v1):** encoding the per-step *token-type mix* (input / output /
cache-write / cache-read). The intended direction — to surface "by step 30 cost is dominated by
cache reads" — is a **stacked-area cost ribbon** (the cumulative axis split into bands by type)
or mini per-step stacked bars / pie glyphs. Rationale and the trap to avoid are in Open
refinements; we validate the plain tally first before adding a second visual language.

## Branching & rejoining

- **Multiple tool calls in one turn** → small parallel branchlets off the spine
  that rejoin (the ~4% `parentUuid` branching from real data).
- **Subagents** → an indented **sub-spine** that branches at the `Agent` call and
  rejoins at its result (linked by `meta.toolUseId`/`agentId`). The subagent's own
  reads/edits draw their own buses within its lane.

## Cross-session view

Files are **shared nodes across sessions.** Stack sessions and let every session's
buses terminate on the *same* file objects. This is the visual payoff of
`File id = repo_id + relpath`, and it makes the two cross-session metrics legible at a glance:

- **Cross-session rework (`retouched`)** — a file every session keeps *re-touching*
  grows a giant multi-session *write/edit* bus: **co-churn and rework you can see from across
  the room.**
- **Re-establishment tax (`rereads @ window`)** — a file *re-read* at the start of many
  sessions but never edited grows a fat recurring *inbound* bus down the left gutter: "you
  keep rediscovering this — pin it." This is the cross-session re-read signal made visible,
  and a primary reason the diagram is a headline part of the MVP, not a late add.

## Rendering approach ⟳

- The hard part is **orthogonal routing + crossing-minimization** (layered graph
  drawing). Candidate: **ELK (Eclipse Layout Kernel)** — layered layout + orthogonal
  routing out of the box. `dagre` is lighter but weaker at orthogonal routing.
  Prototype with ELK before hand-rolling.
- Right angles only — no curves, no diagonals. Bundle same-target edges; merge into
  the file's track.

## Interaction with the analysis

The diagram is not just pretty — it's the **triage surface** for the metrics it renders.
The same Tier-1/2 outputs that the text report ranks (fat buses = unused-context,
re-touch clusters, retry loops, later drift points) are where the eye — and later phases'
expensive attention — should go first. Report and diagram are two views of one analysis:
the report ranks and hedges; the diagram shows the shape.

## Open refinements (for a focused session) ⟳

- **A file that is both read and edited** — show inflow-from-left *and*
  outflow-to-right on one shared object ("consumed and transformed"), or pick one
  side? (Leaning: show both; it's legible and meaningful.)
- **Track ordering** to minimize crossings (by first-touch time? by directory?).
- **Scale** — huge sessions have hundreds of tool calls; need collapsing
  (fold an episode into a band), zoom, or LOD.
- **What's clickable** — click a bus → the reads/edits; click a turn → its tokens;
  click a drift point → the carried-cost callout.
- **Color budget** — per-file color runs out fast; may need color-by-directory with
  per-file shade, or color only the "interesting" files and grey the rest.
- **Width tier thresholds** — the exact log boundaries and gauge count to tune on
  real sessions (the *approach* is decided above; the cutoffs are not).
- **Y = step vs. wall-clock time.** The doc currently says Y = timestamp, but a step-based
  layout (one equal row per tool-call/turn) stays readable where wall-time layout doesn't (a
  3s grep vs. a 5min build occupy wildly different height; idle gaps swamp the canvas). With
  step-based Y, the two margin scales (§Margins) restore the time/cost truth, and the
  divergence between them is itself diagnostic (slow-but-cheap = waiting/thinking;
  fast-but-expensive = a generation burst). Decide on real sessions.
- **Token-type mix encoding (deferred from v1; §Margins).** Goal: spot cost *regime shifts*
  ("by step 30 it's all cache reads") at a glance. **Do NOT map the ratio to RGB directly** —
  4 types → 3 channels makes muddy browns, can't be decoded quantitatively, and fails for
  red/green colorblindness (~8% of men). The goal is *categorical* (which type dominates), not
  quantitative-shade-reading. Preferred direction: a **stacked-area cost ribbon** on the right
  margin (cumulative-token height split into type bands → regime shifts read as a band
  swelling) — uses area/length + categorical color, never shade-of-one-color. Use a
  colorblind-safe categorical palette (Okabe–Ito) for ≤4 types. **Conflict to manage:** color
  is already spent on *file identity* (buses). Segregate the two languages — file color in the
  **gutters**, token-type color in the **right-margin ribbon only** — and reserve the ~4
  token-type hues so they're never assigned to a file. Prototype before adopting.

## Related
- Records → graph that feeds this: [data-structure-report.md](data-structure-report.md).
- Node/edge schema: [session-graph-design.md](session-graph-design.md).
