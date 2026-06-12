# Trust discipline

**A confident wrong diagnosis is the worst possible outcome** — people act on it,
so it is worse than no tool at all. Trustworthiness of the diagnosis is the
central design constraint of HAID. This document is the set of rules that protect
it. Treat these as non-negotiable invariants, not guidelines.

## 1. Prefer measurable signatures over inferred intent

Wherever possible, reframe a causal *category* as a detectable *behavioral
pattern*:

| Tempting inference | Detectable reframe |
|--------------------|--------------------|
| "the prompt was insufficient" | re-prompt: the user had to say it twice (correction episode) |
| "the docs were stale" | behavioral contradiction: read CLAUDE.md, then did the opposite |
| "the agent gold-plated" | edits with no traceable instruction (ORPHAN rate) |
| "the contract was wrong" | co-churn: impl + tests rewritten together |
| "the agent gave up reasoning" | Goodhart confession in the narration |

The objective metrics (re-reads, retry loops, re-touched lines, unused context)
are reasoning-free by construction. They are the spine. Everything inferred sits
above them and is labeled as such.

## 2. Cite or say unknown

For any causal link ("X came from turn N"), the model must **either cite the line
or return "no traceable origin."** There is no third option, and no
best-guess-without-a-citation.

The **"no traceable origin" bucket is among the most valuable outputs in the
tool** — the agent did something with no identifiable trigger (autonomy / scope
creep / data gap). It only exists if the model is *forbidden* from papering over
gaps. The `why()` operation enforces this by returning a typed `Resolution`
(`TRACED` / `AMBIGUOUS` / `SYSTEM_INDUCED` / `ORPHAN`) and never `null` — see
[session-graph-design.md](session-graph-design.md#two-core-operations).

Orphan rate is reported as a first-class metric, not hidden as a parsing failure.

## 3. Hedge the soft layer

Aesthetic and causal interpretation (elegance/cleanliness scores, "why" stories)
sit **clearly labeled on top of** the objective metrics, never masquerading as
fact. The report's visual and rhetorical hierarchy must make the line between
*measured* and *interpreted* obvious at a glance:

- Measured: "this file was read 4× with no edit between reads."
- Interpreted (hedged): "this *suggests* the agent lost track of context — *if so*,
  a SessionStart hook that pins the file could help."

Snapshot quality scores additionally **cannot see relational failures** (code bad
only relative to what it should have been — the canonical case). Never let a
snapshot score stand in for diagnosis.

## 4. Distinguish waste from legitimate work

A detector that flags normal behavior as waste destroys trust as fast as a wrong
causal claim. Every detector carries an explicit "legitimate" carve-out:

- **Re-read after an edit** → legitimate (the file changed). Only no-intervening-
  edit re-reads count.
- **Retry with a changing signature** → escalation/adaptation, not thrash. Only
  `params_delta ≈ 0` chains count.
- **Co-churn for one cycle** → healthy TDD. Only ≥3 cycles signal thrash.
- **Exploratory reads in an open-ended task** → may be legitimate discovery;
  weight by tokens and context, don't flag small ones.

## 5. No silent caps

If the analysis bounds coverage (top-N findings, sampling, skipped a malformed
session, didn't stitch a subagent), **say so in the report.** Silent truncation
reads as "we covered everything" when we didn't. The parser likewise flags
unknown record shapes rather than dropping them
([data-format](claude-code-data-format.md)).

## 6. Confidence is carried, not collapsed

Edges and findings carry a `confidence` (0–1). Structural facts are 1.0; inferred
links are less. The report surfaces confidence rather than rounding everything to
a single authoritative voice. Low-confidence findings are shown as questions, not
verdicts.

## The one-line test

Before any finding ships to the report, ask: *"If this is wrong and the user acts
on it, how bad is it — and could they tell it was a guess?"* If it could be wrong
and is dressed as fact, it violates the discipline. Demote it to a hedged
suggestion or cut it.
