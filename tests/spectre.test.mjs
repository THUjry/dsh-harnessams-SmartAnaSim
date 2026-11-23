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

test('spectre: assembly checks — ideal RCL ranges and geometry suffix rules', async () => {
  const stdout = await runPy('tests/spectre_checks_test.py')
  assert.match(stdout, /spectre checks tests ok/)
})

// Per-session check-params channel (HarnessAMS D-004): exec.agent.id IS the
// SessionId; the tool shells thread it to Python workers as DSH_SESSION_ID
// through py.js sessionEnv (empty overlay when the identity is absent —
// direct server-side calls and tests resolve the project-level fallback).
import { sessionEnv } from '../src/py.js'

test('spectre: sessionEnv maps the exec agent identity onto DSH_SESSION_ID', () => {
  const sid = 'session-aaaa1111-2222-3332-4444-555555555555'
  assert.deepEqual(sessionEnv({ agent: { id: sid } }), { DSH_SESSION_ID: sid })
  assert.deepEqual(sessionEnv({}), {})
  assert.deepEqual(sessionEnv(null), {})
  assert.deepEqual(sessionEnv({ agent: {} }), {})
  assert.deepEqual(sessionEnv(undefined), {})
})
