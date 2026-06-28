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

// Fold per manifest, in job order, shaped by kind. A dead judge surfaces as a null result; the
// caller must re-judge that manifest, never write an answers file with a null/short list (haid
// validates shape + count on read-back and fails loudly).
return manifests.map(m => {
  const rows = judged.filter(r => r && r.manifest === m.manifest).sort((a, b) => a.k - b.k)
  const ok = rows.length === m.n && rows.every(r => r.result != null)
  const out = { manifest: m.manifest, kind: m.kind, fingerprint: m.fingerprint, complete: ok }
  if (m.kind === 'detect') {
    // n === 1: the single job's structured output is {findings:[...]}
    out.findings = ok ? (rows[0].result.findings || []) : null
  } else if (m.kind === 'verify') {
    out.verdicts = ok ? rows.map(r => ({ verdict: r.result.verdict, reason: r.result.reason })) : null
  } else {
    out.winners = ok ? rows.map(r => r.result.winner) : null
  }
  return out
})
