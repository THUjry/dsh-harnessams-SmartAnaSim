# -*- coding: utf-8 -*-
"""synth_helper.py -- generate synthetic S-parameter fixtures for tests.

Test-only asset (not shipped with the package). Builds analytically known
devices, writes them as Touchstone / psfascii / params-CSV files, and prints
the intended ground-truth summary as JSON on stdout where a comparison needs
it. The snp_* tools must recover the known values.

Device math:
  line2(Zc, gl)          uniform-line 2x2 Z: [[Zc*coth(gl), Zc*csch(gl)], ...]
  parallel R-L with shunt C:  Z = 1 / (1/(R + jwL) + jwC)
  expand(sign_rows, Zt)   n-port embedding whose sign-map reduction returns
                          Zt exactly: Zf = D' inv(DD') Zt inv(DD') D + c(I-P);
                          ports outside every sign row (center taps) end up
                          perfectly decoupled (diagonal filler only), so they
                          are exactly floating and exactly groundable.

Usage: python synth_helper.py <kind> <out-path> [extra...]
Kinds: tl2 tl4 tl4s xfmr2 xfmr3 xfmr4 xfmr5 xfmr5_scram xfmr6 ind1 ind2 ind3
       ctl4 ctl4s ctl4bad ctl8 psf_s psf_yz s2pasym
"""

import cmath
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import snp_common as sc  # noqa: E402

FMIN, FMAX, NPTS = 0.5e9, 60e9, 119
CTL_F0 = 30.0e9


def freqs(n=NPTS, lo=FMIN, hi=FMAX):
    return [lo + (hi - lo) * i / (n - 1) for i in range(n)]


def line2(zc, gl):
    sh = cmath.sinh(gl)
    co = cmath.cosh(gl) / sh
    cs = 1.0 / sh
    return [[zc * co, zc * cs], [zc * cs, zc * co]]


def rl_shuntc(r, l, c, w):
    return 1.0 / (1.0 / complex(r, w * l) + complex(0.0, w * c))


def expand(sign_rows, zt, n, filler=2.0):
    """Embed target zt (m x m) into an n x n matrix reducible via sign rows."""
    m = len(sign_rows)
    d = [[0.0] * n for _ in range(m)]
    for r, vec in enumerate(sign_rows):
        for p, s in vec.items():
            d[r][p] = s
    ddt = sc.MATMUL(d, [[d[i][j] for i in range(m)] for j in range(n)])
    ddt_inv = sc.INV(ddt)
    dt = [[d[i][j] for i in range(m)] for j in range(n)]  # D^T
    # Zf = D^T inv(DDT) zt inv(DDT) D + filler*(I - D^T inv(DDT) D)
    left = sc.MATMUL(dt, ddt_inv)
    right = sc.MATMUL(ddt_inv, d)
    core = sc.MATMUL(sc.MATMUL(left, zt), right)
    proj = sc.MATMUL(dt, right)  # D^T inv(DDT) D, n x n
    eye = [[1.0 if i == j else 0.0 for j in range(n)] for i in range(n)]
    z = [[core[i][j] + filler * (eye[i][j] - proj[i][j]) for j in range(n)]
         for i in range(n)]
    return z


def permute(z, perm):
    """z_file[i][j] = z_doc[perm[i]][perm[j]] (perm: file -> doc index)."""
    n = len(z)
    return [[z[perm[i]][perm[j]] for j in range(n)] for i in range(n)]


T4 = [[v / math.sqrt(2.0) for v in row] for row in
      [[1, 1, 0, 0], [0, 0, 1, 1], [1, -1, 0, 0], [0, 0, 1, -1]]]
T4T = [[T4[j][i] for j in range(4)] for i in range(4)]


def ctl_zdoc(f, z0e, z0o, theta0_f0, alpha=0.04, f0=CTL_F0):
    gl = complex(alpha, (math.pi / 2.0) * f / f0)
    zm = [[0j] * 4 for _ in range(4)]
    le = line2(complex(z0e, 0.0), gl)
    lo = line2(complex(z0o, 0.0), gl)
    for i in range(2):
        for j in range(2):
            zm[i][j] = le[i][j]
            zm[2 + i][2 + j] = lo[i][j]
    return sc.MATMUL(sc.MATMUL(T4T, zm), T4)


def write_touchstone(path, freq, mats, zref=50.0):
    n = len(mats[0])
    order = [(0, 0), (1, 0), (0, 1), (1, 1)] if n == 2 else \
        [(i, j) for i in range(n) for j in range(n)]
    lines = ["! synthetic test fixture (generated)", "# HZ S RI R %g" % zref]
    for f, m in zip(freq, mats):
        s = sc.z_to_s2(m, [zref] * n)
        vals = [f]
        for (i, j) in order:
            vals.extend([s[i][j].real, s[i][j].imag])
        lines.append(" ".join("%.12e" % v for v in vals[:9]))
        rest = vals[9:]
        for k in range(0, len(rest), 8):
            lines.append(" " + " ".join("%.12e" % v for v in rest[k:k + 8]))
    Path(path).write_text("\n".join(lines) + "\n", encoding="ascii", newline="\n")


def write_psf(path, freq, mats, kinds=("s",), zref=50.0):
    n = len(mats[0])
    out = ["HEADER",
           '"PSFversion" "1.00"',
           '"simulator" "spectre"',
           '"analysis type" "sp"',
           '"number of ports" %d' % n]
    for k in range(n):
        out.append('"PORT%d:r" %.15e' % (k, zref))
        out.append('"PORT%d:num" %d' % (k, k + 1))
        out.append('"PORT%d:units" "Ohm"' % k)
    out.append("TYPE")
    for nm in kinds:
        out.append('"%s-Param" COMPLEX DOUBLE PROP(' % nm.upper())
        out.append('  "key" "%s"' % ("SP" if nm == "s" else nm.upper() + "P"))
        out.append(")")
    out.append("SWEEP")
    out.append('"freq" "sweep" PROP(')
    out.append('  "units" "Hz"')
    out.append(")")
    out.append("TRACE")
    for nm in kinds:
        for j in range(n):        # emission order is irrelevant (named traces)
            for i in range(n):
                out.append('"%s%d%d" "%s-Param"' % (nm, i + 1, j + 1, nm.upper()))
    out.append("VALUE")
    for f, m in zip(freq, mats):
        out.append('"freq" %.15e' % f)
        for nm in kinds:
            if nm == "s":
                conv = sc.z_to_s2(m, [zref] * n)
            elif nm == "y":
                conv = sc.INV(m)
            else:
                conv = m
            for j in range(n):
                for i in range(n):
                    v = conv[i][j]
                    out.append('"%s%d%d" ( %.15e %.15e )' % (nm, i + 1, j + 1,
                                                             v.real, v.imag))
    out.append("END")
    Path(path).write_text("\n".join(out) + "\n", encoding="ascii", newline="\n")


def xfmr_zt(f):
    w = 2.0 * math.pi * f
    l1, l2, kk = 100e-12, 400e-12, 0.9
    z1 = rl_shuntc(1.0, l1, 300e-15, w)
    z2 = rl_shuntc(2.0, l2, 100e-15, w)
    m = kk * math.sqrt(l1 * l2)
    return [[z1, complex(0, w * m)], [complex(0, w * m), z2]]


def ind_zt(f):
    w = 2.0 * math.pi * f
    return rl_shuntc(1.5, 200e-12, 150e-15, w)


def tl2_z(f):
    zc = 75.0
    eps = 2.25
    length = 2e-3
    beta = 2.0 * math.pi * f * math.sqrt(eps) / 299792458.0
    return line2(complex(zc, 0.0), complex(0.0, beta * length))


def tl4_doc(f):
    gl = complex(0.03, (math.pi / 2.0) * f / 20e9)
    ze = line2(complex(55.0, 0.0), gl)
    zo = line2(complex(50.0, 0.0), gl)
    zm = [[0j] * 4 for _ in range(4)]
    for i in range(2):
        for j in range(2):
            zm[i][j] = ze[i][j]
            zm[2 + i][2 + j] = zo[i][j]
    return sc.MATMUL(sc.MATMUL(T4T, zm), T4)


def emit(path, freq, mats):
    write_touchstone(path, freq, mats)


def main(argv):
    if len(argv) < 3:
        print(__doc__)
        return 2
    kind, out = argv[1], Path(argv[2])
    out.parent.mkdir(parents=True, exist_ok=True)
    fr = freqs()
    info = {"kind": kind}
    if kind == "tl2":
        emit(out, fr, [tl2_z(f) for f in fr])
        sc.write_params_csv(sc.SNPData(fr, [tl2_z(f) for f in fr], "Z", [50.0] * 2,
                                       "synth", "synth tl2", []),
                             out.with_name(out.stem + "_Z.csv"))
        info = {"kind": kind, "Zc": 75.0, "eps_eff": 2.25,
                "theta_deg_at_10G": math.degrees(2 * math.pi * 10e9 * 2e-3 * 1.5 / 299792458.0)}
    elif kind == "tl4":
        emit(out, fr, [tl4_doc(f) for f in fr])
        info = {"kind": kind, "Zc_diff": 100.0, "theta_deg_at_20G": 90.0}
    elif kind == "tl4s":
        # file ports (1,2,3,4) = doc (A-left, A-right, B-left, B-right)
        emit(out, fr, [permute(tl4_doc(f), [0, 2, 1, 3]) for f in fr])
        info = {"kind": kind, "Zc_diff": 100.0, "theta_deg_at_20G": 90.0}
    elif kind == "xfmr2":
        emit(out, fr, [xfmr_zt(f) for f in fr])
        info = {"kind": kind, "L1_H": 100e-12, "L2_H": 400e-12, "k": 0.9,
                "srf1_hz": 1.0 / (2 * math.pi * math.sqrt(100e-12 * 300e-15)),
                "srf2_hz": 1.0 / (2 * math.pi * math.sqrt(400e-12 * 100e-15))}
    elif kind == "xfmr3":
        zt = [xfmr_zt(f) for f in fr]
        mats = [expand([{0: 1.0, 1: -1.0}, {2: 1.0}], z, 3) for z in zt]
        emit(out, fr, mats)
        info = {"kind": kind, "L1_H": 100e-12, "L2_H": 400e-12, "k": 0.9}
    elif kind == "xfmr4":
        zt = [xfmr_zt(f) for f in fr]
        mats = [expand([{0: 1.0, 1: -1.0}, {2: 1.0, 3: -1.0}], z, 4) for z in zt]
        emit(out, fr, mats)
        info = {"kind": kind, "L1_H": 100e-12, "L2_H": 400e-12, "k": 0.9}
    elif kind == "xfmr5":
        # default order: (1,2) primary + ct=5, (3,4) secondary
        zt = [xfmr_zt(f) for f in fr]
        mats = [expand([{0: 1.0, 1: -1.0}, {2: 1.0, 3: -1.0}], z, 5) for z in zt]
        emit(out, fr, mats)
        info = {"kind": kind, "L1_H": 100e-12, "L2_H": 400e-12, "k": 0.9}
    elif kind == "xfmr5_scram":
        # file: 1=sec-a 2=pri-a 3=sec-b 4=pri-b 5=ct
        zt = [xfmr_zt(f) for f in fr]
        doc = [expand([{1: 1.0, 3: -1.0}, {0: 1.0, 2: -1.0}], z, 5) for z in zt]
        emit(out, fr, doc)
        info = {"kind": kind, "L1_H": 100e-12, "L2_H": 400e-12, "k": 0.9,
                "coil1": "2,4", "ct1": "5", "coil2": "1,3"}
    elif kind == "xfmr6":
        zt = [xfmr_zt(f) for f in fr]
        mats = [expand([{0: 1.0, 1: -1.0}, {2: 1.0, 3: -1.0}], z, 6) for z in zt]
        emit(out, fr, mats)
        info = {"kind": kind, "L1_H": 100e-12, "L2_H": 400e-12, "k": 0.9}
    elif kind == "ind1":
        emit(out, fr, [[[ind_zt(f)]] for f in fr])
        info = {"kind": kind, "L_H": 200e-12,
                "srf_hz": 1.0 / (2 * math.pi * math.sqrt(200e-12 * 150e-15))}
    elif kind == "ind2":
        mats = [expand([{0: 1.0, 1: -1.0}], [[ind_zt(f)]], 2) for f in fr]
        emit(out, fr, mats)
        info = {"kind": kind, "L_H": 200e-12,
                "srf_hz": 1.0 / (2 * math.pi * math.sqrt(200e-12 * 150e-15))}
    elif kind == "ind3":
        mats = [expand([{0: 1.0, 1: -1.0}], [[ind_zt(f)]], 3) for f in fr]
        emit(out, fr, mats)
        info = {"kind": kind, "L_H": 200e-12,
                "srf_hz": 1.0 / (2 * math.pi * math.sqrt(200e-12 * 150e-15))}
    elif kind == "ctl4":
        emit(out, fr, [ctl_zdoc(f, 70.0, 40.0, 0) for f in fr])
        info = {"kind": kind, "Z0e": 70.0, "Z0o": 40.0, "theta_deg_at_30G": 90.0,
                "k": (70.0 - 40.0) / (70.0 + 40.0)}
    elif kind == "ctl4s":
        # tb order (IN,THRU,COUP,ISO) = doc (A-left, A-right, B-left, B-right)
        emit(out, fr, [permute(ctl_zdoc(f, 70.0, 40.0, 0), [0, 2, 1, 3]) for f in fr])
        info = {"kind": kind, "Z0e": 70.0, "Z0o": 40.0, "theta_deg_at_30G": 90.0,
                "k": (70.0 - 40.0) / (70.0 + 40.0)}
    elif kind == "ctl4bad":
        # balanced asymmetric coupling on every off-diagonal: no port grouping
        # can block-diagonalize this, so the two-mode theory fails for every
        # candidate and theory_ok must come out 0
        add = {(0, 1): 7.0, (0, 2): 12.0, (0, 3): 16.0, (1, 2): 10.0, (1, 3): 5.0, (2, 3): 9.0}
        mats = []
        for f in fr:
            z = ctl_zdoc(f, 70.0, 40.0, 0)
            for (i, j), v in add.items():
                z[i][j] = z[i][j] + v
                z[j][i] = z[j][i] + v
            mats.append(z)
        emit(out, fr, mats)
        info = {"kind": kind, "Z0e": 70.0, "Z0o": 40.0}
    elif kind == "ctl8":
        mats = [expand([{0: 1.0, 1: -1.0}, {2: 1.0, 3: -1.0},
                        {4: 1.0, 5: -1.0}, {6: 1.0, 7: -1.0}],
                       ctl_zdoc(f, 70.0, 40.0, 0), 8) for f in fr]
        emit(out, fr, mats)
        info = {"kind": kind, "Z0e": 70.0, "Z0o": 40.0, "theta_deg_at_30G": 90.0}
    elif kind == "psf_s":
        write_psf(out, fr, [xfmr_zt(f) for f in fr], kinds=("s",))
        info = {"kind": kind, "L1_H": 100e-12, "k": 0.9}
    elif kind == "psf_yz":
        write_psf(out, fr, [xfmr_zt(f) for f in fr], kinds=("y", "z"))
        info = {"kind": kind, "L1_H": 100e-12, "k": 0.9}
    elif kind == "s2pasym":
        z = [[1 + 2j, 3 + 1j], [0.5 + 0.1j, 2 + 1j]]
        emit(out, [1e9], [z])
        s = sc.z_to_s2(z, [50.0, 50.0])
        info = {"kind": kind,
                "s11": [s[0][0].real, s[0][0].imag],
                "s21": [s[1][0].real, s[1][0].imag],
                "s12": [s[0][1].real, s[0][1].imag],
                "s22": [s[1][1].real, s[1][1].imag]}
    else:
        print("unknown kind %r" % kind, file=sys.stderr)
        return 2
    print(json.dumps(info))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
