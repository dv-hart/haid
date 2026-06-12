# Vision

## What HAID is

An open-source skill / set of scripts you install into Claude Code and run
against your own session history. It triggers an audit in which agents review
your transcripts — your prompts, where the tokens went, what the agent did, and
how it turned out — and produces annotated, coaching-oriented reports.

It is a fun challenge, not a business. It is meant to help people who are
struggling to keep pace with how fast agent tooling is moving. **Nothing leaves
the user's machine** unless they explicitly choose to submit aggregate metrics.

## What it is *not*

Not another token counter. Raw usage accounting is already well covered (ccusage
and similar — see [tooling-landscape.md](tooling-landscape.md)). The entire value
lives one layer up, in *diagnosis and coaching* — telling you not what you spent
but how to get better. **A tool that confidently misdiagnoses is worse than
nothing**, because people act on it, so trustworthiness of the diagnosis is the
central design constraint throughout (see [trust-discipline.md](trust-discipline.md)).

## Goals

- Turn raw session transcripts into actionable feedback on **agent-orchestration
  habits**.
- Ground recommendations in the user's *own* data ("you re-read these files every
  session; a hook fixes it"), not scraped best-practice folklore.
- Reward improvement over time via a **personal trend** ("your efficiency is up
  30% this month"). The per-session value is a **relative** achievement-vs-cost score
  — the achievement is the diff's **placement against an external reference ladder** of
  real code changes (see [scoring-rubric.md](scoring-rubric.md) /
  [difficulty-ladder.md](difficulty-ladder.md)), so it is anchored to real code, not a
  purely self-relative or vacuum-estimated number. The "trend" is your own scores
  tracked across time. We avoid an **involuntary cross-user leaderboard** (comparability,
  Goodhart, privacy); team comparison is strictly **opt-in**.
- Keep any sharing strictly **opt-in and aggregate**.

## Why personal trend, not leaderboard

A global *leaderboard* fails on three counts at once:

- **Comparability** — *raw cost* is not comparable across people or projects.
  (Note: the value rubric tackles this head-on — each axis is a **relative** placement
  against a fixed reference ladder, but the *combined* value is a **stable, deterministic
  scalar per ladder version**, so it's comparable without raw cost; see
  [scoring-rubric.md](scoring-rubric.md) "Combining into achievement and value". The
  no-leaderboard stance is about the other two.)
- **Goodhart** — the moment a number is *ranked*, people optimize the number
  instead of the underlying skill.
- **Privacy** — transcripts are deeply personal/proprietary; cross-user
  comparison pressures sharing.

So per-axis placement is **relative** (against the ladder) while the combined value is a
**stable scalar per ladder version** (calibrated, per session) — shown as a **personal trend**:
you are only ever ranked against your own past, on your own data, on your own machine. Stable
measurement, no involuntary cross-user ranking.

## The canonical test case

The motivating disaster, and the case the tool **must** catch:

> A week of drafting specs with Claude → subagents (each reading only its spec
> doc) writing tests → test-driven implementation → a week of cleanup. The
> subagent-written tests were myopic; at implementation time the agents were
> forced into antipatterns to satisfy the tests and specs.

Why this is the acid test: **every session in it looks successful.** Specs done.
Tests written and passing. Implementation written, tests green. A forward
token/diff scorer sees four clean episodes. The waste exists *nowhere in those
sessions* — it exists only as the cleanup week, and the badness was **relational**
(wrong relative to what it should have been, given a bad contract), so snapshot
quality scores would rate the contorted implementation as roughly fine.

What catches it:

- **Co-churn** in the cleanup window — tests and implementation rewritten
  together — flags the contract itself as wrong and points blame *up* to the
  spec/test layer. No reasoning required.
- **The Goodhart confession** ("the tests are a contract so I need to find a way
  to make the test pass") confirms it from the agent's own narration.

**If the tool catches this, it works.** Every detector and design decision should
be sanity-checked against this scenario. See [detectors.md](detectors.md).

## North-star usage

The user, inside a Claude Code session or via a CLI, asks "how am I doing?" HAID:

1. Reads the relevant transcripts (a session, a day, a code-review window).
2. Builds the session graph.
3. Runs the two passes and the detectors.
4. Renders a report: objective metrics first, lightly-hedged interpretation on
   top, and a few concrete, data-grounded suggestions (e.g. "add this hook",
   "these specs produced contorted code — review the contract").
5. Optionally updates the personal trend score.
