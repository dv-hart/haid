export const meta = {
  name: 'haid-judge',
  description: 'HAID score fan-out: one independent haiku judge per pairwise comparison file; returns winners grouped by manifest, in comparison order.',
  phases: [{ title: 'Judge', detail: 'one haiku judge per pairwise comparison' }],
}

// Mirrors haid's scoring VERDICT_SCHEMA (src/haid/scoring/compare.py): winner + reason.
// haid's read-back only consumes `winners`, but the calibrated prompt asks for a reason too.
const VERDICT_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  properties: {
    winner: { type: 'string', enum: ['A', 'B', 'tie'] },
    reason: { type: 'string' },
  },
  required: ['winner', 'reason'],
}

phase('Judge')

// `args` reaches the script verbatim, and the host model routinely marshals nested data as
// a JSON *string* — so normalize before touching it. This shim is the whole reason this
// workflow is committed and invoked by path rather than re-authored each run.
const input = typeof args === 'string' ? JSON.parse(args) : args
const base = input.base               // dir holding <manifest>__<k>.txt comparison files
const manifests = input.manifests     // [{ manifest, n, fingerprint }] from the splitter
const model = input.model || 'haiku'

// One independent job per comparison. Never batch a manifest's comparisons into a single
// agent: the deterministic counterbalancing baked into each prompt assumes every verdict is
// decided in isolation, blind to the others.
const items = []
for (const m of manifests) {
  for (let k = 0; k < m.n; k++) {
    items.push({ manifest: m.manifest, k, path: `${base}/${m.manifest}__${k}.txt` })
  }
}
log(`judging ${items.length} comparison(s) across ${manifests.length} manifest(s)`)

const judged = await parallel(items.map(it => () =>
  agent(
    `Read the file at ${it.path} using the Read tool, in full. Its entire contents are your ` +
    `complete task instructions: ONE calibrated pairwise comparison of two anonymized code ` +
    `diffs labeled A and B, judged on a single axis, with both diffs inlined. Follow those ` +
    `instructions exactly and decide the winner. Read ONLY this one file — do not open any ` +
    `other file, manifest, or the repository; everything you need is in it. Return your ` +
    `verdict: winner ("A", "B", or "tie") plus a one-line reason.`,
    { label: `judge:${it.manifest}#${it.k}`, phase: 'Judge', schema: VERDICT_SCHEMA, model, agentType: 'general-purpose' }
  ).then(v => ({ manifest: it.manifest, k: it.k, winner: v ? v.winner : null }))
))

// Fold winners back per manifest, in comparison order. A dead judge surfaces as null; the
// caller must re-judge that manifest, never write a verdicts file with a null or short list
// (haid validates A/B/tie and exact count on read-back and fails loudly).
return manifests.map(m => {
  const rows = judged.filter(r => r && r.manifest === m.manifest).sort((a, b) => a.k - b.k)
  const winners = rows.map(r => r.winner)
  return {
    manifest: m.manifest,
    fingerprint: m.fingerprint,
    winners,
    complete: winners.length === m.n && winners.every(w => w !== null),
  }
})
