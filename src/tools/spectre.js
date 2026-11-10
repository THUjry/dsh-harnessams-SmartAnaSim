// spectre family: assembly bridge, local batch sweep, and PSF ASCII parsing.
// Everything here runs on the host that executes the plugin; it is deliberately
// independent of any remote/LSF channel (use the generic batch_* tools to submit
// spectre/spectre_sweep_worker.py to a cluster).
import { runAssembly } from '../projects.js'
import { cliError, readJsonIfExists, resolveDir, runPython } from '../py.js'
import path from 'node:path'

export function spectreTools() {
  return [
    {
      name: 'spectre_assemble',
      description: 'Assemble a Spectre sweep batch: runs the project\'s script/analysis/gen_*.py '
        + '(design_params.json -> Spectre template.scs + sweep_config.json + metadata) with the assembly-layer '
        + 'format/placeholder/unit checks. Run this before spectre_run_sweep or batch_submit.',
      parameters: {
        project_dir: { type: 'string', required: true, description: 'Project root containing script/analysis/gen_*.py' },
        gen_script: { type: 'string', description: 'Assembly script filename (default: the unique gen_*.py under script/analysis)' },
        out_dir: { type: 'string', description: 'Output directory forwarded as --out' },
      },
      async execute(args) {
        return runAssembly(args.project_dir, {
          genScript: args.gen_script,
          outDir: args.out_dir,
          simulator: 'spectre',
        })
      },
    },
    {
      name: 'spectre_run_sweep',
      description: 'Run a local Spectre batch sweep on the host executing this plugin: for a run directory holding '
        + 'template.scs + sweep_config.json, generate per-point netlists (@@PARAM@@ substitution) and run Spectre. '
        + 'Writes NN/netlist.scs, netlist.scs.raw/, spectre.out, point_meta.json and sweep_progress.json. '
        + 'Requires Spectre on this host; cluster runs use the suite\'s batch_submit/batch_poll/batch_fetch.',
      parameters: {
        run_dir: { type: 'string', required: true, description: 'Run directory holding template.scs and sweep_config.json' },
        max_parallel: { type: 'number', description: 'Concurrency (default: config max_parallel)' },
        timeout_sec: { type: 'number', description: 'Overall tool timeout in seconds (default 1200)' },
        spectre_cmd: { type: 'string', description: 'Spectre executable override (default: sweep_config/workspace/PATH)' },
      },
      async execute(args) {
        const runDir = resolveDir(args.run_dir)
        const argv = ['--work-dir', runDir]
        if (args.max_parallel) argv.push('--max-parallel', String(args.max_parallel))
        if (args.spectre_cmd) argv.push('--spectre-cmd', String(args.spectre_cmd))
        const { stdout } = await runPython('spectre/spectre_sweep_worker.py', argv,
          (args.timeout_sec ?? 1200) * 1000)
          .catch((e) => { throw new Error(`spectre sweep failed: ${cliError(e)}`) })
        return { ok: true, output: stdout.trim(), run_dir: runDir, simulator: 'spectre' }
      },
    },
    {
      name: 'spectre_parse',
      description: 'Parse Spectre psfascii output into curves: one point\'s raw directory (netlist.scs.raw) or a '
        + 'whole run directory (NN/netlist.scs.raw). Writes per-point JSON with Re/Im pairs plus a summary index '
        + '(and optional long CSV curves). S-parameter sp.sp files are read by the snp_* tools instead.',
      parameters: {
        raw_dir: { type: 'string', description: 'One point\'s raw directory (use this OR work_dir)' },
        work_dir: { type: 'string', description: 'Run directory holding NN/netlist.scs.raw (use this OR raw_dir)' },
        out_dir: { type: 'string', description: 'Output directory (default: <target>/psf)' },
        signals: { type: 'string', description: 'Comma list of signal names to keep (default: all)' },
        format: { type: 'string', enum: ['json', 'csv'], description: 'Also write per-point long CSV curves (default json)' },
      },
      async execute(args) {
        if (!!args.raw_dir === !!args.work_dir) {
          throw new Error('pass exactly one of raw_dir or work_dir')
        }
        const target = resolveDir(args.raw_dir || args.work_dir)
        const argv = [args.raw_dir ? '--raw-dir' : '--work-dir', target]
        if (args.out_dir) argv.push('--out-dir', path.resolve(args.out_dir))
        if (args.signals) argv.push('--signals', String(args.signals))
        if (args.format) argv.push('--format', String(args.format))
        const { stdout } = await runPython('spectre/spectre_psf_parse.py', argv, 600_000)
          .catch((e) => { throw new Error(`spectre_parse failed: ${cliError(e)}`) })
        const outDir = args.out_dir ? path.resolve(args.out_dir) : path.join(target, 'psf')
        const summary = args.work_dir ? readJsonIfExists(path.join(outDir, 'psf_index.json')) : null
        return { ok: true, output: stdout.trim(), out_dir: outDir, summary }
      },
    },
  ]
}
