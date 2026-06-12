# Detector catalog

These are the reasoning-light patterns that do the heavy lifting, chosen because
they are **detectable** rather than **inferred**. Each is expressed as a graph
query over the structure in [session-graph-design.md](session-graph-design.md).

The guiding principle: **prefer measurable signatures over inferred intent**, and
**distinguish waste from legitimate work** throughout — the value is in not crying
wolf (see [trust-discipline.md](trust-discipline.md)).

> **AS BUILT — Phase 1 (2026-06-06).** Signatures (a)–(d) below are implemented
> in `src/haid/metrics/` and validated on 65 real transcripts. (e) co-churn is Phase 3.
> Three things changed from the original sketches here, and they matter:
> 1. **Unit = the analysis WINDOW, not the episode.** Episodes (Phase 2) aren't built yet;
>    the MVP denominator is a project's sessions over a timeframe (default 30 days). A
>    `timeline` (root→leaf path) is the internal correctness floor (rewind branches don't
>    bleed). See [phase1-build.md](../plans/phase1-build.md) §0.5 + Steps 3+4.
> 2. **Output is a benchmarkable token-RATE, not a verdict.** Each metric reports
>    `wasted tokens / total tokens of that kind`, positioned against a population baseline
>    (`metrics.baseline`) — the same placement-against-reference idea as the scoring axes.
>    This dissolves the false-positive problem: normal iteration sits in the baseline, so
>    only an *above-baseline* rate is notable (e.g. unused-context's baseline median is ~34%
>    — reading-without-editing is normal, not waste).
> 3. **Metric × scope, two orthogonal axes** (see
>    [metrics-output-schema.md](metrics-output-schema.md)). Each of the four metrics is
>    reported at **`session` and `window` scope**, each with its own rate + baseline. Scope is
>    never baked into a name: the **cross-session signals are `metric @ window`** —
>    `rereads @ window` is the **re-establishment tax** (a file rediscovered across sessions),
>    `retouched @ window` is cross-session rework. **One rule per metric; scope is only the
>    memory length** (no per-scope rule). A wider scope sees more, so window rates aren't the
>    arithmetic sum of session rates and each scope keeps its own baseline. The cross-session
>    views (`rereads`/`retries @ window`) were the one genuinely new *computation* — now built.

---

## Objective waste signatures (signature-scanning pass)

### (a) Redundant re-reads
The same file (or overlapping line range) read again with **no intervening edit**.

```
group re-reads edges by (episode, file_id)
  → instances where count ≥ 1
  → waste_tokens = Σ token_estimate(result_bytes of 2nd+ read)
```
The "no intervening edit" guard is baked into `re-reads` edge construction, so
this is a trivial group-by. A re-read *after* an edit is legitimate.

> **Shell reads count too (BUILT).** "Read" is not only the Read tool: Claude reads
> through Bash constantly (`cat f`, `sed -n '10,40p' f`, `head -n 100 f`). The graph
> parses those into a `target_file_id` + absolute `read_span` (`graph/bash_read.py`),
> marks the call `derived_read`, and the read metrics gate on `is_read(tc)` (Read OR
> derived) — so a `cat`-then-`cat`, or a Read-then-`sed` of the same span, is caught.
> The parser is **high-precision** (100% on 1,950 real boxBot Bash calls): it refuses
> search/listing (`grep`/`find`/`ls`), remote (`ssh`), globs, pipelines that transform,
> redirection, multi-file, and command substitution rather than guess. Residual blind
> spots (no-silent-caps): `awk`, last-K `tail` (absolute position unknown → file
> attributed, span `None`), and remote reads.
>
> **Shell writes too (BUILT).** The symmetric case — `sed -i`, `cmd > f`, `>> f`,
> `| tee f`, `cp`/`mv` — is parsed by `graph/bash_write.py` into a `target_file_id` +
> `write_op` (edit/overwrite/append), marked `derived_write`, and exposed via
> `is_write(tc)`. This corrects the read metrics: a re-read *after* a `sed -i` is no
> longer flagged (the seen-ranges clear), and a file written only via shell gets
> unused-context credit. It is **quote-safe** (shlex-tokenized, so a `>` inside a
> quoted `sed` script is not a redirection) and refuses command substitution, remote,
> chaining, globs, multi-file, and `/dev/*`. **Heredocs are handled specially** —
> `parse_heredoc_write` recovers a `cat > f <<EOF … EOF` body's *content* (it's inline in
> the command) into `ToolCall.write_content`, so heredoc writes feed the diff/reconstruction
> (the **bridge**), unlike other shell writes. **Limit (no-silent-caps):** **no** shell write
> currently feeds the content-based rework metric (`retouched`) — it still keys on native
> Edit/Write `old`/`new` lines (heredoc `write_content` is consumed by the bridge, not yet by
> `retouched`). Non-heredoc shell writes (`sed -i`, plain `>`) are content-unrecoverable; the
> bridge detects and **flags** them rather than dropping them. Residual: chained
> (`cd x && sed -i`) and remote writes are not attributed — on real sessions those dominate,
> so write recall is deliberately low in favour of precision (0 false positives on 1,950 real
> Bash calls). The bash-write-to-*source* gap this leaves was measured at ~0–1% (see
> [roadmap.md](../plans/roadmap.md) Phase-5 Bridge note).

### (b) Retry loops
The same failing command/test run repeatedly before the approach changes.

```
for each connected component in `retries` edges within an episode:
  if len(chain) ≥ 2 and all share signature:
    severity = len(chain)
    wasted   = Σ cost(turn(c)) for c in chain[:-1]
report chains with len ≥ 3 as high-severity (thrashing)
```
**Retry vs escalation:** `params_delta ≈ 0` across the chain = pure thrash
(waste). Signature *changing* = the model genuinely adapting strategy (not waste).
Only the former is reported as waste.

**Failure detection — RESOLVED (was the worry here).** A call failed iff its
`tool_result` content block has `is_error: true` — uniform across all tools including
Bash (no stderr/interrupted heuristic needed; the structured `toolUseResult` has no
return-code field, but `is_error` does the job, and error results carry an `Exit code N`
prefix). Verified across the corpus; see [open-questions V6](../plans/open-questions.md).
As built, a retry loop = the **same signature failing ≥2×** in a context (one failure
then a fix is healthy and excluded; a changed signature = adaptation, excluded).

### (c) Re-touched lines  *(strongest rework signal)*
The same logical region edited multiple times — a strong signal a mistake was
made and reworked.

```
for region_lineage in connected(`derives-from`):   # all revisions of one region
  n_edits = count(`edits` into any revision in lineage)
  if n_edits ≥ 2 same episode   → intra-episode rework
  if n_edits ≥ 2 across episodes → re-opened (worse)
  rework_score = n_edits weighted by (lines_touched / time_span)
```

> **AS BUILT:** to avoid blocking on the still-open region-identity question (and on
> line-number drift), the implementation is **content-based**: track the non-trivial
> lines each Edit/Write *produces*, per file, across the window; flag an edit whose
> `old_string` rewrites a line the agent itself produced earlier. Drift-immune, and it
> catches the real churn (rewriting your own fresh output) — including **across sessions**,
> which a per-session view misses entirely. Rate = rewritten-own-output tokens / authored
> tokens (window baseline median ~6%).
>
> **Documentation is counted as a real cost — no file-kind exemption (decided 2026-06-07).**
> Validation showed most `retouched` instances on doc-heavy windows are markdown (iterative
> drafting of plans/docs). We do **not** special-case it. Rationale: writing docs *is* a real
> token cost; it's an **upfront cost that should be repaid with interest** — good docs over a
> 30-day window should cut re-reads of the codebase and lift cleanliness. Whether they
> actually do is exactly what the cross-metric, over-time data reveals: if heavy doc churn
> isn't buying lower `rereads`/better cleanliness, that's a finding (maybe the docs are too
> much), not noise to hide. The metric stays honest; a **report-time breakdown by file kind**
> (`filekind.file_kind(refs.file_id)` → logic/config/test/docs/…) is available whenever a
> view wants to separate code rework from prose iteration. Let the data decide.

### (d) Unused context
Files read but never used downstream — context spent for nothing.

```
for f in files read in episode:
  used = (exists edit/produces on f)                      # edited → used
       OR (f referenced in any later assistant turn.text) # cited → used
       OR (read drove a later Grep/Read of a symbol in f) # followed → used
  if not used: report unused-context
  waste_tokens = f.read_cost + f.resident_cost            # ingestion + carry
```
Report by **token weight**, not count — a 4k-line unused read is the headline; a
10-line one is noise. `resident_cost` (carried in cache until compaction) is
usually the larger term.

### (a-window) `rereads @ window` — the re-establishment tax  *(scope view, Phase 1, BUILT)*
**Not a separate metric — this is `rereads` at `window` scope** (see the metric × scope model
in [metrics-output-schema.md](metrics-output-schema.md)). The same file read in **N separate
sessions** without ever being edited — the agent **rediscovering** the same context at the
start of each session. The `session`-scope view of `rereads` (detector (a) above) is
lost-context within one timeline; the `window`-scope view is this cross-session repetition,
which the session-scope rule deliberately ignores.

```
for file_id in files read anywhere in the window:
  sessions_reading = distinct sessions with a read of file_id, no edit of it in the window
  if len(sessions_reading) ≥ 2:
    waste_tokens = Σ read_tokens(file_id) over all sessions PAST the first
    report re-establishment-tax(file_id, n_sessions=len(sessions_reading))
```
Cheap and deterministic — needs only `File.id` + session count, **no git / line-lineage**.
Remedy (a later phase states it; this metric only measures): *pin it in CLAUDE.md / memory /
a skill so you stop re-reading it.* **This is the headline signal for the cross-session
visualizer** — a file with a fat re-read bus recurring across the stacked-session column.

### (e) Co-churn  *(the canonical-case detector)*
Implementation **and** its tests rewritten together. Implementation-only churn is
normal iteration; tests-and-impl churning together is the signature of a **wrong
contract**, and it points blame *up* the chain (spec/test layer) rather than at
the code.

```
for (r1, r2) in `churns-with` where co_edit_count ≥ 2:
  if is_test(r1.file) XOR is_test(r2.file):   # one impl, one test
    report co-churn pair with co_edit_count (= cycles)
  tighten: same episode, edited within K turns of each other
```
**Not always waste** — TDD looks like this for one cycle. The signal is the
*number of cycles*: 1 = healthy paired edit; ≥3 = thrash. This is the detector
that catches the [canonical test case](vision.md#the-canonical-test-case) in the
cleanup window.

---

## Behavioral / narration detectors

Reasoning-light patterns visible *inside the transcript* — no external ground
truth needed.

### Behavioral contradiction
Agent reads `CLAUDE.md` (or docs) then immediately does the opposite. Surfaces
"stale docs misled the agent" as a contradiction visible within the transcript.

```
for read of a docs/CLAUDE.md file:
  extract stated constraints (cheap LLM, only on these hits)
  if a subsequent edit/command within the episode violates a stated constraint:
    report behavioral-contradiction (cite the doc line + the violating action)
```

### Constraint-rationalization / Goodhart confession
The agent narrating that it is optimizing the *proxy* over the *goal* — Goodhart's
law happening live in the trace. Grep the agent's text/thinking for confessions:

- "the tests are a contract, so I need to find a way to make them pass"
- "to match the existing pattern"
- "I need to make this pass / get this to pass"
- "the spec says X so I'll …" (when X is the proxy, not the goal)

```
scan assistant text/thinking for confession patterns (regex first, LLM to confirm)
  → when co-located with a co-churn or retry-loop, escalate confidence
```
Co-located with co-churn, this *confirms* the canonical case from the agent's own
words.

### Purpose drift
Started on X, drifted into Y, while X's context kept riding along in cache. We do
**not** try to judge whether a cached file is "useful" — we can't see activations,
and absence of evidence isn't evidence of absence. Instead we report only what's
observable, from the **purpose timeline** (see
[intent-taxonomy.md](intent-taxonomy.md)):

```
read the per-message purpose snapshots as one list (the whole timeline is small)
identify threads holistically; a New-directive move whose purpose is *lateral* to
   the prior thread = a drift point (a Refinement to a sub-goal is NOT drift)
for each drift point:
   carried_cost = resident tokens from the prior thread that ride in cache_read
                  across the boundary (deterministic from usage)
   evidence_of_nonuse = prior-thread files later RE-READ after returning
                        (rediscovery) OR never referenced again before compaction
   evidence_of_use    = prior-thread files/symbols cited / reused / re-edited
report as a hedged, cost-quantified pattern — never a "wasted cache" verdict
```
Output reads like: *"This session ran 3 threads; the auth thread's context (~120k
tokens) rode through the billing and bug threads with no downstream reference. If
separable, a fresh session would have saved that — but if you were drawing on auth
context for billing, ignore this."* The strong non-use proxy is **re-reading X
after returning to it** (behavioral evidence it wasn't being usefully held). Flag
only drift **× cost**; exploratory work legitimately wanders.

### Recurrence — a fix that didn't hold *(cross-session; Phase 3)*
The highest-value error-attribution signal is **cross-session**: a symptom fixed in one
session and **re-reported by the user in a later one** — the fix didn't hold. The within-session
Correction/Re-prompt axes miss this; it only shows across the window.

```
for each bugfix episode (work-type = Implementation:bugfix, or a Correction that closed a bug):
  symptom = the user's described problem (+ the files/region the fix touched)
  search the window BACKWARD for an earlier episode fixing the same symptom/region
  if found: report recurrence(first_fix → recurrence) and ask:
     why didn't it hold? wrong layer? a doc/contract missed? (search the project tree)
```
Exemplar: boxBot's "calendar integration is down" memory — fixed 5/13, recurred the morning of
5/14 ([agent-analysis.md §4](../plans/agent-analysis.md)). Distinct from a within-session retry
loop: recurrence spans **sessions** and is anchored on the **user re-reporting**, not a tool
erroring. Route to error attribution: trace the introducing edit, determine what was missed.
**CORRECTION (2026-06-09):** a live why-pass investigation found this exemplar's recurrence does
NOT hold — the 5/12 OAuth production-mode fix held, and the 5/14 failures were different defects
(a dropped-recipient bug, a sticky mute). The detection *mechanism* stands (a user re-report seeds
a recurrence check); the investigation agent refusing to confirm the seeded narrative is the
intended trust behavior, not a failure of the pass.

---

## Soft top layer (hedged, never the spine)

Snapshot quality scores — complexity, elegance, cleanliness of the end-state diff.
(Originality was calibrated and then **dropped** from the scoring model; the live axes are
difficulty and cleanliness, with volume — see [scoring-rubric.md](scoring-rubric.md) Axis
decision.) These are an LLM-confabulation risk and **structurally cannot see relational
failures** (code bad only relative to what it should have been). They sit clearly labeled on
top of the objective metrics, never masquerading as fact.
Use them for color and for the "working backwards from end state" code-review
view, not as a basis for diagnosis.

---

## Detector → failure-class map

| Detector | Pass | Catches | Reasoning load |
|----------|------|---------|----------------|
| Redundant re-reads | signature | silent inefficiency | none |
| Retry loops | signature | silent inefficiency | none |
| Re-touched lines | signature | silent rework | none (content-based; region identity not required) |
| Unused context | signature | silent inefficiency | none |
| `rereads @ window` (scope view) | signature | **re-establishment tax (rediscovery)** | none (file-id + session count) |
| Co-churn | signature | **wrong contract (canonical case)** | none |
| Behavioral contradiction | both | stale docs / misalignment | light (extract constraint) |
| Goodhart confession | user-anchored | proxy-optimization | light (regex + confirm) |
| Correction episodes | user-anchored | misalignment | light (classify message) |
| Recurrence (cross-session) | user-anchored | **a fix that didn't hold** | medium (backward search + project read) |
| Purpose drift | user-anchored | context bloat / wrong session boundary | light (read purpose timeline) + deterministic cost |
| Snapshot quality | soft layer | gross quality only | heavy — hedge it |

## The acid test

Every detector is sanity-checked against the [canonical case](vision.md): four
clean-looking sessions whose waste appears only as a later cleanup week, with
relational badness invisible to snapshot scores. **Co-churn** + **Goodhart
confession** are the two that catch it. If a proposed detector wouldn't have
helped there, it's a nice-to-have, not core.
