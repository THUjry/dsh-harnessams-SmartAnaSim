// spectre_pre_check engine tests: the @@ placement contract (parameters-
// statement-only, @@temperature@@ exemption on simulatorOptions), sweep-
// parameter presence, ideal RCL / multiplier value gates, geometry unit
// rules, and the check-parameter chain (builtin <- project <- session,
// fail-open per key).
import test, { after } from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { runPreCheck, readCheckParamsChain, parseIdealValue, idealNumber } from '../src/precheck.js'

function makeRun(template, config) {
  const dir = mkdtempSync(path.join(tmpdir(), 'hams-precheck-'))
  writeFileSync(path.join(dir, 'template.scs'), template)
  writeFileSync(path.join(dir, 'sweep_config.json'), JSON.stringify(config))
  return dir
}

const goodRc = makeRun(
  '// rc lpf\nsimulator lang=spectre\nparameters r_val=@@r_val@@ c_val=@@c_val@@\n'
  + 'V1 (vin 0) vsource dc=0 mag=1\nR1 (vin vout) resistor r=r_val\nC1 (vout 0) capacitor c=c_val\n'
  + 'save vout\nac ac start=1e3 stop=1e8 dec=50\n',
  { parameters: { r_val: ['1k', '2k', '5k'], c_val: ['1n', '2n', '5n'] }, sweep_mode: 'cross_product', netlist_template: 'template.scs' })

// The shipped sky130 template shape (template_T/ST/Pi family): @@temperature@@
// on simulatorOptions, expression values, nf literals, the declaration line.
const templateTStyle = makeRun(
  '// t-type\nsimulator lang=spectre\nsimulatorOptions options temp=@@temperature@@ reltol=2e-4\n'
  + 'include "sky130_fd_pr__nfet_01v8_lvt.scs"\nparameters temperature=27 SW=@@SW@@ cpf=@@cpf@@\n'
  + 'subckt att_t (in out sw swb)\nRs1 (in mid) resistor r=13.18\nCp (mid n2) capacitor c=(cpf*1e-15)\n'
  + 'M1 (out gb in 0) nlowvt_model__w61p7nf31 l=0.15u w=61.7u nf=31 nrd=(0.14/61.7) nrs=(0.14/61.7) ad=0 as=0 pd=0 ps=0 sa=0 sb=0 sd=0\n'
  + 'ends att_t\nV1 (in 0) port num=1 z0=50\nX1 (in out sw vswb) att_t\nVSW (sw 0) vsource dc=(1.8*SW)\n'
  + 'sp sp start=1e9 stop=40e9 step=100e6\n',
  { parameters: { temperature: [27], SW: [0, 1], cpf: [100, 200, 400] }, sweep_mode: 'cross_product', netlist_template: 'template.scs' })

test('parseIdealValue / idealNumber: SI suffixes, m vs M case sensitivity', () => {
  assert.equal(parseIdealValue('1k'), 1e3)
  assert.equal(parseIdealValue('1Meg'), 1e6)
  assert.equal(parseIdealValue('1m'), 1e-3)
  assert.equal(parseIdealValue('1M'), 1e6)
  assert.equal(parseIdealValue('0.1'), 0.1)
  assert.equal(parseIdealValue('r_val'), null)
  assert.equal(parseIdealValue('(cpf*1e-15)'), null)
  assert.equal(idealNumber(100), 100)
  assert.equal(idealNumber('1p'), 1e-12)
  assert.equal(idealNumber(true), null)
})

test('new-style RC netlist passes clean', async () => {
  const r = await runPreCheck(goodRc, null)
  assert.equal(r.ok, true, JSON.stringify(r))
  assert.deepEqual(r.errors, [])
  assert.deepEqual(r.warnings, [])
  assert.equal(r.template, 'template.scs')
  assert.equal(r.params_source, 'builtin')
  assert.deepEqual(r.checks.map((c) => c.name), ['language', 'params', 'values'])
})

test('the shipped sky130 template shape passes clean', async () => {
  const r = await runPreCheck(templateTStyle, null)
  assert.equal(r.ok, true, JSON.stringify(r))
})

test('@@ placement: parameters statement only, @@temperature@@ exempt on simulatorOptions', async () => {
  const badCircuit = makeRun(
    'simulator lang=spectre\nparameters SW=@@SW@@\nR1 (a b) resistor r=@@SW@@\n',
    { parameters: { SW: [0, 1] } })
  const r1 = await runPreCheck(badCircuit, null)
  assert.equal(r1.ok, false)
  assert.ok(r1.errors.some((e) => e.startsWith('format:') && e.includes('outside the parameters statement')), r1.errors)

  const badSimopts = makeRun(
    'simulator lang=spectre\nsimulatorOptions options temp=@@FSTART@@\n',
    { parameters: {} })
  const r2 = await runPreCheck(badSimopts, null)
  assert.ok(r2.errors.some((e) => e.includes('@@FSTART@@ outside the parameters statement')), r2.errors)

  const badAnalysis = makeRun(
    'simulator lang=spectre\nac ac start=@@FSTART@@ stop=1e9 dec=50\n',
    { parameters: {} })
  const r3 = await runPreCheck(badAnalysis, null)
  assert.ok(r3.errors.some((e) => e.includes('outside the parameters statement')), r3.errors)
})

test('sweep-parameter presence: both directions, and undeclared name references', async () => {
  const t = makeRun(
    'simulator lang=spectre\nparameters SW=@@SW@@\nV1 (a 0) vsource dc=SW\n',
    { parameters: { SW: [0, 1], Cload: ['1p'] } })
  const r = await runPreCheck(t, null)
  assert.ok(r.errors.some((e) => e.includes('without a @@P@@ placeholder') && e.includes('Cload')), r.errors)
  assert.ok(r.warnings.some((e) => e.includes('Cload is never referenced')), r.warnings)

  const t2 = makeRun(
    'simulator lang=spectre\nparameters SW=@@SW@@ OTHER=@@OTHER@@\n',
    { parameters: { SW: [0, 1] } })
  const r2 = await runPreCheck(t2, null)
  assert.ok(r2.errors.some((e) => e.includes('placeholders not declared') && e.includes('OTHER')), r2.errors)

  const t3 = makeRun('simulator lang=spectre\nR1 (a b) resistor r=Rx\n', { parameters: {} })
  const r3 = await runPreCheck(t3, null)
  assert.ok(r3.errors.some((e) => e.includes("no 'parameters' declaration")), r3.errors)
})

test('swept name-reference values: ranges, numeric base units, declared defaults', async () => {
  const t = makeRun(
    'simulator lang=spectre\nparameters r_val=@@r_val@@\nR1 (vin vout) resistor r=r_val\n',
    { parameters: { r_val: ['1k', '2k'] } })
  assert.equal((await runPreCheck(t, null)).ok, true)
  const out = await runPreCheck(makeRun(
    'simulator lang=spectre\nparameters r_val=@@r_val@@\nR1 (vin vout) resistor r=r_val\n',
    { parameters: { r_val: ['1k', '9M'] } }), null)
  assert.ok(out.errors.some((e) => e.startsWith('rcl:') && e.includes('9M')), out.errors)
  // numeric (suffix-less) sweep values are base-unit ohms: 0.5 ok, 0.01 out
  assert.equal((await runPreCheck(makeRun(
    'simulator lang=spectre\nparameters r_val=@@r_val@@\nR1 (a b) resistor r=r_val\n',
    { parameters: { r_val: [0.5] } }), null)).ok, true)
  assert.ok((await runPreCheck(makeRun(
    'simulator lang=spectre\nparameters r_val=@@r_val@@\nR1 (a b) resistor r=r_val\n',
    { parameters: { r_val: [0.01] } }), null)).errors.some((e) => e.startsWith('rcl:')))
  // declared default checked when the name is not swept
  const def = await runPreCheck(makeRun(
    'simulator lang=spectre\nparameters r_val=1k rbork=0.01\nR1 (a b) resistor r=r_val\nR2 (a b) resistor r=rbork\n',
    { parameters: {} }), null)
  const rclErrs = def.errors.filter((e) => e.startsWith('rcl:'))
  assert.equal(rclErrs.length, 1)
  assert.ok(rclErrs[0].includes('rbork'), rclErrs)
  // expression values referencing undeclared names are errors
  const expr = await runPreCheck(makeRun(
    'simulator lang=spectre\nparameters k=2\nR1 (a b) resistor r=(k*rx)\n', { parameters: {} }), null)
  assert.ok(expr.errors.some((e) => e.includes('rx')), expr.errors)
})

test('multiplier gates: literals, name references, config exact names, expressions skipped', async () => {
  const t = makeRun(
    'simulator lang=spectre\nparameters nf=@@nf@@\nM1 (d g s b) nmos l=0.15u w=1u nf=nf\n',
    { parameters: { nf: [31] } })
  assert.equal((await runPreCheck(t, null)).ok, true)
  const out = await runPreCheck(makeRun(
    'simulator lang=spectre\nparameters nf=@@nf@@\nM1 (d g s b) nmos l=0.15u w=1u nf=nf\n',
    { parameters: { nf: [200] } }), null)
  assert.ok(out.errors.some((e) => e.startsWith('mult:')), out.errors)
  const frac = await runPreCheck(makeRun(
    'simulator lang=spectre\nparameters nf=@@nf@@\nM1 (d g s b) nmos l=0.15u w=1u nf=nf\n',
    { parameters: { nf: [2.5] } }), null)
  assert.ok(frac.errors.some((e) => e.startsWith('mult:') && e.includes('not a positive integer')), frac.errors)

  assert.ok((await runPreCheck(makeRun(
    'simulator lang=spectre\nM1 (d g s b) nmos l=0.15u w=1u nf=200\n', { parameters: {} }), null))
    .errors.some((e) => e.startsWith('mult:')))
  assert.equal((await runPreCheck(makeRun(
    'simulator lang=spectre\nR1 (a b) resistor r=1k m=4\n', { parameters: {} }), null)).ok, true)
  assert.ok((await runPreCheck(makeRun(
    'simulator lang=spectre\nR1 (a b) resistor r=1k m=4000\n', { parameters: {} }), null))
    .errors.some((e) => e.startsWith('mult:')))

  const cfg = await runPreCheck(makeRun(
    'simulator lang=spectre\nparameters m=@@m@@\nR1 (a b) resistor r=1k m=m\n',
    { parameters: { m: [5000] } }), null)
  assert.ok(cfg.errors.some((e) => e.startsWith('mult:')), cfg.errors)

  assert.equal((await runPreCheck(makeRun(
    'simulator lang=spectre\nparameters k=2\nR1 (a b) resistor r=1k m=(2*k)\n', { parameters: {} }), null)).ok, true)
})

test('prefix heuristic: C-initial config values, expression exemption, no duplicates', async () => {
  // r=0 is the idiomatic short, exempt from the resistor floor
  assert.equal((await runPreCheck(makeRun(
    'simulator lang=spectre\nRshort (in out) resistor r=0\n', { parameters: {} }), null)).ok, true)

  const out = await runPreCheck(makeRun(
    'simulator lang=spectre\nparameters Cload=@@Cload@@\n', { parameters: { Cload: ['5u'] } }), null)
  assert.ok(out.errors.some((e) => e.startsWith('rcl:') && e.includes('Cload')), out.errors)

  // expression-referenced parameters are exempt (the cpf*1e-15 pattern)
  assert.equal((await runPreCheck(makeRun(
    'simulator lang=spectre\nparameters cpf=@@cpf@@\nCp (a b) capacitor c=(cpf*1e-15)\n',
    { parameters: { cpf: [100, 200] } }), null)).ok, true)

  // bare-name RCL references are checked precisely, without a duplicate finding
  const dup = await runPreCheck(makeRun(
    'simulator lang=spectre\nparameters Cload=@@Cload@@\nC0 (a b) capacitor c=Cload\n',
    { parameters: { Cload: ['5u'] } }), null)
  assert.equal(dup.errors.filter((e) => e.startsWith('rcl:')).length, 1, dup.errors)

  // geometry-position names are lengths (u/n contract), not inductors
  assert.equal((await runPreCheck(makeRun(
    'simulator lang=spectre\nparameters l_geo=@@l_geo@@\nM1 (d g s b) nmos l=l_geo w=1u\n',
    { parameters: { l_geo: ['180n'] } }), null)).ok, true)
})

test('geometry rules: u/n suffix contract and the metres-trap warning', async () => {
  const out = await runPreCheck(makeRun(
    'simulator lang=spectre\nparameters W1=@@W1@@\nM1 (d g s b) nmos l=0.15u w=W1\n',
    { parameters: { W1: [0.5] } }), null)
  assert.ok(out.errors.some((e) => e.startsWith('units:')), out.errors)
  // suffixed literals stay silent (the lookahead must not backtrack l=0.15u -> l=0)
  const quiet = await runPreCheck(makeRun(
    'simulator lang=spectre\nM1 (d g s b) nmos l=0.15u w=61.7u nf=31\n', { parameters: {} }), null)
  assert.deepEqual(quiet.warnings, [])
  const warn = await runPreCheck(makeRun(
    'simulator lang=spectre\nM1 (d g s b) nmos l=0.5 w=2u\n', { parameters: {} }), null)
  assert.ok(warn.warnings.some((w) => w.includes('metres')), warn.warnings)
})

test('language family: declaration, CRLF, non-ASCII, SPICE directives, subckt pairing', async () => {
  const out = await runPreCheck(makeRun(
    // the escaped bytes decode to non-ASCII text in the fixture netlist
    'parameters SW=@@SW@@\r\n.tran 1u\r\nR1 (a b) resistor r=1k // \u4e2d\u6587\r\n',
    { parameters: { SW: [0, 1] } }), null)
  assert.ok(out.errors.some((e) => e.includes("missing 'simulator lang=spectre'")), out.errors)
  assert.ok(out.errors.some((e) => e.includes('CRLF')), out.errors)
  assert.ok(out.errors.some((e) => e.includes('non-ASCII')), out.errors)
  assert.ok(out.errors.some((e) => e.includes('SPICE-only directive')), out.errors)

  const unbalanced = await runPreCheck(makeRun(
    'simulator lang=spectre\nsubckt sub (a b)\nR1 (a b) resistor r=1k\n', { parameters: {} }), null)
  assert.ok(unbalanced.errors.some((e) => e.includes('do not pair')), unbalanced.errors)

  const noFinalNl = await runPreCheck(makeRun(
    'simulator lang=spectre\nR1 (a b) resistor r=1k', { parameters: {} }), null)
  assert.ok(noFinalNl.warnings.some((w) => w.includes('newline')), noFinalNl.warnings)
})

test('input errors are structured, not thrown', async () => {
  const missingDir = await runPreCheck(path.join(tmpdir(), `hams-missing-${Date.now()}`), null)
  assert.equal(missingDir.ok, false)
  assert.ok(missingDir.errors.some((e) => e.startsWith('input: run directory does not exist')))

  const dir = mkdtempSync(path.join(tmpdir(), 'hams-precheck-'))
  const noConfig = await runPreCheck(dir, null)
  assert.ok(noConfig.errors.some((e) => e.includes('sweep_config.json not found')), noConfig.errors)

  writeFileSync(path.join(dir, 'sweep_config.json'), JSON.stringify({ parameters: { X: [1] }, netlist_template: 'nope.scs' }))
  const noTemplate = await runPreCheck(dir, null)
  assert.ok(noTemplate.errors.some((e) => e.includes('template file not found: nope.scs')), noTemplate.errors)
})

test('check-parameter chain: builtin <- project <- session, fail-open, legacy keys ignored', () => {
  const ws = mkdtempSync(path.join(tmpdir(), 'hams-chain-'))
  mkdirSync(path.join(ws, '.harnessams'))
  writeFileSync(path.join(ws, '.harnessams', 'check_params.json'), JSON.stringify({
    rcl: { c: [1e-15, 1e-9] }, mult: { nf: [1, 8] }, unit_check: false,
    bias_check: true, strict_includes: true, // legacy keys: ignored on read
  }))
  writeFileSync(path.join(ws, '.harnessams', 'sessions.json'), JSON.stringify({
    version: 2,
    sessions: {
      sessA: { check_params: { mult: { nf: [1, 64] } } },
      sessB: { check_params: { rcl: { c: [1e-15, 1e-6] } } },
    },
  }))
  const run1 = path.join(ws, 'run1')
  mkdirSync(run1)

  // a path with NO .harnessams anywhere above it (a fresh sibling tmp dir —
  // walking up from ws itself would find the store created below)
  const builtin = readCheckParamsChain(mkdtempSync(path.join(tmpdir(), 'hams-builtin-')), null)
  assert.equal(builtin.source, 'builtin')
  assert.deepEqual(builtin.params.rcl.c, [1e-15, 1e-6])
  assert.deepEqual(builtin.params.mult.nf, [1, 128])

  const project = readCheckParamsChain(run1, 'no-such-session')
  assert.equal(project.source, 'project')
  assert.deepEqual(project.params.rcl.c, [1e-15, 1e-9])
  assert.deepEqual(project.params.mult.nf, [1, 8])
  assert.equal(project.params.unit_check, false)

  const sessA = readCheckParamsChain(run1, 'sessA')
  assert.equal(sessA.source, 'session')
  assert.deepEqual(sessA.params.mult.nf, [1, 64]) // session override
  assert.deepEqual(sessA.params.rcl.c, [1e-15, 1e-9]) // project layer holds

  const sessB = readCheckParamsChain(run1, 'sessB')
  assert.deepEqual(sessB.params.rcl.c, [1e-15, 1e-6]) // session c back to default
  assert.deepEqual(sessB.params.mult.nf, [1, 8]) // project layer holds

  // corrupt files fail open to the layer below
  writeFileSync(path.join(ws, '.harnessams', 'check_params.json'), '{not json')
  const corrupt = readCheckParamsChain(run1, 'sessA')
  assert.equal(corrupt.source, 'session')
  assert.deepEqual(corrupt.params.rcl.c, [1e-15, 1e-6]) // default c back
})

test('the chain drives the gates: same netlist, different effective ranges', async () => {
  const ws = mkdtempSync(path.join(tmpdir(), 'hams-chain2-'))
  mkdirSync(path.join(ws, '.harnessams'))
  writeFileSync(path.join(ws, '.harnessams', 'check_params.json'), JSON.stringify({ mult: { nf: [1, 8] } }))
  writeFileSync(path.join(ws, '.harnessams', 'sessions.json'), JSON.stringify({
    version: 2, sessions: { sessA: { check_params: { mult: { nf: [1, 64] } } } },
  }))
  const runDir = path.join(ws, 'run')
  mkdirSync(runDir)
  writeFileSync(path.join(runDir, 'template.scs'),
    'simulator lang=spectre\nparameters nf=@@nf@@\nM1 (d g s b) nmos l=0.15u w=1u nf=nf\n')
  writeFileSync(path.join(runDir, 'sweep_config.json'), JSON.stringify({ parameters: { nf: [31] } }))

  const viaSession = await runPreCheck(runDir, 'sessA')
  assert.equal(viaSession.ok, true, JSON.stringify(viaSession))
  assert.equal(viaSession.params_source, 'session')

  const viaProject = await runPreCheck(runDir, 'no-such-session')
  assert.equal(viaProject.ok, false)
  assert.equal(viaProject.params_source, 'project')
  assert.ok(viaProject.errors.some((e) => e.startsWith('mult:')), viaProject.errors)
})

test('the tool face resolves the session from exec.agent.id', async () => {
  const { spectreTools } = await import('../src/tools/spectre.js')
  const tool = spectreTools()[0]
  assert.equal(tool.name, 'spectre_pre_check')
  // exec.agent.id rides through when the model calls the tool directly
  const ws = mkdtempSync(path.join(tmpdir(), 'hams-toolface-'))
  mkdirSync(path.join(ws, '.harnessams'))
  writeFileSync(path.join(ws, '.harnessams', 'sessions.json'), JSON.stringify({
    version: 2, sessions: { 'sess-direct': { check_params: { mult: { nf: [1, 64] } } } },
  }))
  const runDir = path.join(ws, 'run')
  mkdirSync(runDir)
  writeFileSync(path.join(runDir, 'template.scs'),
    'simulator lang=spectre\nparameters nf=@@nf@@\nM1 (d g s b) nmos l=0.15u w=1u nf=nf\n')
  writeFileSync(path.join(runDir, 'sweep_config.json'), JSON.stringify({ parameters: { nf: [31] } }))
  const direct = await tool.execute({ run_dir: runDir }, { agent: { id: 'sess-direct' } })
  assert.equal(direct.ok, true, JSON.stringify(direct))
  assert.equal(direct.params_source, 'session')
  // an explicit session_id argument wins (batch_submit passes it because a
  // nested tool call's exec does not reliably carry the original agent id)
  const explicit = await tool.execute({ run_dir: runDir, session_id: 'sess-direct' }, {})
  assert.equal(explicit.ok, true)
  assert.equal(explicit.params_source, 'session')
})

// cleanup the module-level fixtures after the whole file (each test also uses
// throwaway dirs; these two are shared across tests above)
after(() => {
  rmSync(goodRc, { recursive: true, force: true })
  rmSync(templateTStyle, { recursive: true, force: true })
})
