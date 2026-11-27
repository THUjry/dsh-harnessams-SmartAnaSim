# -*- coding: utf-8 -*-
"""snp_extract numeric regressions (run as a plain script; invoked by tests/snp.test.mjs).

Single-process consolidation of the numeric assertions, one python CLI chain
(synth -> read -> extract) per case: fixtures are generated through
tests/synth_helper.py in-process, the reader
and the extractor run through their main(argv) in-process, and every
tolerance carries over unchanged. Regression against the anonymized real EM
data in tests/data keeps its reference values; the CLI 0/1/2 exit contract,
the stderr warning texts, and the numfallback backend parity are asserted
here too (the psf_parse_test.py pattern).
Usage: python -B tests/snp_extract_test.py
"""
import contextlib
import importlib.util
import io
import json
import math
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "snp"))
sys.path.insert(0, str(ROOT / "tests"))
# all top-level imports, the one module identity synth_helper itself uses
import snp_extract as SE  # noqa: E402
import snp_read as SR  # noqa: E402
import synth_helper as sh  # noqa: E402

DATA = ROOT / "tests" / "data"
C_LIGHT = 299792458.0
TWO_PI = 2.0 * math.pi


def approx(actual, expected, tol, label=""):
    if abs(actual - expected) > tol:
        raise AssertionError("%s: expected ~%r +-%r, got %r" % (label, expected, tol, actual))


def synth(kind, name, tmp):
    """Generate a synthetic fixture in-process; returns (path, ground truth)."""
    out = Path(tmp) / name
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = sh.main(["synth_helper.py", kind, str(out)])
    assert rc == 0, (kind, rc)
    return out, json.loads(buf.getvalue())


def read_snp(file, param, out_dir):
    """The documented user chain: snp_read converts raw files to a params CSV."""
    out = Path(out_dir)
    buf, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
        rc = SR.main(["--file", str(file), "--param", param, "--out-dir", str(out)])
    assert rc == 0, (rc, err.getvalue())
    stem = Path(file).stem
    for kind in ("S", "Z", "Y"):
        suf = "_params_%s" % kind
        if stem.endswith(suf):
            stem = stem[: -len(suf)]
    summary = json.loads((out / ("%s_read.json" % stem)).read_text(encoding="utf-8"))
    csv = out / ("%s_params_%s.csv" % (stem, param))
    return summary, csv


def to_csv(file, param, out_dir):
    _summary, csv = read_snp(file, param, out_dir)
    return csv


def run_extract(file, dev, extra, out_dir):
    """Run snp_extract.main() in-process; returns (rc, stdout, stderr)."""
    argv = ["--file", str(file), "--type", dev, "--out-dir", str(out_dir)] + list(extra)
    buf, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
        rc = SE.main(argv)
    return rc, buf.getvalue(), err.getvalue()


def extract(file, dev, extra, out_dir, want_rc=0):
    rc, _stdout, stderr = run_extract(file, dev, extra, out_dir)
    assert rc == want_rc, "rc %r != %r (stderr: %s)" % (rc, want_rc, stderr)
    stem = Path(file).stem
    j = json.loads((Path(out_dir) / ("%s_extract_%s.json" % (stem, dev)))
                   .read_text(encoding="utf-8"))
    return j


def approx_xfmr(j, label=""):
    approx(j["summary"]["L1_H"], 100e-12, 2e-12, label + "L1")
    approx(j["summary"]["L2_H"], 400e-12, 8e-12, label + "L2")
    approx(j["summary"]["k"], 0.9, 0.01, label + "k")


def test_tl_2port():
    # S-kind and Z-kind params CSVs agree: the S-kind converts through to_z
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        f, _ = synth("tl2", "tl2.s2p", tmp)
        j = extract(to_csv(f, "Z", tmp / "read"), "tl",
                    ["--freq-hz", "10e9", "--length-m", "2e-3"], tmp / "out")
        approx(j["summary"]["Zc_ohm"]["re"], 75.0, 0.5, "Zc.re")
        approx(j["summary"]["Zc_ohm"]["im"], 0.0, 1e-6, "Zc.im")
        theta_exp = 360.0 * j["freq_hz"] * 2e-3 * 1.5 / C_LIGHT
        approx(j["summary"]["theta_deg"], theta_exp, 0.2, "theta")
        approx(j["summary"]["eps_eff"], 2.25, 0.01, "eps_eff")
        j2 = extract(to_csv(f, "S", tmp / "read"), "tl",
                     ["--freq-hz", "10e9", "--length-m", "2e-3"], tmp / "out")
        approx(j2["summary"]["Zc_ohm"]["re"], 75.0, 0.5, "Zc.re (S-kind csv)")
        approx(j2["summary"]["theta_deg"], theta_exp, 0.2, "theta (S-kind csv)")


def test_tl_4port():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        f, _ = synth("tl4", "tl4.s4p", tmp)
        j = extract(to_csv(f, "Z", tmp / "read"), "tl", ["--freq-hz", "20e9"], tmp / "out")
        approx(j["summary"]["Zc_ohm"]["re"], 100.0, 0.5, "Zc_diff.re")
        approx(j["summary"]["theta_deg"], 90.0 * j["freq_hz"] / 20e9, 0.3, "theta")
        # scrambled pairing is detected and auto-corrected
        fs, _ = synth("tl4s", "tl4s.s4p", tmp)
        js = extract(to_csv(fs, "Z", tmp / "read"), "tl", ["--freq-hz", "20e9"], tmp / "out")
        assert js["port_plan"]["origin"] == "auto", js["port_plan"]["origin"]
        approx(js["summary"]["Zc_ohm"]["re"], 100.0, 0.5, "Zc_diff.re (auto)")


def test_transformer_2port():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        f, info = synth("xfmr2", "x2.s2p", tmp)
        j = extract(to_csv(f, "Z", tmp / "read"), "transformer",
                    ["--freq-hz", "2e9"], tmp / "out")
        approx_xfmr(j, "xfmr2 ")
        approx(j["summary"]["k_abs"], 0.9, 0.01, "|k|")
        approx(j["summary"]["Q1"], TWO_PI * j["freq_hz"] * 100e-12 / 1.0, 0.03, "Q1")
        approx(j["summary"]["srf1_hz"], info["srf1_hz"], 0.03 * info["srf1_hz"], "SRF1")
        approx(j["summary"]["srf2_hz"], info["srf2_hz"], 0.03 * info["srf2_hz"], "SRF2")
        assert j["summary"]["gmax_db"] is None or j["summary"]["gmax_db"] < 0, \
            j["summary"]["gmax_db"]


def test_transformer_topology_matrix():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        # 3-port: primary differential + secondary single-ended
        f3, _ = synth("xfmr3", "x3.s3p", tmp)
        j3 = extract(to_csv(f3, "Z", tmp / "read"), "transformer",
                     ["--freq-hz", "2e9"], tmp / "out")
        assert j3["port_plan"]["roles"][0]["mode"] == "differential"
        assert j3["port_plan"]["roles"][1]["mode"] == "single-ended"
        approx_xfmr(j3, "xfmr3 ")
        # 4/5/6-port with center taps: the default topologies
        for kind, name, n in (("xfmr4", "x4.s4p", 4), ("xfmr5", "x5.s5p", 5),
                              ("xfmr6", "x6.s6p", 6)):
            f, _ = synth(kind, name, tmp)
            j = extract(to_csv(f, "Z", tmp / "read"), "transformer",
                        ["--freq-hz", "2e9"], tmp / "out")
            assert j["n_ports"] == n, (kind, j["n_ports"])
            assert j["port_plan"]["origin"] == "default", (kind, j["port_plan"]["origin"])
            approx_xfmr(j, kind + " ")
        # scrambled 5-port remapped by explicit coil/ct
        fs, _ = synth("xfmr5_scram", "x5s.s5p", tmp)
        js = extract(to_csv(fs, "Z", tmp / "read"), "transformer",
                     ["--freq-hz", "2e9", "--coil1", "2,4", "--ct1", "5",
                      "--coil2", "1,3"], tmp / "out")
        assert js["port_plan"]["origin"] == "explicit", js["port_plan"]["origin"]
        approx_xfmr(js, "xfmr5_scram ")


def test_transformer_single_ended():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        f4, _ = synth("xfmr4", "x4.s4p", tmp)
        se = extract(to_csv(f4, "Z", tmp / "read"), "transformer",
                     ["--freq-hz", "2e9", "--se", "2"], tmp / "out")
        assert se["port_plan"]["roles"][0]["mode"] == "single-ended"
        approx(se["summary"]["L2_H"], 400e-12, 50e-12, "L2 (primary grounded view)")
        assert 0.8 < se["summary"]["k"] < 1.0, se["summary"]["k"]
        # shorted secondary leakage view on the 2-port file
        f2, _ = synth("xfmr2", "x2.s2p", tmp)
        lk = extract(to_csv(f2, "Z", tmp / "read"), "transformer",
                     ["--freq-hz", "2e9", "--se", "2"], tmp / "out")
        assert lk["port_plan"]["roles"][1]["mode"] == "shorted"
        assert 5e-12 < lk["summary"]["L1_H"] < 0.7e-10, \
            "leakage L1 %r much smaller than magnetizing 100pH" % lk["summary"]["L1_H"]
        assert lk["summary"]["k"] is None


def test_inductor():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        for kind, name in (("ind1", "i.s1p"), ("ind2", "i.s2p"), ("ind3", "i.s3p")):
            f, info = synth(kind, name, tmp)
            j = extract(to_csv(f, "Z", tmp / "read"), "inductor",
                        ["--freq-hz", "2e9"], tmp / "out")
            approx(j["summary"]["L_H"], 200e-12, 4e-12, kind + " L")
            approx(j["summary"]["Q"], TWO_PI * j["freq_hz"] * 200e-12 / 1.5, 0.05, kind + " Q")
            approx(j["summary"]["srf_hz"], info["srf_hz"], 0.03 * info["srf_hz"], kind + " SRF")
        # grounding the center tap changes nothing for the decoupled synthetic tap
        f, _ = synth("ind3", "i3.s3p", tmp)
        g = extract(to_csv(f, "Z", tmp / "read"), "inductor",
                    ["--freq-hz", "2e9", "--se", "3"], tmp / "out")
        assert g["port_plan"]["roles"][0]["ct_state"] == "grounded"
        approx(g["summary"]["L_H"], 200e-12, 4e-12, "L (ct grounded)")


def test_coupled_tl():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        # scrambled 4-port mapping auto-detected, theory holds
        fs, _ = synth("ctl4s", "c.s4p", tmp)
        j = extract(to_csv(fs, "Z", tmp / "read"), "coupled_tl",
                    ["--freq-hz", "30e9"], tmp / "out")
        assert j["port_plan"]["origin"] == "auto"
        approx(j["summary"]["Z0e_ohm"]["re"], 70.0, 0.5, "Z0e")
        approx(j["summary"]["Z0o_ohm"]["re"], 40.0, 0.5, "Z0o")
        approx(j["summary"]["theta_e_deg"], 90.0 * j["freq_hz"] / 30e9, 0.3, "theta_e")
        approx(j["summary"]["k"], 30.0 / 110.0, 0.005, "k")
        assert j["summary"]["theory_ok"] == 1
        # 8-port differential: default pair grouping, same synthetic line params
        f8, _ = synth("ctl8", "c8.s8p", tmp)
        j8 = extract(to_csv(f8, "Z", tmp / "read"), "coupled_tl",
                     ["--freq-hz", "30e9"], tmp / "out")
        assert j8["port_plan"]["origin"] == "default"
        approx(j8["summary"]["Z0e_ohm"]["re"], 70.0, 0.5, "Z0e (8p)")
        approx(j8["summary"]["Z0o_ohm"]["re"], 40.0, 0.5, "Z0o (8p)")
        assert j8["summary"]["theory_ok"] == 1
        # the theory gate flags a non-two-mode device
        fb, _ = synth("ctl4bad", "cbad.s4p", tmp)
        jb = extract(to_csv(fb, "Z", tmp / "read"), "coupled_tl",
                     ["--freq-hz", "30e9"], tmp / "out")
        assert jb["summary"]["theory_ok"] == 0
        assert jb["summary"]["rel_offblock"] > 0.05, "residual above the gate"


def test_real_emx_coupler():
    # vs reference key results
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        j = extract(to_csv(DATA / "coupled_em_01.s4p", "Z", tmp / "read"), "coupled_tl",
                    ["--freq-hz", "94e9"], tmp / "out")
        assert j["ok"] is True
        approx(j["summary"]["Z0e_ohm"]["re"], 116.452, 0.3, "Z0e")
        approx(j["summary"]["Z0o_ohm"]["re"], 19.094, 0.3, "Z0o")
        approx(j["summary"]["theta_e_deg"], 100.514, 0.3, "theta_e")
        approx(j["summary"]["theta_o_deg"], 103.706, 0.3, "theta_o")
        approx(j["summary"]["k"], 0.7182, 0.005, "k")
        approx(j["summary"]["sqrt_Z0eZ0o_ohm"], 47.156, 0.1, "sqrt(Z0e*Z0o)")
        approx(j["summary"]["f_theta_e90_hz"], 84.2e9, 0.5e9, "f(theta_e=90)")
        assert j["summary"]["theory_ok"] == 1


def test_real_psfascii_coupler():
    # Lange reference
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        j = extract(to_csv(DATA / "coupled_ref_01.sp.sp", "Z", tmp / "read"), "coupled_tl",
                    ["--freq-hz", "94e9"], tmp / "out")
        assert j["ok"] is True
        approx(j["summary"]["Z0e_ohm"]["re"], 115.059, 0.3, "Z0e")
        approx(j["summary"]["Z0o_ohm"]["re"], 19.140, 0.3, "Z0o")
        approx(j["summary"]["theta_e_deg"], 98.464, 0.3, "theta_e")
        approx(j["summary"]["theta_o_deg"], 110.076, 0.3, "theta_o")
        approx(j["summary"]["k"], 0.7148, 0.005, "k")
        approx(j["summary"]["rel_offblock"], 0.01255, 0.002, "relative off-block")
        approx(j["summary"]["f_theta_e90_hz"], 85.9e9, 0.5e9, "f(theta_e=90)")
        approx(j["summary"]["f_theta_o90_hz"], 76.9e9, 0.5e9, "f(theta_o=90)")


def test_real_five_port_read():
    with tempfile.TemporaryDirectory() as tmp:
        summary, _csv = read_snp(DATA / "five_port_em_01.s5p", "S", Path(tmp) / "read")
        assert summary["n_ports"] == 5
        assert summary["n_freqs"] == 100
        assert summary["reciprocity_error"] < 1e-10, "reciprocal EM data"


def test_psfascii_s_and_yz_agree():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        fs, _ = synth("psf_s", "p_s.sp.sp", tmp)
        fz, _ = synth("psf_yz", "p_yz.sp.sp", tmp)
        js = extract(to_csv(fs, "Z", tmp / "read"), "transformer",
                     ["--freq-hz", "2e9"], tmp / "out")
        jz = extract(to_csv(fz, "Z", tmp / "read"), "transformer",
                     ["--freq-hz", "2e9"], tmp / "out")
        approx(js["summary"]["L1_H"], 100e-12, 2e-12, "L1 (S file)")
        approx(jz["summary"]["L1_H"], 100e-12, 2e-12, "L1 (YZ file)")
        approx(js["summary"]["k"], 0.9, 0.01, "k (S file)")
        approx(jz["summary"]["k"], 0.9, 0.01, "k (YZ file)")


def test_touchstone_ordering():
    # 2-port v1 ordering is S11,S21,S12,S22 (non-reciprocal fixture: S21 != S12)
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        f, info = synth("s2pasym", "a.s2p", tmp)
        _summary, csv = read_snp(f, "S", tmp / "read")
        rows = {}
        for line in csv.read_text(encoding="utf-8").splitlines():
            if not line or line.startswith("#") or line.startswith("freq_hz"):
                continue
            _freq, i, j, re, im = line.split(",")
            rows["%s%s" % (i, j)] = [float(re), float(im)]
        for name, key, exp in (("S21", "21", info["s21"]), ("S12", "12", info["s12"])):
            got = rows[key]
            assert abs(got[0] - exp[0]) < 1e-9 and abs(got[1] - exp[1]) < 1e-9, \
                (name, got, exp)
        assert rows["21"] != rows["12"], "non-reciprocal fixture: S21 != S12"


def _fallback_common():
    """A second snp_common module instance with the forced-fallback backend
    (SNP_FORCE_FALLBACK is read by _load_backend() at import time)."""
    os.environ["SNP_FORCE_FALLBACK"] = "1"
    try:
        spec = importlib.util.spec_from_file_location(
            "snp_common_fallback", ROOT / "scripts" / "snp" / "snp_common.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        os.environ.pop("SNP_FORCE_FALLBACK", None)
    assert mod.BACKEND == "fallback", mod.BACKEND
    return mod


def test_backend_parity():
    # the numfallback backend matches numpy results: the fallback instance
    # reaches snp_extract through its module-global sc (restored afterwards)
    fb = _fallback_common()
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        f, _ = synth("ctl4s", "c.s4p", tmp)
        csv = to_csv(f, "Z", tmp / "read")
        a = extract(csv, "coupled_tl", ["--freq-hz", "30e9"], tmp / "out")
        saved = SE.sc
        SE.sc = fb
        try:
            b = extract(csv, "coupled_tl", ["--freq-hz", "30e9"], tmp / "out")
        finally:
            SE.sc = saved
        assert a["backend"] == "numpy", a["backend"]
        assert b["backend"] == "fallback", b["backend"]
        approx(b["summary"]["Z0e_ohm"]["re"], a["summary"]["Z0e_ohm"]["re"], 1e-6, "Z0e parity")
        approx(b["summary"]["Z0o_ohm"]["re"], a["summary"]["Z0o_ohm"]["re"], 1e-6, "Z0o parity")
        approx(b["summary"]["theta_e_deg"], a["summary"]["theta_e_deg"], 1e-6, "theta parity")
        f2, _ = synth("xfmr2", "x2.s2p", tmp)
        csv2 = to_csv(f2, "Z", tmp / "read")
        fa = extract(csv2, "transformer", ["--freq-hz", "2e9"], tmp / "out")
        SE.sc = fb
        try:
            fb2 = extract(csv2, "transformer", ["--freq-hz", "2e9"], tmp / "out")
        finally:
            SE.sc = saved
        approx(fb2["summary"]["k"], fa["summary"]["k"], 1e-9, "k parity")
        approx(fb2["summary"]["srf1_hz"], fa["summary"]["srf1_hz"], 1e3, "SRF parity")


def test_out_of_band_and_irrelevant_flag_warnings():
    # an out-of-band target clamps to the nearest endpoint WITH a warning; a
    # coupled_tl-only flag on type tl warns too (stderr diagnostics)
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        f, _ = synth("tl2", "tl2.s2p", tmp)
        csv = to_csv(f, "Z", tmp / "read")
        rc, _out, err = run_extract(csv, "tl",
                                    ["--freq-hz", "1e12", "--theory-gate", "0.1"],
                                    tmp / "out")
        assert rc == 0, (rc, err)
        assert "--freq-hz" in err and "lies outside the data band" in err \
            and "clamping" in err, err
        assert "--theory-gate applies only to type coupled_tl" in err, err


def test_cli_input_rejections():
    # exit 2 usage/IO errors: non-CSV input (convert-first hint), non-finite
    # tokens, a negative length, a nan frequency
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        f, _ = synth("ind1", "ind_ok.s2p", tmp)
        rc, _out, err = run_extract(f, "inductor", ["--coil", "1"], tmp / "out")
        assert rc == 2 and "snp_read" in err, (rc, err)
        bad = tmp / "nan.csv"
        with open(bad, "w", encoding="utf-8", newline="\n") as fh:
            fh.write("# snp_read params dump v1\n# param=Z\n# n_ports=1\n"
                     "freq_hz,i,j,re,im\n1.0e9,1,1,nan,0\n")
        rc, _out, err = run_extract(bad, "inductor", ["--coil", "1"], tmp / "out")
        assert rc == 2 and "non-finite" in err, (rc, err)
        csv = to_csv(f, "Z", tmp / "read")
        rc, _out, err = run_extract(csv, "inductor",
                                    ["--coil", "1", "--length-m", "-0.5"], tmp / "out")
        assert rc == 2 and "--length-m" in err, (rc, err)
        rc, _out, err = run_extract(csv, "inductor",
                                    ["--coil", "1", "--freq-hz", "nan"], tmp / "out")
        assert rc == 2 and "--freq-hz" in err, (rc, err)


def main():
    test_tl_2port()
    test_tl_4port()
    test_transformer_2port()
    test_transformer_topology_matrix()
    test_transformer_single_ended()
    test_inductor()
    test_coupled_tl()
    test_real_emx_coupler()
    test_real_psfascii_coupler()
    test_real_five_port_read()
    test_psfascii_s_and_yz_agree()
    test_touchstone_ordering()
    test_backend_parity()
    test_out_of_band_and_irrelevant_flag_warnings()
    test_cli_input_rejections()
    print("snp extract tests ok")


if __name__ == "__main__":
    main()
