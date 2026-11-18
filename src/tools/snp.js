// sNp family: S-parameter file reading and device extraction (tools only, no UI).
//
// Guide-gated deferred loading: snp_read/snp_extract carry deferLoading:true
// plus a `when` admission predicate, so they stay out of the model-facing
// declarations until snp_guide (always resident) is called. On routes
// declaring toolUpdate (deepseek-flash default) the loop then appends them via
// tool-addition without breaking the KV-cache prefix; on routes without
// toolUpdate the `when` gating still applies while deferLoading is stripped by
// the route (declarations resend, one-time prefix break at activation).
// Activation is one-way — addition-only routes drop tool-removal, so the set
// is never unset — and keyed by agent id string, which survives in-process
// session restore (a reopened session gets a fresh Agent object with the same
// id). After a server restart the set is empty and the model self-heals by
// calling snp_guide again; sub-agents are separate agents and call the guide
// themselves.
import path from 'node:path'
import { cliError, readJsonIfExists, runPython } from '../py.js'

// agent.id strings that called snp_guide in this process (never unset).
const snpActivated = new Set()

const SNP_FLOW_DOC = 'S-parameter tools active for this session (snp_read, snp_extract).\n'
  + '\n'
  + 'Typical flow:\n'
  + '1. snp_read {file} — parse a Touchstone .s1p..sNp, Spectre psfascii sp.sp or a\n'
  + '   params CSV; writes <stem>_params_<P>.csv plus a <stem>_read.json summary\n'
  + '   (band statistics, reciprocity diagnostic, max |Sij|).\n'
  + '2. snp_extract {file, type} — device parameters; type is tl | coupled_tl |\n'
  + '   transformer | inductor. An unphysical result returns ok=false WITH\n'
  + '   summary/port_plan — that is a structured diagnosis, not a failure: inspect\n'
  + '   port_plan and the physicality reasons before retrying.\n'
  + '\n'
  + 'Outputs land beside the input file unless out_dir is given; every write\n'
  + 'prints a "[snp] wrote <path>" line.'

// The Python side is the single owner of output naming: both scripts print
// "[snp] wrote <abs path>" for every file they produce. Parsing those lines
// replaced the old local stem re-derivation, which disagreed with the Python
// suffix stripping (_params_X / _extract_*): a params-CSV input silently lost
// its summary (readJsonIfExists -> null) with no error anywhere.
function wrotePaths(stdout) {
  return String(stdout ?? '').split(/\r?\n/)
    .filter((l) => l.startsWith('[snp] wrote '))
    .map((l) => l.slice('[snp] wrote '.length).trim())
    .filter(Boolean)
}

function fallbackStem(file) {
  return path.basename(String(file)).replace(/\.[^.]+$/, '')
}

export function snpTools() {
  return [
    {
      name: 'snp_guide',
      description: 'Activate the S-parameter tools (snp_read, snp_extract) for this session and return their '
        + 'usage flow. Call when the user wants to read a Touchstone/Spectre S-parameter file or extract '
        + 'device parameters.',
      parameters: {},
      // The registry validates execute() results against output.schema, so
      // the string flow doc needs a string schema (the official guide-tool
      // pattern) instead of makeTool's default object wrapper — returning a
      // bare string under the default schema fails with `"value" must be an
      // object` (caught live on 2026-11-18).
      output: {
        schema: { type: 'string' },
        render: (_args, value) => [{ type: 'text', text: String(value) }],
      },
      async execute(_args, exec) {
        // The admission flag is set only inside a tool call — a call is
        // inherently after the session's first request, which the
        // deferred-addition mechanism requires (tools admitted before the
        // first request land in the declaration baseline and never activate).
        if (exec?.agent?.id) snpActivated.add(exec.agent.id)
        return SNP_FLOW_DOC
      },
    },
    {
      name: 'snp_read',
      deferLoading: true,
      when: ({ agent }) => !!agent?.id && snpActivated.has(agent.id),
      description: 'Read an S-parameter file (Touchstone .s1p..sNp, Spectre psfascii sp.sp, or a snp_read params '
        + 'CSV) and dump per-frequency S/Z/Y matrices to CSV, with header info, band statistics, a reciprocity '
        + 'diagnostic and max |Sij|. Device parameter extraction is snp_extract.',
      parameters: {
        file: { type: 'string', required: true, description: 'Input file path (.sNp / sp.sp / params CSV)' },
        param: { type: 'string', enum: ['S', 'Z', 'Y'], description: 'Matrix kind to dump (default S)' },
        out_dir: { type: 'string', description: 'Output directory (default: beside the input file)' },
      },
      async execute(args) {
        const argv = ['--file', String(args.file)]
        if (args.param) argv.push('--param', String(args.param))
        if (args.out_dir) argv.push('--out-dir', String(args.out_dir))
        const { stdout } = await runPython('snp/snp_read.py', argv, 180_000)
          .catch((e) => { throw new Error(`snp_read failed: ${cliError(e)}`) })
        const dir = args.out_dir ? path.resolve(args.out_dir) : path.dirname(path.resolve(String(args.file)))
        const jsonPath = wrotePaths(stdout).find((p) => p.endsWith('_read.json'))
          ?? path.join(dir, `${fallbackStem(args.file)}_read.json`)
        return {
          ok: true,
          output: stdout.trim(),
          summary: readJsonIfExists(jsonPath),
        }
      },
    },
    {
      name: 'snp_extract',
      deferLoading: true,
      when: ({ agent }) => !!agent?.id && snpActivated.has(agent.id),
      description: 'Extract device parameters from an S-parameter file (same input formats as snp_read). '
        + 'Types: transformer (L1/L2/Q1/Q2/k, two SRFs, Gmax; 2/3/4/5/6 ports), inductor (L/Q/SRF; 1/2/3 ports), '
        + 'tl (Zc/gamma*l/theta, optional eps_eff/alpha via length_m; 2 ports or 4-port differential), coupled_tl '
        + '(even/odd modal Z0e/Z0o/theta/k + theory_ok; 4 ports or 8-port differential). A side listed with one '
        + 'port in coil/coil1/coil2 is single-ended (cold end grounded in the EM setup), two ports are '
        + 'differential; ct/ct1/ct2 name the center tap; --se grounds ports during extraction. Missing or '
        + 'unphysical mappings are auto-adjusted against physicality checks and the result echoes a port_plan. '
        + 'An unphysical result returns ok=false WITH summary/port_plan (the JSON is written even on the '
        + 'unphysical exit) — inspect port_plan and the physicality reasons before retrying.',
      parameters: {
        file: { type: 'string', required: true, description: 'Input file path (.sNp / sp.sp / params CSV)' },
        type: { type: 'string', required: true, enum: ['tl', 'coupled_tl', 'transformer', 'inductor'],
          description: 'tl | coupled_tl | transformer | inductor' },
        freq_hz: { type: 'number', description: 'Extraction frequency in Hz (default: band geometric midpoint)' },
        coil1: { type: 'string', description: 'Side-1 / near differential pair ports, e.g. "1,2"; one port = single-ended side' },
        coil2: { type: 'string', description: 'Side-2 / far differential pair ports' },
        coil3: { type: 'string', description: 'coupled_tl 8-port: third differential pair' },
        coil4: { type: 'string', description: 'coupled_tl 8-port: fourth differential pair' },
        coil: { type: 'string', description: 'Inductor coil ports (1 or 2)' },
        ct1: { type: 'number', description: 'Transformer primary center-tap port number' },
        ct2: { type: 'number', description: 'Transformer secondary center-tap port number' },
        ct: { type: 'number', description: 'Inductor center-tap port number' },
        se: { type: 'string', description: 'Ports to ground during extraction (V=0), comma list, e.g. "2" or "2,4"' },
        length_m: { type: 'number', description: 'Physical length in meters (tl: enables eps_eff and alpha per length)' },
        theory_gate: { type: 'number', description: 'coupled_tl relative off-block residual gate for theory_ok' },
        out_dir: { type: 'string', description: 'Output directory (default: beside the input file)' },
      },
      async execute(args) {
        const argv = ['--file', String(args.file), '--type', String(args.type)]
        for (const [k, v] of Object.entries(args)) {
          if (['file', 'type', 'out_dir'].includes(k) || v === undefined || v === null || v === '') continue
          argv.push(`--${k.replace(/_/g, '-')}`, String(v))
        }
        if (args.out_dir) argv.push('--out-dir', String(args.out_dir))
        // Exit 1 is a CONTRACT of snp_extract.py's main: "unphysical, but the
        // JSON with summary/port_plan is still written" — the structured
        // result IS the diagnosis. Only rethrow when no JSON landed (usage /
        // IO errors exit 2 without writing anything).
        let stdout = ''
        let failed = null
        try {
          ;({ stdout } = await runPython('snp/snp_extract.py', argv, 300_000))
        } catch (e) {
          stdout = typeof e?.stdout === 'string' ? e.stdout : ''
          failed = e
        }
        const dir = args.out_dir ? path.resolve(args.out_dir) : path.dirname(path.resolve(String(args.file)))
        const jsonPath = wrotePaths(stdout).find((p) => p.endsWith(`_extract_${args.type}.json`))
          ?? path.join(dir, `${fallbackStem(args.file)}_extract_${args.type}.json`)
        const json = readJsonIfExists(jsonPath)
        if (failed && !json) {
          throw new Error(`snp_extract failed: ${cliError(failed)}`)
        }
        return {
          ok: !failed,
          output: failed
            ? [stdout.trim(), String(failed?.stderr ?? '').trim()].filter(Boolean).join('\n')
            : stdout.trim(),
          summary: json?.summary ?? null,
          port_plan: json?.port_plan ?? null,
        }
      },
    },
  ]
}
