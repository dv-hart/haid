# Axis calibration playbook — replicate the difficulty pattern for a new axis

> **Purpose.** A self-contained recipe to calibrate a NEW scoring axis using the
> architecture validated for **difficulty**. (Worked twice: **cleanliness** earned its
> place; **originality** was calibrated by this recipe and then **dropped** — it saturated
> and was the least difficulty-distinct axis, so the orthogonality/usefulness gates
> correctly rejected it. See scoring-rubric.md Axis decision. The recipe is axis-agnostic;
> the originality prompt in §3 is retained as a worked example.) A fresh session can
> follow this start to finish. **Canonical entry point:** where this disagrees with
> older docs, this wins. The difficulty axis is the worked example
> ([difficulty-ladder.md](difficulty-ladder.md)); the experiment history is in
> [calibration-experiment.md](calibration-experiment.md) §12–13 and
> [calibration-pilot-1.md](calibration-pilot-1.md).

---

## 0. What's validated — and what was tried and REJECTED

**The proven architecture:**
- **Relative, not absolute.** A score is a *position on a reference ladder*. No
  senior-engineer-hours, no absolute tier-as-score, **no uncertainty bands** (LLMs
  estimate all three badly in a vacuum, and they aren't actionable downstream).
- **Dense-anchors + placement — NOT a sparse sort of many units.** A sparse pairwise
  sort (k≈3 comparisons/unit) mis-ranks the dense middle: a unit floats up via
  transitivity through whatever easy opponents it happened to draw, never colliding
  with the genuinely-hard cluster (this is what put a routine `Arc` copy-on-write
  refactor *above* a serializable-snapshot-isolation tracker). **Fix:** densely
  compare a *small* anchor set **all-pairs** → a bulletproof reference ordering; then
  **place** every new diff against that set (each diff meets the full range, so it
  can't float).
- **Cross-method convergence = validation without human labels.** When the dense
  pairwise ordering and an *independent* absolute classification agree (monotonic),
  trust the ordering.
- **Orthogonality is a hard, tested requirement.** Each axis must be ⊥ **LOC** (volume
  is computed separately and combined) AND ⊥ the **other axes** (else the combined
  score double-counts). Verify with Spearman; a new axis that correlates ~1.0 with
  difficulty or with size is not a new axis.

**Rejected — do not repeat:**
- ❌ **Mined PR review-signals** (reviewers, time-to-merge, commits) as a difficulty
  validator — **falsified** (pilot 1): they measure org/process + size, not difficulty
  (composite ρ=−0.26). May still suit a *process* axis, not these three.
- ❌ **Absolute SEH grounding** — dropped; we score relatively.
- ❌ **Absolute tiering as the score** — it **leaked size** (ρ tier-vs-LOC = +0.39 vs
  the pairwise oracle's −0.05), because tier *descriptions* tie "trivial" to "tiny."
  Keep absolute classification only as the lightweight convergence **cross-check**.
- ❌ **Stars / author reputation** as per-unit labels — wrong granularity, contaminated.

---

## 1. Reusable assets — do NOT re-harvest

- **`out/blinded/U00.diff … U54.diff`** — 55 full-spectrum units (30 personal-project
  commits + 25 OSS PRs), already blinded (identity stripped) and reassembled
  code-files-first. **Axis-agnostic — reuse as-is for any axis.**
- **`out/units_blinded.jsonl`** — private index (`id` → repo, additions/deletions,
  kind, …) for analysis joins and the ⊥-size check. Never shown to the judge.
- **`out/ladder_verdicts.json`** — the difficulty full-sort (for the ⊥-difficulty check).
- **Code (axis-agnostic):**
  - `calibration/bt_h5.py` — `fit_bradley_terry(ids, verdicts, alpha=0.5)`,
    `spearman(x,y)`, `oracle_consistency(...)`.
  - `calibration/ladder.py` — `select` (pick anchors at percentiles) + `placement`
    (Haiku-placement analysis).
  - `calibration/blind.py` — blinding (run `python -m calibration.blind <units.jsonl>`
    if you harvest more units).
  - `calibration/{harvest,pass2,pulls,filekind,config,github,hn}.py` — harvest more
    units: `pass2 --mode commit` (personal repos, no PRs) / `--mode pr` (team repos).
- If you want a bigger/fresher pool, harvest more (it's cheap) — but the existing 55
  span beginner→expert and are enough to build a first ladder for any axis.

---

## 2. The pipeline (run once per axis)

**Step A — rough candidate ordering (sparse sort, ~k=2).** Only to *find* candidate
anchors spanning the axis; rough is fine. Run a Workflow (template §4) over the 55
units with the axis prompt (§3), `k=2` → ~110 comparisons. (Opus throttles ~80
comparisons/run — see §5; resume to finish.) Fit BT → a rough ordering.

**Step B — pick ~10–12 candidate anchors** spanning the rough order (percentiles), e.g.
`python -m calibration.ladder select --verdicts out/<axis>_verdicts.json --anchors 11`.
Skim a few diffs to sanity-check the extremes (we caught a repo-name bias this way).

**Step C — DENSE all-pairs the candidates** (the load-bearing step). Run a Workflow
(template §4) doing **all ordered pairs, both directions** over just the ~11 anchors
(~110 comparisons, counterbalanced). Fit BT → the **bulletproof reference ordering**.
Check `oracle_consistency` ≈ 100% and position-bias (both-direction disagreements) is low.

**Step D — convergence cross-check.** Independently classify each anchor into
**low / mid / high** on the axis (a separate Opus pass; a coarse 3-level scale, NOT a
size-laden SEH scale). Confirm the dense order is monotonic with these labels. If it is
→ the ladder is trustworthy. If not → read the disagreements (usually a sparse-sort or
truncation artifact; a 4th sample or a direct comparison resolves it).

**Step E — orthogonality gates (must pass):**
- `spearman(axis_score, churn)` ≈ 0 (⊥ LOC). If high, harden the prompt's "ignore size."
- `spearman(axis_score, difficulty_score)` is *moderate*, not ~1.0 (distinct axis). Join
  on `id` via `out/ladder_verdicts.json`. If ~1.0, the prompt is secretly re-measuring
  difficulty — sharpen it.

**Step F — lock + place.** Save the anchor set + order to `out/<axis>_anchors.json` and
document it like [difficulty-ladder.md](difficulty-ladder.md). Score a new diff by
placing it against the anchors (Haiku, ~1 comparison/anchor, anchors prompt-cached) →
relative latent position. Validate placement with a Haiku run over held-out units and
`spearman(haiku_rung, dense_anchor_score)` (difficulty got 0.87).

---

## 3. Axis prompts (the ONLY substantive per-axis change)

Keep the structure of the difficulty prompt; swap the question + calibration cautions.
Always: *ignore size*, *ignore surface sophistication*, *judge relative to what the task
requires*.

**Originality** (→ the originality discount):
> ONE axis only: ORIGINALITY = how much genuinely novel problem-solving the change
> required, vs. reassembling patterns a library or standard idiom already provides.
> Which change solves a problem that **lacked an off-the-shelf solution**? Reimplementing
> something a library/stdlib already does well is LOW originality **even if it was hard
> to write**; a genuinely novel approach is HIGH **even if it is small or simple**.
> IGNORE size and IGNORE raw difficulty — a hard-but-derivative reimplementation is LOW.

**Cleanliness / parsimony** (→ the waste/quality pass; makes the 10k-line tangle lose):
> ONE axis only: CLEANLINESS = parsimony. Which change achieves its purpose with **less
> unnecessary complexity** — fewer moving parts, less duplication, no bloat — RELATIVE
> to what the task actually requires? Minimal necessary code = HIGH; over-engineered,
> duplicative, or convoluted code that a much smaller change would achieve = LOW. IGNORE
> raw size: a large change that genuinely needs to be large is fine; a small change that
> is still needlessly convoluted is not. Judge parsimony relative to the task.

**Structured output schema** (same for all axes):
`{ winner: "A"|"B"|"tie", reason: string }` — winner = the diff that is MORE
{original / clean}. For the convergence cross-check, classify each anchor:
`{ level: "low"|"mid"|"high", reason: string }`.

---

## 4. Workflow template (copy, swap AXIS + anchors)

Author scripts inline via the Workflow tool. **Hardcode ids/dir in the script** — the
`args` channel did not reliably deliver (see §5). Dense all-pairs version:

```js
export const meta = {
  name: '<axis>-allpairs', description: 'Dense all-pairs <axis> ordering of anchors',
  phases: [{ title: 'Compare', detail: 'Opus, every anchor vs every other' }],
}
const dir = 'C:/Users/jhart/Documents/software/HAID/out/blinded'
const anchors = ['U37','U39', /* … your ~11 candidate ids … */ ]
const AXIS = `You are a senior staff engineer judging two anonymized code-change diffs
(A and B) on ONE axis only: <PASTE the §3 axis question + the two IGNORE cautions>.
Identifiers are anonymized (PROJECT/OWNER); diffs may be truncated to show code first.`
const SCHEMA = { type:'object', properties:{ winner:{type:'string',enum:['A','B','tie']},
  reason:{type:'string'} }, required:['winner','reason'], additionalProperties:false }
const pairs = []
for (const a of anchors) for (const b of anchors) if (a!==b) pairs.push([a,b])  // counterbalanced
log(`${pairs.length} comparisons over ${anchors.length} anchors`)
const verdicts = await parallel(pairs.map(([a,b]) => async () => {
  const prompt = `${AXIS}\n\nRead these two diff files, decide which is MORE <axis>:\n` +
    `- Diff A: ${dir}/${a}.diff\n- Diff B: ${dir}/${b}.diff\n\nRespond ONLY via structured output.`
  const v = await agent(prompt, { label:`${a} vs ${b}`, phase:'Compare', model:'opus',
    agentType:'general-purpose', schema:SCHEMA })
  if (!v) return null
  return { a, b, winner: v.winner==='A'?a : v.winner==='B'?b : 'tie', reason:v.reason }
}))
return { anchors, verdicts: verdicts.filter(Boolean) }
```

For the sparse Step-A sort, replace the pair-builder with an offset schedule
(`for off in 1..k: pair i with (i+off)%n`) over all 55 ids. For placement (Step F), use
`model:'haiku'`, holdouts × anchors pairs. **Extract the result** from the task output
file: `json.load(open(path))['result']` (the file wraps it under `result`).

---

## 5. Operational gotchas (these WILL bite otherwise)

- **Opus rate limit ≈ 80 comparisons / run** (~2M tokens), after which agents fail their
  structured-output call. Keep a single Opus fan-out ≤ ~75, or **resume**:
  `Workflow({ scriptPath, resumeFromRunId })` — completed agents return cached, only
  failures re-run. Haiku tolerates much larger runs (414 done in one).
- **`args` did not deliver** to the workflow script (`args.x` came back undefined) —
  **hardcode** ids/dir/anchors directly in the script string.
- **PowerShell drops empty-string args** (`--buckets ""` → "expected one argument") —
  pass an explicit value.
- **Result extraction:** the task-output file is JSON `{summary, logs, result:{…}}` —
  read `['result']`, not the whole file.
- **Diffs are alphabetical** → docs/`examples/` lead and eat the truncation budget;
  blinding already reassembles code-first and drops doc/RFC-only PRs (filekind +
  `pass2 --min-code-churn`). Reuse the blinded files; don't re-derive.
- **Blind even from yourself:** never judge by repo name (we mis-called a crash-safe
  SQLite state machine "trivial" from its repo name; the blinded code judgment was right).

---

## 6. Definition of done (per axis)

1. A locked anchor set (~9–11), dense-ordered (100% consistent, low position bias),
   documented like difficulty-ladder.md.
2. Dense order **monotonic** with the independent low/mid/high cross-check (convergence).
3. **⊥-size:** `spearman(axis, churn)` ≈ 0. **Distinct axis:** `spearman(axis,
   difficulty)` clearly below ~0.9.
4. Placement validated: Haiku-on-anchors reproduces the dense order (Spearman, target
   ≳ 0.8).
5. The result feeds the combined score: `achievement = f(volume[LOC, deterministic],
   difficulty, cleanliness)`, `value = achievement ÷ cost(tokens)`, reported as
   **relative** comparison (self / team / corpus) for coaching — not an absolute number.
   (Originality is *not* in the formula — it was dropped; see scoring-rubric.md.)
