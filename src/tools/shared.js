// Shared post-processing tools (simulator-agnostic): they read the data contract
// written by either sweep worker (point_meta.json / sweep_progress.json /
// bestPoints.json) and never spawn a simulator.
import fs from 'node:fs'
import path from 'node:path'
import { randomUUID } from 'node:crypto'
import { cliError, readJsonIfExists, resolveDir, runPython } from '../py.js'

// --- Optional sim_report injection target: the HarnessAMS task dashboard ---
// The sibling dsh-harnessams plugin owns the task store at
// <workspace>/.harnessams/tasks.json (the DSH workspace the sidebar session
// runs in). Writing it from here is an accepted suite-internal coupling: the
// store shape is mirrored below, anything unexpected is refused, never
// rewritten, and every write is atomic (tmp + rename) against the sidebar's
// own writer.

const TASKS_DIR = '.harnessams'
const TASKS_FILE = 'tasks.json'
const MAX_REPORTS = 32 // same cap as the sidebar's writer (dsh-harnessams project.js)

// Walk up from startDir (max 8 levels) to the nearest ancestor holding a
// .harnessams/ task-store directory. The store dir — not harnessams.json — is
// the anchor because the DSH workspace root (where the sidebar's store lives)
// can sit ABOVE the harnessams.json directory (e.g. repo root vs its
// harnessams-workspace/). Returns { storeDir } or null.
export function findTasksStore(startDir) {
  let d = path.resolve(startDir)
  for (let i = 0; i < 8; i++) {
    const storeDir = path.join(d, TASKS_DIR)
    if (fs.existsSync(storeDir) && fs.statSync(storeDir).isDirectory()) return { storeDir }
    const parent = path.dirname(d)
    if (parent === d) break
    d = parent
  }
  return null
}

// Append (or reuse) `reportPath` in task `taskRef` (id first, then exact name)
// and select it on that task's dashboard. Refuses on any unexpected store
// shape; returns { task, report } or { error } — never throws, never clobbers.
export function registerReportIntoTask(storeDir, taskRef, reportPath) {
  const storePath = path.join(storeDir, TASKS_FILE)
  let store
  try {
    store = JSON.parse(fs.readFileSync(storePath, 'utf8'))
  } catch (e) {
    if (e.code === 'ENOENT') store = { version: 1, tasks: [] } // empty store = no tasks yet
    else return { error: `task store unreadable (${storePath}): ${e.message}` }
  }
  if ((store?.version ?? 1) !== 1 || !Array.isArray(store?.tasks)) {
    return { error: `unexpected task store shape (version ${store?.version}) — refusing to write` }
  }
  const ref = String(taskRef)
  const task = store.tasks.find((t) => t?.id === ref) ?? store.tasks.find((t) => t?.name === ref)
  if (!task) {
    const names = store.tasks.map((t) => t?.name).filter(Boolean)
    return {
      error: names.length
        ? `task not found: "${ref}" (tasks: ${names.join(', ')})`
        : 'task store has no tasks yet — create one in the HarnessAMS task sidebar first',
    }
  }
  const abs = path.resolve(reportPath)
  const reports = Array.isArray(task.reports) ? task.reports : []
  let entry = reports.find((r) => r && typeof r.path === 'string' && path.resolve(r.path) === abs)
  if (!entry) {
    if (reports.length >= MAX_REPORTS) {
      return { error: `task "${task.name}" already has ${MAX_REPORTS} reports (max)` }
    }
    entry = { id: 'r-' + randomUUID().slice(0, 8), name: path.basename(abs), path: abs, added_at: new Date().toISOString() }
    reports.push(entry)
    task.reports = reports
  }
  task.dashboard_selected = entry.id
  const tmp = storePath + '.tmp'
  try {
    fs.writeFileSync(tmp, JSON.stringify({ ...store, version: 1 }, null, 2) + '\n', 'utf8')
    fs.renameSync(tmp, storePath)
  } catch (e) {
    try { fs.rmSync(tmp, { force: true }) } catch { /* best effort */ }
    return { error: `task store write failed: ${e.message}` }
  }
  return { task: { id: task.id, name: task.name }, report: entry }
}

export function sharedTools() {
  return [
    {
      name: 'sim_collect',
      description: 'Collect a finished sweep: aggregates every NN/point_meta.json of a run directory into '
        + 'results.json (per-point metrics, min/max/mean summary, bestPoints). The simulator label comes from '
        + 'the run\'s .simulator marker (or the template extension) and drives the report header.',
      parameters: {
        run_dir: { type: 'string', required: true, description: 'Run directory (sweep/RoundN/top)' },
        out: { type: 'string', description: 'Output results.json path (default: <run_dir>/results.json)' },
        simulator: { type: 'string', enum: ['ngspice', 'spectre'], description: 'Override the detected simulator label' },
      },
      async execute(args) {
        const runDir = resolveDir(args.run_dir)
        const argv = ['--work-dir', runDir]
        if (args.out) argv.push('--out', path.resolve(args.out))
        if (args.simulator) argv.push('--simulator', String(args.simulator))
        const { stdout } = await runPython('common/common_collect_results.py', argv, 180_000)
          .catch((e) => { throw new Error(`collect failed: ${cliError(e)}`) })
        const resultsPath = args.out ? path.resolve(args.out) : path.join(runDir, 'results.json')
        const results = readJsonIfExists(resultsPath)
        return {
          ok: true,
          output: stdout.trim(),
          results_file: resultsPath,
          simulator: results?.simulator ?? null,
          summary: results?.summary ?? null,
        }
      },
    },
    {
      name: 'sim_progress',
      description: 'Read a sweep batch\'s progress file (sweep_progress.json in the run directory; no simulation '
        + 'is started). Useful for local runs and for a remote batch after bsub_fetch downloaded its progress file.',
      parameters: {
        run_dir: { type: 'string', required: true, description: 'Run directory holding sweep_progress.json' },
      },
      async execute(args) {
        const runDir = resolveDir(args.run_dir)
        const file = path.join(runDir, 'sweep_progress.json')
        if (!fs.existsSync(file)) return { ok: false, error: `no progress file yet: ${file}` }
        const prog = readJsonIfExists(file)
        if (!prog) return { ok: false, error: `unreadable progress file: ${file}` }
        return {
          ok: true,
          run_dir: runDir,
          simulator: prog.simulator ?? null,
          status: prog.status,
          total: prog.total_points,
          completed: prog.completed,
          succeeded: prog.succeeded,
          failed: prog.failed,
          updated_at: prog.updated_at,
        }
      },
    },
    {
      name: 'sim_report',
      description: 'Generate the sweep-result HTML report (interactive Plotly metric charts, summary cards, '
        + 'per-point table, best point) from sweep/Round*/top/results.json. Works from file:// and inside the '
        + 'WebUI; writes <project>/Analysis/<project>_report.html. With task=<id or exact name>, the report is '
        + 'also registered into that HarnessAMS task\'s dashboard (nearest .harnessams/tasks.json above the '
        + 'project); a registration failure never fails report generation.',
      parameters: {
        project_dir: { type: 'string', required: true, description: 'Project root containing sweep/Round*/top/results.json' },
        round: { type: 'string', description: 'Round name to render (e.g. Round1; default: all Round*)' },
        task: { type: 'string', description: 'Register the generated report into this task\'s dashboard '
          + '(HarnessAMS sidebar): task id or exact task name. Omit to only generate the report.' },
      },
      async execute(args) {
        const project = resolveDir(args.project_dir)
        const argv = ['--project', project]
        if (args.round) argv.push('--round', String(args.round))
        const { stdout } = await runPython('common/common_gen_sweep_report.py', argv, 300_000)
          .catch((e) => { throw new Error(`report generation failed: ${cliError(e)}`) })
        // The script's final stdout line is "[report] N round(s) -> <abs path>";
        // parse it, fall back to the default naming rule if the format drifts.
        const m = /\[report\] \d+ round\(s\) -> (.+)\s*$/.exec(stdout.trim())
        const reportPath = m?.[1] ?? path.join(project, 'Analysis', path.basename(project) + '_report.html')
        const result = { ok: true, output: stdout.trim(), report_dir: path.join(project, 'Analysis'), report_path: reportPath }
        if (args.task) {
          // Optional injection: the report is already on disk, so a failure here
          // is reported, never thrown — the caller can retry with a fixed target.
          const found = findTasksStore(project)
          if (!found) {
            result.registered = false
            result.register_error = `no HarnessAMS task store (.harnessams/tasks.json) found above ${project} — create a task in the HarnessAMS task sidebar first`
          } else {
            const r = registerReportIntoTask(found.storeDir, args.task, reportPath)
            if (r.error) { result.registered = false; result.register_error = r.error }
            else { result.registered = true; result.task = r.task; result.report = r.report }
          }
        }
        return result
      },
    },
    {
      name: 'sim_trash',
      description: 'Workspace trash (safety by default): move paths into <workspace>/.trash instead of deleting, '
        + 'then list, restore, or explicitly purge an entry.',
      parameters: {
        action: { type: 'string', required: true, enum: ['trash', 'list', 'restore', 'purge'],
          description: 'trash | list | restore | purge' },
        path: { type: 'string', description: 'trash: path to move; restore/purge: trash entry name' },
        dest: { type: 'string', description: 'restore destination directory' },
      },
      async execute(args) {
        let argv
        if (args.action === 'list') argv = ['--list']
        else if (args.action === 'restore') argv = ['--restore', String(args.path), String(args.dest ?? '.')]
        else if (args.action === 'purge') argv = ['--purge', String(args.path)]
        else if (args.action === 'trash') argv = [String(args.path)]
        else return { ok: false, error: `unknown action: ${args.action}` }
        const { stdout } = await runPython('common/common_trash.py', argv, 120_000)
          .catch((e) => { throw new Error(`trash operation failed: ${cliError(e)}`) })
        return { ok: true, output: stdout.trim() }
      },
    },
  ]
}
