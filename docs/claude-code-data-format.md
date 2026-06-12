# Claude Code on-disk data format

A reference for everything HAID parses. **The format is undocumented by Anthropic
and drifts across Claude Code versions.** Treat every field as version-dependent
and validate at parse time.

> **Companion:** [data-inventory.md](data-inventory.md) has the exhaustive,
> data-grounded tables (per-tool inputs, structured `toolUseResult` schemas,
> compaction/subagent/attachment detail) from analyzing 38 real sessions across
> 6 versions. This doc is the narrative reference; that one is the field catalog.

Legend for each claim:
- ✅ **Verified** against real session files on this machine (Claude Code `v2.x`,
  observed `version` field present on records).
- 📄 **From docs/research** — plausible, not yet confirmed against local files.
- ⚠️ **Correction** — local files contradict commonly-cited docs.

> Verification context: inspected real sessions under
> `~/.claude/projects/C--Users-jhart-Documents-DataVine-c7-connector/` in
> 2026-06. Re-verify against your own files and version before relying on
> anything here.

## Storage layout ✅

```
~/.claude/                                    # CLAUDE_CONFIG_DIR overrides base
├── projects/
│   └── <encoded-project-path>/               # cwd with separators → dashes
│       ├── <session-uuid>.jsonl              # the session transcript
│       └── <session-uuid>/                   # per-session sidecar dir
│           ├── subagents/
│           │   ├── agent-<id>.jsonl          # one transcript per subagent ✅
│           │   └── agent-<id>.meta.json      # subagent lifecycle metadata ✅
│           └── tool-results/
│               └── <shortid>.txt             # overflow for large tool outputs ✅
├── sessions/            ├── shell-snapshots/
├── tasks/               ├── backups/
├── plugins/             └── session-env/
```

- **Path encoding** ✅: `C:\Users\jhart\Documents\software\HAID` →
  `C--Users-jhart-Documents-software-HAID`. Separators (`:` `\` `/`) become `-`.
- **Session file** ✅: `<session-uuid>.jsonl`, newline-delimited JSON, append-only.
- **Subagent transcripts** ✅: in `<session-uuid>/subagents/`, named
  `agent-<id>.jsonl`, each with a sibling `agent-<id>.meta.json`. ⚠️ **Stitch
  TOP-LEVEL only** — `subagents/` may also hold a nested `workflows/` tree with
  thousands of workflow-agent files (2581 in one real session); a recursive glob is
  catastrophically wrong.
- **Overflow tool results** ✅: in `<session-uuid>/tool-results/`. Filenames vary —
  **both** short random ids (`b5sbdrfml.txt`) **and** `toolu_<id>.txt` forms exist
  across versions — so don't guess them; follow `toolUseResult.persistedOutputPath`
  (an absolute path) instead, with a sidecar `tool-results/<basename>` fallback.

## Record types actually observed ✅

A real 799-line session contained these `type` values (counts shown):

| `type` | count | what it is |
|--------|------:|------------|
| `assistant` | 422 | model turn: text / thinking / `tool_use` blocks + `usage` |
| `user` | 191 | user prompt **and** tool results (see below ⚠️) |
| `ai-title` | 61 | auto-generated session title metadata |
| `last-prompt` | 54 | bookkeeping of the most recent prompt |
| `queue-operation` | 32 | message queue enqueue/dequeue events |
| `attachment` | 29 | injected context (skill listings, deferred-tool deltas, etc.) |
| `system` | 10 | **hook execution records** (see below) |

Across 65 sessions, additional **uuid-less metadata types** appear and must be
registered or a drift detector fires on every session: `custom-title`, `mode`,
`permission-mode`, `pr-link` (carries `prNumber`/`prUrl` — useful for git/PR
grouping), `agent-name`, and `file-history-snapshot` (a rewind-checkpoint **manifest** —
points at a separate full-file blob store, NOT inline content; 918 in one boxBot session but
**zero** in newer Desktop sessions — see "Checkpoint / file-history storage" below). All carry
no `uuid`, so none enters the tree.

⚠️ **No `summary` record** appeared — this session was never compacted. ⚠️ **No
top-level `tool_result` type** exists. 📄 A `summary` type for compaction is
widely documented; verify shape against a genuinely compacted session before
relying on it (see [open-questions](../plans/open-questions.md)).

## Checkpoint / file-history storage — version-dependent (TWO regimes) ✅

Verified on real data (2026-06-07). Checkpoint/rewind file state is stored two different ways
depending on the Claude Code version:

- **Legacy CLI (~v2.1.145, e.g. WSL):** a full-file **versioned blob store** at
  `~/.claude/file-history/<session-id>/<hash>@v<n>` — whole file contents at each checkpoint,
  **not diffs**. Indexed by in-transcript `file-history-snapshot` records, which are a
  **manifest, not content**:
  `{type:"file-history-snapshot", messageId, isSnapshotUpdate, snapshot:{trackedFileBackups:{<path>:{backupFileName:"<hash>@v<n>", version, backupTime}}}}`.
  A file becomes "tracked" once Claude edits it via a tool; each snapshot captures the actual
  disk bytes at that checkpoint.
- **Newer Desktop (~v2.1.161+):** **no** `~/.claude/file-history/` dir and **zero**
  `file-history-snapshot` records; checkpoint state moved to **git** (per-session
  `.claude/worktrees/` observed; Desktop requires git and blocks non-git sessions). The blob
  store is therefore LEGACY.

Claude Code's own rewind **cannot** undo bash-tool writes (`sed -i`, `>`, `cp`) in **either**
regime — a documented limitation. **HAID does not depend on this store**: it reconstructs the
scored diff from the transcript (replay; see [architecture.md](architecture.md) "Working
backwards"), which works on every install including non-git ones.

### Common envelope fields ✅

Seen across most records: `type`, `uuid`, `parentUuid`, `timestamp`,
`sessionId`, `cwd`, `version`, `gitBranch`, `userType`, `entrypoint`,
`isSidechain`. Message-bearing records add `message`. The `version` field on
every record is your per-record compatibility key — branch parsing on it.

### `user` records ✅⚠️

Carry `message` (`role: "user"`, `content` string or content-block array) and
`promptId`. **Crucially, tool results come back as `user` records too**: such
records carry a `toolUseResult` field and a `sourceToolAssistantUUID` pointing at
the assistant turn whose `tool_use` they answer. In the inspected session
`toolUseResult` and `sourceToolAssistantUUID` each appeared 178 times.

➡️ **Parser rule (verified across 7509 result records):** pair a call to its result
by the **`tool_use_id` inside the result's `tool_result` content block** — present
100% of the time. There is **no top-level `sourceToolUseID`** (0/7509 — an earlier
claim to the contrary was wrong), and `sourceToolAssistantUUID` resolves only to the
assistant *turn*, not the specific call. Do **not** look for a `type:"tool_result"`
record; there isn't one.

### `assistant` records ✅

`message.content` is an array of blocks: `text`, `thinking` (with `signature`),
and `tool_use` (`id` like `toolu_…`, `name`, `input`). Also `requestId`,
`stop_reason`, and a `usage` object. Token usage fields observed/expected 📄:
`input_tokens`, `output_tokens`, `cache_creation_input_tokens`,
`cache_read_input_tokens`, plus nested `cache_creation` (ephemeral 1h/5m) and
`server_tool_use` (web search/fetch counts). Older sessions may lack the cache
fields.

### `system` records — hooks ✅

These log **hook execution** and carry: `subtype`, `hookCount`, `hookInfos`,
`hookErrors`, `preventedContinuation`, `stopReason`, `hasOutput`, `level`,
`toolUseID`. This means hook activity is **auditable from the transcript** — HAID
can see when a hook fired, blocked continuation, or errored. Relevant because
HAID *recommends* hooks as fixes and can later confirm they took effect.

### MCP attribution ✅

`attributionMcpServer` / `attributionMcpTool` fields appeared (twice) — identifies
which MCP server/tool produced a result.

## Threading — the session is a forest ✅ (verified across 65 sessions)

Records link via `uuid` ← `parentUuid`. This is **not a line and not a single
tree — it is a forest** (a file can hold >1 root). Dedup is by `uuid` (no
collisions seen; uuid-less metadata records sit outside the tree). Order by
`timestamp` within an `agentId` scope **and within ONE root→leaf path** — never
across the flattened file, or rewound branches interleave.

**Four distinct branch shapes — do not conflate them:**

| Shape | On-disk signature | Rewind? |
|-------|-------------------|:-------:|
| **Structural fork** | one assistant turn, ≥2 children: parallel `tool_use`→multiple `tool_result`, async-subagent attach, or the final reply sitting past `leafUuid` | No — normal graph |
| **Rewind / abandoned branch** | an **off-active-path `user` *text* prompt** + its descendants; two sub-shapes: a *sibling fork* (one parent, ≥2 children with the same/edited prompt = edit-and-resubmit) or an *off-path chain* | **Yes** |
| **Resume (new trunk)** | a 2nd `parentUuid:null` root in the same file (rare; often a *"Continue from where you left off."* prompt) | No |
| **Interrupt** | `"[Request interrupted by user]"` user record | Branch stops |

- **Active branch** = the latest `last-prompt.leafUuid`, walked via `parentUuid`
  to a root. ⚠️ This pointer is **absent/dangling in ~18% of files (10/55 boxBot)**,
  so a fallback is mandatory: the latest-`timestamp` main-thread leaf.
- **~20% of sessions branch** (11/55 boxBot). `isSidechain: true` marks
  subagent/parallel-stream records.
- ⚠️ **False-positive trap:** synthetic-content records *look* like user prompts but
  are not instructions — content wrapped in `<command-name|message|args>`,
  `<local-command-stdout|stderr|caveat>` (slash/`/login` clusters), `<bash-input|
  stdout|stderr>` (ctrl-B bash mode), or `<task-notification>` (bg-task notices), plus
  `tool_result` carriers and the interrupt marker. Exclude all of these from
  rewind/instruction detection.

➡️ **Why HAID cares:** waste metrics must be scoped **within one timeline** (a
root→leaf path the model actually experienced). Two reads on *different* branches
are not a redundant re-read — the model never had the first in context on the
second. Count instruction roots once: branches are alternate continuations of one
instruction, not new roots.

## Compaction ✅ (now verified)

⚠️ The widely-cited `type:"summary"` record is **not** how current versions do it.
Verified shape:
- A `system` record, `subtype:"compact_boundary"`, carrying `compactMetadata`
  `{trigger:"manual"|"auto", preTokens, postTokens, preCompactDiscoveredTools[],
  durationMs}`, plus `logicalParentUuid` and `isMeta:true`. Real examples observed:
  manual 262,707→6,702 tokens; auto 168,373→7,453.
- The summary itself is a **`user` record with `isCompactSummary:true`**.
- Post-compaction context is re-injected via an `attachment` of subtype
  `compact_file_reference`.

The JSONL is append-only, so pre-compaction turns are retained on disk even though
runtime context dropped them. This makes "agent acted on an instruction compaction
dropped from context" a *detectable* failure, and `preTokens`/`postTokens` give the
compaction magnitude for free. See [data-inventory.md](data-inventory.md).

## Truncation 📄 (verify)

- Large `Read`/`Bash` outputs are truncated in the JSONL and overflow to
  `tool-results/<shortid>.txt` ✅ (overflow dir confirmed; exact threshold not
  measured). Commonly cited limits: ~30k chars default, `BASH_MAX_OUTPUT_LENGTH`
  up to ~150k.
- **Edit inputs are preserved intact** 📄: `Edit.old_string`/`new_string` are
  load-bearing and not truncated — this is what makes precise per-turn diffs
  possible. Verify against your own large edits.
- `Write` logs the **resulting content, not a delta** 📄 — "what changed" requires
  diffing against prior known state.
- ⚠️ **`originalFile` (full pre-edit content on Edit/Write results) is present on *most*
  results but is *sometimes* `None`** (observed on large files, e.g. a ~20KB markdown). Code
  must not assume it's populated. HAID's bridge sources the baseline from the *earliest*
  captured `originalFile` for a file window-wide, and falls back to replaying `structuredPatch`
  hunks when none exists (see [architecture.md](architecture.md)).

## Known gaps the parser must respect

Roughly in order of concern:

1. **Non-tool changes are invisible.** The stream only sees edits made *through
   edit tools*. Hand-edits, formatters/linters on save, codegen, and especially
   anything a `Bash` call does (`sed -i`, build steps, `git checkout`) change
   files with no `Edit` block. The ledger can look clean while disk has diverged.
   This is for catching *out-of-band* divergence — **not** for building the scored diff:
   HAID reconstructs that from the transcript (replay), never git (the bash-write-to-source
   gap was measured at ~0–1%; see [roadmap.md](../plans/roadmap.md) Phase-5 Bridge note). Git
   stays optional, for Phase-4 cross-session blame/anchors only.
   - ✅ **Read side now covered:** shell *reads* (`cat`/`sed -n`/`head`/`tail`) are
     parsed into a `target_file_id` + `read_span` at build time (`graph/bash_read.py`,
     conservative — refuses `grep`/`ssh`/globs/pipelines/redirection), so read
     accounting (rereads, unused_context, `reads` edges) no longer misses them.
   - ✅ **Write side now covered (detection):** shell *writes* (`sed -i`, `cmd > f`,
     `>> f`, `| tee f`, `cp`/`mv`) are parsed into a `target_file_id` + `write_op` by
     `graph/bash_write.py` (quote-safe via shlex; refuses command substitution, remote,
     chaining, globs, `/dev/*`). This clears the re-read seen-ranges and grants
     unused-context credit so the read metrics stop firing falsely after a shell edit.
     **Heredoc writes (`cat > f <<EOF … EOF`) also have their CONTENT recovered**
     (`parse_heredoc_write` → `ToolCall.write_content`), so they feed the reconstructed diff.
     **Still a gap:** the *content* of *non-heredoc* shell writes (`sed -i`, plain `>`) isn't
     recoverable, so those don't feed the content-based rework metric — but the bridge
     **detects and flags** them rather than dropping them. Measured residual ~0–1% of source
     churn; git reconciliation is an optional verifier, not required.
2. **`Write` is lossier than `Edit`** — content, not delta.
3. **Truncation hits outputs, not edit inputs** — large reads/bash output get
   truncated/overflowed; edit inputs generally intact. Version/config dependent.
4. **Subagents are separate files** — auditing the canonical case means stitching
   several JSONLs (parent + N subagents), not reading one. Link via
   `meta.toolUseId` → parent `Agent` tool_use. ⚠️ Two real caveats: `meta.toolUseId`
   is **often `null`** (27/37 boxBot subagents — version/spawn-path drift), so such
   subagents are parsed but unattributable to a specific call (surface, don't drop);
   and the parent call may live in a **resumed sibling session**, so link against the
   project's full session set, not one file.
5. **Cross-session line drift** — each session is its own file; line numbers for
   the same logical location drift as the file evolves. → anchor regions by
   content hash + git blame, not line number (see
   [ADR-0003](decisions/0003-line-identity-anchoring.md)).

## Settings & hooks (for the "recommend a fix" feature) 📄

| Scope | Path | Shared |
|-------|------|--------|
| User | `~/.claude/settings.json` | no |
| Project | `.claude/settings.json` | yes (committed) |
| Local | `.claude/settings.local.json` | no (gitignored) |

Hook events HAID may recommend wiring: `SessionStart`, `UserPromptSubmit`,
`PreToolUse`, `PostToolUse`, `Stop`. A `PreToolUse` hook receives JSON on stdin
including `transcript_path`, `tool_name`, `tool_input` — which is also how HAID
itself could be triggered live in a later phase.

## Parser checklist (derived from the above)

- [ ] Read line-by-line; tolerate trailing/partial last line on active sessions.
- [ ] Branch on `version`; validate each record against a known shape; **flag
      unknown shapes loudly**, never silently drop.
- [ ] Index `assistant.tool_use.id` → result-bearing `user` record via
      `sourceToolAssistantUUID` / content `tool_use_id`.
- [ ] Resolve overflow: when a result references a `tool-results/*.txt`, load it.
- [ ] Discover and stitch `subagents/agent-*.jsonl` (+ `.meta.json`).
- [ ] Detect `summary` records → mark a compaction boundary.
- [ ] Skip/track metadata records (`ai-title`, `last-prompt`, `queue-operation`).
- [ ] Cache parsed output keyed by `(session_id, file_hash)`; for active
      sessions, parse only bytes appended since last offset.
