// post family tests: the collect-style diagnostics tool (python self-test +
// the JS orchestration contract over a real spawn — argparse owns the
// exactly-one rule, JS only forwards args and passes the JSON report through).
import test from 'node:test'
import assert from 'node:assert/strict'
import { execFile } from 'node:child_process'
import { promisify } from 'node:util'
import { copyFileSync, mkdirSync, mkdtempSync, readdirSync, writeFileSync } from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { toolDefinitions } from '../src/index.js'
import { makeTool } from '../src/tool.js'

const execFileAsync = promisify(execFile)
const ROOT = path.join(path.dirname(fileURLToPath(import.meta.url)), '..')
const DATA = path.join(ROOT, 'tests', 'data', 'psf', 'point_ac', 'netlist.scs.raw')

async function runPy(rel) {
  const { stdout } = await execFileAsync('python', ['-B', path.join(ROOT, rel)],
    { timeout: 300_000, cwd: ROOT })
  return stdout
}

function postTool() {
  return makeTool(toolDefinitions().find((d) => d.name === 'post_check'))
}

function buildWorkDir() {
  const tmp = mkdtempSync(path.join(os.tmpdir(), 'postcheck-'))
  const raw = path.join(tmp, '00', 'netlist.scs.raw')
  mkdirSync(raw, { recursive: true })
  for (const name of readdirSync(DATA)) copyFileSync(path.join(DATA, name), path.join(raw, name))
  writeFileSync(path.join(tmp, '00', 'point_meta.json'),
    JSON.stringify({ status: 'success', signals: { vth: 0.45 } }) + '\n')
  writeFileSync(path.join(tmp, 'sweep_progress.json'),
    JSON.stringify({ status: 'completed', total_points: 1, completed: 1, succeeded: 1,
      failed: 0, points: [{ index: 0, status: 'success' }] }) + '\n')
  return tmp
}

test('post: the collector script passes its python self-test', async () => {
  const stdout = await runPy('tests/post_check_test.py')
  assert.match(stdout, /post_check tests ok/)
})

test('post: post_check executes through the real python channel and returns the report', async () => {
  const report = await postTool().execute({ work_dir: buildWorkDir() }, {})
  assert.equal(report.ok, true)
  assert.equal(report.status, 'success')
  assert.equal(report.mode, 'work-dir')
  assert.equal(report.points.present, 1)
  assert.deepEqual(report.issues, [])
  assert.equal(typeof report.summary, 'string')
  assert.equal(typeof report.duration_ms, 'number')
})

test('post: raw_dir single-point mode works through the tool', async () => {
  const report = await postTool().execute({ raw_dir: DATA }, {})
  assert.equal(report.mode, 'raw-dir')
  assert.equal(report.points.present, 1)
})

test('post: argparse owns the exactly-one rule (JS only orchestrates)', async () => {
  await assert.rejects(() => postTool().execute({}, {}),
    /post_check failed:[\s\S]*one of the arguments --work-dir --raw-dir is required/)
  await assert.rejects(() => postTool().execute({ work_dir: 'x', raw_dir: 'y' }, {}),
    /post_check failed:[\s\S]*not allowed with argument/)
})

test('post: a missing directory fails loud with the script output', async () => {
  await assert.rejects(() =>
    postTool().execute({ work_dir: path.join(os.tmpdir(), 'no-such-hams-dir') }, {}),
    /post_check failed:[\s\S]*work dir does not exist/)
})
