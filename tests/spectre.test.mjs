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
