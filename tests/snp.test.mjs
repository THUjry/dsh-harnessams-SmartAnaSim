// snp_* tool tests: synthetic fixtures with analytically known devices
// (tests/synth_helper.py) plus regression against anonymized real EM data
// (tests/data). Run with bare `node --test` from the repo root.
import test from 'node:test'
import assert from 'node:assert/strict'
import { execFile } from 'node:child_process'
import { promisify } from 'node:util'
import { mkdtempSync, readFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const execFileAsync = promisify(execFile)
const ROOT = path.join(path.dirname(fileURLToPath(import.meta.url)), '..')
const EXTRACT = path.join(ROOT, 'scripts', 'snp_extract.py')
const READER = path.join(ROOT, 'scripts', 'snp_read.py')
const SYNTH = path.join(ROOT, 'tests', 'synth_helper.py')
const DATA = path.join(ROOT, 'tests', 'data')

const tmp = () => mkdtempSync(path.join(tmpdir(), 'snp-'))

async function synth(kind, name) {
  const dir = tmp()
  const file = path.join(dir, name)
  const { stdout } = await execFileAsync('python', [SYNTH, kind, file], { timeout: 120_000 })
  return { dir, file, info: JSON.parse(stdout) }
}

async function extract(file, type, args = [], env = {}) {
  const out = tmp()
  const argv = [EXTRACT, '--file', file, '--type', type, '--out-dir', out, ...args]
  const { stdout } = await execFileAsync('python', argv,
    { timeout: 300_000, env: { ...process.env, ...env } })
  const stem = path.basename(file).replace(/\.[^.]+$/, '')
  const j = JSON.parse(readFileSync(path.join(out, `${stem}_extract_${type}.json`), 'utf8'))
  return { j, stdout, out }
}

async function readSnp(file, param = 'S') {
  const out = tmp()
  const { stdout } = await execFileAsync(
    'python', [READER, '--file', file, '--param', param, '--out-dir', out],
    { timeout: 120_000 })
  const stem = path.basename(file).replace(/\.[^.]+$/, '')
  const j = JSON.parse(readFileSync(path.join(out, `${stem}_read.json`), 'utf8'))
  const csv = readFileSync(path.join(out, `${stem}_params_${param}.csv`), 'utf8')
  return { j, stdout, out, csv, stem, dir: out }
}

function approx(actual, expected, tol, label = '') {
  const ok = Math.abs(actual - expected) <= tol
  assert.ok(ok, `${label} expected ~${expected} +-${tol}, got ${actual}`)
}

const GHz = 1e9

test('tl: ideal 2-port line (touchstone + Z-params CSV agree)', async () => {
  const s = await synth('tl2', 'tl2.s2p')
  const { j } = await extract(s.file, 'tl', ['--freq-hz', '10e9', '--length-m', '2e-3'])
  approx(j.summary.Zc_ohm.re, 75, 0.5, 'Zc.re')
  approx(j.summary.Zc_ohm.im, 0, 1e-6, 'Zc.im')
  const thetaExp = 360 * j.freq_hz * 2e-3 * 1.5 / 299792458.0
  approx(j.summary.theta_deg, thetaExp, 0.2, 'theta')
  approx(j.summary.eps_eff, 2.25, 0.01, 'eps_eff')
  // same numbers from the Z-kind params CSV (conversion cross-check)
  const { j: j2 } = await extract(path.join(s.dir, 'tl2_Z.csv'), 'tl',
    ['--freq-hz', '10e9', '--length-m', '2e-3'])
  approx(j2.summary.Zc_ohm.re, 75, 0.5, 'Zc.re (csv)')
  approx(j2.summary.theta_deg, thetaExp, 0.2, 'theta (csv)')
})

test('tl: 4-port differential line (default pairing)', async () => {
  const s = await synth('tl4', 'tl4.s4p')
  const { j } = await extract(s.file, 'tl', ['--freq-hz', '20e9'])
  approx(j.summary.Zc_ohm.re, 100, 0.5, 'Zc_diff.re')
  const thetaExp = 90 * j.freq_hz / 20e9
  approx(j.summary.theta_deg, thetaExp, 0.3, 'theta')
})

test('tl: 4-port scrambled pairing found by auto adjustment', async () => {
  const s = await synth('tl4s', 'tl4s.s4p')
  const { j } = await extract(s.file, 'tl', ['--freq-hz', '20e9'])
  assert.equal(j.port_plan.origin, 'auto')
  approx(j.summary.Zc_ohm.re, 100, 0.5, 'Zc_diff.re')
})

test('transformer: 2-port file (L/Q/k/SRF/Gmax)', async () => {
  const s = await synth('xfmr2', 'x.s2p')
  const { j } = await extract(s.file, 'transformer', ['--freq-hz', '2e9'])
  approx(j.summary.L1_H, 100e-12, 2e-12, 'L1')
  approx(j.summary.L2_H, 400e-12, 8e-12, 'L2')
  approx(j.summary.k, 0.9, 0.01, 'k')
  approx(j.summary.k_abs, 0.9, 0.01, '|k|')
  const q1Exp = 2 * Math.PI * j.freq_hz * 100e-12 / 1.0
  approx(j.summary.Q1, q1Exp, 0.03, 'Q1')
  approx(j.summary.srf1_hz, s.info.srf1_hz, 0.03 * s.info.srf1_hz, 'SRF1')
  approx(j.summary.srf2_hz, s.info.srf2_hz, 0.03 * s.info.srf2_hz, 'SRF2')
  assert.ok(j.summary.gmax_db === null || j.summary.gmax_db < 0, 'passive Gmax < 0 dB')
})

test('transformer: 3-port (primary differential + secondary single-ended)', async () => {
  const s = await synth('xfmr3', 'x.s3p')
  const { j } = await extract(s.file, 'transformer', ['--freq-hz', '2e9'])
  assert.equal(j.port_plan.roles[0].mode, 'differential')
  assert.equal(j.port_plan.roles[1].mode, 'single-ended')
  approx(j.summary.L1_H, 100e-12, 2e-12, 'L1')
  approx(j.summary.L2_H, 400e-12, 8e-12, 'L2')
  approx(j.summary.k, 0.9, 0.01, 'k')
})

test('transformer: 4/5/6-port with center taps (default topologies)', async () => {
  for (const [kind, name, n] of [['xfmr4', 'x.s4p', 4], ['xfmr5', 'x.s5p', 5], ['xfmr6', 'x.s6p', 6]]) {
    const s = await synth(kind, name)
    const { j } = await extract(s.file, 'transformer', ['--freq-hz', '2e9'])
    assert.equal(j.n_ports, n)
    assert.equal(j.port_plan.origin, 'default')
    approx(j.summary.L1_H, 100e-12, 2e-12, `${kind} L1`)
    approx(j.summary.L2_H, 400e-12, 8e-12, `${kind} L2`)
    approx(j.summary.k, 0.9, 0.01, `${kind} k`)
  }
})

test('transformer: scrambled 5-port remapped by explicit coil/ct', async () => {
  const s = await synth('xfmr5_scram', 'x.s5p')
  const { j } = await extract(s.file, 'transformer',
    ['--freq-hz', '2e9', '--coil1', '2,4', '--ct1', '5', '--coil2', '1,3'])
  assert.equal(j.port_plan.origin, 'explicit')
  approx(j.summary.L1_H, 100e-12, 2e-12, 'L1')
  approx(j.summary.L2_H, 400e-12, 8e-12, 'L2')
  approx(j.summary.k, 0.9, 0.01, 'k')
})

test('transformer: extraction-time single-ended (--se)', async () => {
  const s4 = await synth('xfmr4', 'x.s4p')
  const se = await extract(s4.file, 'transformer', ['--freq-hz', '2e9', '--se', '2'])
  assert.equal(se.j.port_plan.roles[0].mode, 'single-ended')
  approx(se.j.summary.L2_H, 400e-12, 50e-12, 'L2 (primary grounded view)')
  assert.ok(se.j.summary.k > 0.8 && se.j.summary.k < 1.0, 'k sane')
  // shorted secondary leakage view on the 2-port file
  const s2 = await synth('xfmr2', 'x.s2p')
  const lk = await extract(s2.file, 'transformer', ['--freq-hz', '2e9', '--se', '2'])
  assert.equal(lk.j.port_plan.roles[1].mode, 'shorted')
  assert.ok(lk.j.summary.L1_H > 5e-12 && lk.j.summary.L1_H < 0.7e-10,
    `leakage L1 ${lk.j.summary.L1_H} much smaller than magnetizing 100pH`)
  assert.equal(lk.j.summary.k, null)
})

test('inductor: 1/2/3-port, differential and tap-grounded views', async () => {
  for (const [kind, name] of [['ind1', 'i.s1p'], ['ind2', 'i.s2p'], ['ind3', 'i.s3p']]) {
    const s = await synth(kind, name)
    const { j } = await extract(s.file, 'inductor', ['--freq-hz', '2e9'])
    approx(j.summary.L_H, 200e-12, 4e-12, `${kind} L`)
    const qExp = 2 * Math.PI * j.freq_hz * 200e-12 / 1.5
    approx(j.summary.Q, qExp, 0.05, `${kind} Q`)
    approx(j.summary.srf_hz, s.info.srf_hz, 0.03 * s.info.srf_hz, `${kind} SRF`)
  }
  // grounding the center tap changes nothing for the decoupled synthetic tap
  const s = await synth('ind3', 'i3.s3p')
  const g = await extract(s.file, 'inductor', ['--freq-hz', '2e9', '--se', '3'])
  assert.equal(g.j.port_plan.roles[0].ct_state, 'grounded')
  approx(g.j.summary.L_H, 200e-12, 4e-12, 'L (ct grounded)')
})

test('coupled_tl: 4-port scrambled mapping auto-detected, theory_ok=1', async () => {
  const s = await synth('ctl4s', 'c.s4p')
  const { j } = await extract(s.file, 'coupled_tl', ['--freq-hz', '30e9'])
  assert.equal(j.port_plan.origin, 'auto')
  approx(j.summary.Z0e_ohm.re, 70, 0.5, 'Z0e')
  approx(j.summary.Z0o_ohm.re, 40, 0.5, 'Z0o')
  approx(j.summary.theta_e_deg, 90 * j.freq_hz / 30e9, 0.3, 'theta_e')
  approx(j.summary.k, 30 / 110, 0.005, 'k')
  assert.equal(j.summary.theory_ok, 1)
})

test('coupled_tl: 8-port differential (default pair grouping)', async () => {
  const s = await synth('ctl8', 'c.s8p')
  const { j } = await extract(s.file, 'coupled_tl', ['--freq-hz', '30e9'])
  assert.equal(j.port_plan.origin, 'default')
  approx(j.summary.Z0e_ohm.re, 70, 0.5, 'Z0e')
  approx(j.summary.Z0o_ohm.re, 40, 0.5, 'Z0o')
  assert.equal(j.summary.theory_ok, 1)
})

test('coupled_tl: theory gate flags a non-two-mode device', async () => {
  const s = await synth('ctl4bad', 'c.s4p')
  const { j } = await extract(s.file, 'coupled_tl', ['--freq-hz', '30e9'])
  assert.equal(j.summary.theory_ok, 0)
  assert.ok(j.summary.rel_offblock > 0.05, 'residual above the gate')
})

test('real data: EMX .s4p coupler regression (vs reference key results)', async () => {
  const { j } = await extract(path.join(DATA, 'coupled_em_01.s4p'), 'coupled_tl',
    ['--freq-hz', '94e9'])
  assert.equal(j.ok, true)
  approx(j.summary.Z0e_ohm.re, 116.452, 0.3, 'Z0e')
  approx(j.summary.Z0o_ohm.re, 19.094, 0.3, 'Z0o')
  approx(j.summary.theta_e_deg, 100.514, 0.3, 'theta_e')
  approx(j.summary.theta_o_deg, 103.706, 0.3, 'theta_o')
  approx(j.summary.k, 0.7182, 0.005, 'k')
  approx(j.summary.sqrt_Z0eZ0o_ohm, 47.156, 0.1, 'sqrt(Z0e*Z0o)')
  approx(j.summary.f_theta_e90_hz, 84.2e9, 0.5e9, 'f(theta_e=90)')
  assert.equal(j.summary.theory_ok, 1)
})

test('real data: psfascii coupler regression (Lange reference)', async () => {
  const { j } = await extract(path.join(DATA, 'coupled_ref_01.sp.sp'), 'coupled_tl',
    ['--freq-hz', '94e9'])
  assert.equal(j.ok, true)
  approx(j.summary.Z0e_ohm.re, 115.059, 0.3, 'Z0e')
  approx(j.summary.Z0o_ohm.re, 19.140, 0.3, 'Z0o')
  approx(j.summary.theta_e_deg, 98.464, 0.3, 'theta_e')
  approx(j.summary.theta_o_deg, 110.076, 0.3, 'theta_o')
  approx(j.summary.k, 0.7148, 0.005, 'k')
  approx(j.summary.rel_offblock, 0.01255, 0.002, 'relative off-block')
  approx(j.summary.f_theta_e90_hz, 85.9e9, 0.5e9, 'f(theta_e=90)')
  approx(j.summary.f_theta_o90_hz, 76.9e9, 0.5e9, 'f(theta_o=90)')
})

test('real data: anonymized 5-port file parses (snp_read)', async () => {
  const { j } = await readSnp(path.join(DATA, 'five_port_em_01.s5p'))
  assert.equal(j.n_ports, 5)
  assert.equal(j.n_freqs, 100)
  assert.ok(j.reciprocity_error < 1e-10, 'reciprocal EM data')
})

test('round trip: snp_read CSV feeds snp_extract', async () => {
  const s = await synth('xfmr2', 'x.s2p')
  const r = await readSnp(s.file, 'Z')
  const direct = await extract(s.file, 'transformer', ['--freq-hz', '2e9'])
  const viaCsv = await extract(path.join(r.dir, `${r.stem}_params_Z.csv`), 'transformer',
    ['--freq-hz', '2e9'])
  approx(viaCsv.j.summary.L1_H, direct.j.summary.L1_H, 1e-15, 'L1 via CSV')
  approx(viaCsv.j.summary.k, direct.j.summary.k, 1e-12, 'k via CSV')
})

test('psfascii: S-param and Y/Z-param files parse to the same device', async () => {
  const ss = await synth('psf_s', 'p_s.sp.sp')
  const sz = await synth('psf_yz', 'p_yz.sp.sp')
  const js = await extract(ss.file, 'transformer', ['--freq-hz', '2e9'])
  const jz = await extract(sz.file, 'transformer', ['--freq-hz', '2e9'])
  approx(js.j.summary.L1_H, 100e-12, 2e-12, 'L1 (S file)')
  approx(jz.j.summary.L1_H, 100e-12, 2e-12, 'L1 (YZ file)')
  approx(js.j.summary.k, 0.9, 0.01, 'k (S file)')
  approx(jz.j.summary.k, 0.9, 0.01, 'k (YZ file)')
})

test('touchstone: 2-port v1 ordering is S11,S21,S12,S22', async () => {
  const s = await synth('s2pasym', 'a.s2p')
  const { csv } = await readSnp(s.file, 'S')
  const rows = {}
  for (const line of csv.split('\n')) {
    if (!line || line.startsWith('#') || line.startsWith('freq_hz')) continue
    const [f, i, jj, re, im] = line.split(',')
    rows[`${i}${jj}`] = [parseFloat(re), parseFloat(im)]
  }
  const close = (name, got, exp) => {
    assert.ok(Math.abs(got[0] - exp[0]) < 1e-9 && Math.abs(got[1] - exp[1]) < 1e-9,
      `${name}: expected ${exp}, got ${got}`)
  }
  close('S21', rows['21'], s.info.s21)
  close('S12', rows['12'], s.info.s12)
  assert.notDeepEqual(rows['21'], rows['12'], 'non-reciprocal fixture: S21 != S12')
})

test('backend parity: SNP_FORCE_FALLBACK=1 matches numpy results', async () => {
  const s = await synth('ctl4s', 'c.s4p')
  const a = await extract(s.file, 'coupled_tl', ['--freq-hz', '30e9'])
  const b = await extract(s.file, 'coupled_tl', ['--freq-hz', '30e9'],
    { SNP_FORCE_FALLBACK: '1' })
  assert.equal(b.j.backend, 'fallback')
  approx(b.j.summary.Z0e_ohm.re, a.j.summary.Z0e_ohm.re, 1e-6, 'Z0e parity')
  approx(b.j.summary.Z0o_ohm.re, a.j.summary.Z0o_ohm.re, 1e-6, 'Z0o parity')
  approx(b.j.summary.theta_e_deg, a.j.summary.theta_e_deg, 1e-6, 'theta parity')
  const sx = await synth('xfmr2', 'x.s2p')
  const fa = await extract(sx.file, 'transformer', ['--freq-hz', '2e9'])
  const fb = await extract(sx.file, 'transformer', ['--freq-hz', '2e9'],
    { SNP_FORCE_FALLBACK: '1' })
  approx(fb.j.summary.k, fa.j.summary.k, 1e-9, 'k parity')
  approx(fb.j.summary.srf1_hz, fa.j.summary.srf1_hz, 1e3, 'SRF parity')
})
