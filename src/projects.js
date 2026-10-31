// Assembly-layer bridge: a project supplies script/analysis/gen_*.py, which
// reads design_params.json, runs its simulator's checks, and writes the
// template netlist + sweep_config.json + metadata. The plugin only locates and
// executes that script (intermediate steps are not codified).
import fs from 'node:fs'
import path from 'node:path'
import { execFile } from 'node:child_process'
import { promisify } from 'node:util'
import { cliError } from './py.js'

const execFileAsync = promisify(execFile)

export async function runAssembly(projectDir, { genScript, outDir, simulator } = {}) {
  if (!projectDir) throw new Error('project_dir is required')
  const proj = path.resolve(projectDir)
  const analysisDir = path.join(proj, 'script', 'analysis')
  if (!fs.existsSync(analysisDir)) {
    throw new Error(`assembly script directory not found: ${analysisDir} (expected script/analysis/gen_*.py)`)
  }
  let gen = genScript
  if (!gen) {
    const candidates = fs.readdirSync(analysisDir).filter((f) => /^gen_.*\.py$/.test(f))
    if (candidates.length !== 1) {
      throw new Error(`gen_script required (found ${candidates.length} gen_*.py under ${analysisDir}: `
        + `${candidates.join(', ') || 'none'})`)
    }
    gen = candidates[0]
  }
  if (path.basename(gen) !== gen) throw new Error('gen_script must be a filename inside script/analysis')
  const script = path.join(analysisDir, gen)
  if (!fs.existsSync(script)) throw new Error(`assembly script not found: ${script}`)

  const argv = [script]
  if (outDir) argv.push('--out', String(outDir))
  const { stdout } = await execFileAsync('python', argv, {
    timeout: 120_000,
    windowsHide: true,
    encoding: 'utf8',
    cwd: proj,
    env: { ...process.env, HARNESSAMS_SIMULATOR: simulator || '' },
  }).catch((e) => { throw new Error(`assembly failed: ${cliError(e)}`) })
  return { ok: true, output: String(stdout || '').trim(), project_dir: proj, simulator }
}
