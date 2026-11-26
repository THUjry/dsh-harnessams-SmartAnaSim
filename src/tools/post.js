// post family: the on-demand data-integrity diagnostic. post_check spawns the
// spectre_post_check.py collector (soft parse entry + strict accessor rules
// applied in collect-all mode) over a sweep run directory or a single point's
// raw directory. Pure orchestration: the JS side forwards arguments, spawns
// the script and passes the JSON report through — every check (argparse's
// exactly-one rule included) and every classification lives in the Python
// layer. Not part of the routine pipeline: the tool description carries the
// call-only-when-results-look-wrong posture. Pure tool surface: no UI.
import { runPython, sessionEnv, cliError } from '../py.js'

export function postTools(ctx = {}) {
  return [
    {
      name: 'post_check',
      description: 'Diagnose simulation-output data integrity on demand — call it when sweep results look '
        + 'wrong, empty or incomplete, NOT as part of the routine pipeline. Takes a sweep run directory '
        + '(work_dir holding NN/ point dirs + sweep_progress.json) or a single point raw directory '
        + '(raw_dir). It scans every point (bad points are reported, never skipped) and returns a '
        + 'classified, aggregated issue report: coverage vs sweep_progress, per-point statuses, parse '
        + 'errors, missing/non-finite signal values, non-monotonic axes, parser-dropped VALUE lines '
        + '(STRUCT data) and errlog tails. Zero file artifacts; the report rides in the tool result.',
      parameters: {
        work_dir: { type: 'string', description: 'Sweep run directory holding NN point dirs + sweep_progress.json' },
        raw_dir: { type: 'string', description: "One point's raw output directory (netlist.scs.raw) for single-point diagnosis" },
      },
      async execute(args, exec) {
        const argv = []
        if (args.work_dir != null) argv.push('--work-dir', args.work_dir)
        if (args.raw_dir != null) argv.push('--raw-dir', args.raw_dir)
        let stdout
        try {
          ;({ stdout } = await runPython('spectre/spectre_post_check.py', argv, 120_000, { env: sessionEnv(exec, ctx), signal: exec?.signal }))
        } catch (e) {
          // argparse usage errors (neither/both dirs) and IO failures land here
          throw new Error(`post_check failed: ${cliError(e)}`)
        }
        return JSON.parse(stdout)
      },
    },
  ]
}
