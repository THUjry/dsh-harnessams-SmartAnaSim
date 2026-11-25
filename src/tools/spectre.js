// spectre family: the netlist pre-check face. The recommended workflow
// hand-writes the run directory (template*.scs + sweep_config.json — see the
// bundled smart-spectre skill for the contract); this tool validates it before
// submission: language format (simulator lang=spectre, no SPICE directives,
// LF/ASCII, @@P@@ placeholders only on the parameters statement), sweep-
// parameter presence, and the value gates (ideal R/C/L ranges, nf/m multiplier
// ranges, geometry unit suffixes) resolved through the workspace
// check-parameter chain (project check_params.json / the calling session's
// confirmed table). Pure JS checks — nothing executes, nothing is written.
// Pure tool surface: no UI, no scheduling channel — this package is usable on
// its own.
import { runPreCheck } from '../precheck.js'

export function spectreTools() {
  return [
    {
      name: 'spectre_pre_check',
      description: 'Validate a hand-written Spectre run directory (template*.scs + sweep_config.json) '
        + 'before submitting it: language format (simulator lang=spectre declared, no SPICE-only '
        + 'directives, LF/ASCII text, @@P@@ placeholders only on the parameters statement — the '
        + 'circuit references parameters by name), sweep-parameter presence (every swept parameter '
        + 'needs its @@P@@ placeholder, every placeholder needs its config entry), and the value '
        + 'gates from the workspace check-parameter chain (ideal R/C/L ranges, nf/m multiplier '
        + 'ranges, u/n geometry suffixes; project check_params.json plus the calling session\'s '
        + 'confirmed table). Pure checks — nothing executes. batch_submit runs this automatically '
        + 'before staging; call it directly while writing the netlist to catch issues early.',
      parameters: {
        run_dir: { type: 'string', required: true, description: 'Run directory holding the template and sweep_config.json' },
        session_id: { type: 'string', description: 'Session whose confirmed check-params table applies (default: the calling session)' },
      },
      async execute(args, exec) {
        // The session layer resolves through the caller's identity; batch_submit
        // passes it explicitly because a nested tool call's exec does not
        // reliably carry the original agent id.
        const sid = typeof args.session_id === 'string' && args.session_id
          ? args.session_id
          : (typeof exec?.agent?.id === 'string' && exec.agent.id ? exec.agent.id : null)
        return runPreCheck(args.run_dir, sid)
      },
    },
  ]
}
