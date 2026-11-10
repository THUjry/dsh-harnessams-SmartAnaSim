# -*- coding: utf-8 -*-
"""Spectre assembly check tests: ideal RCL ranges plus the geometry-suffix
rules that share the same line pass (run as a plain script; invoked by
tests/spectre.test.mjs).

Usage: python -B tests/spectre_checks_test.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from spectre.spectre_checks import (  # noqa: E402
    IDEAL_RCL_RANGES, check_netlist_format, parse_ideal_value,
)

SWEEP_PARAMS = {"r_val": ["1k", "2k", "5k"], "c_val": ["1n", "2n", "5n"]}

GOOD = """\
simulator lang=spectre
parameters r_val=1k c_val=1n
V1 (vin 0) vsource dc=0 mag=1
R1 (vin vout) resistor r=@@r_val@@
C1 (vout 0) capacitor c=@@c_val@@
Rlim (vin mid) res r=0.1
Rbig (mid vout) resistor r=1Meg
Cload (vout 0) cap c=1u
Lser (vin mid) inductor l=0.1p
Ltank (mid 0) ind l=10n
M1 (d g s b) nmos w=@@w_val@@ l=@@l_geo@@
ac ac start=1e3 stop=1e9 dec=50
save vout
"""
GOOD_PARAMS = dict(SWEEP_PARAMS, w_val=["2u"], l_geo=["180n"])


def errors_of(text, **kw):
    return check_netlist_format(text, **kw)[0]


def warnings_of(text, **kw):
    return check_netlist_format(text, **kw)[1]


def test_parse_ideal_value():
    cases = {
        "1k": 1e3, "2K": 2e3, "1Meg": 1e6, "1MEG": 1e6, "1M": 1e6, "2m": 2e-3,
        "0.1": 0.1, "1e-9": 1e-9, "1p": 1e-12, "4.7u": 4.7e-6, "10n": 1e-8,
        "1f": 1e-15, "0.5,": 0.5, "+2k": 2e3, "-3k": -3e3, "1e3k": 1e6,
    }
    for text, expected in cases.items():
        got = parse_ideal_value(text)
        assert got is not None and abs(got - expected) <= abs(expected) * 1e-12, \
            "parse_ideal_value(%r) = %r, want %r" % (text, got, expected)
    for text in ("", "rbase", "rbase*2", "@@r@@", "1x", "1kQ", "1pF", "k1"):
        assert parse_ideal_value(text) is None, "parse_ideal_value(%r) should be None" % text
    # bare number = base unit (no metres trap for ideal devices)
    assert parse_ideal_value("0.5") == 0.5


def test_in_range_passes():
    errors, warnings = check_netlist_format(GOOD, parameters=GOOD_PARAMS)
    assert errors == [], errors
    assert warnings == [], warnings


def test_boundary_values_inclusive():
    t = ("simulator lang=spectre\n"
         "R1 (a b) resistor r=0.1\n"
         "R2 (a b) resistor r=1M\n"
         "C1 (a b) capacitor c=1f\n"
         "C2 (a b) capacitor c=1u\n"
         "L1 (a b) inductor l=0.1p\n"
         "L2 (a b) inductor l=10n\n")
    assert errors_of(t) == [], errors_of(t)


def test_out_of_range_literals():
    cases = [
        ("R1 (a b) resistor r=0.05", "R1"),          # below R floor
        ("R1 (a b) resistor r=10M", "R1"),           # above R ceiling
        ("R1 (a b) res r=0", "R1"),                  # short
        ("C1 (a b) capacitor c=0.5f", "C1"),         # below C floor
        ("C1 (a b) capacitor c=4.7u", "C1"),         # above C ceiling
        ("L1 (a b) inductor l=0.05p", "L1"),         # below L floor
        ("L1 (a b) inductor l=100n", "L1"),          # above L ceiling
    ]
    for line, name in cases:
        t = "simulator lang=spectre\n%s\n" % line
        errs = [e for e in errors_of(t) if e.startswith("rcl:")]
        assert len(errs) == 1, "expected exactly one rcl error for %r, got %r" % (line, errs)
        assert name in errs[0] and "outside the ideal range" in errs[0], errs[0]


def test_case_sensitive_milli_vs_mega():
    assert errors_of("simulator lang=spectre\nR1 (a b) resistor r=500m\n") == []  # 0.5 ohm
    errs = errors_of("simulator lang=spectre\nR1 (a b) resistor r=500M\n")
    assert any(e.startswith("rcl:") for e in errs), errs  # 500M = 5e8 ohm > ceiling


def test_swept_placeholder_values():
    t = ("simulator lang=spectre\nparameters r_val=1k\n"
         "R1 (vin vout) resistor r=@@r_val@@\n")
    assert errors_of(t, parameters={"r_val": ["1k", "2k"]}) == []
    errs = errors_of(t, parameters={"r_val": ["1k", "9M"]})
    assert any(e.startswith("rcl:") and "9M" in e for e in errs), errs
    # numeric (suffix-less) sweep values are base-unit ohms: 0.5 ok, 0.01 out
    assert errors_of(t, parameters={"r_val": [0.5]}) == []
    assert any(e.startswith("rcl:") for e in errors_of(t, parameters={"r_val": [0.01]}))


def test_template_parameters_reference():
    t = ("simulator lang=spectre\nparameters rload=1M rbork=0.01\n"
         "R1 (a b) resistor r=rload\n"
         "R2 (a b) resistor r=rbork\n")
    errs = [e for e in errors_of(t) if e.startswith("rcl:")]
    assert len(errs) == 1 and "rbork" in errs[0], errs  # 1M in range, 0.01 below floor
    # unknown reference or expression default is skipped, not guessed
    t2 = ("simulator lang=spectre\nparameters rload=rbase*2\n"
          "R1 (a b) resistor r=rload\n")
    assert errors_of(t2) == []
    t3 = "simulator lang=spectre\nR1 (a b) resistor r=undefined_name\n"
    assert errors_of(t3) == []


def test_geometry_rules_untouched():
    # placeholder geometry values keep the u/n contract (mosfet line)
    t = ("simulator lang=spectre\nparameters w_val=2u l_geo=180n\n"
         "M1 (d g s b) nmos w=@@w_val@@ l=@@l_geo@@\n")
    assert errors_of(t, parameters={"w_val": ["2u"], "l_geo": ["180n"]}) == []
    errs = errors_of(t, parameters={"w_val": ["60"], "l_geo": ["180n"]})
    assert any(e.startswith("units:") for e in errs), errs
    # literal geometry without suffix still warns (metres trap)
    t2 = "simulator lang=spectre\nM1 (d g s b) nmos l=0.5 w=2u\n"
    assert any("metres" in w for w in warnings_of(t2)), warnings_of(t2)
    # ...but not on an ideal inductor line (l is inductance there, checked by range)
    t3 = "simulator lang=spectre\nL1 (a b) inductor l=0.5\n"
    assert warnings_of(t3) == [], warnings_of(t3)
    errs = errors_of(t3)
    assert len(errs) == 1 and errs[0].startswith("rcl:"), errs
    # other devices (vsource) never trip the geometry rules
    t4 = "simulator lang=spectre\nV1 (a 0) vsource dc=0 mag=1\n"
    assert errors_of(t4) == [] and warnings_of(t4) == []


def test_rcl_ranges_override():
    t = "simulator lang=spectre\nL1 (a b) inductor l=0.5u\n"
    assert any(e.startswith("rcl:") for e in errors_of(t))
    widened = dict(IDEAL_RCL_RANGES, l=(1e-13, 1e-6))
    assert errors_of(t, rcl_ranges=widened) == []


def test_non_rcl_instances_untouched():
    t = ("simulator lang=spectre\n"
         "XD (a b) sky13_fd_pr__nch2 w=2u l=180n\n"
         "X1 (a b) mysubckt r=5\n")
    assert errors_of(t) == [] and warnings_of(t) == []


def main():
    test_parse_ideal_value()
    test_in_range_passes()
    test_boundary_values_inclusive()
    test_out_of_range_literals()
    test_case_sensitive_milli_vs_mega()
    test_swept_placeholder_values()
    test_template_parameters_reference()
    test_geometry_rules_untouched()
    test_rcl_ranges_override()
    test_non_rcl_instances_untouched()
    print("spectre checks tests ok")


if __name__ == "__main__":
    main()
