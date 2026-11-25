// spectre family: the netlist pre-check face. The recommended workflow
// hand-writes the run directory (template*.scs + sweep_config.json — see the
// bundled smart-spectre skill for the contract); this tool is the validator
// for one: language format (simulator lang=spectre, no SPICE directives,
// LF/ASCII, @@P@@ placeholders only on the parameters statement, no
// scientific notation — SI suffixes only), sweep-parameter presence (an
// undeclared @@temperature@@ is a warning: the worker fills 27 degC), and the
// value gates (ideal R/C/L ranges on literals and parameter names —
// expressions at R/C/L value positions are rejected, nf/m multiplier ranges,
// geometry unit suffixes) resolved through the workspace check-parameter chain
// (project check_params.json / the calling session's confirmed table). Pure JS
// checks — nothing executes, nothing is written. batch_submit runs this
// automatically before staging, so a direct call is a re-run of the same
// check, not a required pipeline step.
// Pure tool surface: no UI, no scheduling channel — this package is usable on
// its own.
import { runPreCheck } from '../precheck.js'

export function spectreTools() {
  return [
    {
      name: 'spectre_pre_check',
      description: 'Validate a hand-written Spectre run directory (template*.scs + sweep_config.json): '
        + 'language format (simulator lang=spectre declared, no SPICE-only directives, LF/ASCII '
        + 'text, no scientific notation — SI suffixes only, @@P@@ placeholders only on the '
        + 'parameters statement — the circuit references parameters by name), sweep-parameter '
        + 'presence (every swept parameter needs its @@P@@ placeholder, every placeholder needs '
        + 'its config entry; an undeclared @@temperature@@ stays a warning — the worker fills '
        + '27 degC), and the value gates from the workspace check-parameter chain (ideal R/C/L '
        + 'ranges on literals and parameter names — expressions at R/C/L value positions are '
        + 'rejected; nf/m multiplier ranges, u/n geometry suffixes; project check_params.json '
        + 'plus the calling session\'s confirmed table). Pure checks — nothing executes. '
        + 'batch_submit runs this automatically before staging, so a direct call is a re-run '
        + 'of the same check, not a required pipeline step.',
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
