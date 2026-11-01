// Tests for the sim_report injection target (task=<id|name>): locating the
// HarnessAMS task store above a project dir, the guarded read-modify-write of
// tasks.json, and the full tool.execute flow against a minimal sweep fixture.
// Run: bare `node --test` from the repo root.
import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdtemp, mkdir, writeFile, readFile, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { sharedTools, findTasksStore, registerReportIntoTask } from '../src/tools/shared.js'
import { makeTool } from '../src/tool.js'

const TASK_STORE = {
  version: 1,
  tasks: [
    { id: 'task-a', name: 'OTA 装配验证', description: 'demo task', created_at: '2026-10-30T00:00:00.000Z' },
    { id: 'task-b', name: 'R4 复现', description: '', created_at: '2026-10-30T00:00:00.000Z' },
  ],
}

async function makeWorkspace() {
  const ws = await mkdtemp(path.join(tmpdir(), 'hams-inject-'))
  const storeDir = path.join(ws, '.harnessams')
  await mkdir(storeDir, { recursive: true })
  await writeFile(path.join(storeDir, 'tasks.json'), JSON.stringify(TASK_STORE, null, 2) + '\n', 'utf8')
  return { ws, storeDir }
}

test('inject: findTasksStore walks up to the nearest .harnessams and returns null without one', async () => {
  const { ws } = await makeWorkspace()
  const deep = path.join(ws, 'proj', 'sub')
  await mkdir(deep, { recursive: true })
  assert.deepEqual(findTasksStore(deep), { storeDir: path.join(ws, '.harnessams') })

  // nearest ancestor wins over a farther one
  const inner = path.join(deep, '.harnessams')
  await mkdir(inner, { recursive: true })
  assert.deepEqual(findTasksStore(deep), { storeDir: inner })

  const bare = await mkdtemp(path.join(tmpdir(), 'hams-inject-bare-'))
  assert.equal(findTasksStore(bare), null)
  await rm(bare, { recursive: true, force: true })
  await rm(ws, { recursive: true, force: true })
})

test('inject: registerReportIntoTask appends, selects, dedupes by path, preserves the store', async () => {
  const { ws, storeDir } = await makeWorkspace()
  const reportPath = path.join(ws, 'demo1', 'Analysis', 'demo1_report.html')
  try {
    const r1 = registerReportIntoTask(storeDir, 'OTA 装配验证', reportPath) // by name
    assert.ok(!r1.error, r1.error)
    assert.equal(r1.task.id, 'task-a')
    assert.match(r1.report.id, /^r-[\w-]{1,8}$/)
    assert.equal(r1.report.path, path.resolve(reportPath))
    assert.equal(r1.report.name, 'demo1_report.html')

    const store1 = JSON.parse(await readFile(path.join(storeDir, 'tasks.json'), 'utf8'))
    const t1 = store1.tasks[0]
    assert.equal(t1.description, 'demo task') // untouched fields survive
    assert.equal(t1.reports.length, 1)
    assert.equal(t1.dashboard_selected, r1.report.id)

    // same path again → same entry reused, still selected
    const r2 = registerReportIntoTask(storeDir, 'task-a', reportPath) // by id
    assert.ok(!r2.error, r2.error)
    assert.equal(r2.report.id, r1.report.id)
    const store2 = JSON.parse(await readFile(path.join(storeDir, 'tasks.json'), 'utf8'))
    assert.equal(store2.tasks[0].reports.length, 1)

    // other task untouched; a second report can go to task-b
    const other = path.join(ws, 'demo2', 'Analysis', 'demo2_report.html')
    const r3 = registerReportIntoTask(storeDir, 'task-b', other)
    assert.ok(!r3.error, r3.error)
    const store3 = JSON.parse(await readFile(path.join(storeDir, 'tasks.json'), 'utf8'))
    assert.equal(store3.tasks[0].reports.length, 1)
    assert.equal(store3.tasks[1].reports.length, 1)
    assert.equal(store3.tasks[1].dashboard_selected, r3.report.id)
    assert.equal(store3.version, 1)
  } finally {
    await rm(ws, { recursive: true, force: true })
  }
})

test('inject: registerReportIntoTask refuses bad shapes and unknown tasks without writing', async () => {
  const { ws, storeDir } = await makeWorkspace()
  try {
    const before = await readFile(path.join(storeDir, 'tasks.json'), 'utf8')

    const miss = registerReportIntoTask(storeDir, 'nope', path.join(ws, 'x.html'))
    assert.match(miss.error, /task not found: "nope" \(tasks: OTA 装配验证, R4 复现\)/)

    // unwritable shape: version bumped by "someone else" → refuse, file intact
    const storePath = path.join(storeDir, 'tasks.json')
    await writeFile(storePath, JSON.stringify({ ...TASK_STORE, version: 2 }), 'utf8')
    const bad = registerReportIntoTask(storeDir, 'task-a', path.join(ws, 'x.html'))
    assert.match(bad.error, /unexpected task store shape \(version 2\)/)
    assert.equal(await readFile(storePath, 'utf8'), JSON.stringify({ ...TASK_STORE, version: 2 }))

    // broken JSON → refused, not clobbered
    await writeFile(storePath, '{oops', 'utf8')
    const broken = registerReportIntoTask(storeDir, 'task-a', path.join(ws, 'x.html'))
    assert.match(broken.error, /task store unreadable/)

    // missing file inside the store dir = empty store → guided error
    await writeFile(storePath, before, 'utf8')
    const empty = registerReportIntoTask(storeDir, 'task-a', path.join(ws, 'x.html'))
    await rm(storePath)
    const noTasks = registerReportIntoTask(storeDir, 'task-a', path.join(ws, 'x.html'))
    assert.match(noTasks.error, /no tasks yet/)

    // 32-report cap
    await writeFile(storePath, before, 'utf8')
    const filled = {
      version: 1,
      tasks: [{ ...TASK_STORE.tasks[0], reports: Array.from({ length: 32 }, (_, i) => ({ id: 'r-' + i, name: 'r' + i, path: path.join(ws, 'r' + i + '.html'), added_at: 'x' })) }],
    }
    await writeFile(storePath, JSON.stringify(filled), 'utf8')
    const cap = registerReportIntoTask(storeDir, 'task-a', path.join(ws, 'new.html'))
    assert.match(cap.error, /already has 32 reports \(max\)/)
  } finally {
    await rm(ws, { recursive: true, force: true })
  }
})

test('inject: sim_report e2e — generates the report and registers it into the task', async () => {
  const { ws, storeDir } = await makeWorkspace()
  try {
    // minimal sweep fixture: one round, two points over one grouping param
    const top = path.join(ws, 'demoX', 'sweep', 'Round1', 'top')
    await mkdir(top, { recursive: true })
    await writeFile(path.join(top, 'results.json'), JSON.stringify({
      simulator: 'ngspice',
      n_points: 2, n_success: 2, n_partial: 0, n_failed: 0,
      points: [
        { params: { vgs: 0.6 }, metrics: { gain: 60.0 } },
        { params: { vgs: 0.7 }, metrics: { gain: 65.0 } },
      ],
      summary: {},
    }), 'utf8')

    const tool = makeTool(sharedTools().find((d) => d.name === 'sim_report'))
    const res = await tool.execute({ project_dir: path.join(ws, 'demoX'), task: 'OTA 装配验证' })
    assert.equal(res.ok, true)
    assert.equal(res.registered, true)
    assert.equal(res.task.id, 'task-a')
    assert.match(res.report_path, /demoX_report\.html$/)

    const reportOnDisk = await readFile(res.report_path, 'utf8') // throws if missing
    assert.match(reportOnDisk, /<!DOCTYPE html>/)
    const store = JSON.parse(await readFile(path.join(storeDir, 'tasks.json'), 'utf8'))
    assert.equal(store.tasks[0].reports.length, 1)
    assert.equal(store.tasks[0].reports[0].path, path.resolve(res.report_path))
    assert.equal(store.tasks[0].dashboard_selected, store.tasks[0].reports[0].id)

    // no task param → plain generation, no registered field
    const plain = await tool.execute({ project_dir: path.join(ws, 'demoX') })
    assert.equal(plain.ok, true)
    assert.equal(plain.registered, undefined)
    assert.match(plain.report_path, /demoX_report\.html$/)

    // wrong task → report ok, registration refused with the task list
    const bad = await tool.execute({ project_dir: path.join(ws, 'demoX'), task: 'ghost' })
    assert.equal(bad.ok, true)
    assert.equal(bad.registered, false)
    assert.match(bad.register_error, /task not found: "ghost" \(tasks: OTA 装配验证, R4 复现\)/)
  } finally {
    await rm(ws, { recursive: true, force: true })
  }
})
