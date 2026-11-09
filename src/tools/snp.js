// sNp family: S-parameter file reading and device extraction (tools only, no UI).
import path from 'node:path'
import { cliError, readJsonIfExists, runPython } from '../py.js'

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
      name: 'snp_read',
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
