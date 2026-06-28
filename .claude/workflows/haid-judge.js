export const meta = {
  name: 'haid-judge',
  description: 'HAID score fan-out: one independent haiku judge per scoring job (pairwise comparison, defect detection, or defect verification); returns answers grouped by manifest, in order.',
  phases: [{ title: 'Judge', detail: 'one haiku judge per scoring job' }],
}

// Each manifest carries its OWN schema + kind (from the splitter, sourced from haid's manifest),
// so this workflow never hardcodes a schema and cannot drift from haid/scoring. The three kinds:
//   pairwise -> {winner, reason}   folded to winners[]    -> <manifest>.verdicts.json
//   detect   -> {findings:[...]}   single job             -> <manifest>.findings.json
//   verify   -> {verdict, reason}  folded to verdicts[]   -> <manifest>.verdicts.json

phase('Judge')

// `args` reaches the script verbatim, and the host model routinely marshals nested data as a
// JSON *string* — so normalize before touching it. This shim is the whole reason this workflow
// is committed and invoked by path rather than re-authored each run.
const input = typeof args === 'string' ? JSON.parse(args) : args
const base = input.base               // dir holding <manifest>__<k>.txt prompt files
const manifests = input.manifests     // [{ manifest, kind, n, fingerprint, schema }] from splitter
const model = input.model || 'haiku'

// One independent job per prompt file. Never batch a manifest's jobs into one agent: pairwise
// counterbalancing and per-finding verification both assume each judgment is decided in isolation.
const items = []
for (const m of manifests) {
  for (let k = 0; k < m.n; k++) {
    items.push({ manifest: m.manifest, kind: m.kind, schema: m.schema, k,
                 path: `${base}/${m.manifest}__${k}.txt` })
  }
}
log(`judging ${items.length} job(s) across ${manifests.length} manifest(s)`)

const judged = await parallel(items.map(it => () =>
  agent(
    `Read the file at ${it.path} using the Read tool, in full. Its entire contents are your ` +
    `complete task instructions for ONE self-contained scoring job (everything you need — any ` +
    `code diff — is inlined). Follow those instructions exactly. Read ONLY this one file — do ` +
    `not open any other file, manifest, or the repository. Return your answer as structured ` +
    `output matching the required schema.`,
    { label: `judge:${it.manifest}#${it.k}`, phase: 'Judge', schema: it.schema, model,
      agentType: 'general-purpose' }
  ).then(r => ({ manifest: it.manifest, k: it.k, result: r }))
))

// The `schema` option asks the harness to force a StructuredOutput tool call and return a
// validated object — but a custom `agentType` only gets that as an *appended instruction*, not a
// hard constraint, so haiku routinely ignores it and emits fenced/inline JSON as its final text.
// In that case agent() returns the raw string, not an object. Accept BOTH: when forcing fires,
// `coerce` is a no-op passthrough; when it doesn't, we extract the last complete JSON object from
// the reply — the "tolerant extraction" runner rule the skill mandates. This makes the workflow
// independent of whether forcing succeeds in a given harness.
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
