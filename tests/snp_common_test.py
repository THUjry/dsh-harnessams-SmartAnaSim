# -*- coding: utf-8 -*-
"""snp_common loader tests (run as a plain script; invoked by tests/snp.test.mjs).

Covers the 2026-11-26 robustness batch: Touchstone option-line unit scanning
(position-independent, with a non-standard-order warning), psfascii port
base unification (EMX lowercase "port<k>:" is 1-based) and multi-digit
detect_n (ports >= 10), duplicate-frequency params-CSV folding, and the
z_to_s2 single-z0 broadcast guard.
Usage: python -B tests/snp_common_test.py
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from snp import snp_common as sc  # noqa: E402


def test_touchstone_unit_order():
    # "# MA R 50 GHZ" puts the unit at token 4: it is applied (used to stay
    # HZ silently) and a warning marks the non-standard order
    with tempfile.TemporaryDirectory() as tmp:
        p = os.path.join(tmp, "ord.s2p")
        with open(p, "w", encoding="utf-8", newline="\n") as f:
            f.write("# MA R 50 GHZ\n")
            f.write("! one 2-port point\n")
            f.write("1 0.1 0 0.2 10 0.3 20 0.4 30\n")
        zd = sc.parse_touchstone(p)
        assert abs(zd.freq[0] - 1e9) < 1e-6, zd.freq  # GHZ applied, not HZ
        assert any("non-standard order" in w and "GHZ" in w for w in zd.warnings), zd.warnings
        # the standard first position stays silent
        p2 = os.path.join(tmp, "std.s2p")
        with open(p2, "w", encoding="utf-8", newline="\n") as f:
            f.write("# GHZ MA R 50\n")
            f.write("1 0.1 0 0.2 10 0.3 20 0.4 30\n")
        zd2 = sc.parse_touchstone(p2)
        assert abs(zd2.freq[0] - 1e9) < 1e-6, zd2.freq
        assert not any("non-standard order" in w for w in zd2.warnings), zd2.warnings


def test_psf_port_base_unification():
    # EMX lowercase "port<k>:r" carries a 1-based k: the no-num fallback used
    # a blanket k+1, so "port1:r 25" landed on port 2 and port 1 kept the 50
    # default. (Spectre's uppercase "PORT<k>" is 0-based and stays k+1.)
    def write(path, port_line):
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write("HEADER\n")
            f.write('"number of ports" 2\n')
            f.write(port_line + "\n")
            f.write("VALUE\n")
            f.write('"freq" 1e9\n')
            for i in (1, 2):
                for j in (1, 2):
                    f.write('"s%d%d" (0.1 0.0)\n' % (i, j))
            f.write("END\n")

    with tempfile.TemporaryDirectory() as tmp:
        emx = os.path.join(tmp, "emx.sp")
        write(emx, '"port1:r" 25')
        zd = sc.parse_psfascii(emx)
        assert zd.n_ports == 2, zd.n_ports
        assert abs(zd.z0[0] - 25.0) < 1e-12, zd.z0  # port 1, not port 2
        assert abs(zd.z0[1] - 50.0) < 1e-12, zd.z0

        spectre = os.path.join(tmp, "spectre.sp")
        write(spectre, '"PORT0:r" 25')
        zd2 = sc.parse_psfascii(spectre)
        assert abs(zd2.z0[0] - 25.0) < 1e-12, zd2.z0  # 0-based k -> port 1


def test_psf_detect_n_multi_digit():
    # ports >= 10: trace names concatenate the two port numbers ("s1010"),
    # which the single-digit regex could never see — an even digit count
    # splits half and half
    with tempfile.TemporaryDirectory() as tmp:
        p = os.path.join(tmp, "big.sp")
        with open(p, "w", encoding="utf-8", newline="\n") as f:
            f.write("HEADER\n")
            f.write('"number of ports" 10\n')
            f.write("VALUE\n")
            f.write('"freq" 1e9\n')
            for i in range(1, 11):
                for j in range(1, 11):
                    f.write('"s%d%d" (0.05 0.0)\n' % (i, j))
            f.write("END\n")
        zd = sc.parse_psfascii(p)
        assert zd.n_ports == 10, zd.n_ports
        assert len(zd.mats[0]) == 10 and len(zd.mats[0][0]) == 10
        # the 2-digit names of a small file still resolve
        p2 = os.path.join(tmp, "small.sp")
        with open(p2, "w", encoding="utf-8", newline="\n") as f:
            f.write("HEADER\n")
            f.write("VALUE\n")
            f.write('"freq" 1e9\n')
            for i in (1, 2):
                for j in (1, 2):
                    f.write('"s%d%d" (0.1 0.0)\n' % (i, j))
            f.write("END\n")
        zd2 = sc.parse_psfascii(p2)
        assert zd2.n_ports == 2, zd2.n_ports


def _write_colon_sp(path, n):
    """A colon-spelling psfascii fixture with per-cell distinct values
    (real=i/100, imag=j/100) so matrix placement is checkable cell by cell."""
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("HEADER\n")
        f.write('"number of ports" %d\n' % n)
        f.write("VALUE\n")
        f.write('"freq" 1e9\n')
        for i in range(1, n + 1):
            for j in range(1, n + 1):
                f.write('"s%d:%d" (%.2f %.2f)\n' % (i, j, i / 100.0, j / 100.0))
        f.write("END\n")


def test_psf_colon_trace_names():
    # spectre writes the COLON spelling from ten ports up ("s1:12") — the
    # old `\w+` capture dropped every such VALUE line silently, so a >= 10
    # port sp.sp read as "no S/Z/Y traces found"
    with tempfile.TemporaryDirectory() as tmp:
        p = os.path.join(tmp, "colon10.sp")
        _write_colon_sp(p, 10)
        zd = sc.parse_psfascii(p)
        assert zd.n_ports == 10, zd.n_ports
        assert len(zd.mats[0]) == 10 and len(zd.mats[0][5]) == 10
        assert abs(zd.mats[0][0][0].real - 0.01) < 1e-12, zd.mats[0][0][0]
        assert abs(zd.mats[0][4][9].real - 0.05) < 1e-12, zd.mats[0][4][9]
        assert abs(zd.mats[0][4][9].imag - 0.10) < 1e-12, zd.mats[0][4][9]
        # a 25-port deck (the 2026-11-26 incident shape) parses end to end
        p25 = os.path.join(tmp, "colon25.sp")
        _write_colon_sp(p25, 25)
        zd25 = sc.parse_psfascii(p25)
        assert zd25.n_ports == 25, zd25.n_ports
        assert abs(zd25.mats[0][24][24].real - 0.25) < 1e-12, zd25.mats[0][24][24]


def test_psf_colon_names_stay_distinct_at_11_ports():
    # at >= 11 ports the concatenated spelling is ambiguous ("s111" would be
    # both (1,11) and (11,1)) — which is WHY spectre writes colons there. The
    # colon names must map to their exact cells, not collapse into one key.
    with tempfile.TemporaryDirectory() as tmp:
        p = os.path.join(tmp, "colon11.sp")
        _write_colon_sp(p, 11)
        zd = sc.parse_psfascii(p)
        assert zd.n_ports == 11, zd.n_ports
        assert abs(zd.mats[0][0][10].real - 0.01) < 1e-12, zd.mats[0][0][10]   # (1,11)
        assert abs(zd.mats[0][10][0].real - 0.11) < 1e-12, zd.mats[0][10][0]   # (11,1)
        assert abs(zd.mats[0][10][0].imag - 0.01) < 1e-12, zd.mats[0][10][0]


def test_params_csv_duplicate_frequency():
    # a repeated frequency block used to merge silently over the first copy
    # (and emit the merged matrix twice); the first copy now wins, with a
    # warning naming the frequency
    with tempfile.TemporaryDirectory() as tmp:
        p = os.path.join(tmp, "dup.csv")
        with open(p, "w", encoding="utf-8", newline="\n") as f:
            f.write("# snp_read params dump v1\n")
            f.write("# param=S\n")
            f.write("# n_ports=2\n")
            f.write("# zref=50\n")
            f.write("freq_hz,i,j,re,im\n")
            for i, j, v in ((1, 1, 0.1), (1, 2, 0.2), (2, 1, 0.3), (2, 2, 0.4)):
                f.write("1000000000.0,%d,%d,%g,0\n" % (i, j, v))
            # duplicate block: same frequency again (0.5 overwrites nothing)
            f.write("1000000000.0,1,1,0.5,0\n")
            f.write("1000000000.0,1,2,0.6,0\n")
            f.write("1000000000.0,2,1,0.7,0\n")
            f.write("1000000000.0,2,2,0.8,0\n")
            # a second, healthy frequency
            for i in (1, 2):
                for j in (1, 2):
                    f.write("2000000000.0,%d,%d,0.9,0\n" % (i, j))
        zd = sc.parse_params_csv(p)
        assert len(zd.freq) == 2, zd.freq  # the duplicate collapsed to one
        assert abs(zd.mats[0][0][0].real - 0.1) < 1e-12, zd.mats[0]  # first kept
        assert any("duplicate" in w for w in zd.warnings), zd.warnings


def test_z_to_s2_single_z0_broadcast():
    # a single z0 entry with an n>1 matrix (params CSV "zref=50") used to
    # raise IndexError on sq[i]; it broadcasts to every port now, to_z's
    # precedent
    zmat = [[50.0 + 0j, 1.0 + 0j], [1.0 + 0j, 50.0 + 0j]]
    s = sc.z_to_s2(zmat, [50.0])
    assert len(s) == 2 and len(s[0]) == 2, s
    # and the well-fed case still matches the two-entry form
    s2 = sc.z_to_s2(zmat, [50.0, 50.0])
    assert abs(s[0][0] - s2[0][0]) < 1e-9, (s[0][0], s2[0][0])


def main():
    test_touchstone_unit_order()
    test_psf_port_base_unification()
    test_psf_detect_n_multi_digit()
    test_psf_colon_trace_names()
    test_psf_colon_names_stay_distinct_at_11_ports()
    test_params_csv_duplicate_frequency()
    test_z_to_s2_single_z0_broadcast()
    print("snp_common tests ok")


if __name__ == "__main__":
    main()
