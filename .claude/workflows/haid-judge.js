export const meta = {
  name: 'haid-judge',
  description: 'HAID score fan-out: one independent haiku judge per scoring job (pairwise comparison, defect detection, or defect verification); returns answers grouped by manifest, in order.',
  phases: [{ title: 'Judge', detail: 'one haiku judge per scoring job' }],
}

// Schemas arrive TOP-LEVEL by kind (from the splitter, sourced from haid's manifests), so this
// workflow never hardcodes a schema and cannot drift from haid/scoring. They ride top-level — not
// nested per manifest — because the host model marshals `args` and drops nested-in-array data, which
// would leave judges with no schema and silently disable structured-output forcing. The three kinds:
//   pairwise -> {winner, reason}   folded to winners[]    -> <manifest>.verdicts.json
//   detect   -> {findings:[...]}   single job             -> <manifest>.findings.json
//   verify   -> {verdict, reason}  folded to verdicts[]   -> <manifest>.verdicts.json

phase('Judge')

// `args` reaches the script verbatim, and the host model routinely marshals nested data as a
// JSON *string* — so normalize before touching it. This shim is the whole reason this workflow
// is committed and invoked by path rather than re-authored each run.
const input = typeof args === 'string' ? JSON.parse(args) : args
const base = input.base               // dir holding <manifest>__<k>.txt prompt files
const manifests = input.manifests     // [{ manifest, kind, n, fingerprint }] from splitter
const schemas = input.schemas || {}   // { pairwise|detect|verify: <schema> } — TOP-LEVEL, by kind
const model = input.model || 'haiku'

// FAIL LOUD if any kind's schema is missing. Forcing the StructuredOutput tool depends entirely on a
// real schema object reaching agent(); a missing schema silently turns forcing off and the whole run
// degrades to free-text (the diagnosed millions-of-tokens failure). Abort cheaply instead.
for (const kind of new Set(manifests.map(m => m.kind))) {
  if (!schemas[kind] || typeof schemas[kind] !== 'object')
    throw new Error(`haid-judge: no schema for kind '${kind}' in args.schemas — structured-output ` +
      `forcing would be disabled; aborting. Re-run split_score_manifests.py and pass its stdout ` +
      `verbatim as args (the per-kind schemas must survive at the top level).`)
}

// One independent job per prompt file. Never batch a manifest's jobs into one agent: pairwise
// counterbalancing and per-finding verification both assume each judgment is decided in isolation.
const items = []
for (const m of manifests) {
  for (let k = 0; k < m.n; k++) {
    items.push({ manifest: m.manifest, kind: m.kind, schema: schemas[m.kind], k,
                 path: `${base}/${m.manifest}__${k}.txt` })
  }
}
log(`judging ${items.length} job(s) across ${manifests.length} manifest(s)`)

// agentType 'Explore' (not general-purpose): each judge reads ONE self-contained file and emits a
// structured verdict — a one-shot, read-only task. Explore is the only built-in that SKIPS the
// project's CLAUDE.md + git status, which (a) cuts per-judge context/cache materially on repos with
// a large CLAUDE.md and (b) is more correct — an anonymized-diff judge should not see the target
// project's house rules. Measured: it reads only the file (no wandering), forces StructuredOutput,
// and returns ~20% fewer output tokens than general-purpose at identical verdicts.
const judged = await parallel(items.map(it => () =>
  agent(
    `Read the file at ${it.path} using the Read tool, in full. Its entire contents are your ` +
    `complete task instructions for ONE self-contained scoring job (everything you need — any ` +
    `code diff — is inlined). Follow those instructions exactly. Read ONLY this one file — do ` +
    `not open any other file, manifest, or the repository. Return your answer as structured ` +
    `output matching the required schema.`,
    { label: `judge:${it.manifest}#${it.k}`, phase: 'Judge', schema: it.schema, model,
      agentType: 'Explore' }
  ).then(r => ({ manifest: it.manifest, k: it.k, result: r }))
))

// With a real schema at spawn (guaranteed above), the harness FORCES a StructuredOutput tool call
// and agent() returns a validated object — verified reliable even on 25k-token diffs, including with
// agentType:'general-purpose'. (The earlier production failures were NOT haiku ignoring the schema:
// the per-manifest schema was dropped when the host model marshalled `args`, so agent() got
// schema:undefined and forcing never engaged; the top-level-by-kind hand-off + the guard above fix
// that.) `coerce` stays only as a defensive passthrough — a forced reply is already an object, so the
// last-ditch JSON extraction never fires on the happy path; if forcing is ever absent, an unparseable
// reply fails the shape check below and the job is re-judged, never silently mis-scored.
function lastJsonObject(text) {
  if (typeof text !== 'string') return null
  let depth = 0, start = -1, inStr = false, esc = false, best = null
  for (let i = 0; i < text.length; i++) {
    const c = text[i]
    if (inStr) { if (esc) esc = false; else if (c === '\\') esc = true; else if (c === '"') inStr = false; continue }
    if (c === '"') inStr = true
    else if (c === '{') { if (depth === 0) start = i; depth++ }
    else if (c === '}' && depth > 0 && --depth === 0 && start >= 0) best = text.slice(start, i + 1)
  }
  if (best === null) return null
  try { return JSON.parse(best) } catch (e) { return null }
}
const coerce = r => r == null ? null : (typeof r === 'object' ? r : lastJsonObject(r))
// Validate SHAPE only (not values): empty `findings` is legitimate for a clean diff, and haid
// does authoritative value-validation on read-back (winner enum, counts, fingerprint). A parse
// miss must fail the shape check so it surfaces as a dead judge — re-judged — never a silent null
// that still reports complete:true.
function validShape(o, kind) {
  if (o == null || typeof o !== 'object') return false
  if (kind === 'detect') return Array.isArray(o.findings)
  if (kind === 'verify') return typeof o.verdict === 'string'
  return typeof o.winner === 'string' // pairwise
}

// Fold per manifest, in job order, shaped by kind. A dead judge OR an unparseable reply surfaces
// as an incomplete group; the caller must re-judge that manifest, never write an answers file
// with a null/short list (haid validates shape + count on read-back and fails loudly).
return manifests.map(m => {
  const rows = judged.filter(r => r && r.manifest === m.manifest).sort((a, b) => a.k - b.k)
                     .map(r => ({ ...r, parsed: coerce(r.result) }))
  const bad = rows.filter(r => !validShape(r.parsed, m.kind)).map(r => r.k)
  const ok = rows.length === m.n && bad.length === 0
  if (!ok) log(`${m.manifest}: incomplete (${rows.length}/${m.n} returned, unparseable jobs: [${bad}])`)
  const out = { manifest: m.manifest, kind: m.kind, fingerprint: m.fingerprint, complete: ok }
  if (m.kind === 'detect') {
    // n === 1: the single job's structured output is {findings:[...]}
    out.findings = ok ? (rows[0].parsed.findings || []) : null
  } else if (m.kind === 'verify') {
    out.verdicts = ok ? rows.map(r => ({ verdict: r.parsed.verdict, reason: r.parsed.reason })) : null
  } else {
    out.winners = ok ? rows.map(r => r.parsed.winner) : null
  }
  return out
})
