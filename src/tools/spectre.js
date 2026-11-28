// spectre family: the netlist pre-check face. The recommended workflow
// hand-writes the run directory (template*.scs + sweep_config.json — see the
// bundled smart-spectre skill for the contract); this tool is the validator
// for one: language format (simulator lang=spectre, no SPICE directives,
// LF/ASCII, @@P@@ placeholders on the parameters statement and on include
// statements (path/section text sweeps), no scientific notation — SI suffixes
// only, template size gates — warn above 10 KB, reject above 100 KB),
// sweep-parameter presence (an undeclared @@temperature@@ is a warning:
// the worker fills 27 degC), the value gates (ideal R/C/L ranges on
// literals and parameter names — expressions at R/C/L value positions are
// rejected, the nf range with its warning threshold, m rejected by default
// with a same-line // m-release comment as the release, geometry unit
// suffixes), and the name gates (reserved-word collisions from the merged
// official tables — all errors — and identifier name forms: C identifiers
// everywhere, integer and bang nets only, the backslash escape banned)
// resolved through the workspace check-parameter chain
// (project check_params.json / the calling session's confirmed table). Pure JS
// checks — nothing executes, nothing is written. A managed sweep flow stages
// run directories only after running this same check automatically, so a
// direct call is a re-run of the same check, not a required pipeline step.
// Pure tool surface: no UI, no scheduling channel — this package is usable on
// its own.
import { runPreCheck } from '../precheck.js'
import { effectiveSessionId } from '../subagent.js'

export function spectreTools(ctx = {}) {
  return [
    {
      name: 'spectre_pre_check',
      description: 'Validate a hand-written Spectre run directory (template*.scs + sweep_config.json): '
        + 'language format (simulator lang=spectre declared, no SPICE-only directives, LF/ASCII '
        + 'text, no scientific notation — SI suffixes only, @@P@@ placeholders on the parameters '
        + 'statement — the circuit references parameters by name — and on include statements, '
        + 'where the path or the section= value may sweep), sweep-parameter '
        + 'presence (every swept parameter needs its @@P@@ placeholder, every placeholder needs '
        + 'its config entry; an undeclared @@temperature@@ stays a warning — the worker fills '
        + '27 degC), and the value gates from the workspace check-parameter chain (ideal R/C/L '
        + 'ranges on literals and parameter names — expressions at R/C/L value positions are '
        + 'rejected; nf range with a warning threshold above it (nf_warn, default 50), m '
        + 'rejected by default — a // m-release comment on the m= line releases it, then the '
        + 'range gate applies; u/n geometry suffixes; plus the name gates (reserved words '
        + 'from the merged official tables — keywords, constants, math functions, reserved '
        + 'parameters, analysis and primitive names — are errors on name slots; names must '
        + 'be C identifiers, integer and bang forms allowed on net names only, the '
        + 'backslash escape banned) and the template size gates (warning above 10 KB, '
        + 'reject above 100 KB); project check_params.json '
        + 'plus the calling session\'s confirmed table). sweep_config.json shape: parameters is '
        + 'a dict of value lists (SI-suffixed strings for swept R/C/L values, plain strings for '
        + 'include path/section sweeps), plus sweep_mode '
        + 'and netlist_template. Pure checks — nothing executes. '
        + 'A managed sweep flow stages run directories only after running this same check '
        + 'automatically, so a direct call is a re-run of the same check, not a required '
        + 'pipeline step.',
      parameters: {
        run_dir: { type: 'string', required: true, description: 'Run directory holding the template and sweep_config.json' },
        session_id: { type: 'string', description: 'Session whose confirmed check-params table applies (default: the calling session — a delegated subagent resolves to the topmost user-visible session)' },
      },
      async execute(args, exec) {
        // The session layer resolves through the caller's identity; a
        // managed-flow caller passes it explicitly because a nested tool
        // call's exec does not reliably carry the original agent id. A
        // subagent caller anchors on
        // its topmost user-visible ancestor (src/subagent.js).
        const sid = typeof args.session_id === 'string' && args.session_id
          ? args.session_id
          : effectiveSessionId(ctx, exec?.agent)
        return runPreCheck(args.run_dir, sid)
      },
    },
  ]
}
