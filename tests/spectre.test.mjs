// Spectre local channel tests: PSF ASCII parsing and the sweep worker mechanics
// (fake process layer; no Spectre installation required).
import test from 'node:test'
import assert from 'node:assert/strict'
import { execFile } from 'node:child_process'
import { promisify } from 'node:util'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const execFileAsync = promisify(execFile)
const ROOT = path.join(path.dirname(fileURLToPath(import.meta.url)), '..')

async function runPy(rel) {
  const { stdout } = await execFileAsync('python', ['-B', path.join(ROOT, rel)],
    { timeout: 300_000, cwd: ROOT })
  return stdout
}

test('spectre: PSF ASCII parser handles swept/delta/complex and info scalars', async () => {
  const stdout = await runPy('tests/psf_parse_test.py')
  assert.match(stdout, /psf parser tests ok/)
})

test('spectre: sweep worker statuses, smart timeout, markers and progress', async () => {
  const stdout = await runPy('tests/spectre_worker_test.py')
  assert.match(stdout, /spectre worker tests ok/)
})

test('spectre: netlist checks — @@ placement, RCL/mult ranges, geometry suffix rules', async () => {
  const stdout = await runPy('tests/spectre_checks_test.py')
  assert.match(stdout, /spectre checks tests ok/)
})

// Per-session check-params channel: the calling session's
// id rides DSH_SESSION_ID to the Python workers through py.js sessionEnv; a
// delegated subagent resolves to its topmost user-visible ancestor
// (src/subagent.js), and an absent identity (direct server-side calls, tests)
// yields an empty overlay — the project-level fallback chain.
import { sessionEnv } from '../src/py.js'

test('spectre: sessionEnv maps the exec agent identity onto DSH_SESSION_ID', () => {
  const sid = 'session-aaaa1111-2222-3332-4444-555555555555'
  assert.deepEqual(sessionEnv({ agent: { id: sid } }, {}), { DSH_SESSION_ID: sid })
  assert.deepEqual(sessionEnv({}, {}), {})
  assert.deepEqual(sessionEnv(null, {}), {})
  assert.deepEqual(sessionEnv({ agent: {} }, {}), {})
  assert.deepEqual(sessionEnv(undefined, {}), {})
})

test('spectre: sessionEnv rides the subagent\'s topmost user-visible ancestor', () => {
  const rootId = 'session-bbbb1111-2222-3332-4444-555555555555'
  const root = { id: rootId, session: { header: {} } }
  const child = {
    id: 'session-cccc1111-2222-3332-4444-555555555555',
    session: { header: { origin: 'subagent', parentSession: rootId, isSeeded: true } },
  }
  const agents = new Map([[rootId, root]])
  const ctx = { agents: { get: (id) => agents.get(id) } }
  assert.deepEqual(sessionEnv({ agent: child }, ctx), { DSH_SESSION_ID: rootId })
  // A broken lineage (ancestor not live) falls back to the raw child id.
  assert.deepEqual(sessionEnv({ agent: child }, {}), { DSH_SESSION_ID: child.id })
})
