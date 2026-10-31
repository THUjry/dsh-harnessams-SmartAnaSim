# -*- coding: utf-8 -*-
"""PSF ASCII parser tests (run as a plain script; invoked by tests/spectre.test.mjs).

Usage: python -B tests/psf_parse_test.py
"""
import json
import math
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from spectre import spectre_psf_parse as P  # noqa: E402

DATA = ROOT / "tests" / "data" / "psf" / "point_ac" / "netlist.scs.raw"


def approx(actual, expected, tol=1e-12, label=""):
    if abs(actual - expected) > tol:
        raise AssertionError("%s: expected %r, got %r" % (label, expected, actual))


def test_parse_directory():
    data = P.parse_psf_ascii_directory(str(DATA))
    assert "ac_freq" in data, sorted(data)
    freqs = data["ac_freq"]
    assert len(freqs) == 3, freqs
    approx(freqs[0], 1e9, label="freq0")
    approx(freqs[2], 1.2e9, label="freq2")
    vout = data["ac_VOUT"]
    assert len(vout) == 3, vout
    assert isinstance(vout[0], complex)
    approx(vout[0].real, 1.0, label="vout0.re")
    # delta compression: the middle step carries no VOUT -> previous state persists
    approx(vout[1].real, 1.0, label="vout1.re (carry forward)")
    approx(vout[1].imag, 0.1, label="vout1.im (carry forward)")
    approx(vout[2].real, 1.2, label="vout2.re")
    approx(vout[2].imag, 0.12, label="vout2.im")
    approx(data["dcOp_M0:gm"], 1.906e-4, label="gm")
    approx(data["dcOp_R1:res"], 1250.0, label="res")


def test_scalar_signals():
    scalars = P.scalar_signals(str(DATA))
    assert set(scalars) == {"dcOp_M0:gm", "dcOp_M0:vth", "dcOp_R1:res"}, sorted(scalars)
    approx(scalars["dcOp_M0:vth"], 0.45, label="vth")
    capped = P.scalar_signals(str(DATA), max_keys=2)
    assert len(capped) == 2, capped


def test_encode_values():
    enc = P._encode_values([1 + 2j, complex(3, -4)])
    assert enc["complex"] is True
    assert enc["values"] == [[1.0, 2.0], [3.0, -4.0]], enc
    enc2 = P._encode_values([1.0, math.nan])
    assert enc2["complex"] is False
    assert enc2["values"][0] == 1.0 and enc2["values"][1] is None, enc2


def test_cli_work_dir():
    with tempfile.TemporaryDirectory() as tmp:
        pdir = os.path.join(tmp, "00", "netlist.scs.raw")
        os.makedirs(pdir)
        for name in ("ac.ac", "dcOp.info"):
            shutil.copyfile(str(DATA / name), os.path.join(pdir, name))
        out = os.path.join(tmp, "psf")
        rc = P.main(["--work-dir", tmp, "--out-dir", out, "--format", "csv"])
        assert rc == 0, rc
        index = json.load(open(os.path.join(out, "psf_index.json"), encoding="utf-8"))
        assert len(index["points"]) == 1, index
        assert index["points"][0]["point"] == "00"
        assert index["points"][0]["csv"], index
        payload = json.load(open(index["points"][0]["json"], encoding="utf-8"))
        assert "ac_VOUT" in payload["signals"], payload["signals"].keys()

        bad = os.path.join(tmp, "bad", "netlist.scs.raw")
        os.makedirs(bad)
        rc = P.main(["--raw-dir", bad, "--out-dir", os.path.join(tmp, "psfbad")])
        assert rc == 1, rc


def test_cli_errors():
    assert P.main(["--raw-dir", "does-not-exist"]) == 2
    assert P.main(["--work-dir", "does-not-exist"]) == 2


def main():
    test_parse_directory()
    test_scalar_signals()
    test_encode_values()
    test_cli_work_dir()
    test_cli_errors()
    print("psf parser tests ok")


if __name__ == "__main__":
    main()
