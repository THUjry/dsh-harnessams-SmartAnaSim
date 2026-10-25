// HarnessAMS-SmartAnaSim plugin entry (plain-JS publish entry, no build chain; dev-mount loads this file directly).
// Architecture: the plugin is only a "channel" — tool executes spawn plain Python scripts shipped
// with the package (scripts/sweep_worker_ngspice.py etc.); assembly/check/adjudication logic lives
// entirely in the script layer (intermediate steps are not codified).
// Tool surface: assemble / run / collect / progress / report / trash; script contracts are in each script's docstring.
import { execFile } from 'node:child_process'
import { fileURLToPath } from 'node:url'
import path from 'node:path'
import { promisify } from 'node:util'

const execFileAsync = promisify(execFile)
// scripts/ lives at the package root, one level above src/
const SCRIPTS_DIR = path.join(path.dirname(fileURLToPath(import.meta.url)), '..', 'scripts')

async function loadDefineTool() {
  // defineTool is only a typing/defaults wrapper; fall back to identity when unresolvable.
  try {
    const mod = await import('@deepseek-ai/dsh-tools')
    return mod.defineTool ?? ((t) => t)
  } catch {
    return (t) => t
  }
}

function py(scriptName, args, timeoutMs) {
  return execFileAsync('python', [path.join(SCRIPTS_DIR, scriptName), ...args], {
    timeout: timeoutMs ?? 900_000,
    maxBuffer: 32 * 1024 * 1024,
    windowsHide: true,
    encoding: 'utf8',
  })
}

function cliError(e) {
  const detail = [e.stdout, e.stderr].filter(Boolean).join('\n').slice(-2000)
  return detail || String(e.message || e)
}

const TOOLS = [
  {
    name: 'sim_assemble',
    description: 'Assemble a sweep batch: invokes the demo project\'s gen_*.py script (design_params.json -> template.spi + sweep_config.json + cell_meta.json). The three checks (dimensions / bias<Vdd / netlist format) reject at the assembly layer; a non-conforming batch generates no netlist.',
    parameters: {
      project_dir: { type: 'string', required: true, description: 'Demo project root containing script/analysis/gen_*.py' },
      gen_script: { type: 'string', required: false, description: 'Assembly script filename (default: auto-discover the unique gen_*.py under script/analysis)' },
      out_dir: { type: 'string', required: false, description: 'Output directory (default: the script\'s own default, e.g. sweep/Round1/top)' },
    },
    async execute(args) {
      const fs = await import('node:fs')
      const proj = path.resolve(args.project_dir)
      const analysisDir = path.join(proj, 'script', 'analysis')
      let gen = args.gen_script
      if (!gen) {
        const cands = fs.readdirSync(analysisDir).filter((f) => /^gen_.*\.py$/.test(f))
        if (cands.length !== 1) {
          return { ok: false, error: `gen_script required (found ${cands.length} gen_*.py under ${analysisDir}: ${cands.join(', ')})` }
        }
        gen = cands[0]
      }
      const cli = [path.join(analysisDir, gen)]
      if (args.out_dir) cli.push('--out', args.out_dir)
      const { stdout } = await execFileAsync('python', cli, { timeout: 120_000, windowsHide: true, encoding: 'utf8', cwd: proj })
        .catch((e) => { throw new Error(`assembly failed: ${cliError(e)}`) })
      return { ok: true, output: stdout.trim(), project_dir: proj }
    },
  },
  {
    name: 'sim_run_sweep',
    description: 'Run a local ngspice batch simulation: for a run directory holding template.spi + sweep_config.json, generate per-point netlists and simulate them (@@PARAM@@ substitution; per-point point_meta.json; bestPoints.json persisted as found; sweep_progress.json progress). Waits synchronously for completion.',
    parameters: {
      run_dir: { type: 'string', required: true, description: 'Run directory (holds template.spi and sweep_config.json)' },
      max_parallel: { type: 'number', required: false, description: 'Concurrency (default: config max_parallel)' },
      timeout_sec: { type: 'number', required: false, description: 'Overall timeout in seconds (default 900)' },
    },
    async execute(args) {
      const argv = ['--work-dir', args.run_dir]
      if (args.max_parallel) argv.push('--max-parallel', String(args.max_parallel))
      const { stdout } = await py('sweep_worker_ngspice.py', argv, (args.timeout_sec ?? 900) * 1000)
        .catch((e) => { throw new Error(`sweep failed: ${cliError(e)}`) })
      return { ok: true, output: stdout.trim(), run_dir: path.resolve(args.run_dir) }
    },
  },
  {
    name: 'sim_collect',
    description: 'Collect simulation results: gathers every point_meta.json in a run directory into results.json (per-point metrics + min/max/mean summary + bestPoints).',
    parameters: {
      run_dir: { type: 'string', required: true, description: 'Run directory' },
    },
    async execute(args) {
      const { stdout } = await py('collect_results.py', ['--work-dir', args.run_dir])
        .catch((e) => { throw new Error(`collect failed: ${cliError(e)}`) })
      const fs = await import('node:fs')
      const resultsPath = path.join(path.resolve(args.run_dir), 'results.json')
      let summary = null
      try {
        summary = JSON.parse(fs.readFileSync(resultsPath, 'utf8')).summary
      } catch { /* the script already printed */ }
      return { ok: true, output: stdout.trim(), results_file: resultsPath, summary }
    },
  },
  {
    name: 'sim_progress',
    description: 'Check batch simulation progress (reads sweep_progress.json in the run directory; does not simulate).',
    parameters: {
      run_dir: { type: 'string', required: true, description: 'Run directory' },
    },
    async execute(args) {
      const fs = await import('node:fs')
      const p = path.join(path.resolve(args.run_dir), 'sweep_progress.json')
      if (!fs.existsSync(p)) return { ok: false, error: `no progress file yet: ${p}` }
      const prog = JSON.parse(fs.readFileSync(p, 'utf8'))
      return { ok: true, status: prog.status, total: prog.total_points, completed: prog.completed,
               succeeded: prog.succeeded, failed: prog.failed, updated_at: prog.updated_at }
    },
  },
  {
    name: 'sim_report',
    description: 'Generate the sweep-result HTML report (interactive Plotly charts + summary tables; works both via file:// double-click and embedded in the WebUI). Produces <project>/Analysis/<name>_report.html.',
    parameters: {
      project_dir: { type: 'string', required: true, description: 'Demo project root' },
      round: { type: 'string', required: false, description: 'Round name (e.g. Round1; default: all Round*)' },
    },
    async execute(args) {
      const argv = ['--project', args.project_dir]
      if (args.round) argv.push('--round', args.round)
      const { stdout } = await py('gen_sweep_report.py', argv, 300_000)
        .catch((e) => { throw new Error(`report generation failed: ${cliError(e)}`) })
      return { ok: true, output: stdout.trim() }
    },
  },
  {
    name: 'sim_trash',
    description: 'Trash: move workspace paths into .trash/ (deletion never destroys by default; safety-by-default convention). Supports list/restore/purge.',
    parameters: {
      action: { type: 'string', required: true, description: 'trash | list | restore | purge' },
      path: { type: 'string', required: false, description: 'trash: path to move; restore/purge: trash entry name' },
      dest: { type: 'string', required: false, description: 'restore destination directory' },
    },
    async execute(args) {
      let argv = []
      if (args.action === 'list') argv = ['--list']
      else if (args.action === 'restore') argv = ['--restore', String(args.path), String(args.dest ?? '.')]
      else if (args.action === 'purge') argv = ['--purge', String(args.path)]
      else if (args.action === 'trash') argv = [String(args.path)]
      else return { ok: false, error: `unknown action: ${args.action}` }
      const { stdout } = await py('trash.py', argv).catch((e) => { throw new Error(`trash operation failed: ${cliError(e)}`) })
      return { ok: true, output: stdout.trim() }
    },
  },
]

export const name = 'dsh-harnessams-smart-ana-sim'
export const inject = ['tools']

export function apply(ctx) {
  ctx.effect(() => {
    void (async () => {
      const defineTool = await loadDefineTool()
      for (const t of TOOLS) {
        const tool = defineTool({
          name: t.name,
          description: t.description,
          parameters: t.parameters,
          output: {
            schema: { type: 'object', properties: { ok: { type: 'boolean' } } },
            render: (_args, value) => [{ type: 'text', text: typeof value === 'string' ? value : JSON.stringify(value, null, 2) }],
          },
          execute: t.execute,
        })
        ctx.tools.register(tool)
      }
      if (ctx.logger?.info) ctx.logger.info('[smart-ana-sim] registered %d tools', TOOLS.length)
      else console.log('[smart-ana-sim] registered', TOOLS.length, 'tools')
    })()
  }, 'smart-ana-sim tools')
}
