// Thin dsh-remote shell used by the generic bsub tools.
//
// Coupling contract (dsh-remote 0.8.66, verified against the installed package and
// the project's upgrade checklist):
//   - only rw_machines / rw_exec / rw_upload / rw_download are consumed;
//   - ctx.tools.execute REQUIRES callId + AbortSignal;
//   - those tools bind to a machine from the calling session's cwd inside a
//     dsh-remote mirror, so a real live session must be passed as the agent
//     context (a fabricated session does not survive the tool pipeline);
//   - rw_machines answers with a TEXT listing (workspace rides on the auth line);
//   - rw_exec appends result markers to the END of the text (see parseRwExecText);
//   - rw_upload / rw_download move a single file each and enforce the remote
//     sandbox write fence (a workspace-write session may only write under the
//     mirror's remotePath).
//
// Mirror discovery (findMirrorForMachine / findEnclosingMirror) reproduces
// dsh-remote's `$DSH_HOME/remote-workspaces` layout by convention; it does not
// import dsh-remote internals (separate npm package).
import crypto from 'node:crypto'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'

export function resultText(r) {
  if (r == null) return ''
  if (typeof r === 'string') return r
  if (typeof r.value === 'string') return r.value
  if (r.value && typeof r.value === 'object' && typeof r.value.text === 'string') return r.value.text
  if (Array.isArray(r.content)) return r.content.map((b) => (b && typeof b.text === 'string' ? b.text : '')).join('\n')
  return ''
}

// Parse the rw_machines text listing (dsh-remote 0.8.54+): each entry is a header
// line plus an auth line carrying `workspace=<path>`; group labels are extra lines
// that match no entry, and the listing ends with a rw_connect hint line.
export function parseRwMachinesText(text) {
  const machines = []
  const lines = String(text ?? '').split(/\r?\n/)
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i]
    if (!line.startsWith('- ')) continue
    const m = line.match(/^-\s+(\S+)\s+"([^"]*)"\s+\[([^\]]*)\]\s*([^@\s]*)@(\S+):(\d+)\s*(.*)$/)
    if (!m) continue
    const [, id, name, , username, host, port, tail] = m
    const ws = (lines[i + 1] ?? '').match(/workspace=(\S+)/) ?? String(tail).match(/workspace=(\S+)/)
    machines.push({
      id,
      name: name || host,
      host,
      port: Number(port) || 22,
      username: username || '',
      current: /\[CURRENT\]/.test(tail),
      workspace: ws ? ws[1] : undefined,
    })
  }
  return machines
}

export function remoteWorkspacesRoot() {
  return path.join(process.env.DSH_HOME ?? path.join(os.homedir(), '.dsh'), 'remote-workspaces')
}

function readMirrorMeta(dir) {
  try {
    const meta = JSON.parse(fs.readFileSync(path.join(dir, '.dsh-remote-meta.json'), 'utf8'))
    if (!meta?.host) return null
    return {
      mirrorDir: dir,
      host: String(meta.host),
      port: Number(meta.port) || 22,
      username: String(meta.username ?? ''),
      remotePath: String(meta.remotePath ?? ''),
    }
  } catch {
    return null
  }
}

export function findMirrorForMachine(machine, root = remoteWorkspacesRoot()) {
  if (!machine?.host) return null
  const port = Number(machine.port) || 22
  const user = String(machine.username ?? '').toLowerCase()
  let hostDirs = []
  try { hostDirs = fs.readdirSync(root, { withFileTypes: true }) } catch { return null }
  for (const hd of hostDirs) {
    if (!hd.isDirectory()) continue
    let entries = []
    try { entries = fs.readdirSync(path.join(root, hd.name), { withFileTypes: true }) } catch { continue }
    for (const e of entries) {
      if (!e.isDirectory()) continue
      const meta = readMirrorMeta(path.join(root, hd.name, e.name))
      if (!meta) continue
      if (meta.host.toLowerCase() !== String(machine.host).toLowerCase()) continue
      if (meta.port !== port) continue
      if (user && meta.username && meta.username.toLowerCase() !== user) continue
      return meta
    }
  }
  return null
}

// A DSH workspace inside a dsh-remote mirror IS a remote project: walk up from a
// session cwd to the nearest .dsh-remote-meta.json.
export function findEnclosingMirror(startDir) {
  if (!startDir) return null
  let d = path.resolve(String(startDir))
  for (let i = 0; i < 32; i++) {
    const meta = readMirrorMeta(d)
    if (meta) return meta
    const parent = path.dirname(d)
    if (parent === d) break
    d = parent
  }
  return null
}

// Borrow a live session whose cwd sits inside the mirror (the agent context that
// makes rw_exec / rw_upload / rw_download bind to that machine).
export function sessionInMirror(ctx, mirrorDir) {
  if (!mirrorDir) return null
  const sessions = typeof ctx?.get === 'function' ? ctx.get('sessions') : null
  if (!sessions || typeof sessions.list !== 'function') return null
  let live = []
  try { live = sessions.list() } catch { return null }
  const norm = (p) => String(p ?? '').replace(/\\/g, '/').replace(/\/+$/, '').toLowerCase()
  const root = norm(mirrorDir)
  for (const s of live) {
    const cwd = norm(s?.header?.cwd)
    // The session id rides along as `id`: the tool pipeline and dsh-remote's
    // rw_exec owner both read exec.agent.id, so a nested { agent: { session } }
    // is rejected before the command ever runs.
    if (cwd && (cwd === root || cwd.startsWith(root + '/'))) return { id: s.id, session: s }
  }
  return null
}

export function callerSession(execCtx) {
  const session = execCtx?.agent?.session
  return session && typeof session?.header?.cwd === 'string' ? session : null
}

export async function listMachines(ctx, timeoutMs = 10_000) {
  const def = ctx?.tools?.get?.('rw_machines')
  if (!def) return { ok: false, machines: [], reason: 'rw_machines is not registered (dsh-remote not installed?)' }
  const r = await callTool(ctx, 'rw_machines', {}, { timeoutMs })
  if (r.isError) return { ok: false, machines: [], reason: r.text || 'rw_machines failed' }
  const machines = parseRwMachinesText(r.text)
  if (!machines.length) return { ok: false, machines: [], reason: 'rw_machines listing empty or not parseable' }
  return { ok: true, machines }
}

export function shq(s) {
  return '"' + String(s ?? '').replace(/([$"\\`])/g, '\\$1') + '"'
}

// rw_exec result text (dsh-remote formatExecResult): the trailing markers come
// after the stderr section. 0.8.53+ may replace the whole text with a promotion
// marker when the command outlives its timeout (never a clean exit).
export function parseRwExecText(text) {
  let raw = String(text ?? '')
  const promoted = raw.match(/\[still running after \d+ms; moved to background job ([^\]]+)\]/)
  if (promoted) {
    return { stdout: '', stderr: '', exitCode: 0, timedOut: false, promotedJob: promoted[1] }
  }
  const timedOut = /\[command timed out after \d+ms\]/.test(raw)
  raw = raw.replace(/\[command timed out after \d+ms\]\s*$/, '').replace(/\[cancelled[^\]]*\]\s*$/, '')
  let exitCode = 0
  const m = raw.match(/\[exit code: (-?\d+)\]\s*$/)
  if (m) { exitCode = Number(m[1]); raw = raw.slice(0, m.index) }
  else if (/\[exit code:/.test(raw)) exitCode = 1
  let stdout = raw
  let stderr = ''
  const at = stdout.indexOf('-- stderr --')
  if (at >= 0) {
    stderr = stdout.slice(at + '-- stderr --'.length).trim()
    stdout = stdout.slice(0, at)
  }
  return { stdout: stdout.trimEnd(), stderr, exitCode, timedOut, promotedJob: '' }
}

export async function callTool(ctx, name, args, { agent, timeoutMs = 40_000 } = {}) {
  const def = ctx?.tools?.get?.(name)
  if (!def) return { isError: true, text: `${name} is not registered (install/enable dsh-remote)` }
  try {
    const r = await ctx.tools.execute({
      callId: 'hams-sim-' + crypto.randomUUID(),
      name,
      arguments: args,
      signal: AbortSignal.timeout(timeoutMs),
      ...(agent ? { agent } : {}),
    })
    return { isError: !!r?.isError, text: resultText(r) }
  } catch (e) {
    return { isError: true, text: String(e?.message ?? e) }
  }
}

export async function execRemote(ctx, agent, command, { timeoutMs = 40_000 } = {}) {
  // promoteOnTimeout=false keeps every command on the borrowed session's plain
  // foreground exec path. The promotion path needs a job controller FOR THAT
  // AGENT, which a borrowed session (a subagent, or an idle remote project
  // session) may not have — and these batch commands are bounded anyway.
  const r = await callTool(ctx, 'rw_exec',
    { command, timeoutMs, promoteOnTimeout: false },
    { agent, timeoutMs: timeoutMs + 30_000 })
  if (r.isError) return { ok: false, isError: true, text: r.text, stdout: '', stderr: r.text,
    exitCode: -1, timedOut: false, promotedJob: '' }
  const parsed = parseRwExecText(r.text)
  return {
    ok: parsed.exitCode === 0 && !parsed.timedOut && !parsed.promotedJob,
    isError: false,
    text: r.text,
    ...parsed,
  }
}

export async function uploadFile(ctx, agent, localPath, remotePath) {
  const r = await callTool(ctx, 'rw_upload', { localPath, path: remotePath }, { agent, timeoutMs: 120_000 })
  return { ok: !r.isError && /\buploaded\b/i.test(r.text), text: r.text }
}

export async function downloadFile(ctx, agent, remotePath, localPath) {
  const r = await callTool(ctx, 'rw_download', { path: remotePath, localPath }, { agent, timeoutMs: 180_000 })
  return { ok: !r.isError && /\bdownloaded\b/i.test(r.text), text: r.text }
}

// Resolve the machine, its local mirror, the remote workspace root, and the live
// session used as the rw_* agent context.
export async function resolveTarget(ctx, execCtx, machineId) {
  if (machineId) {
    const list = await listMachines(ctx)
    if (!list.ok) throw new Error(`dsh-remote machines unavailable: ${list.reason}`)
    const machine = list.machines.find((m) => m.id === machineId || m.name === machineId)
    if (!machine) {
      throw new Error(`no such dsh-remote machine: ${machineId} (known: `
        + `${list.machines.map((m) => m.id).join(', ')})`)
    }
    const mirror = findMirrorForMachine(machine)
    if (!mirror) {
      throw new Error(`no local mirror for machine ${machine.name}: pick its remote workspace once in `
        + 'dsh-remote (rw_pick_workspace), then retry')
    }
    if (!mirror.remotePath) throw new Error(`mirror ${mirror.mirrorDir} has no remotePath in its metadata`)
    let agent = sessionInMirror(ctx, mirror.mirrorDir)
    if (!agent) {
      const caller = callerSession(execCtx)
      if (caller) {
        const enclosing = findEnclosingMirror(caller.header.cwd)
        if (enclosing && path.resolve(enclosing.mirrorDir) === path.resolve(mirror.mirrorDir)) {
          agent = { id: caller.id, session: caller }
        }
      }
    }
    if (!agent) {
      throw new Error(`no live session in machine ${machine.name}'s remote project — open that remote `
        + 'project once in dsh-remote so a session keeps the connection, then retry')
    }
    return { machine, mirror, remoteRoot: mirror.remotePath, agent }
  }

  const caller = callerSession(execCtx)
  if (!caller) throw new Error('pass machine_id (or call this from a session inside a dsh-remote mirror)')
  const mirror = findEnclosingMirror(caller.header.cwd)
  if (!mirror) {
    throw new Error('the calling session is not inside a dsh-remote mirror — pass machine_id')
  }
  if (!mirror.remotePath) throw new Error(`mirror ${mirror.mirrorDir} has no remotePath in its metadata`)
  return {
    machine: { id: mirror.host, name: mirror.host, host: mirror.host,
      port: mirror.port, username: mirror.username },
    mirror,
    remoteRoot: mirror.remotePath,
    agent: { id: caller.id, session: caller },
  }
}
