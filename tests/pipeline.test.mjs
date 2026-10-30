// Pipeline-script smoke: the shipped Python layer must at least compile with the
// system python (the plugin spawns it at runtime; syntax errors would break every tool).
import test from 'node:test'
import assert from 'node:assert/strict'
import { execFile } from 'node:child_process'
import { promisify } from 'node:util'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const execFileAsync = promisify(execFile)
const HERE = path.dirname(fileURLToPath(import.meta.url))
const SCRIPTS = path.join(HERE, '..', 'scripts')
const ASSETS = path.join(HERE, '..', 'assets')

test('pipeline scripts compile with the system python', async () => {
  const files = [
    'sweep_worker_ngspice.py', 'checks.py', 'collect_results.py',
    'gen_sweep_report.py', 'trash.py',
    'snp_common.py', 'snp_read.py', 'snp_extract.py',
  ].map((f) => path.join(SCRIPTS, f)).concat([path.join(ASSETS, 'numfallback.py')])
  const { stdout } = await execFileAsync(
    'python', ['-m', 'py_compile', ...files],
    { timeout: 60_000 },
  )
  assert.equal(stdout, '')
})
