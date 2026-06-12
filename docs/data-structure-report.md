# The data structure we're working with — a report

A walkthrough of the **actual records** in your Claude Code transcripts and how
they assemble into the HAID graph, with real (trimmed/redacted) examples. The aim
is a shared, concrete picture so we can discuss the Tier 1 (deterministic) and
Tier 2 (rule-based) graph construction.

Source: 38 real sessions across your DataVine + software projects, Claude Code
2.1.92–2.1.156. Field catalog in [data-inventory.md](data-inventory.md); raw
shapes from [`research/`](../research/). All examples below are real records,
string fields truncated as `…[+N chars]`, secrets `«REDACTED»`.

---

## 1. The shape of a session

A session is **one JSONL file** = an append-only list of records. Each record is
one JSON object. There are 9 record types, but two carry the substance
(`assistant`, `user`); the rest are metadata/control.

**Two orderings coexist, and the distinction matters:**

- **Reading order = `timestamp`** (within an *agent scope* — the main chain, or one
  subagent). This is "the linear order of actions." Always reliable.
- **Causal links = `parentUuid` → `uuid`.** Mostly linear (~96%), but *branches*
  whenever the assistant fires multiple tools in one turn, or at meta-events
  (subagents, compaction). So: **read by timestamp, attribute by parentUuid +
  the `tool_result` block's `tool_use_id`.**

Every record shares an envelope:

```jsonc
{
  "type": "user",
  "uuid": "7debd612-…",          // this record's id
  "parentUuid": null,            // previous record in the causal chain (null = first)
  "sessionId": "120cc867-…",
  "timestamp": "2026-05-29T03:28:45.972Z",
  "cwd": "C:\\Users\\jhart\\Documents\\DataVine\\c7-connector",
  "gitBranch": "main",           // ← free git anchor on every record
  "version": "2.1.149",          // ← compatibility key; branch parsing on it
  "isSidechain": false,          // ← true inside subagents
  "userType": "external", "entrypoint": "claude-desktop"
}
```

Those envelope fields alone give us: session identity, ordering, the git branch at
the time, the CC version, and whether we're inside a subagent — all for free, on
every record.

---

## 2. The five kinds of payload

Mapped to your mental model. Each shown with a real record.

### 2a. Punctuation — a user instruction

A `user` record whose `message.content` is a **string** (and which has *no*
`toolUseResult`) is a real human message — an episode boundary.

```jsonc
{
  "type": "user",
  "parentUuid": null,
  "promptId": "a56e26a9-…",
  "permissionMode": "auto",
  "message": { "role": "user",
    "content": "For Aubaine Wine, we are going to generate a report. I want the
                report to contain two parts, a public part and a private part…[+1381 chars]" },
  "uuid": "7debd612-…", "timestamp": "2026-05-29T03:28:45.972Z", "version": "2.1.149"
}
```

→ **Node:** `Instruction` (and its `Turn`). Text is present in full for
classification. `permissionMode` tells us the mode in force (`auto`, `plan`, …).

### 2b. Action out — an assistant turn with a tool call

`assistant` records carry a `message.content` array of blocks: `thinking`, `text`,
and `tool_use`. (In your corpus: 2,310 tool_use, 1,117 text, 783 thinking blocks.)
Each tool call is a block like:

```jsonc
{ "type": "tool_use", "id": "toolu_01AfE4…", "name": "Edit",
  "input": { "file_path": "…/01_aubaine_overview.py",
             "old_string": "…", "new_string": "…", "replace_all": false } }
```

→ **Node:** `ToolCall` (tool name + literal target in `input`). **The target of
every action is a literal field** — `file_path` / `command` / `pattern` / `url` /
`subagent_type`. No inference to know *what* was acted on.

The assistant record also carries the **cost** of producing that turn:

```jsonc
"usage": { "input_tokens": …, "output_tokens": …,
           "cache_creation_input_tokens": …, "cache_read_input_tokens": …,
           "iterations": [ … ], "service_tier": "standard" }
```

→ token weight for the turn, present on all 4,210 assistant records.

### 2c. Information in — a tool result

Results come back as a `user` record carrying **`toolUseResult`** (a structured
dict) plus a `message.content[]` `tool_result` block whose **`tool_use_id`** links
back to the call (there is no top-level `sourceToolUseID`). This is the richest, most
useful payload.

**Read** — exact range + truncation flag:

```jsonc
"toolUseResult": {
  "type": "text",
  "file": { "filePath": "…/AUBAINE_DQ_REPORT_2026-05-28.md",
            "content": "# Aubaine Wine – Data-Quality Audit…[+44835 chars]",
            "numLines": 770, "startLine": 1, "totalLines": 973,
            "truncatedByTokenCap": true } }     // ← only saw 770 of 973 lines
```

→ `reads` edge ToolCall→File with exact `startLine..startLine+numLines` and a
truncation flag. **Redundant-read and unused-context detection fall straight out
of this.**

### 2d. The change — an Edit result with `structuredPatch`

This is the goldmine. The Edit result hands us a **unified diff**:

```jsonc
"toolUseResult": {
  "filePath": "…/01_aubaine_overview.py",
  "oldString": "… AS active,\n…[+72 chars]",
  "newString": "… AS active_n,\n…[+70 chars]",
  "originalFile": null,            // (full pre-edit content when present)
  "structuredPatch": [
    { "oldStart": 164, "oldLines": 8, "newStart": 164, "newLines": 8,
      "lines": [ "     # ---- WINE CLUB ----",
                 "     (\"08_member_status\", \"\"\"",
                 "-    … AS active,",        // '-' = removed
                 "+    … AS active_n,",      // '+' = added
                 "…[+6 more items]" ] } ],
  "userModified": false,           // ← did the USER hand-edit before this tool ran?
  "replaceAll": false }
```

→ `produces`/`edits` edge ToolCall→File, with the **exact touched line ranges**
(`newStart`, `newLines`) — no diffing on our side. `originalFile` reconstructs file
state; `userModified` flags out-of-band human edits. Re-touched-lines becomes a
group-by over these ranges.

### 2e. Control / context side-channels

- **Bash result** — the structured `toolUseResult` has **no exit-code field**:
  ```jsonc
  "toolUseResult": { "stdout": "{\"username\":\"python\",\"password\":\"«REDACTED»\",…}",
                     "stderr": "", "interrupted": false, "noOutputExpected": false }
  ```
  (This real result leaked DB credentials into the transcript — a finding HAID
  itself could flag.) **But success/failure does NOT need inferring** (correction
  2026-06-06): a failed call is flagged by `is_error: true` on the `tool_result`
  content block (uniform across all tools incl. Bash; error results carry an
  `Exit code N` prefix and no `toolUseResult` dict). The `stderr`/`interrupted`
  heuristic in §6 is superseded — see [open-questions V6](../plans/open-questions.md).

- **`attachment` records** = context injected outside the user's prose. 13 subtypes;
  the notable one for change-tracking is **`edited_text_file`** (the user edited a
  file in their editor — an external change, with a snippet):
  ```jsonc
  "attachment": { "type": "edited_text_file",
                  "filename": "…/memory/MEMORY.md",
                  "snippet": "1\t- [Infra layout]…[+837 chars]" }
  ```

- **`system` record (hook)** — `stop_hook_summary` with `hookInfos`, `hookErrors`,
  `preventedContinuation`, `toolUseID`. Hook activity is auditable.

- **`mode` record** — `{type:"mode", mode:"plan"|"acceptEdits"|…}` — permission-mode
  transitions.

- **`file-history-snapshot` record** — a rewind-checkpoint **manifest** (uuid-less, not in the
  tree): `{snapshot:{trackedFileBackups:{<path>:{backupFileName:"<hash>@v<n>", version,
  backupTime}}}}`, pointing at a full-file blob store at
  `~/.claude/file-history/<session-id>/<hash>@v<n>` (whole contents, not diffs). **Version-
  dependent:** legacy CLI (~v2.1.145) only; newer Desktop (~v2.1.161+) drops it and moves
  checkpoints to git. Rewind can't undo bash writes in either regime. HAID doesn't use it —
  the scored diff is replayed from the transcript (see [architecture.md](architecture.md)).

---

## 3. A real contiguous slice → the graph it produces

Here are 8 consecutive records from a real session (c7-connector), trimmed to
essentials. Watch the branch.

```
ts        type       uuid      parentUuid  payload
20:38:14  user       08ef2a6c  19f4f235    "Lets take a look at the cleanup items first…"   (instruction)
20:38:35  assistant  f41e4ceb  08ef2a6c    thinking
20:38:36  assistant  335d683c  f41e4ceb    text "Good corrections — let me handle those…"
20:39:15  assistant  f62b2e1e  335d683c    tool_use Write  id=toolu_01V4  {file_path, content}
20:39:16  assistant  14c334bc  f62b2e1e    tool_use Bash   id=toolu_01S7  {command, …}
20:39:16  user       c8ae76a4  f62b2e1e    tool_result→01V4  toolUseResult{structuredPatch, userModified}
20:39:22  user       8778e4d0  14c334bc    tool_result→01S7  toolUseResult{stdout, stderr}
20:39:22  attachment 6f9e4c89  8778e4d0    (post-tool context)
```

Two things to notice in real data:
1. **`f62b2e1e` (the Write) has two children** — the next assistant record
   (`14c334bc`, the Bash) *and* the Write's own result (`c8ae76a4`). That's the
   ~4% branching: assistant records chain linearly while each result hangs off its
   call. Pair calls↔results with the `tool_result` block's `tool_use_id`, not parent order.
2. The assistant text "Good corrections" tells us the instruction `08ef2a6c` was
   itself a **correction** — a Tier 3 label, but note the human signal is right
   there to read.

### The Tier 1 graph from that slice

```
            ┌─────────────────────────────────────────────┐
            │ Episode (trigger = Instruction 08ef2a6c)     │
            │                                              │
 Instruction 08ef ──responds-to── Turn(think) ─ Turn(text) │
        │                                                  │
        │ triggers (positional within episode)            │
        ▼                                                  │
   ToolCall Write(01V4) ──produces──▶ File(01_aubaine_overview.py)
        │                              ▲   (lines 164–171, from structuredPatch)
        │                              │
   ToolCall Bash(01S7) ──action──▶ (command; result: stdout/stderr)
            └──────────────────────────────────────────────┘
   token weights on each Turn (from usage)
```

Every node and edge above is built from a **literal field** — no model involved.

---

## 4. Tier 1 — deterministic construction (confidence 1.0)

The full mapping from field → graph element. This is "no model touches it."

### Nodes

| Node | Built from | Key attributes (from fields) |
|------|-----------|------------------------------|
| `Session` | the file + first/last record | `sessionId`, `cwd`, git branch, version, start/end ts, `agentId` if subagent |
| `Turn` | each `assistant`/`user` record | role, ts, `usage` (assistant), `is_meta` |
| `Instruction` | `user` record, string content, no `toolUseResult` | text, `permissionMode`, ts |
| `ToolCall` | each `tool_use` block | `name`, `input` (literal target), `id` |
| `File` | any `file_path`/`filePath` seen | `repo_id+relpath` (from `cwd`+path) |
| `Region` | `structuredPatch` hunks on edits | line ranges, `originalFile` anchor |

### Edges

| Edge | Source field | Confidence |
|------|-------------|-----------:|
| `responds-to` (turn spine) | `parentUuid` / timestamp order | 1.0 |
| call ↔ result pairing | **`tool_result.tool_use_id`** (no top-level `sourceToolUseID`) | 1.0 |
| `reads` ToolCall→File/Region | Read/Grep/Glob result (`file{}`, `filenames[]`) + ranges | 1.0 |
| `produces`/`edits` ToolCall→File/Region | Edit/Write `structuredPatch` line ranges | 1.0 |
| `reads`/`produces`/`edits` (shell) | Bash command parsed (`bash_read.py`/`bash_write.py`); `via:"shell"`, `derived_read`/`derived_write` | 1.0 |
| token weight on Turn | `message.usage` | 1.0 |
| subagent link Parent→Sub | `meta.json.toolUseId` ↔ parent `Agent` call; `agentId` | 1.0 |
| subagent cost rollup | `Agent` result `totalTokens`/`toolStats` | 1.0 |
| compaction boundary | `system`/`compact_boundary` + `compactMetadata` | 1.0 |
| external file edit | `attachment.edited_text_file`; Edit `userModified` | 1.0 |
| attribution | `attributionSkill`/`Agent`/`Mcp…` on assistant | 1.0 |
| hook firing | `system`/`stop_hook_summary` | 1.0 |
| mode transition | `mode` record / `permissionMode` | 1.0 |

**Subagent stitching, concretely.** A parent `Agent` call:
```jsonc
{ "tool_use": "Agent", "id": "toolu_01NGKL…", "input": {
    "description": "Aubaine website SEO audit", "subagent_type": "general-purpose",
    "run_in_background": true } }
```
its result names the child + where its output lives:
```jsonc
"toolUseResult": { "isAsync": true, "status": "async_launched",
  "agentId": "a3183e94…", "outputFile": "…/tasks/a3183e94….",
  "canReadOutputFile": true }
```
and the child's `subagents/agent-a3183e94….meta.json`:
```jsonc
{ "agentType": "general-purpose", "description": "Aubaine website SEO audit",
  "toolUseId": "toolu_01NGKL…" }          // ← links child file back to the parent call
```
→ parent ToolCall `toolu_01NGKL` —spawns→ Session(subagent `a3183e94`), and the
subagent's own records (a full transcript) attach under it. **The canonical
"subagents wrote myopic tests" case is fully reconstructable from these three
links.** (Note `isAsync` — background subagents; their results land in `outputFile`,
not inline.)

---

## 5. Tier 2 — rule-based derivation (no model, confidence ~0.9)

These edges aren't single fields; they're computed from Tier 1 facts by a fixed
rule. All four MVP waste metrics live here.

### Re-read (redundant)
```
group reads by File within an episode, ordered by ts
for consecutive reads R1,R2 of same file with OVERLAPPING ranges
   and NO `edits`/`produces` on that file between them:
        add re-reads edge R2→R1, waste = token_estimate(R2.numLines)
```
*Why Tier 2 not 3:* "overlapping range" and "intervening edit" are exact from
`startLine/numLines` + edit timestamps. The only judgment (was the re-read
*needed*) is deferred to the report's hedge.

### Re-touched lines  *(the structuredPatch payoff)*
```
for each File, collect all edit hunks (newStart..newStart+newLines) with ts
project each hunk forward through later hunks' line-shifts (anchor on content)
if ≥2 edits hit the same logical region → re-touch, count = #edits
```
Concrete: in the slice, if a later edit in the same episode touches lines ~164–171
of `01_aubaine_overview.py` again, that's a re-touch=2 on that region.

### Retry loop
```
normalize each Bash/PowerShell command → signature (strip volatile args)
group consecutive same-signature calls within an episode
if the earlier call "failed" (see §6 rule) and the command repeats → retry chain
chain length ≥3 = thrash
```

### Co-churn (impl + tests)
```
within an episode, collect Files with `edits`
for File pair (A,B) edited within K turns of each other, ≥2 cycles:
   if is_test(A) XOR is_test(B)  → co-churn   (is_test = path/name rule)
```

Each Tier 2 edge carries its rule id and a `confidence` (<1.0) so the report can
say *what* fired and hedge *why it might be fine*.

---

## 6. Gotchas — made concrete

Things the real data forces us to handle; good discussion points.

1. **Branching turns** (from §3): one assistant turn can emit several tool calls;
   results interleave by timestamp. → spine = timestamp; pairing = `tool_result.tool_use_id`.
2. **Truncated reads** (real: 770 of 973 lines, `truncatedByTokenCap:true`): the
   agent only *saw* part of the file. Affects "did it have the context?" reasoning.
   Full content overflows to `tool-results/*.txt` / Bash `persistedOutputPath`.
3. **No Bash exit code** (real: only `stdout/stderr/interrupted`). Failure must be
   a **rule**: `interrupted || stderr matches error-ish || returnCodeInterpretation`
   — cross-checked against the next assistant turn's narration. This is the one core
   signal that isn't a clean field; precision here gates retry-loop quality.
4. **Async subagents** (real: `isAsync:true`, `status:"async_launched"`): the result
   is a launch receipt; the real output is in `outputFile` / the subagent JSONL, and
   completes *later* in wall-clock. Stitching must follow `agentId`, not assume the
   result block holds the work.
5. **Secrets in transcripts** (real: DB creds in a Bash `stdout`): we are parsing
   sensitive data. Reinforces local-only, and is itself a candidate finding.
6. **Empty/elided thinking** (real: a `thinking` block with empty text): don't
   assume every block has content.
7. **Compaction renumbers reality** (real: 262,707→6,702 tokens at a
   `compact_boundary`, `logicalParentUuid` bridging it): token accounting and
   "what was in context" must respect compaction boundaries.

8. **Shell-write content is partially recoverable.** Shell writes are detected by
   `bash_write.py` (file-touch edge + range), but their *content* is recovered only for
   **heredocs** (`cat > f <<EOF … EOF`, body is inline → fed to the diff bridge). Non-heredoc
   shell writes (`sed -i`, plain `>`/`>>`, `tee`) are detected and **flagged**, not silently
   dropped, and don't feed the content-based rework metric. Measured source gap ~0–1%.

---

## 7. What's left for Tier 3 (agents)

Everything above is structure + rules. Agents are invoked only for **labels and
judgment** on top: instruction intent / is-correction (with the deterministic
proxies as priors), ambiguous `triggers` resolution (the ORPHAN cases), behavioral
contradiction, Goodhart confession, "was this context actually used," and quality
scoring. The structure they annotate is never confabulated — which is the whole
trust posture ([trust-discipline.md](trust-discipline.md)).

---

## Discussion starters

- Is **timestamp-within-agent-scope** the right spine, with parentUuid kept only
  for call/result and meta-branch attribution? (I think yes.)
- **Region identity**: anchor on `structuredPatch` line ranges + `originalFile`
  content hashing (language-agnostic) vs. enclosing-symbol (needs a parser)?
- **Bash failure rule** (§6.3): how aggressive, and do we always cross-check the
  next turn's narration?
- Do we model `attachment` context (skill listings, reminders, edited_text_file) as
  first-class `information-in` nodes, or fold them into the turn they attach to?
- MVP scope: single session (L0+L1+Tier 2 metrics) before episodes — agreed?
