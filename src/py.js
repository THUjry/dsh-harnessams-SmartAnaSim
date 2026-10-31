// Python pipeline channel: every tool executes a plain script shipped under
// scripts/ in a fresh `python` process (no build step, system Python only).
import { execFile } from 'node:child_process'
import { promisify } from 'node:util'
import { fileURLToPath } from 'node:url'
import fs from 'node:fs'
import path from 'node:path'

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
  return execFileAsync('python', [abs, ...args], {
    timeout: timeoutMs,
    maxBuffer: 32 * 1024 * 1024,
    windowsHide: true,
    encoding: 'utf8',
    ...options,
  })
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

export function resolveDir(dir) {
  if (!dir || typeof dir !== 'string') throw new Error('a directory path is required')
  const abs = path.resolve(dir)
  if (!fs.existsSync(abs)) throw new Error(`directory does not exist: ${abs}`)
  return abs
}
