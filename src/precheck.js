// Spectre netlist pre-check engine (pure JS, 2026-11-25). The recommended
// workflow hand-writes the run directory (template*.scs + sweep_config.json);
// this module is the gate that keeps those netlists honest without any python
// on the tool path: language format (lang declaration, SPICE-directive
// rejection, LF/ASCII hygiene, @@P@@ placement — parameters statement plus
// include statements whose path/section text sweeps, no scientific notation —
// SI suffixes only, template size gates — warn above 10 KB, reject above
// 100 KB), sweep-parameter presence (an undeclared @@temperature@@ is a
// warning: the worker fills the default 27 degC), the value gates (ideal
// R/C/L ranges on literals and parameter names — expressions at R/C/L value
// positions are rejected, the nf range with its warning threshold, m rejected
// by default with a same-line // m-release comment as the release, geometry
// unit suffixes), and the name gates (reserved-word collisions from the
// merged official tables — all errors — plus identifier name forms: C
// identifiers everywhere, integer and bang forms on net names only, the
// backslash escape banned outright).
// check parameters resolve through the same chain the retired
// python assembly layer used: built-in defaults <- <workspace>/.harnessams/
// check_params.json <- the calling session's confirmed table in sessions.json
// (fail-open per key; mirrors scripts/common/common_workspace.py). The check
// families are independent pure functions run concurrently — file reads and
// families both go through Promise.all, so nothing serializes behind a slow
// sibling. Nothing here executes or writes: checks only.
// (covered by tests/precheck.test.mjs; the submit-side gate by the
// HarnessAMS repo's tests/batch.test.mjs)
import fs from 'node:fs'
import fsp from 'node:fs/promises'
import path from 'node:path'

// ---- check-parameter chain -------------------------------------------------

export const DEFAULT_CHECK_PARAMS = {
  rcl: { r: [0.1, 1e6], c: [1e-15, 1e-6], l: [1e-13, 1e-8] },
  mult: { nf: [1, 128], m: [1, 1000] },
  nf_warn: 50,
  unit_check: true,
}

// One [lo, hi] pair from an untrusted source: finite, positive, ordered.
function validPair(value) {
  if (!Array.isArray(value) || value.length !== 2) return null
  if (!value.every((v) => typeof v === 'number' && Number.isFinite(v) && v > 0)) return null
  return value[0] < value[1] ? [Number(value[0]), Number(value[1])] : null
}

// Multiplier pairs are counts: positive integers, ordered.
function validMultPair(value) {
  if (!Array.isArray(value) || value.length !== 2) return null
  if (!value.every((v) => Number.isInteger(v) && v > 0)) return null
  return value[0] < value[1] ? [value[0], value[1]] : null
}

function freshDefaults() {
  return {
    rcl: { r: [...DEFAULT_CHECK_PARAMS.rcl.r], c: [...DEFAULT_CHECK_PARAMS.rcl.c], l: [...DEFAULT_CHECK_PARAMS.rcl.l] },
    mult: { nf: [...DEFAULT_CHECK_PARAMS.mult.nf], m: [...DEFAULT_CHECK_PARAMS.mult.m] },
    nf_warn: DEFAULT_CHECK_PARAMS.nf_warn,
    unit_check: DEFAULT_CHECK_PARAMS.unit_check,
  }
}

// BOM-tolerant (aligned with common_workspace.read_check_params, which reads
// with utf-8-sig: a BOM'd project file must apply in BOTH chains, not only
// the python one).
function readJsonSafe(file) {
  try {
    const text = fs.readFileSync(file, 'utf8')
    return JSON.parse(text.charCodeAt(0) === 0xfeff ? text.slice(1) : text)
  } catch { return null }
}

// Merge one layer (project file or session table) over params, per key,
// fail-open. Unknown keys — e.g. bias_check / strict_includes left behind by
// the pre-mult schema — are ignored on read and vanish on the next write.
function applyLayer(params, raw) {
  if (!raw || typeof raw !== 'object') return
  if (raw.rcl && typeof raw.rcl === 'object') {
    for (const k of ['r', 'c', 'l']) {
      const p = validPair(raw.rcl[k])
      if (p) params.rcl[k] = p
    }
  }
  if (raw.mult && typeof raw.mult === 'object') {
    for (const k of ['nf', 'm']) {
      const p = validMultPair(raw.mult[k])
      if (p) params.mult[k] = p
    }
  }
  if (Number.isInteger(raw.nf_warn) && raw.nf_warn > 0) params.nf_warn = raw.nf_warn
  if (typeof raw.unit_check === 'boolean') params.unit_check = raw.unit_check
}

// Effective check parameters for the workspace tree above startDir. Walks up
// (max 8 levels) looking for a .harnessams store, checking both the plain
// directory and a harnessams-workspace/ subdirectory — the two placement
// conventions the python walker matched. Returns { params, source } with
// source = 'session' | 'project' | 'builtin' (which layer last applied).
export function readCheckParamsChain(startDir, sessionId) {
  let d = path.resolve(String(startDir || '.'))
  for (let i = 0; i < 8; i++) {
    for (const cand of [d, path.join(d, 'harnessams-workspace')]) {
      const store = path.join(cand, '.harnessams')
      if (!fs.existsSync(store)) continue
      try { if (!fs.statSync(store).isDirectory()) continue } catch { continue }
      const params = freshDefaults()
      let source = 'builtin'
      const raw = readJsonSafe(path.join(store, 'check_params.json'))
      if (raw && typeof raw === 'object') {
        applyLayer(params, raw)
        source = 'project'
      }
      if (sessionId) {
        const sessions = readJsonSafe(path.join(store, 'sessions.json'))
        const own = sessions?.sessions?.[sessionId]?.check_params
        if (own && typeof own === 'object') {
          applyLayer(params, own)
          source = 'session'
        }
      }
      return { params, source }
    }
    const parent = path.dirname(d)
    if (parent === d) break
    d = parent
  }
  return { params: freshDefaults(), source: 'builtin' }
}

// ---- shared parsing ---------------------------------------------------------

const PLACEHOLDER = /@@(\w+)(?::[^@]*)?@@/g
const SPICE_DIRECTIVES = ['.control', '.endc', '.end', '.lib', '.option', '.include', '.param', '.model', '.tran', '.ac', '.dc']
const LENGTH_ATTRS = new Set(['l', 'w', 'lr', 'wr', 'rl', 'rw'])
const RCL_DEVICE = { resistor: 'r', res: 'r', capacitor: 'c', cap: 'c', inductor: 'l', ind: 'l' }
const RCL_UNIT = { r: 'ohm', c: 'farad', l: 'henry' }
const RCL_RANGE_TEXT = { r: '0.1..1M ohm', c: '1f..1u farad', l: '0.1p..10n henry' }
// Spectre suffixes are case-sensitive only for M (mega) vs m (milli); the rest
// pair upper/lower; 'meg' (any case) is tolerated as an alias for M.
const RCL_SUFFIX = { T: 1e12, G: 1e9, M: 1e6, K: 1e3, k: 1e3, m: 1e-3, U: 1e-6, u: 1e-6, N: 1e-9, n: 1e-9, P: 1e-12, p: 1e-12, F: 1e-15, f: 1e-15 }
// Multiplier attributes (case-sensitive whole words): device fingers and the
// instance multiplier. Counts, not SI values.
const MULT_ATTRS = ['nf', 'm']
const RCL_INSTANCE = /^\s*(\w+)\s*\(([^)]*)\)\s*([A-Za-z_]\w*)\s+(.*)$/
const RCL_ATTR_VALUE = /\b([rcl])\s*=\s*(\S+)/g
// Existence-only probe for the geometry metres trap: no /g (stateless .test).
// The lookahead class includes '.' — without it the engine backtracks the
// number ('l=0.15u' would match as 'l=0' with '.' passing the lookahead).
const LITERAL_LENGTH = /\b(l|w|lr|wr|rl|rw)\s*=\s*([0-9]*\.?[0-9]+(?:[eE][+-]?[0-9]+)?)(?![0-9a-zA-Z_.])/i
const GEOM_NAME_REF = /\b(l|w|lr|wr|rl|rw)\s*=\s*([A-Za-z_]\w*)\b/gi
const MULT_ATTR_RE = { nf: /\bnf\s*=\s*(\S+)/g, m: /\bm\s*=\s*(\S+)/g }
// m-usage probe and the release comment for the default-rejected m policy.
// M_USAGE is stateless (a /g regex would carry lastIndex across .test calls);
// M_RELEASE matches the token inside a // comment on the SAME line as the m=
// usage — analyzeLines drops whole-line comments, so a standalone // m-release
// line never releases anything.
const M_USAGE = /\bm\s*=/
const M_RELEASE = /\/\/.*\bm-release\b/
// Template-size gates: a hand-written template*.scs is typically a few KB —
// past the warn line large definitions belong in their own included files,
// and past the hard cap the file is almost certainly a generated or flattened
// netlist mounted in the netlist_template slot.
const TEMPLATE_WARN_BYTES = 10 * 1024
const TEMPLATE_MAX_BYTES = 100 * 1024
// Reserved-word tables, the merged official lists (the root repo's
// reserved-words research note under dev/todo): Reference netlist keywords (39 plus
// the prot/unprot abbreviations, both cases), the User-Guide keyword subset
// that extends to net/parameter names (39 minus the seven net-legal words),
// built-in math/physics constants, math functions, the reserved parameters
// (must not be declared), analogmodel, and the avoid-surface approximations
// (known analysis and primitive names on name slots — the shipped `ac ac` /
// `sp sp` analysis statements carry no parenthesized net list, so their
// convention never reaches the instance-name extraction). Membership is
// case-sensitive: the manuals list the words "case specified".
const KEYWORDS = new Set(['altergroup', 'correlate', 'else', 'end', 'ends', 'export', 'for', 'function', 'global', 'ic', 'if', 'in', 'input', 'inline', 'invisible', 'library', 'local', 'march', 'model', 'nodeset', 'out', 'output', 'parameters', 'paramset', 'plot', 'print', 'protect', 'pwr', 'real', 'return', 'save', 'sens', 'statistics', 'subckt', 'to', 'truncate', 'unprotect', 'vary', 'visible', 'prot', 'PROT', 'unprot', 'UNPROT'])
const UG_KEYWORDS = new Set(['altergroup', 'correlate', 'else', 'end', 'ends', 'export', 'for', 'function', 'global', 'ic', 'if', 'inline', 'library', 'local', 'march', 'model', 'nodeset', 'parameters', 'paramset', 'plot', 'print', 'protect', 'pwr', 'real', 'return', 'save', 'sens', 'statistics', 'subckt', 'to', 'truncate', 'vary', 'visible'])
const CONSTANTS = new Set(['M_1_PI', 'M_2_PI', 'M_2_SQRTPI', 'M_DEGPERRAD', 'M_E', 'M_LN10', 'M_LN2', 'M_LOG10E', 'M_LOG2E', 'M_PI', 'M_PI_2', 'M_PI_4', 'M_SQRT1_2', 'M_SQRT2', 'M_TWO_PI', 'P_C', 'P_CELSIUS0', 'P_EPS0', 'P_H', 'P_K', 'P_Q', 'P_U0'])
const MATH_FUNCS = new Set(['abs', 'acos', 'acosh', 'asin', 'asinh', 'atan', 'atan2', 'atanh', 'ceil', 'cos', 'cosh', 'exp', 'floor', 'hypot', 'int', 'log', 'log10', 'max', 'min', 'pow', 'sin', 'sinh', 'sqrt', 'tan', 'tanh'])
const RESERVED_PARAMS = new Set(['temp', 'tnom', 'scale', 'scalem', 'freq', 'time'])
const ANALYSIS_NAMES = new Set(['ac', 'dc', 'sp', 'tran', 'pss', 'sweeppss', 'noise', 'stb', 'xf', 'fourier', 'envlp'])
const PRIMITIVE_NAMES = new Set(['resistor', 'res', 'capacitor', 'cap', 'inductor', 'ind', 'vsource', 'isource', 'port', 'bsource', 'tline', 'nmos', 'pmos'])
// Position-specific ban sets — every collision is an ERROR (no warning tier):
// name slots take the Reference keyword ban plus constants, math functions,
// analogmodel and the avoid-surface lists; net slots take the constants, math
// functions and User-Guide keywords (in/out/input/output/invisible/unprotect/
// visible stay legal as net names per both manuals); parameter declarations
// add the reserved parameters. `m` is owned by the multiplier gate above
// (already stricter than the official implicit-parameter rule).
const NAME_BAN = new Set([...KEYWORDS, ...CONSTANTS, ...MATH_FUNCS, ...ANALYSIS_NAMES, ...PRIMITIVE_NAMES, 'analogmodel'])
const NET_BAN = new Set([...CONSTANTS, ...MATH_FUNCS, ...UG_KEYWORDS])
const PARAM_BAN = new Set([...RESERVED_PARAMS, ...CONSTANTS, ...MATH_FUNCS, ...UG_KEYWORDS, 'analogmodel'])
// Name-slot extraction shapes: any parenthesized net list after the head marks
// a device instance (statements never carry one after subckt is handled
// first); analysis lines (ac ac start=... / sp sp ports=[...]) have none.
const INSTANCE_SHAPE = /^(\S+)\s*\(([^)]*)\)/
const SUBCKT_DEF = /^(?:inline\s+)?subckt\s+(\S+)\s*\(([^)]*)\)/i
const MODEL_STMT = /^model\s+(\S+)/i
const PARAMETERS_STMT = /^parameters\s+(.+)$/i
const GLOBAL_STMT = /^global\s+(.+)$/i
// Legal unescaped name forms (User Guide identifier rules): a strict C
// identifier everywhere; net names additionally a pure integer (leading zeros
// significant) or a bang suffix (vdd!). The backslash escape — the official
// legacy compatibility back door — is banned outright; every other form
// (unescaped '-', '$', '@', blanks, tabs, punctuation) is an error.
const IDENT_FORM = /^[A-Za-z_][A-Za-z0-9_]*$/
const INT_NET_FORM = /^[0-9]+$/
const BANG_NET_FORM = /^[A-Za-z_][A-Za-z0-9_]*!$/
// Scientific notation anywhere on a code line (comments already stripped):
// number-shaped only — the lookbehind keeps identifier tails (a net named
// 'n1e5') out of the match, so '1e-15', '2.5e-4' and '1e+03' all flag.
const SCI_NOTATION = /(?<![A-Za-z0-9_.])\d+(?:\.\d+)?[eE][+-]?\d+/
const TEMPLATE_NAME_RE = /^template[\w.-]*\.scs$/

const g = (n) => String(Number(Number(n).toPrecision(6)))

function rangeText(kind, lo, hi) {
  return lo === DEFAULT_CHECK_PARAMS.rcl[kind][0] && hi === DEFAULT_CHECK_PARAMS.rcl[kind][1]
    ? RCL_RANGE_TEXT[kind] : `${g(lo)}..${g(hi)} ${RCL_UNIT[kind]}`
}

// Parse an ideal R/C/L netlist value ('1k', '2.5u', '0.1', '1Meg') into the
// base unit. A bare number is the base unit — unlike geometry lengths there is
// no metres trap for ideal devices. Returns null when text is not a plain
// literal (name, expression or unknown suffix) so callers skip it, not guess.
export function parseIdealValue(text) {
  const token = String(text).trim().replace(/[;,]+$/, '')
  const m = /^([+-]?\d*\.?\d+(?:[eE][+-]?\d+)?)([a-zA-Z]*)$/.exec(token)
  if (!m) return null
  if (!m[2]) return Number(m[1])
  const factor = m[2].toLowerCase() === 'meg' ? 1e6 : RCL_SUFFIX[m[2]]
  return factor === undefined ? null : Number(m[1]) * factor
}

// Base-unit number for a sweep-config value: strings parse as SI literals,
// numbers are taken as-is (bare = base unit); bool/other types are uncheckable.
export function idealNumber(value) {
  if (typeof value === 'boolean') return null
  if (typeof value === 'number') return Number.isFinite(value) ? value : null
  if (typeof value === 'string') return parseIdealValue(value)
  return null
}

// Integer count from a numeric or numeric-string value, else null — the value
// forms a multiplier position accepts.
function parseCount(value) {
  if (typeof value === 'number') return Number.isInteger(value) ? value : null
  if (typeof value === 'string' && /^[0-9]+$/.test(value.trim())) return Number(value.trim())
  return null
}

// A positive integer within [lo, hi]; integer strings ('8') count.
function multViolation(value, lo, hi) {
  const n = parseCount(value)
  if (n === null) return 'is not a positive integer (multipliers are counts)'
  if (n < lo || n > hi) return `outside the multiplier range ${lo}..${hi}`
  return null
}

// Code-line model: { n: original line number, s: stripped text }, comments
// (//) and blanks dropped — the same stripped view the python checker used.
function analyzeLines(text) {
  const raw = text.split('\n')
  const code = []
  for (let i = 0; i < raw.length; i++) {
    const s = raw[i].trim()
    if (!s || s.startsWith('//')) continue
    code.push({ n: i + 1, s })
  }
  return { raw, code }
}

// Template-level 'parameters' statements: name -> literal token (last wins).
// Declared names are the resolution targets for circuit name references.
function parseTemplateParams(code) {
  const out = {}
  for (const l of code) {
    if (!/^parameters\b/i.test(l.s)) continue
    for (const am of l.s.matchAll(/(\w+)\s*=\s*(\S+)/g)) out[am[1]] = am[2].replace(/[;,]$/, '')
  }
  return out
}

// The values a circuit name-reference resolves to: the sweep-config list when
// the name is swept, else the template's declared default literal.
function valuesFor(name, parameters, def) {
  if (parameters && Object.hasOwn(parameters, name)) {
    const v = parameters[name]
    return Array.isArray(v) ? v : [v]
  }
  return def !== undefined ? [def] : []
}

// ---- family: language format ------------------------------------------------

function checkLanguage(text, raw, code, templateName) {
  const errors = []
  const warnings = []
  if (text.includes('\r')) {
    const n = raw.filter((l) => l.endsWith('\r')).length
    errors.push(`format: CRLF line endings on ${n} line(s) — the netlist must be LF-only`)
  }
  if (text && !text.endsWith('\n')) warnings.push('format: the netlist does not end with a newline')
  if (text.length > TEMPLATE_WARN_BYTES) {
    warnings.push(`format: template is ${text.length} bytes, above the ${TEMPLATE_WARN_BYTES}-byte warning threshold — move large circuit definitions into separate files and include them`)
  }
  for (let i = 0; i < raw.length; i++) {
    const line = raw[i]
    const ctrl = /[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]/.exec(line)
    if (ctrl) errors.push(`format: control character 0x${ctrl[0].charCodeAt(0).toString(16)} at line ${i + 1}`)
    if (/[^\x00-\x7F]/.test(line)) errors.push(`format: non-ASCII character at line ${i + 1} — Spectre netlists are ASCII-only`)
  }
  if (!code.some((l) => /^simulator\s+lang\s*=\s*spectre\b/i.test(l.s))) {
    errors.push("format: missing 'simulator lang=spectre' declaration")
  }
  for (const l of code) {
    const low = l.s.toLowerCase()
    for (const d of SPICE_DIRECTIVES) {
      if (low === d || low.startsWith(d + ' ')) {
        errors.push(`format: SPICE-only directive ${d} is not valid in a Spectre netlist (line ${l.n})`)
        break
      }
    }
  }
  // Scientific notation is banned in netlists (2026-11-25 ruling): every
  // number is a plain decimal or carries an explicit SI suffix, so no scale
  // factor can hide in an exponent form (the retired c=(cpf*1e-15) idiom).
  for (const l of code) {
    const m = SCI_NOTATION.exec(l.s)
    if (m) errors.push(`format: scientific notation '${m[0]}' at line ${l.n} — netlists use SI suffixes (1f, 100M, 200u) or plain decimals, never exponent form`)
  }
  const nSub = code.filter((l) => /^(inline\s+)?subckt\b/i.test(l.s)).length
  const nEnds = code.filter((l) => /^ends\b/i.test(l.s)).length
  if (nSub !== nEnds) errors.push(`format: subckt(${nSub}) and ends(${nEnds}) counts do not pair`)
  // @@ placement (2026-11-25 ruling): placeholders live on the parameters
  // statement; two exemptions — @@temperature@@ on a simulatorOptions line
  // (the shipped sky130 templates sweep it there — spectre does not reliably
  // take a parameter reference in simulatorOptions) and include statements,
  // whose path/section text sweeps (dspf extracts, corner sections: the
  // worker substitutes text-wide, so the placeholder resolves there exactly
  // like a parameters-line one). Anywhere else — device lines, subckt bodies,
  // analysis statements, comments already stripped — the circuit must
  // reference parameters by name.
  for (const l of code) {
    if (!l.s.includes('@@')) continue
    const names = [...l.s.matchAll(PLACEHOLDER)].map((m) => m[1])
    if (/^parameters\b/i.test(l.s)) continue
    if (/^include\b/i.test(l.s)) continue
    if (/^simulatorOptions\b/i.test(l.s)) {
      const bad = names.filter((nm) => nm !== 'temperature')
      if (bad.length) errors.push(`format: @@${bad[0]}@@ outside the parameters statement (line ${l.n}) — only @@temperature@@ is allowed on a simulatorOptions line`)
      continue
    }
    errors.push(`format: @@${names.join('@@ / @@')}@@ placeholder outside the parameters statement (line ${l.n}) — the circuit must reference parameters by name`)
  }
  if (templateName && !TEMPLATE_NAME_RE.test(path.posix.basename(templateName))) {
    warnings.push('naming: netlist_template should be a template*.scs family name (default template.scs)')
  }
  return { errors, warnings }
}

// ---- family: sweep parameters ------------------------------------------------

function checkSweepParams(code, config) {
  const errors = []
  const warnings = []
  const parameters = config && typeof config.parameters === 'object' && config.parameters ? config.parameters : null
  const used = new Set()
  // Include-line placeholders resolve through the same text-wide substitution
  // (the swept string lands in the include path or the section= value).
  const includeUsed = new Set()
  for (const l of code) for (const m of l.s.matchAll(PLACEHOLDER)) {
    used.add(m[1])
    if (/^include\b/i.test(l.s)) includeUsed.add(m[1])
  }
  if (parameters) {
    const declared = new Set(Object.keys(parameters))
    // Temperature is the one placeholder that may stay undeclared: the sweep
    // worker fills the default 27 degC (2026-11-25 ruling — warn, never block).
    const missing = [...used].filter((n) => !declared.has(n) && n !== 'temperature').sort()
    if (missing.length) {
      errors.push(`params: template placeholders not declared in sweep_config.parameters: ${missing.join(', ')} (unresolved placeholders reach spectre as literal text)`)
    }
    if (used.has('temperature') && !declared.has('temperature')) {
      warnings.push('params: @@temperature@@ is not declared in sweep_config.parameters — the worker fills the default 27 (degC)')
    }
    const absent = [...declared].filter((n) => !used.has(n)).sort()
    if (absent.length) {
      errors.push(`params: swept parameters without a @@P@@ placeholder in the template: ${absent.join(', ')} — the sweep cannot vary them`)
    }
    // A sweep that only varies include text needs no parameters statement:
    // include-line placeholders take effect through substitution, so the
    // no-statement note fires only for parameters nothing else covers.
    if (declared.size && !code.some((l) => /^parameters\b/i.test(l.s))
      && [...declared].some((n) => !includeUsed.has(n))) {
      warnings.push("format: no 'parameters' statement found although the sweep declares parameters")
    }
    const circuit = code.filter((l) => !/^parameters\b/i.test(l.s)).map((l) => l.s).join('\n')
    for (const name of declared) {
      if (!new RegExp(`\\b${name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}\\b`).test(circuit)) {
        warnings.push(`params: swept parameter ${name} is never referenced by the circuit`)
      }
    }
  } else if (used.size) {
    errors.push('params: the template carries @@P@@ placeholders but sweep_config.json declares no parameters')
  }
  return { errors, warnings }
}

// ---- family: value gates ------------------------------------------------------

function checkValues(code, config, params) {
  const errors = []
  const warnings = []
  const parameters = config && typeof config.parameters === 'object' && config.parameters ? config.parameters : null
  const templateParams = parseTemplateParams(code)
  const unitCheck = params.unit_check !== false
  const nfWarn = params.nf_warn
  const nfWarnMsg = (subject) =>
    `mult: ${subject} is at or above the nf warning threshold ${nfWarn} — large finger counts are unusual; verify the device (threshold: check_params nf_warn)`
  // Names already checked precisely at an ideal-RCL value position — the
  // prefix heuristic below skips them to avoid duplicate findings.
  const resolvedNames = new Set()
  // Config names referenced at geometry attribute positions (l=/w=/…): they are
  // lengths under the u/n unit contract, NOT RCL-kind values — the prefix
  // heuristic below must not read 'l_geo' as an inductor.
  const geomChecked = new Set()
  // Config names whose values were already checked at an nf/m value position —
  // the exact-name config pass below skips them to avoid duplicate findings.
  const multChecked = new Set()

  // m is rejected by default: any m= usage — instance line, parameters
  // statement, literal / name / expression — errors unless that same line
  // carries a // m-release comment. Writing m= without an error is not
  // results scaling by m: only primitives and subckt instances scale,
  // Verilog-A/CMI models ignore the reserved name, and sources/tline leave it
  // meaningless — hence the explicit per-line release. The range gates below
  // still run on released lines.
  for (const l of code) {
    if (!M_USAGE.test(l.s)) continue
    if (M_RELEASE.test(l.s)) continue
    errors.push(`mult: line ${l.n} uses m= — the instance multiplier is rejected by default (only primitives and subckt instances scale; Verilog-A/CMI models ignore it); verify the semantics and add a // m-release comment on that line (the ${params.mult.m[0]}..${params.mult.m[1]} range gate then applies)`)
  }

  for (const l of code) {
    if (/^parameters\b/i.test(l.s)) continue
    const im = RCL_INSTANCE.exec(l.s)
    if (!im) continue
    const kind = RCL_DEVICE[im[3].toLowerCase()]
    if (kind) {
      const [lo, hi] = params.rcl[kind]
      for (const am of l.s.matchAll(RCL_ATTR_VALUE)) {
        const attr = am[1]
        const token = am[2].replace(/[;,]$/, '')
        if (attr !== kind) continue
        if (token.startsWith('@@')) continue // placement family reports it
        const num = parseIdealValue(token)
        if (num !== null) {
          // 0 is the legal degenerate element (r=0/l=0 short, c=0 open — the
          // 2026-11-25 r=0 ruling extended to every kind); the plausibility
          // range applies above it
          if (num !== 0 && (num < lo || num > hi)) {
            errors.push(`rcl: ${im[1]} (${im[3]}) ${attr}=${token} (${g(num)} ${RCL_UNIT[kind]}) outside the ideal range ${rangeText(kind, lo, hi)}`)
          }
          continue
        }
        if (/^[A-Za-z_]\w*$/.test(token)) {
          resolvedNames.add(token)
          if (!Object.hasOwn(templateParams, token)) {
            errors.push(`params: ${im[1]} (${im[3]}) ${attr}=${token} references a parameter with no 'parameters' declaration`)
            continue
          }
          for (const v of valuesFor(token, parameters, templateParams[token])) {
            const n = idealNumber(v)
            if (n !== null && n !== 0 && (n < lo || n > hi)) {
              errors.push(`rcl: ${im[1]} (${im[3]}) ${attr}=${token} value ${JSON.stringify(v)} outside the ideal range ${rangeText(kind, lo, hi)}`)
            }
          }
          continue
        }
        // Expressions are banned at ideal R/C/L value positions (2026-11-25
        // ruling): their scale cannot be verified, and the retired exemption
        // let whole fF-scale scans slip an nF-tightened bound. The value must
        // be a literal or a bare parameter name with suffixed sweep values.
        errors.push(`rcl: ${im[1]} (${im[3]}) ${attr}=${token} — expressions are not allowed at ideal R/C/L value positions; use ${attr}=<param> with suffixed sweep values`)
      }
    } else {
      // geometry on non-ideal-RCL lines: the metres trap. A bare number means
      // metres in Spectre (warning); a swept name must carry u/n suffixes
      // (error) — the whole pass is gated by the workspace unit_check switch.
      if (unitCheck && LITERAL_LENGTH.test(l.s)) {
        warnings.push(`units: literal geometry value without a unit suffix in ${JSON.stringify(l.s.slice(0, 120))} (Spectre reads it as metres)`)
      }
      if (unitCheck && parameters) {
        for (const am of l.s.matchAll(GEOM_NAME_REF)) {
          geomChecked.add(am[2])
          const vals = parameters[am[2]]
          if (!Array.isArray(vals)) continue
          for (const v of vals) {
            if (typeof v !== 'string' || !/^[0-9]*\.?[0-9]+\s*[un]$/i.test(v.trim())) {
              errors.push(`units: ${am[1].toLowerCase()}=${am[2]} swept value ${JSON.stringify(v)} must be a u/n-suffixed string (e.g. '1.5u'); a bare number means metres in Spectre`)
              break
            }
          }
        }
      }
    }
    // multiplier gates (nf/m) apply to every instance line, ideal RCL included
    for (const attr of MULT_ATTRS) {
      for (const am of l.s.matchAll(MULT_ATTR_RE[attr])) {
        const token = am[1].replace(/[;,]$/, '')
        const [lo, hi] = params.mult[attr]
        if (/^[0-9]+$/.test(token)) {
          const n = Number(token)
          if (n < lo || n > hi) errors.push(`mult: ${im[1]} ${attr}=${token} outside the multiplier range ${lo}..${hi}`)
          if (attr === 'nf' && n >= nfWarn) warnings.push(nfWarnMsg(`${im[1]} nf=${token}`))
          continue
        }
        if (/^[0-9]*\.?[0-9]+(?:[eE][+-]?[0-9]+)?$/.test(token)) {
          errors.push(`mult: ${im[1]} ${attr}=${token} is not a positive integer (multipliers are counts)`)
          continue
        }
        if (/^[A-Za-z_]\w*$/.test(token)) {
          if (!Object.hasOwn(templateParams, token)) {
            errors.push(`params: ${im[1]} ${attr}=${token} references a parameter with no 'parameters' declaration`)
            continue
          }
          multChecked.add(token)
          for (const v of valuesFor(token, parameters, templateParams[token])) {
            const bad = multViolation(v, lo, hi)
            if (bad) errors.push(`mult: ${attr}=${token} value ${JSON.stringify(v)} ${bad}`)
            const n = attr === 'nf' ? parseCount(v) : null
            if (n !== null && n >= nfWarn) warnings.push(nfWarnMsg(`nf=${token} value ${JSON.stringify(v)}`))
          }
        }
        // anything else (expressions like m=(2*SW)) is skipped: unknown scale
      }
    }
  }

  // exact-name config parameters nf / m
  if (parameters) {
    for (const attr of MULT_ATTRS) {
      const vals = parameters[attr]
      if (vals === undefined || multChecked.has(attr)) continue
      const [lo, hi] = params.mult[attr]
      for (const v of (Array.isArray(vals) ? vals : [vals])) {
        const bad = multViolation(v, lo, hi)
        if (bad) errors.push(`mult: swept parameter ${attr} value ${JSON.stringify(v)} ${bad}`)
        const n = attr === 'nf' ? parseCount(v) : null
        if (n !== null && n >= nfWarn) warnings.push(nfWarnMsg(`swept parameter nf value ${JSON.stringify(v)}`))
      }
    }
    // prefix heuristic (the user-facing "find the C-prefixed parameter and
    // check it as a capacitor" net): applies to parameters the netlist has NOT
    // resolved precisely at a bare-name RCL value (those skip to avoid
    // duplicate findings). No expression exemption anymore (2026-11-25
    // ruling): expressions at R/C/L value positions are errors, and a
    // c/r/l-prefixed config value is checkable as-is wherever it is used.
    for (const [name, vals] of Object.entries(parameters)) {
      if (!/^[cCrRlL]/.test(name)) continue
      if (resolvedNames.has(name) || geomChecked.has(name)) continue
      if (LENGTH_ATTRS.has(name)) continue
      const kind = name[0].toLowerCase()
      const [lo, hi] = params.rcl[kind]
      for (const v of (Array.isArray(vals) ? vals : [vals])) {
        const n = idealNumber(v)
        if (n !== null && n !== 0 && (n < lo || n > hi)) {
          errors.push(`rcl: swept parameter ${name} value ${JSON.stringify(v)} outside the ideal ${kind.toUpperCase()} range ${rangeText(kind, lo, hi)} (name starts with '${name[0]}')`)
        }
      }
    }
  }
  return { errors, warnings }
}

// ---- name-slot extraction (reserved-word + identifier families) ---------------

// Every name-bearing slot as { kind, n, name } — kinds: instance, subckt,
// model, param, net. Extraction runs on the pre-comment body of each code
// line: inline // tails stay in l.s (analyzeLines drops only whole-line
// comments), and tokens from a comment tail must never masquerade as names.
// The instance master slot (the primitive/model reference after the net
// list) is deliberately NOT extracted: it names an external library object,
// not a hand-written name.
function collectNames(code) {
  const out = []
  for (const l of code) {
    const body = l.s.includes('//') ? l.s.slice(0, l.s.indexOf('//')) : l.s
    const sm = SUBCKT_DEF.exec(body)
    if (sm) {
      out.push({ kind: 'subckt', n: l.n, name: sm[1] })
      for (const net of sm[2].trim().split(/\s+/)) if (net) out.push({ kind: 'net', n: l.n, name: net })
      continue
    }
    const im = INSTANCE_SHAPE.exec(body)
    if (im) {
      out.push({ kind: 'instance', n: l.n, name: im[1] })
      for (const net of im[2].trim().split(/\s+/)) if (net) out.push({ kind: 'net', n: l.n, name: net })
      continue
    }
    const mm = MODEL_STMT.exec(body)
    if (mm) { out.push({ kind: 'model', n: l.n, name: mm[1] }); continue }
    const pm = PARAMETERS_STMT.exec(body)
    if (pm) {
      for (const tok of pm[1].trim().split(/\s+/)) {
        const eq = tok.indexOf('=')
        if (eq > 0) out.push({ kind: 'param', n: l.n, name: tok.slice(0, eq) })
      }
      continue
    }
    const gm = GLOBAL_STMT.exec(body)
    if (gm) {
      for (const net of gm[1].trim().split(/\s+/)) if (net) out.push({ kind: 'net', n: l.n, name: net })
    }
  }
  return out
}

// ---- family: reserved words ----------------------------------------------------

// Which official list a banned name comes from (message detail).
function describeReserved(name) {
  if (RESERVED_PARAMS.has(name)) return 'a reserved parameter (must not be declared)'
  if (KEYWORDS.has(name) || UG_KEYWORDS.has(name)) return 'a Spectre netlist keyword'
  if (CONSTANTS.has(name)) return 'a built-in math/physics constant'
  if (MATH_FUNCS.has(name)) return 'a built-in math function'
  if (ANALYSIS_NAMES.has(name)) return 'an analysis name'
  if (PRIMITIVE_NAMES.has(name)) return 'a built-in primitive name'
  return 'reserved (analogmodel)'
}

function checkReservedWords(code, config) {
  const errors = []
  const slotText = { instance: 'instance name', subckt: 'subckt name', model: 'model name', net: 'net name', param: 'parameter' }
  for (const { kind, n, name } of collectNames(code)) {
    const ban = kind === 'net' ? NET_BAN : kind === 'param' ? PARAM_BAN : NAME_BAN
    if (ban.has(name)) errors.push(`naming: ${slotText[kind]} '${name}' (line ${n}) is ${describeReserved(name)} — rename it`)
  }
  const parameters = config && typeof config.parameters === 'object' && config.parameters ? config.parameters : null
  if (parameters) {
    for (const name of Object.keys(parameters)) {
      if (PARAM_BAN.has(name)) errors.push(`naming: swept parameter '${name}' is ${describeReserved(name)} — rename it`)
    }
  }
  return { errors, warnings: [] }
}

// ---- family: identifier forms ----------------------------------------------------

function checkIdentifiers(code) {
  const errors = []
  const slotText = { instance: 'instance name', subckt: 'subckt name', model: 'model name', net: 'net name', param: 'parameter name' }
  for (const { kind, n, name } of collectNames(code)) {
    if (IDENT_FORM.test(name)) continue
    if (kind === 'net' && (INT_NET_FORM.test(name) || BANG_NET_FORM.test(name))) continue
    if (name.startsWith('\\')) {
      errors.push(`naming: ${slotText[kind]} '${name}' (line ${n}) uses the backslash escape — a legacy compatibility back door, not allowed here; rename it`)
      continue
    }
    if (kind !== 'net' && /^[0-9]/.test(name)) {
      errors.push(`naming: ${slotText[kind]} '${name}' (line ${n}) must begin with a letter or underscore (node names may be integers) — rename it`)
      continue
    }
    errors.push(`naming: ${slotText[kind]} '${name}' (line ${n}) has an illegal form — only letters, digits and underscores fit a name (net names may add a bang suffix); rename it`)
  }
  return { errors, warnings: [] }
}

// ---- orchestrator --------------------------------------------------------------

const dedup = (arr) => [...new Set(arr)]

export async function runPreCheck(runDir, sessionId) {
  const dir = path.resolve(String(runDir || ''))
  if (!fs.existsSync(dir)) {
    return { ok: false, errors: [`input: run directory does not exist: ${dir}`], warnings: [], template: null, params_source: 'builtin', checks: [] }
  }
  const config = readJsonSafe(path.join(dir, 'sweep_config.json'))
  const templateName = (config && typeof config.netlist_template === 'string' && config.netlist_template) || 'template.scs'
  const templateFile = path.join(dir, templateName)
  // Parallel input load: the template text and the check-parameter chain are
  // independent reads; a missing template is an input error, not a throw — the
  // caller (the model or a managed flow) reads the structured list either way.
  const [templateRes, chain] = await Promise.all([
    fsp.readFile(templateFile, 'utf8').then((text) => ({ ok: true, text }), (e) => ({ ok: false, error: e })),
    Promise.resolve(readCheckParamsChain(dir, sessionId)),
  ])
  const inputErrors = []
  if (!config) inputErrors.push(`input: sweep_config.json not found or not valid JSON in ${dir}`)
  if (!templateRes.ok) inputErrors.push(`input: template file not found: ${templateName} (sweep_config.netlist_template names it)`)
  // Size gate (the template is already in memory at this point): a hand-
  // written template*.scs is typically a few KB, so a file past the hard cap
  // is almost certainly a generated or flattened netlist mounted in the
  // netlist_template slot — reject it before the regex families see it.
  if (templateRes.ok && templateRes.text.length > TEMPLATE_MAX_BYTES) {
    inputErrors.push(`input: template ${templateName} is ${templateRes.text.length} bytes, over the ${TEMPLATE_MAX_BYTES}-byte pre-check limit — split the netlist with include statements (a hand-written template*.scs is typically a few KB)`)
  }
  if (inputErrors.length) {
    return { ok: false, errors: inputErrors, warnings: [], template: templateName, params_source: chain.source, checks: [] }
  }

  const text = templateRes.text
  const { raw, code } = analyzeLines(text)
  // Independent families, run concurrently (each pure, no shared state); the
  // per-family timing rides along for the checks[] digest.
  const families = [
    ['language', () => checkLanguage(text, raw, code, templateName)],
    ['params', () => checkSweepParams(code, config)],
    ['values', () => checkValues(code, config, chain.params)],
    ['reserved', () => checkReservedWords(code, config)],
    ['identifiers', () => checkIdentifiers(code)],
  ]
  const checks = await Promise.all(families.map(([name, fn]) => Promise.resolve().then(() => {
    const t0 = Date.now()
    const r = fn()
    return { name, ms: Date.now() - t0, errors: r.errors, warnings: r.warnings }
  })))
  const errors = dedup(checks.flatMap((c) => c.errors))
  const warnings = dedup(checks.flatMap((c) => c.warnings))
  return {
    ok: errors.length === 0,
    errors,
    warnings,
    template: templateName,
    params_source: chain.source,
    checks: checks.map((c) => ({ name: c.name, ms: c.ms, errors: c.errors.length, warnings: c.warnings.length })),
  }
}
