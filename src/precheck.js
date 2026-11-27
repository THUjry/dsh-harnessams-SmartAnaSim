// Spectre netlist pre-check engine (pure JS, 2026-11-25). The recommended
// workflow hand-writes the run directory (template*.scs + sweep_config.json);
// this module is the gate that keeps those netlists honest without any python
// on the tool path: language format (lang declaration, SPICE-directive
// rejection, LF/ASCII hygiene, @@P@@ placement, no scientific notation — SI
// suffixes only), sweep-parameter presence (an undeclared @@temperature@@ is a
// warning: the worker fills the default 27 degC), and the value gates (ideal
// R/C/L ranges on literals and parameter names — expressions at R/C/L value
// positions are rejected, nf/m multiplier ranges, geometry unit suffixes).
// check parameters resolve through the same chain the retired
// python assembly layer used: built-in defaults <- <workspace>/.harnessams/
// check_params.json <- the calling session's confirmed table in sessions.json
// (fail-open per key; mirrors scripts/common/common_workspace.py). The check
// families are independent pure functions run concurrently — file reads and
// families both go through Promise.all, so nothing serializes behind a slow
// sibling. Nothing here executes or writes: checks only.
import fs from 'node:fs'
import fsp from 'node:fs/promises'
import path from 'node:path'

// ---- check-parameter chain -------------------------------------------------

export const DEFAULT_CHECK_PARAMS = {
  rcl: { r: [0.1, 1e6], c: [1e-15, 1e-6], l: [1e-13, 1e-8] },
  mult: { nf: [1, 128], m: [1, 1000] },
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

// A positive integer within [lo, hi]; integer strings ('8') count.
function multViolation(value, lo, hi) {
  let n
  if (typeof value === 'number') {
    if (!Number.isInteger(value)) return 'is not a positive integer (multipliers are counts)'
    n = value
  } else if (typeof value === 'string' && /^[0-9]+$/.test(value.trim())) {
    n = Number(value.trim())
  } else {
    return 'is not a positive integer (multipliers are counts)'
  }
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
  // statement only; the single exemption is @@temperature@@ on a
  // simulatorOptions line (the shipped sky130 templates sweep it there —
  // spectre does not reliably take a parameter reference in simulatorOptions).
  // Anywhere else — device lines, subckt bodies, analysis statements, comments
  // already stripped — the circuit must reference parameters by name.
  for (const l of code) {
    if (!l.s.includes('@@')) continue
    const names = [...l.s.matchAll(PLACEHOLDER)].map((m) => m[1])
    if (/^parameters\b/i.test(l.s)) continue
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
  for (const l of code) for (const m of l.s.matchAll(PLACEHOLDER)) used.add(m[1])
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
    if (declared.size && !code.some((l) => /^parameters\b/i.test(l.s))) {
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
