// ngspice family: assembly bridge + local batch sweep. The simulator name is part
// of every tool name; shared post-processing lives under sim_* (shared.js).
import { runAssembly } from '../projects.js'
import { cliError, resolveDir, runPython } from '../py.js'

export function ngspiceTools() {
  return [
    {
      name: 'ngspice_assemble',
      description: 'Assemble an ngspice sweep batch: runs the project\'s script/analysis/gen_*.py '
        + '(design_params.json -> SPICE template + sweep_config.json + metadata) with the assembly-layer '
        + 'netlist/dimension/bias checks. Run this before ngspice_run_sweep.',
      parameters: {
        project_dir: { type: 'string', required: true, description: 'Project root containing script/analysis/gen_*.py' },
        gen_script: { type: 'string', description: 'Assembly script filename (default: the unique gen_*.py under script/analysis)' },
        out_dir: { type: 'string', description: 'Output directory forwarded as --out (default: the script\'s own default)' },
      },
      async execute(args) {
        return runAssembly(args.project_dir, {
          genScript: args.gen_script,
          outDir: args.out_dir,
          simulator: 'ngspice',
        })
      },
    },
    {
      name: 'ngspice_run_sweep',
      description: 'Run a local ngspice batch sweep: for a run directory holding template.spi + sweep_config.json, '
        + 'generate per-point netlists (@@PARAM@@ substitution) and simulate them. Writes NN/netlist.spi, '
        + 'ngspice.out, point_meta.json, sweep_progress.json and crash-safe bestPoints.json; waits synchronously.',
      parameters: {
        run_dir: { type: 'string', required: true, description: 'Run directory holding template.spi and sweep_config.json' },
        max_parallel: { type: 'number', description: 'Concurrency (default: config max_parallel)' },
        timeout_sec: { type: 'number', description: 'Overall tool timeout in seconds (default 900)' },
      },
      async execute(args) {
        const runDir = resolveDir(args.run_dir)
        const argv = ['--work-dir', runDir]
        if (args.max_parallel) argv.push('--max-parallel', String(args.max_parallel))
        const { stdout } = await runPython('ngspice/ngspice_sweep_worker.py', argv,
          (args.timeout_sec ?? 900) * 1000)
          .catch((e) => { throw new Error(`ngspice sweep failed: ${cliError(e)}`) })
        return { ok: true, output: stdout.trim(), run_dir: runDir, simulator: 'ngspice' }
      },
    },
  ]
}
