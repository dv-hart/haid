# Tooling landscape — what to build on

Findings from a research pass on the existing ecosystem. The goal is to **build on
existing tools rather than reinventing**, while keeping HAID's differentiator
(diagnosis + coaching over a causal session graph) which no existing tool covers.

> Confidence note: licenses/maturity marked **[unverified]** could not be
> independently confirmed and must be checked before depending on them. The format
> details that matter most have been verified locally — see
> [claude-code-data-format.md](claude-code-data-format.md).

## The incumbent: ccusage — reference only

- **Repo:** https://github.com/ryoppippi/ccusage · TypeScript · MIT · very active.
- The dominant local usage analyzer (Claude Code, Codex, Copilot, OpenCode, …).
  Reads `~/.claude/projects/*.jsonl`, produces daily/weekly/session cost+token
  reports. Has a documented library surface (`ccusage/data-loader`) and an MCP
  package.
- **Recommendation: reference, don't depend.** It solves the cost/token problem
  HAID explicitly is *not* solving, and its loader is tuned for cost aggregation,
  not turn-graph reconstruction or tool-call causality. **Worth studying** for its
  `~/.claude/projects` discovery + **cross-resume dedup** logic (a real gotcha:
  resumed sessions can duplicate records).

## Other session/transcript tools — reference only

None does diagnosis/coaching or builds a causal graph with waste metrics. Useful
as parsing/rendering references:

| Tool | Lang | License | Notes |
|------|------|---------|-------|
| [claude-code-log](https://github.com/daaain/claude-code-log) | Python | MIT | JSONL → HTML/MD timeline + token tracking. Closest Python prior art; good parse/render reference. |
| [claude-code-viewer (d-kimuson)](https://github.com/d-kimuson/claude-code-viewer) | TS | [unverified] | Notable for **strict schema validation** of every line — good completeness reference. |
| [claude-code-transcripts (simonw)](https://github.com/simonw/claude-code-transcripts) | Python | [unverified] | Publishes transcripts to clean HTML. Export reference. |
| [ccusage-py](https://github.com/m6k/ccusage-py) | Python | [unverified] | **Streaming/generator** JSONL processing — memory-efficient parsing reference. |
| claude-history, claude-code-history-viewer, claude-JSONL-browser | mixed | [unverified] | Viewers/search; reference only. |

**Takeaway:** many viewers/exporters exist; HAID's niche (waste diagnosis +
coaching over a causal graph) is genuinely unfilled.

## Typed JSONL parsing — the most reusable layer

- **[`claude-code-transcripts` Rust crate](https://docs.rs/claude-code-transcripts)**
  · MIT OR Apache-2.0. Strongly-typed `Entry` variants for *every* line kind
  **plus a round-trip validator to detect schema drift** — the most rigorous typed
  model found. **Port its enum layout and especially its schema-drift validator
  idea** regardless of our language. This is the single most valuable pattern in
  the landscape (the format is undocumented and drifts).
- **[`@constellos/claude-code-kit`](https://www.npmjs.com/package/@constellos/claude-code-kit)**
  · TS · Zod transcript schemas + `parseTranscript` + notably **`getAgentEdits()`**
  (directly relevant to the re-touched-lines metric). License/version
  **[unverified]** — verify before depending. Candidate to *depend on* if we go TS.

⚠️ There is **no official Anthropic schema** for the transcript format. Whatever
we build, copy the schema-drift-validator pattern.

## Graph library

Needs: in-memory build, **backwards traversal** (node → trigger), weighting.
Session graphs are small (hundreds–thousands of nodes), so library choice is about
ergonomics, not performance.

- **Python — networkx** · BSD · `DiGraph` with native `predecessors()`/
  `ancestors()` (= backwards traversal), arbitrary node/edge attributes.
  **Recommended.** Its perf weakness is irrelevant at our graph sizes.
- **Python — rustworkx** · faster but integer-index model, more friction; speed
  wasted at our scale. **Avoid for now.**
- **JS/TS — graphology** · MIT · mature, `graphology-traversal` (BFS/DFS). The
  pick if we go TS; some satellite packages are stale, core is maintained.

## Persistence

- **SQLite** — recommended as the persistence/cache layer (parsed transcripts +
  computed metrics; cache by `(session_id, file_hash)`; incremental re-parse of
  appended bytes). Embedded, file-per-project, portable.
- **kuzu** (embedded graph DB) — ⚠️ **repo archived Oct 2025.** Avoid building on
  an archived DB. (An "acquired by Apple" rumor is **[unverified]**.)
- **DuckDB** — recursive CTEs can express traversals but it's awkward as a graph
  engine; reference only.
- **Verdict:** we almost certainly **don't need a graph DB.** Build in-memory
  (networkx), persist computed artifacts to SQLite. See
  [ADR-0002](decisions/0002-graph-build-vs-persist.md).

## Skill / plugin packaging

Canonical skill structure (per Anthropic's own skill-development skill):

```
skill-name/
├── SKILL.md          # YAML frontmatter (name, description req'd) + Markdown body
├── scripts/          # executable code, run without loading into context
├── references/       # docs loaded on demand (progressive disclosure)
└── assets/           # templates/icons
```

Conventions that matter: `description` in **third person with literal trigger
phrases** (drives activation accuracy); body imperative, ~1.5–2k words, push
detail into `references/`. **Distribute as a plugin** (`.claude-plugin/plugin.json`
+ `skills/`), with the heavy analysis logic in `scripts/` (a Python or Node CLI)
so it runs token-efficiently outside the model context. Plugin skills are
namespaced (`/haid:report`).

## Recommended stack

The defining choice is **language** (see
[ADR-0001](decisions/0001-language-and-stack.md)). Current recommendation —
**Python core**:

- **Parsing:** own typed parser (Pydantic/dataclasses) modeled on the Rust
  crate's `Entry` variants + its schema-drift validator; streaming approach à la
  `ccusage-py`.
- **Graph:** networkx (`DiGraph`, `predecessors`/`ancestors`).
- **Persistence:** SQLite, cache by file-hash.
- **Packaging:** Claude Code **plugin** wrapping a **skill**, analysis in
  `scripts/` as a `uvx`-installable CLI (like claude-code-log).

TypeScript is the viable alternative (depend on `@constellos/claude-code-kit` +
graphology) if we'd rather live in the Node ecosystem — decided in ADR-0001.

## Open items to verify before committing a dependency

1. `@constellos/claude-code-kit` **license and version**.
2. Licenses for the viewer tools marked [unverified] above.
3. ccusage's cross-resume **dedup** logic — replicate the approach.
