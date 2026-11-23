// spectre family: the assembly bridge. The tool runs the project's own
// gen_*.py on this host to produce the run-directory inputs (template*.scs +
// sweep_config.json) for the sweep workers shipped under scripts/; the
// workers themselves are plain python scripts that run anywhere (see the
// bundled smart-spectre skill for the full contract). Pure tool surface:
// no UI, no scheduling channel — this package is usable on its own.
import { runAssembly } from '../projects.js'
import { sessionEnv } from '../py.js'

export function spectreTools() {
  return [
    {
      name: 'spectre_assemble',
      description: 'Assemble Spectre sweep inputs: runs the project\'s script/analysis/gen_*.py '
        + '(design_params.json -> Spectre template*.scs + sweep_config.json + metadata) with the '
        + 'assembly-layer format/placeholder/unit checks. The generated run directory feeds the '
        + 'sweep workers shipped under this package\'s scripts/ tree (plain python; the bundled '
        + 'smart-spectre skill documents the full contract).',
      parameters: {
        project_dir: { type: 'string', required: true, description: 'Project root containing script/analysis/gen_*.py' },
        gen_script: { type: 'string', description: 'Assembly script filename (default: the unique gen_*.py under script/analysis)' },
        out_dir: { type: 'string', description: 'Output directory forwarded as --out' },
      },
      async execute(args, exec) {
        return runAssembly(args.project_dir, {
          genScript: args.gen_script,
          outDir: args.out_dir,
          simulator: 'spectre',
          extraEnv: sessionEnv(exec),
        })
      },
    },
  ]
}
