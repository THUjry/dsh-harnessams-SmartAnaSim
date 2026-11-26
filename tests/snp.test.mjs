// snp_* tool tests. The numeric regressions, CLI exit contracts, stderr
// warnings, and backend parity live in the single-process python self-tests
// (tests/snp_extract_test.py + tests/snp_common_test.py, the psf_parse_test.py
// pattern — one python process each); this file keeps the JS<->python
// tool-face contracts and mounts the self-tests. Run with bare `node --test`
// from the repo root.
import test, { after } from 'node:test'
import assert from 'node:assert/strict'
import { execFile } from 'node:child_process'
import { promisify } from 'node:util'
import { mkdtempSync, readFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { snpTools } from '../src/tools/snp.js'

const execFileAsync = promisify(execFile)
const ROOT = path.join(path.dirname(fileURLToPath(import.meta.url)), '..')
const READER = path.join(ROOT, 'scripts', 'snp', 'snp_read.py')
const SYNTH = path.join(ROOT, 'tests', 'synth_helper.py')

const tmpDirs = []
const tmp = () => {
  const dir = mkdtempSync(path.join(tmpdir(), 'snp-'))
  tmpDirs.push(dir)
  return dir
}
after(() => {
  for (const dir of tmpDirs) rmSync(dir, { recursive: true, force: true })
})

async function synth(kind, name) {
  const dir = tmp()
  const file = path.join(dir, name)
  const { stdout } = await execFileAsync('python', ['-B', SYNTH, kind, file], { timeout: 120_000 })
  return { dir, file, info: JSON.parse(stdout) }
}

async function readSnp(file, param = 'S') {
  const out = tmp()
  const { stdout } = await execFileAsync(
    'python', ['-B', READER, '--file', file, '--param', param, '--out-dir', out],
    { timeout: 120_000 })
  const stem = path.basename(file).replace(/\.[^.]+$/, '')
  const j = JSON.parse(readFileSync(path.join(out, `${stem}_read.json`), 'utf8'))
  const csv = readFileSync(path.join(out, `${stem}_params_${param}.csv`), 'utf8')
  return { j, stdout, out, csv, stem, dir: out }
}

async function toCsv(file, param = 'Z') {
  // snp_extract accepts params CSVs only: convert raw fixtures through the
  // reader — the documented user chain (snp_read first, then snp_extract).
  const r = await readSnp(file, param)
  return path.join(r.dir, `${r.stem}_params_${param}.csv`)
}

test('self-test: snp_extract numeric regressions run in one process', async () => {
  const { stdout } = await execFileAsync('python',
    ['-B', path.join(ROOT, 'tests', 'snp_extract_test.py')],
    { timeout: 120_000, cwd: ROOT })
  assert.match(stdout, /snp extract tests ok/)
})

test('snp_common: touchstone unit order, psf port base, duplicate freq, z0 guard', async () => {
  const { stdout } = await execFileAsync('python',
    ['-B', path.join(ROOT, 'tests', 'snp_common_test.py')],
    { timeout: 120_000, cwd: ROOT })
  assert.match(stdout, /snp_common tests ok/)
})

// ---------------------------------------------------------------------------
// Tool-face contract regressions (2026-11-09 code review): the JS tools parse
// the Python "[snp] wrote <path>" lines instead of re-deriving the stem, and
// the unphysical exit-1 result must surface as a structured ok=false return.
// ---------------------------------------------------------------------------

test('tool face: snp_read returns a summary for a params-CSV input (stem contract)', async () => {
  // The Python side strips a previous _params_X suffix from the output stem;
  // the old JS re-derivation did not, so x_params_S.csv lost its summary
  // silently (readJsonIfExists -> null) — the exact bug this pins out.
  const s = await synth('xfmr2', 'xfmr2.s2p')
  const first = await readSnp(s.file) // touchstone -> <stem>_params_S.csv
  const csv = path.join(first.dir, `${first.stem}_params_S.csv`)
  const res = await snpTools().find((d) => d.name === 'snp_read').execute({ file: csv })
  assert.equal(res.ok, true)
  assert.ok(res.summary, 'summary must not be null for a params-CSV input')
  assert.equal(res.summary.param, 'S')
  assert.equal(res.summary.n_ports, 2)
})

test('tool face: unphysical snp_extract (exit 1) returns ok=false WITH summary/port_plan', async () => {
  // A tl line read as a transformer is non-physical (L collapses, |k|>1) —
  // snp_extract.py writes the JSON and exits 1 by contract; the tool must
  // surface the structured diagnosis instead of throwing it away.
  const s = await synth('tl2', 'tl2.s2p')
  const res = await snpTools().find((d) => d.name === 'snp_extract')
    .execute({ file: await toCsv(s.file), type: 'transformer' })
  assert.equal(res.ok, false)
  assert.ok(res.port_plan, 'port_plan must survive the unphysical exit')
  assert.ok(res.summary, 'summary must survive the unphysical exit')
  assert.match(res.output, /\[snp\]/)
})

test('tool face: snp_extract with no JSON on failure still throws', async () => {
  const missing = path.join(tmpdir(), `definitely-missing-${Date.now()}.csv`)
  await assert.rejects(() => snpTools().find((d) => d.name === 'snp_extract').execute({ file: missing, type: 'tl' }),
    /snp_extract failed/)
})

test('tool face: snp_extract rejects unknown parameter keys with the valid list', async () => {
  // the argv passthrough used to forward ANY key as an invented CLI flag
  // (validateArgs only checks declared properties) — reject here instead,
  // before any spawn: no fixture needed
  await assert.rejects(() => snpTools().find((d) => d.name === 'snp_extract')
    .execute({ file: 'never-read.csv', type: 'tl', freq: 1e9 }),
    /unknown parameter\(s\): freq\b.*valid keys: file, type, freq_hz/)
})
