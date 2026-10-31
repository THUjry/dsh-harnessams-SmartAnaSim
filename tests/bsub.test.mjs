// Generic bsub (LSF) tools: target resolution, upload/submit/poll/fetch against a
// mocked dsh-remote tool shell (no network, no SSH, no LSF required).
import test from 'node:test'
import assert from 'node:assert/strict'
import { execFile } from 'node:child_process'
import { promisify } from 'node:util'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { toolDefinitions } from '../src/index.js'
import { makeTool } from '../src/tool.js'
import { parseRwExecText, parseRwMachinesText, resolveTarget } from '../src/remote.js'
import { parsePollOutput, resolveRemoteDir, sanitizeRel } from '../src/bsub.js'

const execFileAsync = promisify(execFile)
const OLD_DSH_HOME = process.env.DSH_HOME

function tmp(prefix) {
  return fs.mkdtempSync(path.join(os.tmpdir(), prefix))
}

function setupMirror() {
  const home = tmp('hams-dsh-')
  const mirror = path.join(home, 'remote-workspaces', 'node1-192.0.2.11-22', 'sim')
  fs.mkdirSync(mirror, { recursive: true })
  fs.writeFileSync(path.join(mirror, '.dsh-remote-meta.json'), JSON.stringify({
    host: '192.0.2.11', port: 22, username: 'op', remotePath: '/home/op/sim',
  }))
  process.env.DSH_HOME = home
  return { home, mirror }
}

function restoreEnv() {
  if (OLD_DSH_HOME === undefined) delete process.env.DSH_HOME
  else process.env.DSH_HOME = OLD_DSH_HOME
}

const MACHINES_TEXT = '- node1  "node1"  [native]  op@192.0.2.11:22  [CURRENT]\n'
  + '    auth: key:secret workspace=/home/op/sim\n'

function pollText({ meta = 2, total = 2, status = 'completed', bjobs = '777 RUN' } = {}) {
  const prog = JSON.stringify({
    status, total_points: total, completed: meta, succeeded: meta, failed: 0,
    simulator: 'spectre', compute_node: 'node3',
  })
  return `__BJOB__\n${bjobs}\n__META__\n${meta}\n__DIRS__\n${meta}\n`
    + `__PROG__\n${prog}\n__ERR__\n`
}

function makeCtx({ mirror, sessions = null, poll = {}, archive = null, failUpload = false,
  existing = 0 } = {}) {
  const calls = []
  const live = sessions ?? [{ header: { cwd: mirror } }]
  const text = (s) => ({ isError: false, content: [{ type: 'text', text: s }] })
  return {
    calls,
    get: (name) => (name === 'sessions' ? { list: () => live } : undefined),
    tools: {
      get: (name) => (['rw_machines', 'rw_exec', 'rw_upload', 'rw_download'].includes(name)
        ? { name } : undefined),
      execute: async (input) => {
        calls.push(input)
        const a = input.arguments || {}
        if (input.name === 'rw_machines') return text(MACHINES_TEXT)
        if (input.name === 'rw_upload') {
          if (failUpload) return { isError: true, content: [{ type: 'text', text: 'upload denied' }] }
          return text(`uploaded 12 bytes from ${a.localPath} → ${a.path}`)
        }
        if (input.name === 'rw_download') {
          if (archive) fs.copyFileSync(archive, a.localPath)
          return text(`downloaded bytes from ${a.path} → ${a.localPath}`)
        }
        const cmd = String(a.command || '')
        if (cmd.includes('__BJOB__')) return text(pollText(poll))
        if (cmd.includes('sleep 1; bjobs')) return text('777 RUN\n__STARTED__\n')
        if (cmd.includes('bsub -q')) {
          return text('Job <777> is submitted to queue <normal>.\n')
        }
        if (cmd.includes('tar czf .bsub_fetch.tgz')) return text('512\n')
        if (cmd.includes('| sort | head -200')) {
          return text('./00/point_meta.json\n./00/netlist.scs.raw/sp.sp\n')
        }
        if (cmd.includes('wc -l')) return text(`${existing}\n`)
        return text('')
      },
    },
  }
}

function makeRunDir({ template = 'template.scs', config } = {}) {
  const dir = tmp('hams-run-')
  fs.writeFileSync(path.join(dir, template), 'simulator lang=spectre\nparameters SW=@@SW@@\n')
  fs.writeFileSync(path.join(dir, 'sweep_config.json'), JSON.stringify(config || {
    netlist_template: 'template.scs',
    parameters: { SW: [0, 1] },
    sweep_mode: 'cross_product',
  }))
  return dir
}

async function makeArchive() {
  const root = tmp('hams-remote-')
  fs.mkdirSync(path.join(root, '00', 'netlist.scs.raw'), { recursive: true })
  fs.writeFileSync(path.join(root, '00', 'point_meta.json'),
    JSON.stringify({ status: 'success', signals: { m: 1 } }))
  fs.writeFileSync(path.join(root, '00', 'netlist.scs.raw', 'sp.sp'),
    '# Hz S RI R 50\n1.0e9 1 0 0 0\n')
  fs.writeFileSync(path.join(root, 'sweep_progress.json'),
    JSON.stringify({ status: 'completed', total_points: 2, completed: 2 }))
  const archive = path.join(tmp('hams-arch-'), 'fetch.tgz')
  await execFileAsync('tar', ['-czf', archive, '-C', root, '.'], { timeout: 60_000 })
  return archive
}

function tool(ctx, name) {
  const def = toolDefinitions(ctx).find((d) => d.name === name)
  assert.ok(def, name)
  return makeTool(def)
}

test('dsh-remote text contracts: machines listing and rw_exec markers', () => {
  const machines = parseRwMachinesText(MACHINES_TEXT)
  assert.equal(machines.length, 1)
  assert.equal(machines[0].id, 'node1')
  assert.equal(machines[0].host, '192.0.2.11')
  assert.equal(machines[0].port, 22)
  assert.equal(machines[0].workspace, '/home/op/sim')
  assert.equal(machines[0].current, true)

  const ok = parseRwExecText('hello\n[exit code: 0]')
  assert.equal(ok.stdout, 'hello')
  assert.equal(ok.exitCode, 0)
  const bad = parseRwExecText('oops\n-- stderr --\nbroken\n[exit code: 2]')
  assert.equal(bad.exitCode, 2)
  assert.match(bad.stderr, /broken/)
  const promoted = parseRwExecText('[still running after 20000ms; moved to background job job-9]')
  assert.equal(promoted.promotedJob, 'job-9')
  const timed = parseRwExecText('x\n[command timed out after 20000ms]')
  assert.equal(timed.timedOut, true)
})

test('path guards and poll parsing', () => {
  assert.throws(() => sanitizeRel('../x.py'), /unsafe relative path/)
  assert.throws(() => resolveRemoteDir('/home/op/sim', '../evil'), /escapes the remote workspace/)
  assert.equal(resolveRemoteDir('/home/op/sim', 'batch1'), '/home/op/sim/batch1')
  const parsed = parsePollOutput(pollText({ meta: 2, total: 2 }), { total: 2 })
  assert.equal(parsed.state, 'completed')
  assert.equal(parsed.completed, 2)
  const empty = parsePollOutput(pollText({ meta: 2, bjobs: 'No job found' }), { total: 2 })
  assert.equal(empty.state, 'completed')
  const running = parsePollOutput(pollText({ meta: 1, total: 2, status: 'running' }), { total: 2 })
  assert.equal(running.state, 'running')
})

test('bsub_submit uploads the worker scripts and run files, then records the job', async () => {
  const env = setupMirror()
  const run = makeRunDir()
  try {
    const ctx = makeCtx({ mirror: env.mirror })
    const res = await tool(ctx, 'bsub_submit').execute({
      run_dir: run,
      remote_dir: 'batch1',
      machine_id: 'node1',
      worker: 'spectre/spectre_sweep_worker.py',
      worker_args: ['--config', 'sweep_config.json', '--max-parallel', '2'],
    }, {})
    assert.equal(res.ok, true, JSON.stringify(res))
    assert.equal(res.job_id, '777')
    assert.equal(res.remote_dir, '/home/op/sim/batch1')
    assert.equal(res.state, 'running')

    const job = JSON.parse(fs.readFileSync(path.join(run, '.bsub_job.json'), 'utf8'))
    assert.equal(job.job_id, '777')
    assert.equal(job.total_points, 2)
    assert.equal(job.remote_dir, '/home/op/sim/batch1')

    const uploads = ctx.calls.filter((c) => c.name === 'rw_upload')
      .map((c) => c.arguments.path)
    assert.ok(uploads.includes('/home/op/sim/.harnessams/scripts/spectre/spectre_sweep_worker.py'))
    assert.ok(uploads.includes('/home/op/sim/.harnessams/scripts/common/common_sweep_engine.py'))
    assert.ok(uploads.includes('/home/op/sim/batch1/template.scs'))
    assert.ok(uploads.includes('/home/op/sim/batch1/sweep_config.json'))
    assert.equal(uploads.length, 16, uploads.join('\n'))

    const bsub = ctx.calls.find((c) => c.name === 'rw_exec'
      && String(c.arguments.command).includes('bsub -q'))
    assert.ok(bsub, 'bsub command sent')
    assert.match(bsub.arguments.command, /"--work-dir" "\/home\/op\/sim\/batch1"/)
    assert.match(bsub.arguments.command, /spectre_sweep_worker\.py/)
  } finally {
    restoreEnv()
  }
})

test('bsub_poll reads the job file and reports completed', async () => {
  const env = setupMirror()
  const run = makeRunDir()
  try {
    fs.writeFileSync(path.join(run, '.bsub_job.json'), JSON.stringify({
      machine_id: 'node1', remote_dir: '/home/op/sim/batch1', job_id: '777', total_points: 2,
    }))
    const ctx = makeCtx({ mirror: env.mirror, poll: { meta: 2, total: 2 } })
    const res = await tool(ctx, 'bsub_poll').execute({ run_dir: run }, {})
    assert.equal(res.ok, true)
    assert.equal(res.state, 'completed')
    assert.equal(res.completed, 2)
    assert.equal(res.job_id, '777')
  } finally {
    restoreEnv()
  }
})

test('bsub_fetch downloads the archive and extracts it into the run directory', async () => {
  const env = setupMirror()
  const run = makeRunDir()
  const archive = await makeArchive()
  try {
    fs.writeFileSync(path.join(run, '.bsub_job.json'), JSON.stringify({
      machine_id: 'node1', remote_dir: '/home/op/sim/batch1', job_id: '777', total_points: 2,
    }))
    const ctx = makeCtx({ mirror: env.mirror, archive })
    const res = await tool(ctx, 'bsub_fetch').execute({
      run_dir: run,
      includes: ['*/point_meta.json', '*/netlist.scs.raw/sp.sp', 'sweep_progress.json'],
    }, {})
    assert.equal(res.ok, true, JSON.stringify(res))
    assert.equal(res.mode, 'archive')
    assert.equal(res.fetched.points, 1)
    assert.equal(res.fetched.sp_files, 1)
    assert.ok(fs.existsSync(path.join(run, '00', 'point_meta.json')))
    assert.ok(fs.existsSync(path.join(run, '00', 'netlist.scs.raw', 'sp.sp')))
    assert.ok(fs.existsSync(path.join(run, 'sweep_progress.json')))
    assert.ok(fs.existsSync(path.join(run, 'fetch_777.tgz')))
  } finally {
    restoreEnv()
  }
})

test('bsub tools reject escaped paths, missing mirrors and missing live sessions', async () => {
  const env = setupMirror()
  try {
    const ctx = makeCtx({ mirror: env.mirror })
    await assert.rejects(() => tool(ctx, 'bsub_submit').execute({
      run_dir: makeRunDir(), remote_dir: 'batch1', machine_id: 'node1', worker: '../evil.py',
    }, {}), /unsafe relative path/)

    await assert.rejects(() => tool(ctx, 'bsub_submit').execute({
      run_dir: makeRunDir(), remote_dir: '../evil', machine_id: 'node1',
      worker: 'spectre/spectre_sweep_worker.py',
    }, {}), /escapes the remote workspace/)

    await assert.rejects(() => tool(ctx, 'bsub_submit').execute({
      run_dir: makeRunDir(), remote_dir: 'batch1', machine_id: 'node1',
      worker: 'spectre/spectre_sweep_worker.py',
      remote_scripts_dir: '/tmp/elsewhere', sync_scripts: true,
    }, {}), /remote_scripts_dir must be under the remote workspace/)

    const empty = makeCtx({ mirror: env.mirror })
    empty.get = () => ({ list: () => [] })
    await assert.rejects(() => tool(empty, 'bsub_submit').execute({
      run_dir: makeRunDir(), remote_dir: 'batch1', machine_id: 'node1',
      worker: 'spectre/spectre_sweep_worker.py',
    }, {}), /no live session/)

    process.env.DSH_HOME = tmp('hams-empty-')
    const noMirror = makeCtx({ mirror: env.mirror })
    await assert.rejects(() => resolveTarget(noMirror, {}, 'node1'), /no local mirror/)
  } finally {
    restoreEnv()
  }
})

test('bsub_submit enforces the template.scs upload convention', async () => {
  const env = setupMirror()
  try {
    const run = makeRunDir({
      template: 'template_thorough.scs',
      config: { netlist_template: 'template_thorough.scs', parameters: { SW: [0, 1] } },
    })
    const ctx = makeCtx({ mirror: env.mirror })
    await assert.rejects(() => tool(ctx, 'bsub_submit').execute({
      run_dir: run, remote_dir: 'batch1', machine_id: 'node1',
      worker: 'spectre/spectre_sweep_worker.py',
    }, {}), /netlist_template is "template_thorough.scs"/)

    const run2 = makeRunDir({ template: 'template_thorough.scs' })
    await tool(ctx, 'bsub_submit').execute({
      run_dir: run2, remote_dir: 'batch2', machine_id: 'node1',
      worker: 'spectre/spectre_sweep_worker.py',
    }, {})
    const uploads = ctx.calls.filter((c) => c.name === 'rw_upload')
      .map((c) => c.arguments.path)
    assert.ok(uploads.includes('/home/op/sim/batch2/template.scs'), uploads.join('\n'))
  } finally {
    restoreEnv()
  }
})

test('bsub_submit surfaces upload failures and refuses to clobber a batch', async () => {
  const env = setupMirror()
  try {
    const ctx = makeCtx({ mirror: env.mirror, failUpload: true })
    await assert.rejects(() => tool(ctx, 'bsub_submit').execute({
      run_dir: makeRunDir(), remote_dir: 'batch1', machine_id: 'node1',
      worker: 'spectre/spectre_sweep_worker.py',
    }, {}), /upload failed/)

    const clobberCtx = makeCtx({ mirror: env.mirror, existing: 3 })
    await assert.rejects(() => tool(clobberCtx, 'bsub_submit').execute({
      run_dir: makeRunDir(), remote_dir: 'batch1', machine_id: 'node1',
      worker: 'spectre/spectre_sweep_worker.py',
    }, {}), /already holds 3 point_meta\.json/)
  } finally {
    restoreEnv()
  }
})
