// Generic LSF (bsub) batch channel for remote runs.
//
// This layer knows nothing about a simulator: it uploads a run directory plus the
// plugin's worker scripts, submits a worker script through bsub, polls the LSF
// job and the batch's progress files, and fetches the produced files back with
// tar. A simulator-specific flow is only a choice of worker + worker_args (for
// example spectre/spectre_sweep_worker.py --config sweep_config.json ...).
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { execFile } from 'node:child_process'
import { promisify } from 'node:util'
import { SCRIPTS_DIR } from './py.js'
import { shq } from './remote.js'

const execFileAsync = promisify(execFile)

export const DEFAULT_INCLUDES = ['point_meta.json', 'sweep_progress.json',
  'errlog.txt', 'bsub.out', 'bsub.err']

// Every Python script shipped under scripts/ (relative POSIX paths). Uploaded as
// a unit so any worker and its shared modules resolve on the remote host.
export function pluginScripts() {
  const out = []
  const walk = (dir, prefix) => {
    for (const ent of fs.readdirSync(dir, { withFileTypes: true })) {
      if (ent.name === '__pycache__') continue
      const rel = prefix ? prefix + '/' + ent.name : ent.name
      const abs = path.join(dir, ent.name)
      if (ent.isDirectory()) walk(abs, rel)
      else if (ent.name.endsWith('.py')) out.push({ rel, local: abs })
    }
  }
  walk(SCRIPTS_DIR, '')
  return out.sort((a, b) => a.rel.localeCompare(b.rel))
}

export function remoteJoin(...parts) {
  return path.posix.join(...parts)
}

export function normalizeRemoteRoot(root) {
  const r = path.posix.normalize(String(root ?? '').replace(/\\/g, '/'))
  if (!r || r === '.' || !r.startsWith('/')) throw new Error(`remotePath must be absolute: ${root}`)
  return r.replace(/\/+$/, '') || '/'
}

export function resolveRemoteDir(remoteRoot, remoteDir) {
  const root = normalizeRemoteRoot(remoteRoot)
  if (!remoteDir || typeof remoteDir !== 'string') throw new Error('remote_dir is required')
  const raw = remoteDir.replace(/\\/g, '/')
  const candidate = raw.startsWith('/') ? path.posix.normalize(raw) : path.posix.join(root, raw)
  if (candidate !== root && !candidate.startsWith(root + '/')) {
    throw new Error(`remote_dir escapes the remote workspace (${root}): ${remoteDir}`)
  }
  return candidate
}

// Generic workspace config lookup (harnessams.json bare or inside
// harnessams-workspace/), used only for optional remote defaults.
export function readWorkspaceConfig() {
  const roots = [process.cwd(), SCRIPTS_DIR]
  for (const start of roots) {
    let d = path.resolve(start)
    for (let i = 0; i < 8; i++) {
      for (const dir of (d, path.join(d, 'harnessams-workspace'))) {
        const file = path.join(dir, 'harnessams.json')
        if (fs.existsSync(file)) {
          try {
            return { config: JSON.parse(fs.readFileSync(file, 'utf8')), dir }
          } catch { /* unreadable config: keep walking */ }
        }
      }
      const parent = path.dirname(d)
      if (parent === d) break
      d = parent
    }
  }
  return { config: {}, dir: null }
}

export function defaultRemotePython() {
  return readWorkspaceConfig().config.remote_python || 'python3'
}

export function defaultRemoteScriptsDir(remoteRoot) {
  const configured = readWorkspaceConfig().config.remote_scripts_dir
  return configured || remoteJoin(remoteRoot, '.harnessams', 'scripts')
}

export function sanitizeRel(rel) {
  const normalized = path.posix.normalize(String(rel ?? '').replace(/\\/g, '/'))
  if (!normalized || normalized === '.' || normalized.startsWith('/')
      || normalized === '..' || normalized.startsWith('../')) {
    throw new Error(`unsafe relative path: ${rel}`)
  }
  return normalized
}

export function collectRunFiles(runDir, files) {
  if (Array.isArray(files) && files.length) {
    return files.map((rel) => {
      const safe = sanitizeRel(rel)
      const local = path.resolve(runDir, safe)
      if (!local.startsWith(path.resolve(runDir) + path.sep)) {
        throw new Error(`file escapes run_dir: ${rel}`)
      }
      if (!fs.existsSync(local) || !fs.statSync(local).isFile()) {
        throw new Error(`run file not found: ${rel}`)
      }
      return { local, name: safe }
    })
  }
  return fs.readdirSync(runDir, { withFileTypes: true })
    .filter((e) => e.isFile() && !e.name.startsWith('.') && !/^fetch_.*\.tgz$/.test(e.name))
    .map((e) => ({ local: path.join(runDir, e.name), name: e.name }))
    .sort((a, b) => a.name.localeCompare(b.name))
}

export function templateUploadPlan(runDir, files, config) {
  const templates = files.filter((f) => /^template[._]/.test(path.posix.basename(f.name)))
  if (templates.length > 1) {
    throw new Error(`multiple template files in ${runDir}: ${templates.map((f) => f.name).join(', ')}`)
  }
  const declared = config?.netlist_template
  if (templates.length) {
    const base = path.posix.basename(templates[0].name)
    if (base !== 'template.scs') templates[0].uploadName = 'template.scs'
  }
  if (declared && declared !== 'template.scs') {
    throw new Error(`sweep_config.netlist_template is ${JSON.stringify(declared)}; the remote batch `
      + 'convention always uploads the template as template.scs — set netlist_template to "template.scs"')
  }
  if (!files.some((f) => f.name === 'sweep_config.json')) {
    throw new Error('sweep_config.json is required in run_dir (the worker reads it remotely)')
  }
  return files
}

export function buildSubmitCommand({ remoteDir, queue, jobName, python, workerPath, workerArgs }) {
  const args = (workerArgs || []).map(String)
  if (!args.includes('--work-dir')) args.push('--work-dir', remoteDir)
  const parts = ['cd', shq(remoteDir), '&&', 'bsub',
    '-q', shq(queue || 'normal'),
    '-J', shq(jobName || 'harnessams'),
    '-o', 'bsub.out', '-e', 'bsub.err',
    shq(python), shq(workerPath), ...args.map(shq)]
  return parts.join(' ')
}

export function parseJobId(text) {
  const m = String(text ?? '').match(/Job\s*<(\d+)>\s+is\s+submitted/i)
  return m ? m[1] : null
}

export function postSubmitCommand(remoteDir, jobId) {
  return `cd ${shq(remoteDir)} && sleep 1; bjobs -noheader -o 'jobid stat' ${shq(jobId)} 2>&1; `
    + 'test -f errlog.txt && echo __ERRLOG__; test -f .worker_started && echo __STARTED__'
}

export function pollCommand({ remoteDir, jobId, scheduler }) {
  const bjob = scheduler === 'none'
    ? 'echo __BJOB__; echo "(scheduler=none)"'
    : `echo __BJOB__; bjobs -noheader -o 'jobid stat exec_host' ${shq(jobId)} 2>&1`
  return `cd ${shq(remoteDir)} || exit 9; ${bjob}; `
    + `echo __META__; ls -1 ${shq(normalizeRemoteRoot(remoteDir))}/*/point_meta.json 2>/dev/null | wc -l; `
    + `echo __DIRS__; ls -1d ${shq(normalizeRemoteRoot(remoteDir))}/*/ 2>/dev/null | wc -l; `
    + 'echo __PROG__; cat sweep_progress.json 2>/dev/null; '
    + 'echo __ERR__; [ -f errlog.txt ] && tail -n 5 errlog.txt || true'
}

function section(text, name) {
  const m = text.match(new RegExp(`__${name}__\\n?([\\s\\S]*?)(?=\\n__[A-Z]+__|$)`))
  return m ? m[1].trim() : ''
}

export function parsePollOutput(text, { total } = {}) {
  const bjob = section(text, 'BJOB')
  const meta = Number(section(text, 'META')) || 0
  const dirs = Number(section(text, 'DIRS')) || 0
  const errlog = section(text, 'ERR')
  let progress = null
  const progressText = section(text, 'PROG')
  if (progressText) {
    try { progress = JSON.parse(progressText) } catch { progress = null }
  }
  const jobState = /(^|\n)\s*\d+\s+(PEND|PSUSP)/.test(bjob) ? 'queued'
    : /(^|\n)\s*\d+\s+RUN/.test(bjob) ? 'running'
      : /(^|\n)\s*\d+\s+(DONE)/.test(bjob) ? 'done'
        : /(^|\n)\s*\d+\s+(EXIT|ZOMBI)/.test(bjob) ? 'failed'
          : /No (unfinished )?job found/i.test(bjob) || !bjob ? '' : 'unknown'
  const progressTotal = Number(progress?.total_points) || Number(total) || 0
  const completed = meta
  let state = 'unknown'
  if (progressTotal && completed >= progressTotal) state = 'completed'
  else if (progress?.status === 'completed') state = 'completed'
  else if (progress?.status === 'failed' || jobState === 'failed') state = 'failed'
  else if (jobState === 'queued') state = 'queued'
  else if (jobState === 'running' || (completed > 0 && progressTotal && completed < progressTotal)) state = 'running'
  else if (jobState === 'done' && progressTotal && completed < progressTotal) state = 'partial'
  else if (errlog) state = 'failed'
  return {
    state,
    job_state: jobState || 'unknown',
    completed,
    dirs,
    total: progressTotal || null,
    succeeded: Number(progress?.succeeded) || 0,
    failed: Number(progress?.failed) || 0,
    progress_status: progress?.status ?? null,
    compute_node: progress?.compute_node ?? null,
    errlog_tail: errlog || null,
  }
}

function findConditions(includes) {
  const conds = []
  for (const raw of includes) {
    const inc = String(raw).replace(/\\/g, '/').replace(/^\.\//, '')
    if (!inc) continue
    if (inc.includes('/')) conds.push(`-path ${shq('./' + inc)}`)
    else conds.push(`-name ${shq(inc)}`)
  }
  return conds
}

export function fetchCommand({ remoteDir, includes }) {
  const conds = findConditions(includes)
  const expr = conds.length === 1 ? conds[0] : `\\( ${conds.join(' -o ')} \\)`
  return `cd ${shq(remoteDir)} && find . -maxdepth 5 ${expr} -print | `
    + 'tar czf .bsub_fetch.tgz -T -; wc -c < .bsub_fetch.tgz'
}

export function listCommand({ remoteDir, includes }) {
  const conds = findConditions(includes)
  const expr = conds.length === 1 ? conds[0] : `\\( ${conds.join(' -o ')} \\)`
  return `cd ${shq(remoteDir)} && find . -maxdepth 5 ${expr} -print | sort | head -200`
}

export function parseRemoteList(text) {
  return String(text ?? '').split(/\r?\n/)
    .map((s) => s.trim())
    .filter((s) => s.startsWith('./'))
    .map((s) => s.slice(2))
    .filter((s) => s && !s.startsWith('../') && !path.posix.isAbsolute(s))
}

export async function extractArchive(archive, runDir) {
  await execFileAsync('tar', ['-xzf', archive, '-C', runDir], {
    timeout: 180_000, windowsHide: true, encoding: 'utf8',
  })
}

export function countFetched(runDir) {
  let points = 0
  let spFiles = 0
  for (const ent of fs.readdirSync(runDir, { withFileTypes: true })) {
    if (!ent.isDirectory() || !/^\d{2,}$/.test(ent.name)) continue
    const pdir = path.join(runDir, ent.name)
    if (fs.existsSync(path.join(pdir, 'point_meta.json'))) points += 1
    if (fs.existsSync(path.join(pdir, 'netlist.scs.raw', 'sp.sp'))) spFiles += 1
  }
  return { points, sp_files: spFiles }
}

export function jobFile(runDir) {
  return path.join(runDir, '.bsub_job.json')
}

export function readJob(runDir) {
  try {
    return JSON.parse(fs.readFileSync(jobFile(runDir), 'utf8'))
  } catch {
    return null
  }
}

export function writeJob(runDir, job) {
  fs.writeFileSync(jobFile(runDir), JSON.stringify(job, null, 2) + '\n', 'utf8')
}

export function manifestSummary() {
  return pluginScripts().map((s) => s.rel)
}

export function platformNote() {
  return os.platform() === 'win32'
    ? 'archive extraction uses the Windows tar (bsdtar); a missing/incompatible tar falls back to per-file downloads'
    : 'archive extraction uses tar'
}
