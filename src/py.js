// Python pipeline channel: every tool executes a plain script shipped under
// scripts/ in a fresh `python` process (no build step, system Python only).
import { execFile } from 'node:child_process'
import { promisify } from 'node:util'
import { fileURLToPath } from 'node:url'
import fs from 'node:fs'
import path from 'node:path'
import { effectiveSessionId } from './subagent.js'

const execFileAsync = promisify(execFile)

export const PKG_ROOT = path.join(path.dirname(fileURLToPath(import.meta.url)), '..')
export const SCRIPTS_DIR = path.join(PKG_ROOT, 'scripts')

export function scriptPath(relPath) {
  const abs = path.resolve(SCRIPTS_DIR, relPath)
  if (!abs.startsWith(path.resolve(SCRIPTS_DIR) + path.sep)) {
    throw new Error(`script path escapes scripts/: ${relPath}`)
  }
  return abs
}

export async function runPython(scriptRel, args, timeoutMs = 900_000, options = {}) {
  const abs = scriptPath(scriptRel)
  if (!fs.existsSync(abs)) throw new Error(`pipeline script missing: ${abs}`)
  // options.env MERGES into the child env (callers thread per-session facts
  // like DSH_SESSION_ID through; a full env replacement would drop PATH etc.).
  const { env: extraEnv, ...rest } = options
  const opts = {
    timeout: timeoutMs,
    maxBuffer: 32 * 1024 * 1024,
    windowsHide: true,
    encoding: 'utf8',
    // Python < 3.15 encodes piped stdout by locale (cp936 on zh-CN Windows)
    // while we decode utf-8 — force UTF-8 mode so non-ASCII paths in output
    // (e.g. "[snp] wrote <path>") survive the round trip.
    env: { ...process.env, PYTHONUTF8: '1', ...extraEnv },
    ...rest,
  }
  try {
    return await execFileAsync('python', [abs, ...args], opts)
  } catch (e) {
    // 'python' with no python3 alias (bare Linux) used to fail the whole
    // chain on ENOENT; retry once under python3 before surfacing the error
    if (e?.code === 'ENOENT') {
      return execFileAsync('python3', [abs, ...args], opts)
    }
    throw e
  }
}

export function cliError(e) {
  const detail = [e?.stdout, e?.stderr].filter(Boolean).join('\n').slice(-2000)
  return detail || String(e?.message ?? e)
}

export function readJsonIfExists(file) {
  try {
    return JSON.parse(fs.readFileSync(file, 'utf8'))
  } catch {
    return null
  }
}

// Per-session env overlay for Python workers (HarnessAMS D-005): the calling
// session's id rides DSH_SESSION_ID and common_workspace.read_check_params
// resolves that session's confirmed check table with it. A delegated subagent
// anchors on its topmost user-visible ancestor (src/subagent.js), so the
// worker reads the user session's confirmed table. Absent identity (direct
// server-side calls, tests) returns an empty overlay — the workers then
// resolve the project-level fallback chain.
export function sessionEnv(exec, ctx) {
  const sid = effectiveSessionId(ctx, exec?.agent)
  return sid ? { DSH_SESSION_ID: sid } : {}
}

export function resolveDir(dir) {
  if (!dir || typeof dir !== 'string') throw new Error('a directory path is required')
  const abs = path.resolve(dir)
  if (!fs.existsSync(abs)) throw new Error(`directory does not exist: ${abs}`)
  return abs
}
