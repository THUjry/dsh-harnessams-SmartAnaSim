// spectre_pre_check engine tests: the @@ placement contract (parameters
// statement, @@temperature@@ exemption on simulatorOptions, include
// statements for path/section sweeps), the no-scientific-notation rule,
// sweep-parameter presence (undeclared @@temperature@@ = warning + worker
// default 27), ideal RCL / multiplier value gates (expressions rejected at RCL
// positions, zero = legal degenerate, the nf warning threshold, the
// default-rejected m with its same-line // m-release comment), geometry unit
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
  + 'save vout\nac ac start=1k stop=100M dec=50\n',
  { parameters: { r_val: ['1k', '2k', '5k'], c_val: ['1n', '2n', '5n'] }, sweep_mode: 'cross_product', netlist_template: 'template.scs' })

// The shipped sky130 template shape (template_T/ST/Pi family): @@temperature@@
// on simulatorOptions, bare-name capacitor with suffixed sweep values, nf
// literals, the declaration line, SI suffixes everywhere.
const templateTStyle = makeRun(
  '// t-type\nsimulator lang=spectre\nsimulatorOptions options temp=@@temperature@@ reltol=200u\n'
  + 'include "sky130_fd_pr__nfet_01v8_lvt.scs"\nparameters temperature=27 SW=@@SW@@ cpf=@@cpf@@\n'
  + 'subckt att_t (in out sw swb)\nRs1 (in mid) resistor r=13.18\nCp (mid n2) capacitor c=cpf\n'
  + 'M1 (out gb in 0) nlowvt_model__w61p7nf31 l=0.15u w=61.7u nf=31 nrd=(0.14/61.7) nrs=(0.14/61.7) ad=0 as=0 pd=0 ps=0 sa=0 sb=0 sd=0\n'
  + 'ends att_t\nV1 (in 0) port num=1 z0=50\nX1 (in out sw vswb) att_t\nVSW (sw 0) vsource dc=(1.8*SW)\n'
  + 'sp sp start=1G stop=40G step=100M\n',
  { parameters: { temperature: [27], SW: [0, 1], cpf: ['100f', '200f', '400f'] }, sweep_mode: 'cross_product', netlist_template: 'template.scs' })

test('parseIdealValue / idealNumber: SI suffixes, m vs M case sensitivity', () => {
  assert.equal(parseIdealValue('1k'), 1e3)
  assert.equal(parseIdealValue('1Meg'), 1e6)
  assert.equal(parseIdealValue('1m'), 1e-3)
  assert.equal(parseIdealValue('1M'), 1e6)
  assert.equal(parseIdealValue('0.1'), 0.1)
  assert.equal(parseIdealValue('r_val'), null)
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
  assert.deepEqual(r.checks.map((c) => c.name), ['language', 'params', 'values', 'reserved', 'identifiers'])
})

test('the shipped sky130 template shape passes clean', async () => {
  const r = await runPreCheck(templateTStyle, null)
  assert.equal(r.ok, true, JSON.stringify(r))
})

test('@@ placement: parameters statement, @@temperature@@ exempt on simulatorOptions', async () => {
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
    'simulator lang=spectre\nac ac start=@@FSTART@@ stop=1G dec=50\n',
    { parameters: {} })
  const r3 = await runPreCheck(badAnalysis, null)
  assert.ok(r3.errors.some((e) => e.includes('outside the parameters statement')), r3.errors)
})

test('no scientific notation: exponent forms flag, identifier tails do not', async () => {
  const sci = await runPreCheck(makeRun(
    'simulator lang=spectre\nsimulatorOptions options reltol=2e-4\nC1 (a b) capacitor c=1.5e-13\nsp sp start=1e9 stop=40G step=100M\n',
    { parameters: {} }), null)
  const sciErrs = sci.errors.filter((e) => e.includes('scientific notation'))
  assert.equal(sciErrs.length, 3, sciErrs)
  assert.ok(sciErrs.some((e) => e.includes("'2e-4'")), sciErrs)
  assert.ok(sciErrs.some((e) => e.includes("'1.5e-13'")), sciErrs)
  assert.ok(sci.ok === false)

  // identifier-shaped tails (a net named n1e5) are not exponent matches
  const ident = await runPreCheck(makeRun(
    'simulator lang=spectre\nR1 (n1e5 b) resistor r=1k\nR2 (n1e5 b2e7) resistor r=2k\n',
    { parameters: {} }), null)
  assert.deepEqual(ident.errors, [], ident.errors)
})

test('undeclared @@temperature@@ is a warning (worker default 27), never an error', async () => {
  const missing = await runPreCheck(makeRun(
    '// t-style\nsimulator lang=spectre\nsimulatorOptions options temp=@@temperature@@ reltol=200u\n'
    + 'parameters temperature=27 SW=@@SW@@\nVSW (sw 0) vsource dc=(1.8*SW)\n',
    { parameters: { SW: [0, 1] } }), null)
  assert.equal(missing.ok, true, JSON.stringify(missing))
  assert.deepEqual(missing.errors, [])
  assert.ok(missing.warnings.some((w) => w.includes('@@temperature@@') && w.includes('27')), missing.warnings)

  // declared temperature: no warning at all
  const declared = await runPreCheck(makeRun(
    'simulator lang=spectre\nsimulatorOptions options temp=@@temperature@@ reltol=200u\n'
    + 'parameters temperature=27 SW=@@SW@@\nVSW (sw 0) vsource dc=(1.8*SW)\n',
    { parameters: { temperature: [27], SW: [0, 1] } }), null)
  assert.equal(declared.ok, true)
  assert.deepEqual(declared.warnings, [])

  // every other undeclared placeholder stays an error
  const other = await runPreCheck(makeRun(
    'simulator lang=spectre\nparameters SW=@@SW@@ OTHER=@@OTHER@@\nV1 (a 0) vsource dc=SW\n',
    { parameters: { SW: [0, 1] } }), null)
  assert.ok(other.errors.some((e) => e.includes('placeholders not declared') && e.includes('OTHER')), other.errors)
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
  // expressions at RCL value positions are rejected outright
  const expr = await runPreCheck(makeRun(
    'simulator lang=spectre\nparameters k=2\nR1 (a b) resistor r=(k*rx)\n', { parameters: {} }), null)
  assert.ok(expr.errors.some((e) => e.includes('expressions are not allowed')), expr.errors)
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
    'simulator lang=spectre\nR1 (a b) resistor r=1k m=4 // m-release\n', { parameters: {} }), null)).ok, true)
  assert.ok((await runPreCheck(makeRun(
    'simulator lang=spectre\nR1 (a b) resistor r=1k m=4000 // m-release\n', { parameters: {} }), null))
    .errors.some((e) => e.startsWith('mult:')))

  const cfg = await runPreCheck(makeRun(
    'simulator lang=spectre\nparameters m=@@m@@ // m-release\nR1 (a b) resistor r=1k m=m // m-release\n',
    { parameters: { m: [5000] } }), null)
  assert.ok(cfg.errors.some((e) => e.startsWith('mult:')), cfg.errors)

  assert.equal((await runPreCheck(makeRun(
    'simulator lang=spectre\nparameters k=2\nR1 (a b) resistor r=1k m=(2*k) // m-release\n', { parameters: {} }), null)).ok, true)
})

test('include statements may carry @@ placeholders: path and section sweeps', async () => {
  // the swept string lands in the include path or the section= value — an
  // include-only sweep is a complete, warning-free run
  const pathSweep = await runPreCheck(makeRun(
    'simulator lang=spectre\ninclude "corner_@@corner@@.scs"\n',
    { parameters: { corner: ['tt', 'ss'] } }), null)
  assert.equal(pathSweep.ok, true, JSON.stringify(pathSweep))
  assert.deepEqual(pathSweep.warnings, [], pathSweep.warnings)

  const sectionSweep = await runPreCheck(makeRun(
    'simulator lang=spectre\ninclude "sky130.scs" section=@@sec@@\n',
    { parameters: { sec: ['tt', 'ff'] } }), null)
  assert.equal(sectionSweep.ok, true, JSON.stringify(sectionSweep))
  assert.deepEqual(sectionSweep.warnings, [], sectionSweep.warnings)

  // a declared parameter nothing covers keeps both findings: the missing
  // placeholder error AND the no-parameters-statement note
  const uncovered = await runPreCheck(makeRun(
    'simulator lang=spectre\ninclude "sky130.scs"\n',
    { parameters: { SW: [0, 1] } }), null)
  assert.ok(uncovered.errors.some((e) => e.includes('without a @@P@@ placeholder') && e.includes('SW')), uncovered.errors)
  assert.ok(uncovered.warnings.some((w) => w.includes("no 'parameters' statement")), uncovered.warnings)

  // placement still rejects @@ on device and analysis lines
  const badCircuit = await runPreCheck(makeRun(
    'simulator lang=spectre\nparameters SW=@@SW@@\nR1 (a b) resistor r=@@SW@@\n',
    { parameters: { SW: [0, 1] } }), null)
  assert.ok(badCircuit.errors.some((e) => e.includes('outside the parameters statement')), badCircuit.errors)
})

test('nf warning threshold: warns at or above nf_warn, errors above the range', async () => {
  // a literal at the default threshold warns, never fails
  const literal = await runPreCheck(makeRun(
    'simulator lang=spectre\nM1 (d g s b) nmos l=0.15u w=1u nf=50\n', { parameters: {} }), null)
  assert.equal(literal.ok, true, JSON.stringify(literal))
  assert.ok(literal.warnings.some((w) => w.startsWith('mult:') && w.includes('nf=50') && w.includes('warning threshold 50')), literal.warnings)

  // below the threshold: silent
  const low = await runPreCheck(makeRun(
    'simulator lang=spectre\nM1 (d g s b) nmos l=0.15u w=1u nf=31\n', { parameters: {} }), null)
  assert.deepEqual(low.warnings, [], low.warnings)

  // above the range stays an error
  const over = await runPreCheck(makeRun(
    'simulator lang=spectre\nM1 (d g s b) nmos l=0.15u w=1u nf=200\n', { parameters: {} }), null)
  assert.ok(over.errors.some((e) => e.startsWith('mult:') && e.includes('nf=200')), over.errors)

  // a swept name and a config exact-name entry both warn on their values
  const name = await runPreCheck(makeRun(
    'simulator lang=spectre\nparameters nf=@@nf@@\nM1 (d g s b) nmos l=0.15u w=1u nf=nf\n',
    { parameters: { nf: [64] } }), null)
  assert.ok(name.warnings.some((w) => w.startsWith('mult:') && w.includes('64')), name.warnings)
  const exact = await runPreCheck(makeRun(
    'simulator lang=spectre\nM1 (d g s b) nmos l=0.15u w=1u\n',
    { parameters: { nf: [64] } }), null)
  assert.ok(exact.warnings.some((w) => w.startsWith('mult:') && w.includes('64')), exact.warnings)

  // the threshold follows the check-parameter chain: a project nf_warn of 80
  // silences nf=64
  const ws = mkdtempSync(path.join(tmpdir(), 'hams-nfwarn-'))
  mkdirSync(path.join(ws, '.harnessams'))
  writeFileSync(path.join(ws, '.harnessams', 'check_params.json'), JSON.stringify({ nf_warn: 80 }))
  const runDir = path.join(ws, 'run')
  mkdirSync(runDir)
  writeFileSync(path.join(runDir, 'template.scs'), 'simulator lang=spectre\nM1 (d g s b) nmos l=0.15u w=1u nf=64\n')
  writeFileSync(path.join(runDir, 'sweep_config.json'), JSON.stringify({ parameters: {} }))
  const raised = await runPreCheck(runDir, null)
  assert.equal(raised.params_source, 'project')
  assert.deepEqual(raised.warnings, [], raised.warnings)
})

test('m is rejected by default; a same-line // m-release comment releases it', async () => {
  // any m= — m=1 included — errors without the release, and the message
  // teaches the release idiom
  const plain = await runPreCheck(makeRun(
    'simulator lang=spectre\nR1 (a b) resistor r=1k m=1\n', { parameters: {} }), null)
  assert.equal(plain.ok, false)
  assert.ok(plain.errors.some((e) => e.startsWith('mult:') && e.includes('m-release')), plain.errors)

  // the release must sit on the m= line itself — a standalone comment line
  // releases nothing
  const standalone = await runPreCheck(makeRun(
    '// m-release\nsimulator lang=spectre\nR1 (a b) resistor r=1k m=2\n', { parameters: {} }), null)
  assert.ok(standalone.errors.some((e) => e.startsWith('mult:') && e.includes('m-release')), standalone.errors)

  // same-line release passes; the range gate still applies beyond it
  const released = await runPreCheck(makeRun(
    'simulator lang=spectre\nR1 (a b) resistor r=1k m=4 // m-release\n', { parameters: {} }), null)
  assert.equal(released.ok, true, JSON.stringify(released))
  const outOfRange = await runPreCheck(makeRun(
    'simulator lang=spectre\nR1 (a b) resistor r=1k m=4000 // m-release\n', { parameters: {} }), null)
  assert.ok(outOfRange.errors.some((e) => e.startsWith('mult:') && e.includes('4000')), outOfRange.errors)

  // two m= lines, one released: exactly the unreleased line errors
  const mixed = await runPreCheck(makeRun(
    'simulator lang=spectre\nR1 (a b) resistor r=1k m=4 // m-release\nR2 (a b) resistor r=2k m=2\n',
    { parameters: {} }), null)
  assert.equal(mixed.errors.filter((e) => e.startsWith('mult:')).length, 1, mixed.errors)
  assert.ok(mixed.errors.some((e) => e.includes('line 3')), mixed.errors)

  // a parameters-statement m and an expression m both count as usage
  const declared = await runPreCheck(makeRun(
    'simulator lang=spectre\nparameters m=2\nR1 (a b) resistor r=1k\n', { parameters: {} }), null)
  assert.ok(declared.errors.some((e) => e.startsWith('mult:')), declared.errors)
  const expr = await runPreCheck(makeRun(
    'simulator lang=spectre\nparameters k=2\nR1 (a b) resistor r=1k m=(2*k)\n', { parameters: {} }), null)
  assert.ok(expr.errors.some((e) => e.startsWith('mult:') && e.includes('m-release')), expr.errors)
})

test('reserved words: every name-slot collision is an error, shipped shapes stay clean', async () => {
  // keywords (and the prot abbreviation) on instance names, a keyword as a
  // subckt name, a constant as a model name
  const kw = await runPreCheck(makeRun(
    'simulator lang=spectre\nsave (a b) resistor r=1k\nprot (a b) resistor r=1k\n'
    + 'subckt parameters (x y)\nends parameters\nmodel M_PI bsim4\n',
    { parameters: {} }), null)
  const kwErrs = kw.errors.filter((e) => e.startsWith('naming:'))
  assert.ok(kwErrs.some((e) => e.includes("instance name 'save'") && e.includes('keyword')), kwErrs)
  assert.ok(kwErrs.some((e) => e.includes("instance name 'prot'")), kwErrs)
  assert.ok(kwErrs.some((e) => e.includes("subckt name 'parameters'")), kwErrs)
  assert.ok(kwErrs.some((e) => e.includes("model name 'M_PI'") && e.includes('constant')), kwErrs)

  // reserved parameters must never be declared — parameters statement and
  // sweep-config keys are both declarations
  const decl = await runPreCheck(makeRun(
    'simulator lang=spectre\nparameters temp=27 x=@@x@@\nR1 (a b) resistor r=1k\n',
    { parameters: { x: [1] } }), null)
  assert.ok(decl.errors.some((e) => e.includes("parameter 'temp'") && e.includes('reserved parameter')), decl.errors)
  const cfg = await runPreCheck(makeRun(
    'simulator lang=spectre\nparameters x=@@x@@\nR1 (a b) resistor r=1k\n',
    { parameters: { x: [1], time: [1] } }), null)
  assert.ok(cfg.errors.some((e) => e.includes("'time'") && e.startsWith('naming:')), cfg.errors)

  // a math function as a net name; math function, analysis name and
  // primitive name as instance names
  const net = await runPreCheck(makeRun(
    'simulator lang=spectre\nR1 (max b) resistor r=1k\n', { parameters: {} }), null)
  assert.ok(net.errors.some((e) => e.includes("net name 'max'")), net.errors)
  const inst = await runPreCheck(makeRun(
    'simulator lang=spectre\nsin (a b) resistor r=1k\ntran (a b) resistor r=1k\nresistor (a b) resistor r=1k\n',
    { parameters: {} }), null)
  assert.ok(inst.errors.some((e) => e.includes("instance name 'sin'") && e.includes('math function')), inst.errors)
  assert.ok(inst.errors.some((e) => e.includes("instance name 'tran'") && e.includes('analysis name')), inst.errors)
  assert.ok(inst.errors.some((e) => e.includes("instance name 'resistor'") && e.includes('primitive')), inst.errors)

  // in/out stay legal as net names (both manuals carve them out) — the
  // shipped sky130 shape keeps passing clean
  const ok = await runPreCheck(templateTStyle, null)
  assert.equal(ok.ok, true, JSON.stringify(ok))
})

test('name forms: C identifiers, integer and bang nets legal; escapes and specials rejected', async () => {
  // bang and pure integers are the official net-name relaxations (global
  // nets included); leading zeros are significant, so 07 and 7 coexist
  const legal = await runPreCheck(makeRun(
    'simulator lang=spectre\nglobal vdd!\nR1 (vdd! 0) resistor r=1k\nR2 (07 7) resistor r=2k\n',
    { parameters: {} }), null)
  assert.equal(legal.ok, true, JSON.stringify(legal))

  // unescaped specials on a net, digit-leading and bang instance names, an
  // escaped name (the banned legacy back door), a $-parameter
  const bad = await runPreCheck(makeRun(
    'simulator lang=spectre\nR1 (my-net b) resistor r=1k\n2N2222 (a b) resistor r=1k\n'
    + 'vdd! (a b) resistor r=1k\n\\2N2222 (a b) resistor r=1k\nparameters $foo=1\n',
    { parameters: {} }), null)
  const formErrs = bad.errors.filter((e) => e.startsWith('naming:'))
  assert.ok(formErrs.some((e) => e.includes("net name 'my-net'")), formErrs)
  assert.ok(formErrs.some((e) => e.includes("instance name '2N2222'") && e.includes('letter or underscore')), formErrs)
  assert.ok(formErrs.some((e) => e.includes("instance name 'vdd!'")), formErrs)
  assert.ok(formErrs.some((e) => e.includes("'\\2N2222'") && e.includes('backslash escape')), formErrs)
  assert.ok(formErrs.some((e) => e.includes("parameter name '$foo'")), formErrs)
  assert.equal(bad.ok, false)
})

test('template size gates: warn above 10 KB, reject above 100 KB', async () => {
  // an ~11 KB clean template warns but still runs every family
  const big = 'simulator lang=spectre\n' + 'R1 (a b) resistor r=1k\n'.repeat(500)
  const warn = await runPreCheck(makeRun(big, { parameters: {} }), null)
  assert.equal(warn.ok, true, JSON.stringify(warn))
  assert.ok(warn.warnings.some((w) => w.startsWith('format:') && w.includes('11523 bytes') && w.includes('10240-byte warning threshold')), warn.warnings)
  assert.equal(warn.checks.length, 5)

  // past the hard cap the run dies as a structured input error before any
  // family runs
  const huge = 'simulator lang=spectre\n' + 'R1 (a b) resistor r=1k\n'.repeat(5000)
  const reject = await runPreCheck(makeRun(huge, { parameters: {} }), null)
  assert.equal(reject.ok, false)
  assert.ok(reject.errors.some((e) => e.startsWith('input:') && e.includes('102400') && e.includes('include')), reject.errors)
  assert.deepEqual(reject.checks, [])
})

test('prefix heuristic: C-initial config values, expressions rejected, no duplicates', async () => {
  // r=0 is the idiomatic short, exempt from the resistor floor
  assert.equal((await runPreCheck(makeRun(
    'simulator lang=spectre\nRshort (in out) resistor r=0\n', { parameters: {} }), null)).ok, true)

  const out = await runPreCheck(makeRun(
    'simulator lang=spectre\nparameters Cload=@@Cload@@\n', { parameters: { Cload: ['5u'] } }), null)
  assert.ok(out.errors.some((e) => e.startsWith('rcl:') && e.includes('Cload')), out.errors)

  // the new idiom: bare-name reference + suffixed sweep values, checked
  // precisely by the bare-name path (c=0f degenerate point included)
  assert.equal((await runPreCheck(makeRun(
    'simulator lang=spectre\nparameters cpf=@@cpf@@\nCp (a b) capacitor c=cpf\n',
    { parameters: { cpf: ['0f', '56f', '150f'] } }), null)).ok, true)

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

test('zero is the legal degenerate element for every RCL kind', async () => {
  assert.equal((await runPreCheck(makeRun(
    'simulator lang=spectre\nR0 (a b) resistor r=0\nC0 (a b) capacitor c=0\nL0 (a b) inductor l=0\n',
    { parameters: {} }), null)).ok, true)
  // the degenerate point also rides through as a swept suffixed value
  assert.equal((await runPreCheck(makeRun(
    'simulator lang=spectre\nparameters cpf=@@cpf@@ l_val=@@l_val@@\nC0 (a b) capacitor c=cpf\nL0 (a b) inductor l=l_val\n',
    { parameters: { cpf: ['0f'], l_val: ['0n'] } }), null)).ok, true)
})

test('a session-tightened 1nF capacitor floor blocks the fF scan', async () => {
  // The netlist uses the c=cpf idiom with suffixed values and the session
  // layer tightens c to [1n, 1uF] — every nonzero fF value must flag, so
  // the managed flow's gate throws.
  const ws = mkdtempSync(path.join(tmpdir(), 'hams-1nf-'))
  mkdirSync(path.join(ws, '.harnessams'))
  writeFileSync(path.join(ws, '.harnessams', 'sessions.json'), JSON.stringify({
    version: 2, sessions: { sessTight: { check_params: { rcl: { c: [1e-9, 1e-6] } } } },
  }))
  const runDir = path.join(ws, 'run')
  mkdirSync(runDir)
  writeFileSync(path.join(runDir, 'template.scs'),
    'simulator lang=spectre\nsimulatorOptions options temp=@@temperature@@ reltol=200u\n'
    + 'parameters temperature=27 SW=@@SW@@ cpf=@@cpf@@\nCp1 (in n2) capacitor c=cpf\nRp1 (in n2) resistor r=60.62\n'
    + 'VSW (sw 0) vsource dc=(1.8*SW)\n')
  writeFileSync(path.join(runDir, 'sweep_config.json'),
    JSON.stringify({ parameters: { SW: [0, 1], cpf: ['0f', '10f', '56f', '150f'] } }))

  const r = await runPreCheck(runDir, 'sessTight')
  assert.equal(r.params_source, 'session')
  assert.equal(r.ok, false)
  const flagged = r.errors.filter((e) => e.startsWith('rcl:') && e.includes('cpf'))
  // 10f / 56f / 150f all sit below 1e-9; the 0f degenerate point stays legal
  assert.equal(flagged.length, 3, flagged)
  assert.ok(flagged.every((e) => e.includes('outside the ideal range')), flagged)
  assert.ok(!r.errors.some((e) => e.includes('"0f"')), r.errors)
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

test('check-parameter chain: builtin <- project <- session, fail-open', () => {
  const ws = mkdtempSync(path.join(tmpdir(), 'hams-chain-'))
  mkdirSync(path.join(ws, '.harnessams'))
  // the project file carries a BOM (a zh-CN Windows hand-edit): python reads
  // the same file with utf-8-sig, so the JS chain must adopt it too
  writeFileSync(path.join(ws, '.harnessams', 'check_params.json'),
    '\uFEFF' + JSON.stringify({
      rcl: { c: [1e-15, 1e-9] }, mult: { nf: [1, 8] }, nf_warn: 8, unit_check: false,
    }))
  writeFileSync(path.join(ws, '.harnessams', 'sessions.json'), JSON.stringify({
    version: 2,
    sessions: {
      sessA: { check_params: { mult: { nf: [1, 64] }, nf_warn: 64 } },
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
  assert.equal(builtin.params.nf_warn, 50)

  const project = readCheckParamsChain(run1, 'no-such-session')
  assert.equal(project.source, 'project', 'the BOM must not read as a corrupt file')
  assert.deepEqual(project.params.rcl.c, [1e-15, 1e-9])
  assert.deepEqual(project.params.mult.nf, [1, 8])
  assert.equal(project.params.nf_warn, 8)
  assert.equal(project.params.unit_check, false)

  const sessA = readCheckParamsChain(run1, 'sessA')
  assert.equal(sessA.source, 'session')
  assert.deepEqual(sessA.params.mult.nf, [1, 64]) // session override
  assert.deepEqual(sessA.params.rcl.c, [1e-15, 1e-9]) // project layer holds
  assert.equal(sessA.params.nf_warn, 64) // session override

  const sessB = readCheckParamsChain(run1, 'sessB')
  assert.deepEqual(sessB.params.rcl.c, [1e-15, 1e-6]) // session c back to default
  assert.deepEqual(sessB.params.mult.nf, [1, 8]) // project layer holds
  assert.equal(sessB.params.nf_warn, 8) // no session key: project layer holds

  // corrupt files fail open to the layer below
  writeFileSync(path.join(ws, '.harnessams', 'check_params.json'), '{not json')
  const corrupt = readCheckParamsChain(run1, 'sessA')
  assert.equal(corrupt.source, 'session')
  assert.deepEqual(corrupt.params.rcl.c, [1e-15, 1e-6]) // default c back
  assert.equal(corrupt.params.nf_warn, 64) // session layer holds
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
  // an explicit session_id argument wins (a managed-flow caller passes it because a
  // nested tool call's exec does not reliably carry the original agent id)
  const explicit = await tool.execute({ run_dir: runDir, session_id: 'sess-direct' }, {})
  assert.equal(explicit.ok, true)
  assert.equal(explicit.params_source, 'session')
  // a delegated subagent resolves to its topmost user-visible ancestor: the
  // confirmed table that applies is the USER session's, keyed through
  // ctx.agents (the walk), never the child's own id
  const user = { id: 'sess-direct', session: { header: {} } }
  const child = {
    id: 'sess-child',
    session: { header: { origin: 'subagent', parentSession: 'sess-direct', isSeeded: true } },
  }
  const agents = new Map([['sess-direct', user]])
  const scoped = spectreTools({ agents: { get: (id) => agents.get(id) } })[0]
  const inherited = await scoped.execute({ run_dir: runDir }, { agent: child })
  assert.equal(inherited.ok, true, JSON.stringify(inherited))
  assert.equal(inherited.params_source, 'session')
  const orphan = await scoped.execute({ run_dir: runDir }, {
    agent: { ...child, session: { header: { origin: 'subagent', parentSession: 'sess-gone', isSeeded: true } } },
  })
  // no project check_params.json in this fixture: a broken lineage falls back
  // to the child's own id, hits no session entry, lands on the builtin layer
  assert.equal(orphan.params_source, 'builtin')
})

// cleanup the module-level fixtures after the whole file (each test also uses
// throwaway dirs; these two are shared across tests above)
after(() => {
  rmSync(goodRc, { recursive: true, force: true })
  rmSync(templateTStyle, { recursive: true, force: true })
})
