// ngspice local channel tests: batch engine and worker statuses with a fake
// process layer (the portable ngspice binary is not required for unit tests).
import test from 'node:test'
import assert from 'node:assert/strict'
import { execFile } from 'node:child_process'
import { promisify } from 'node:util'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const execFileAsync = promisify(execFile)
const ROOT = path.join(path.dirname(fileURLToPath(import.meta.url)), '..')

test('ngspice: engine helpers and worker statuses', async () => {
  const { stdout } = await execFileAsync('python',
    ['-B', path.join(ROOT, 'tests', 'ngspice_worker_test.py')],
    { timeout: 300_000, cwd: ROOT })
  assert.match(stdout, /ngspice worker tests ok/)
})
