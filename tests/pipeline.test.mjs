// Pipeline-script smoke: every shipped Python script must parse with the system
// python (the plugin spawns them at runtime; syntax errors would break every tool).
// ast.parse is used instead of py_compile so no __pycache__ is written into the repo.
import test from 'node:test'
import assert from 'node:assert/strict'
import { execFile } from 'node:child_process'
import { promisify } from 'node:util'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const execFileAsync = promisify(execFile)
const HERE = path.dirname(fileURLToPath(import.meta.url))
const ROOT = path.join(HERE, '..')

function pyFiles(dir) {
  const out = []
  for (const ent of fs.readdirSync(dir, { withFileTypes: true })) {
    if (ent.name === '__pycache__') continue
    const abs = path.join(dir, ent.name)
    if (ent.isDirectory()) out.push(...pyFiles(abs))
    else if (ent.name.endsWith('.py')) out.push(abs)
  }
  return out
}

test('pipeline scripts parse with the system python', async () => {
  const files = pyFiles(path.join(ROOT, 'scripts'))
    .concat([path.join(ROOT, 'assets', 'numfallback.py')])
  // 9 shipped scripts (common x3, spectre x3, snp x3) + numfallback;
  // the analysis scripts (collect/report/trash) moved to the HarnessAMS plugin.
  assert.ok(files.length >= 10, `expected at least 10 scripts, got ${files.length}`)
  const code = 'import ast,sys\n'
    + 'for p in sys.argv[1:]:\n'
    + '    ast.parse(open(p, encoding="utf-8").read(), p)\n'
  const { stdout } = await execFileAsync('python', ['-B', '-c', code, ...files],
    { timeout: 120_000 })
  assert.equal(stdout, '')
})

test('scripts keep the simulator/common prefix convention', () => {
  const groups = {
    common: /^common_/,
    spectre: /^spectre_/,
    snp: /^snp_/,
  }
  for (const [dir, pattern] of Object.entries(groups)) {
    const names = fs.readdirSync(path.join(ROOT, 'scripts', dir))
      .filter((n) => n.endsWith('.py'))
    assert.ok(names.length, `scripts/${dir} is empty`)
    for (const n of names) assert.match(n, pattern, `scripts/${dir}/${n}`)
  }
})
