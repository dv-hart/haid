export const meta = {
  name: 'haid-tag',
  description: 'HAID tag fan-out: one independent haiku agent per session branch; each labels every marked message in its transcript and echoes the short ref. Returns labels grouped by branch, in order.',
  phases: [{ title: 'Tag', detail: 'one haiku agent per session branch' }],
}

// The manifest's labels-array schema travels in `args` (from split_tag_manifest.py), so this
// workflow never hardcodes a schema and cannot drift from haid.intent.taxonomy. Each agent reads
// ONE branch-transcript file and returns {labels:[{ref, move, work_type, impl_kind, purpose}]} —
// echoing the short ref in each CLASSIFY marker, NEVER a full uuid. aggregate_tag_answers.py
// reattaches uuids from the manifest, so the model is never asked to copy a 36-char id.

phase('Tag')

// `args` reaches the script verbatim, and the host model routinely marshals nested data as a JSON
// *string* — so normalize before touching it. This shim is the whole reason this workflow is
// committed and invoked by path rather than re-authored each run.
const input = typeof args === 'string' ? JSON.parse(args) : args
const base = input.base              // dir holding <stem>.txt branch-transcript files
const schema = input.schema          // the labels-array schema, from haid's manifest
const jobs = input.jobs              // [{ job_id, n_targets, path }] from the splitter
const model = input.model || 'haiku'

log(`tagging ${jobs.length} branch(es), ${jobs.reduce((n, j) => n + j.n_targets, 0)} message(s)`)

const tagged = await parallel(jobs.map(j => () =>
  agent(
    `Read the file at ${j.path} using the Read tool, in full. Its entire contents are your ` +
    `complete task instructions for ONE session branch (the whole transcript is inlined). ` +
    `Classify EVERY message marked '>>> CLASSIFY THIS MESSAGE — ref: … <<<' and no others, in ` +
    `order, copying each marker's short ref verbatim. Read ONLY this one file — do not open any ` +
    `other file, manifest, or the repository. Return structured output matching the required schema.`,
    { label: `tag:${j.job_id}`, phase: 'Tag', schema, model, agentType: 'general-purpose' }
  ).then(r => ({ job_id: j.job_id, n_targets: j.n_targets, result: r }))
))

// With a real schema at spawn, the harness FORCES a StructuredOutput tool call and agent() returns a
// validated object — reliable even with agentType:'general-purpose' (verified on the score path). The
// schema reaches agent() because it rides TOP-LEVEL in args (see above); nested-in-array data would be
// dropped in marshalling and silently disable forcing. `coerce` stays only as a defensive passthrough:
// a forced reply is already an object, so the last-ditch JSON extraction never fires on the happy path;
// if forcing is ever absent, an unparseable reply fails the count check below and the branch is re-run.
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

// Fold per branch, in job order. A branch is complete only when its reply parses to a labels array
// of EXACTLY n_targets entries — a dead agent, an unparseable reply, or a wrong count surfaces as
// an incomplete group so the caller re-runs that branch, never writes a short/null answers file.
// aggregate_tag_answers.py does authoritative ref/enum validation against the manifest after this.
return tagged.map(t => {
  const o = coerce(t && t.result)
  const labels = o && Array.isArray(o.labels) ? o.labels : null
  const complete = !!labels && labels.length === t.n_targets
  if (!complete) {
    const got = labels ? `${labels.length}/${t.n_targets} labels` : 'unparseable reply'
    log(`${t.job_id}: incomplete (${got})`)
  }
  return { job_id: t.job_id, n_targets: t.n_targets, complete, labels: complete ? labels : null }
})
