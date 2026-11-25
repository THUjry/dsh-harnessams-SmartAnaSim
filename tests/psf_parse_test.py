# -*- coding: utf-8 -*-
"""PSF ASCII parser tests (run as a plain script; invoked by tests/spectre.test.mjs).

Covers the two-layer contract: the soft entry (PsfResult folding, never
raising), one-level STRUCT flattening, and the hard accessors.
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
SP_FILE = ROOT / "tests" / "data" / "coupled_ref_01.sp.sp"  # 4-port, 50->150 GHz

STRUCT_INFO = """TYPE
dc
VALUE
"M0:gm" "S" 1.906000e-04
"M1" { "gm" 2.500000e-04 "gds" 1.100000e-05 }
"M2" "vth" 4.500000e-01 "vgs" 6.000000e-01
"PLAIN" 3.5
"NAME" "just-a-string"
"WEIRD" [1 2 3]
END
"""


def approx(actual, expected, tol=1e-12, label=""):
    if abs(actual - expected) > tol:
        raise AssertionError("%s: expected %r, got %r" % (label, expected, actual))


def raises(match, fn, *args):
    try:
        fn(*args)
    except ValueError as exc:
        if match not in str(exc):
            raise AssertionError("expected %r in %r" % (match, str(exc)))
        return
    raise AssertionError("expected ValueError with %r" % match)


def test_parse_directory():
    res = P.parse_psf_ascii_directory(str(DATA))
    assert res.ok and res.status == "success", (res.status, res.errors, res.warnings)
    data = res.data
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
    # per-file metadata feeds diagnostics (post_check consumes it)
    names = [f["name"] for f in res.metadata["files"]]
    assert "ac.ac" in names and "dcOp.info" in names, names
    ac_rec = [f for f in res.metadata["files"] if f["name"] == "ac.ac"][0]
    assert ac_rec["kind"] == "swept" and ac_rec["sweep_var"] == "freq" and ac_rec["prefix"] == "ac_"


def test_soft_failures():
    res = P.parse_psf_ascii_file(os.path.join(str(DATA), "no-such.info"))
    assert res.status == "error" and res.ok is False
    assert any("not found" in e for e in res.errors), res.errors
    with tempfile.TemporaryDirectory() as tmp:
        empty = os.path.join(tmp, "empty.info")
        open(empty, "w", encoding="utf-8").close()
        res = P.parse_psf_ascii_file(empty)
        assert res.status == "failure" and any("empty" in e for e in res.errors)

        junk = os.path.join(tmp, "junk.info")
        with open(junk, "w", encoding="utf-8", newline="\n") as f:
            f.write("hello\nworld\n")
        res = P.parse_psf_ascii_file(junk)
        assert res.status == "failure" and any("VALUE" in e for e in res.errors)

        # one bad + one good file in a raw dir -> partial: the good data parses,
        # the bad file folds into errors and never stops the merge
        raw = os.path.join(tmp, "netlist.scs.raw")
        os.makedirs(raw)
        shutil.copyfile(str(DATA / "ac.ac"), os.path.join(raw, "ac.ac"))
        open(os.path.join(raw, "dcOp.info"), "w", encoding="utf-8").close()
        res = P.parse_psf_ascii_directory(raw)
        assert res.status == "partial" and res.data, (res.status, res.errors)
        assert "ac_VOUT" in res.data
        assert any("empty" in e for e in res.errors)


def test_struct_flattening():
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "dcOpStruct.info")
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(STRUCT_INFO)
        res = P.parse_psf_ascii_file(path)
        assert res.status == "partial", (res.status, res.warnings)
        d = res.data
        # the already-flat Spectre operating-point shape keeps working
        approx(d["M0:gm"], 1.906e-4, label="flat gm")
        # one-level STRUCT flattening: braced and bare member sequences
        approx(d["M1:gm"], 2.5e-4, label="struct gm")
        approx(d["M1:gds"], 1.1e-5, label="struct gds")
        approx(d["M2:vth"], 0.45, label="bare member vth")
        approx(d["M2:vgs"], 0.6, label="bare member vgs")
        # plain scalars and strings survive untouched
        assert d["PLAIN"] == 3.5 and d["NAME"] == "just-a-string"
        # what fits no shape becomes a warning, not silent loss
        assert len(res.warnings) == 1 and "unparsed VALUE line" in res.warnings[0], res.warnings
        # the worker helper picks the flattened finite floats up
        scalars = P.scalar_signals(tmp)
        assert scalars["dcOpStruct_M1:gds"] == 1.1e-5, scalars
        assert "dcOpStruct_NAME" not in scalars


def test_scalar_signals():
    scalars = P.scalar_signals(str(DATA))
    assert set(scalars) == {"dcOp_M0:gm", "dcOp_M0:vth", "dcOp_R1:res"}, sorted(scalars)
    approx(scalars["dcOp_M0:vth"], 0.45, label="vth")
    capped = P.scalar_signals(str(DATA), max_keys=2)
    assert len(capped) == 2, capped


def test_accessors():
    data = {"a": 1.5, "b": True, "c": float("inf"), "d": 1 + 2j,
            "e": [1.0, 2.0], "f": [1.0, float("nan")], "g": [True], "h": []}
    assert P.scalar(data, "a") == 1.5
    raises("is a bool", P.scalar, data, "b")
    raises("not finite", P.scalar, data, "c")
    raises("complex", P.scalar, data, "d")
    raises("not found", P.scalar, data, "zz")
    assert P.vector(data, "e") == [complex(1.0), complex(2.0)]
    raises("empty list", P.vector, data, "h")
    raises("not finite", P.vector, data, "f")
    raises("is a bool", P.vector, data, "g")
    raises("not found", P.vector, data, "zz")
    # axis = vector + real + strictly increasing (composed rules)
    assert P.axis({"t": [1.0, 2.0, 3.5]}, "t") == [1.0, 2.0, 3.5]
    raises("must be real", P.axis, {"t": [1.0, complex(2.0, 0.5)]}, "t")
    raises("strictly increasing", P.axis, {"t": [1.0, 1.0]}, "t")
    raises("strictly increasing", P.axis, {"t": [2.0, 1.0]}, "t")
    assert P.frequency_hz({"freq": [1.0, 2.0]}) == [1.0, 2.0]
    raises("not found", P.frequency_hz, {"x": [1.0]})
    # read_psf_ascii: the contract -> exception conversion point
    bad = P.parse_psf_ascii_file(os.path.join(str(DATA), "no-such.info"))
    raises("PSF ASCII file not found", P.read_psf_ascii, bad)
    ok = P.parse_psf_ascii_file(str(DATA / "dcOp.info"))
    assert P.read_psf_ascii(ok) is ok.data


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
        p0 = index["points"][0]
        assert p0["point"] == "00" and p0["status"] == "success" and p0["csv"]
        payload = json.load(open(p0["json"], encoding="utf-8"))
        assert payload["status"] == "success" and payload["errors"] == []
        assert "ac_VOUT" in payload["signals"], payload["signals"].keys()

        # a point WITHOUT a raw dir stays in the index with its error (never skipped)
        os.makedirs(os.path.join(tmp, "01"))
        rc = P.main(["--work-dir", tmp, "--out-dir", out])
        assert rc == 0, rc
        index = json.load(open(os.path.join(out, "psf_index.json"), encoding="utf-8"))
        assert len(index["points"]) == 2, index
        p1 = [p for p in index["points"] if p["point"] == "01"][0]
        assert p1["status"] == "error" and p1["json"] is None
        assert index["errors"] == [{"point": "01", "error": "no *.raw directory"}], index["errors"]

        bad = os.path.join(tmp, "bad", "netlist.scs.raw")
        os.makedirs(bad)
        rc = P.main(["--raw-dir", bad, "--out-dir", os.path.join(tmp, "psfbad")])
        assert rc == 1, rc
        payload = json.load(open(os.path.join(tmp, "psfbad", "bad_psf.json"), encoding="utf-8"))
        assert payload["status"] == "failure" and payload["errors"], payload


def test_cli_errors():
    assert P.main(["--raw-dir", "does-not-exist"]) == 2
    assert P.main(["--work-dir", "does-not-exist"]) == 2
    # a standalone .sp psfascii file parses with outputs beside it
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "solo")
        rc = P.main(["--raw-dir", str(SP_FILE), "--out-dir", out])
        assert rc == 0, rc
        payload = json.load(open(os.path.join(out, "coupled_ref_01.sp_psf.json"), encoding="utf-8"))
        assert payload["status"] == "success", payload
        assert "freq" in payload["signals"] and "s21" in payload["signals"]


def test_sp_directory_flatten():
    # sp.sp inside a raw directory joins the merged signals: freq + s11..s44
    # complex curves (delegated to snp_common.parse_psfascii, S > Z > Y).
    with tempfile.TemporaryDirectory() as tmp:
        raw = os.path.join(tmp, "netlist.scs.raw")
        os.makedirs(raw)
        shutil.copyfile(str(SP_FILE), os.path.join(raw, "sp.sp"))
        res = P.parse_psf_ascii_directory(raw)
        data = res.data
        assert res.status == "success", res.errors
        assert "freq" in data, sorted(data)
        expected = ["s%d%d" % (i, j) for i in range(1, 5) for j in range(1, 5)]
        assert all(k in data for k in expected), sorted(data)
        assert len(data["freq"]) == 101, len(data["freq"])
        approx(data["freq"][0], 50e9, label="sp freq0")
        assert isinstance(data["s21"][0], complex)
        assert len(data["s21"]) == len(data["freq"])
        # the flatten merges with the classic analyses present in the same dir
        for name in ("ac.ac", "dcOp.info"):
            shutil.copyfile(str(DATA / name), os.path.join(raw, name))
        merged = P.parse_psf_ascii_directory(raw).data
        assert "ac_VOUT" in merged and "s21" in merged, sorted(merged)


def main():
    test_parse_directory()
    test_soft_failures()
    test_struct_flattening()
    test_scalar_signals()
    test_accessors()
    test_encode_values()
    test_cli_work_dir()
    test_cli_errors()
    test_sp_directory_flatten()
    print("psf parser tests ok")


if __name__ == "__main__":
    main()
