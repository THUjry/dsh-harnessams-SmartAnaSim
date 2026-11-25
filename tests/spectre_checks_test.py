# -*- coding: utf-8 -*-
"""Spectre netlist check tests: @@ placement (parameters-statement-only
contract), the no-scientific-notation rule, sweep-parameter presence
(undeclared @@temperature@@ = warning + worker default 27), ideal RCL ranges
(expressions rejected at R/C/L positions, zero = legal degenerate), multiplier
(nf/m) gates, the geometry-suffix rules that share the same line pass, and the
check_params.json workspace defaults/switches (rcl / mult / unit_check).
Run as a plain script; invoked by tests/spectre.test.mjs.

Usage: python -B tests/spectre_checks_test.py
"""
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from common import common_workspace  # noqa: E402
from spectre.spectre_checks import (  # noqa: E402
    CheckError, IDEAL_RCL_RANGES, MULT_RANGES, check_netlist_format,
    norm_length_si, parse_ideal_value,
)

SWEEP_PARAMS = {"r_val": ["1k", "2k", "5k"], "c_val": ["1n", "2n", "5n"]}

# New-style template: @@P@@ placeholders ONLY on the parameters statement, the
# circuit references parameters by name, SI suffixes everywhere (no scientific
# notation).
GOOD = """\
simulator lang=spectre
parameters r_val=@@r_val@@ c_val=@@c_val@@ w_val=@@w_val@@ l_geo=@@l_geo@@
V1 (vin 0) vsource dc=0 mag=1
R1 (vin vout) resistor r=r_val
C1 (vout 0) capacitor c=c_val
Rlim (vin mid) res r=0.1
Rbig (mid vout) resistor r=1Meg
Cload (vout 0) cap c=1u
Lser (vin mid) inductor l=0.1p
Ltank (mid 0) ind l=10n
M1 (d g s b) nmos w=w_val l=l_geo
ac ac start=1k stop=1G dec=50
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
    # 0 is the legal degenerate element for every kind (r=0/l=0 short, c=0
    # open — the 2026-11-25 r=0 ruling extended), exempt from the floor
    assert errors_of("simulator lang=spectre\nR1 (a b) resistor r=0\n"
                     "C1 (a b) capacitor c=0\nL1 (a b) inductor l=0\n") == []


def test_case_sensitive_milli_vs_mega():
    assert errors_of("simulator lang=spectre\nR1 (a b) resistor r=500m\n") == []  # 0.5 ohm
    errs = errors_of("simulator lang=spectre\nR1 (a b) resistor r=500M\n")
    assert any(e.startswith("rcl:") for e in errs), errs  # 500M = 5e8 ohm > ceiling


def test_no_scientific_notation():
    """Exponent forms flag on any code line; identifier tails do not match."""
    t = ("simulator lang=spectre\nsimulatorOptions options reltol=2e-4\n"
         "C1 (a b) capacitor c=1.5e-13\nsp sp start=1e9 stop=40G step=100M\n")
    errs = [e for e in errors_of(t) if "scientific notation" in e]
    assert len(errs) == 3, errs
    assert any("'2e-4'" in e for e in errs), errs
    assert any("'1.5e-13'" in e for e in errs), errs
    # identifier-shaped tails (nets named n1e5 / b2e7) are not exponent matches
    assert errors_of("simulator lang=spectre\nR1 (n1e5 b) resistor r=1k\n"
                     "R2 (n1e5 b2e7) resistor r=2k\n") == []


def test_undeclared_temperature_warns():
    """An undeclared @@temperature@@ is a warning (worker default 27 degC),
    never an error; every other undeclared placeholder stays an error."""
    t = ("simulator lang=spectre\nsimulatorOptions options temp=@@temperature@@ reltol=200u\n"
         "parameters temperature=27 SW=@@SW@@\nVSW (sw 0) vsource dc=(1.8*SW)\n")
    errs, warns = check_netlist_format(t, parameters={"SW": [0, 1]})
    assert errs == [], errs
    assert any("@@temperature@@" in w and "27" in w for w in warns), warns
    # declared temperature: no warning at all
    errs, warns = check_netlist_format(t, parameters={"temperature": [27], "SW": [0, 1]})
    assert errs == [] and warns == [], (errs, warns)
    # other undeclared placeholders stay errors
    t2 = "simulator lang=spectre\nparameters SW=@@SW@@ OTHER=@@OTHER@@\nV1 (a 0) vsource dc=SW\n"
    errs = errors_of(t2, parameters={"SW": [0, 1]})
    assert any("placeholders not declared" in e and "OTHER" in e for e in errs), errs


def test_placeholder_placement():
    """@@ lives on the parameters statement only; the sole exemption is
    @@temperature@@ on a simulatorOptions line."""
    t = ("simulator lang=spectre\nparameters SW=@@SW@@\n"
         "R1 (a b) resistor r=@@SW@@\n")
    errs = errors_of(t, parameters={"SW": [0, 1]})
    assert any(e.startswith("format:") and "outside the parameters statement" in e
               for e in errs), errs
    t2 = ("simulator lang=spectre\n"
          "simulatorOptions options temp=@@temperature@@ reltol=200u\n"
          "parameters temperature=27\n")
    assert errors_of(t2, parameters={"temperature": [27]}) == [], errors_of(t2)
    t3 = "simulator lang=spectre\nsimulatorOptions options temp=@@FSTART@@\n"
    errs = errors_of(t3)
    assert any("@@FSTART@@ outside the parameters statement" in e for e in errs), errs
    # analysis statements are circuit-side: no @@ there either
    t4 = "simulator lang=spectre\nac ac start=@@FSTART@@ stop=1G dec=50\n"
    errs = errors_of(t4)
    assert any("outside the parameters statement" in e for e in errs), errs


def test_sweep_param_presence():
    t = "simulator lang=spectre\nparameters SW=@@SW@@\nV1 (a 0) vsource dc=SW\n"
    # swept parameter without its @@P@@ placeholder
    errs = errors_of(t, parameters={"SW": [0, 1], "Cload": ["1p"]})
    assert any("without a @@P@@ placeholder" in e and "Cload" in e for e in errs), errs
    # placeholder without its config entry (reaches spectre as literal text)
    t2 = "simulator lang=spectre\nparameters SW=@@SW@@ OTHER=@@OTHER@@\n"
    errs = errors_of(t2, parameters={"SW": [0, 1]})
    assert any("placeholders not declared" in e and "OTHER" in e for e in errs), errs
    # circuit name references must be declared
    t3 = "simulator lang=spectre\nR1 (a b) resistor r=Rx\n"
    errs = errors_of(t3)
    assert any("no 'parameters' declaration" in e for e in errs), errs


def test_swept_name_reference_values():
    t = ("simulator lang=spectre\nparameters r_val=@@r_val@@\n"
         "R1 (vin vout) resistor r=r_val\n")
    assert errors_of(t, parameters={"r_val": ["1k", "2k"]}) == []
    errs = errors_of(t, parameters={"r_val": ["1k", "9M"]})
    assert any(e.startswith("rcl:") and "9M" in e for e in errs), errs
    # numeric (suffix-less) sweep values are base-unit ohms: 0.5 ok, 0.01 out
    assert errors_of(t, parameters={"r_val": [0.5]}) == []
    assert any(e.startswith("rcl:") for e in errors_of(t, parameters={"r_val": [0.01]}))
    # the template's declared default is checked when the name is not swept
    t2 = ("simulator lang=spectre\nparameters r_val=1k rbork=0.01\n"
          "R1 (a b) resistor r=r_val\n"
          "R2 (a b) resistor r=rbork\n")
    errs = [e for e in errors_of(t2) if e.startswith("rcl:")]
    assert len(errs) == 1 and "rbork" in errs[0], errs  # 1k in range, 0.01 below floor
    # expression defaults are skipped, not guessed
    t3 = ("simulator lang=spectre\nparameters rload=rbase*2\n"
          "R1 (a b) resistor r=rload\n")
    assert errors_of(t3) == []
    # expressions at R/C/L value positions are rejected outright (2026-11-25
    # ruling)
    t4 = ("simulator lang=spectre\nparameters k=2\n"
          "R1 (a b) resistor r=(k*rx)\n")
    errs = errors_of(t4)
    assert any("expressions are not allowed" in e for e in errs), errs


def test_multiplier_gates():
    # name reference: swept values are counts in range
    t = ("simulator lang=spectre\nparameters nf=@@nf@@\n"
         "M1 (d g s b) nmos l=0.15u w=1u nf=nf\n")
    assert errors_of(t, parameters={"nf": [31]}) == []
    errs = errors_of(t, parameters={"nf": [200]})
    assert any(e.startswith("mult:") for e in errs), errs
    errs = errors_of(t, parameters={"nf": [2.5]})
    assert any(e.startswith("mult:") and "not a positive integer" in e for e in errs), errs
    # literal multipliers on any instance line, ideal RCL included
    t2 = "simulator lang=spectre\nM1 (d g s b) nmos l=0.15u w=1u nf=200\n"
    assert any(e.startswith("mult:") for e in errors_of(t2)), errors_of(t2)
    t3 = "simulator lang=spectre\nM1 (d g s b) nmos l=0.15u w=1u nf=2.5\n"
    assert any("not a positive integer" in e for e in errors_of(t3)), errors_of(t3)
    assert errors_of("simulator lang=spectre\nR1 (a b) resistor r=1k m=4\n") == []
    assert any(e.startswith("mult:") for e in
               errors_of("simulator lang=spectre\nR1 (a b) resistor r=1k m=4000\n"))
    # config-side exact-name parameters
    t5 = "simulator lang=spectre\nparameters m=@@m@@\nR1 (a b) resistor r=1k m=m\n"
    assert any(e.startswith("mult:") for e in errors_of(t5, parameters={"m": [5000]}))
    # expression multipliers are skipped (unknown scale)
    t6 = "simulator lang=spectre\nparameters k=2\nR1 (a b) resistor r=1k m=(2*k)\n"
    assert errors_of(t6, parameters={}) == []


def test_prefix_heuristic():
    # C-initial config parameter checked as a capacitor when not otherwise resolved
    t = "simulator lang=spectre\nparameters Cload=@@Cload@@\n"
    errs = errors_of(t, parameters={"Cload": ["5u"]})
    assert any(e.startswith("rcl:") and "Cload" in e for e in errs), errs
    # the retired c=(cpf*1e-15) pattern: the expression is an ERROR now, and
    # the heuristic no longer exempts the c-prefixed name (its bare values
    # read as base-unit farads and flag on their own)
    t2 = ("simulator lang=spectre\nparameters cpf=@@cpf@@\n"
          "Cp (a b) capacitor c=(cpf*1e-15)\n")
    errs = errors_of(t2, parameters={"cpf": [100, 200]})
    assert any("expressions are not allowed" in e for e in errs), errs
    assert any(e.startswith("rcl:") and "cpf" in e for e in errs), errs
    # the new idiom: bare-name reference + suffixed sweep values, checked
    # precisely by the bare-name path (c=0f degenerate point included)
    t2b = ("simulator lang=spectre\nparameters cpf=@@cpf@@\n"
           "Cp (a b) capacitor c=cpf\n")
    assert errors_of(t2b, parameters={"cpf": ["0f", "56f", "150f"]}) == []
    # bare-name RCL references are checked precisely, without a duplicate
    # prefix finding
    t3 = "simulator lang=spectre\nparameters Cload=@@Cload@@\nC0 (a b) capacitor c=Cload\n"
    errs = [e for e in errors_of(t3, parameters={"Cload": ["5u"]}) if e.startswith("rcl:")]
    assert len(errs) == 1, errs
    # geometry-position names are lengths (u/n contract), not inductors
    t4 = "simulator lang=spectre\nparameters l_geo=@@l_geo@@\nM1 (d g s b) nmos l=l_geo w=1u\n"
    assert errors_of(t4, parameters={"l_geo": ["180n"]}) == [], errors_of(t4)


def test_geometry_rules():
    # swept geometry names keep the u/n contract (mosfet line)
    t = ("simulator lang=spectre\nparameters w_val=@@w_val@@ l_geo=@@l_geo@@\n"
         "M1 (d g s b) nmos w=w_val l=l_geo\n")
    assert errors_of(t, parameters={"w_val": ["2u"], "l_geo": ["180n"]}) == []
    errs = errors_of(t, parameters={"w_val": ["60"], "l_geo": ["180n"]})
    assert any(e.startswith("units:") for e in errs), errs
    # literal geometry without suffix still warns (metres trap) — the lookahead
    # must not backtrack ('l=0.15u' is suffixed and stays silent)
    t2 = "simulator lang=spectre\nM1 (d g s b) nmos l=0.15u w=61.7u nf=31\n"
    assert warnings_of(t2) == [], warnings_of(t2)
    t2b = "simulator lang=spectre\nM1 (d g s b) nmos l=0.5 w=2u\n"
    assert any("metres" in w for w in warnings_of(t2b)), warnings_of(t2b)
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
    # multiplier ranges override the same way
    t2 = "simulator lang=spectre\nM1 (d g s b) nmos l=0.15u w=1u nf=200\n"
    assert any(e.startswith("mult:") for e in errors_of(t2))
    assert errors_of(t2, mult_ranges=dict(MULT_RANGES, nf=(1, 300))) == []


def test_non_rcl_instances_untouched():
    t = ("simulator lang=spectre\n"
         "XD (a b) sky13_fd_pr__nch2 w=2u l=180n\n"
         "X1 (a b) mysubckt r=5\n")
    assert errors_of(t) == [] and warnings_of(t) == []


def test_check_params_config():
    """Workspace defaults and switches from .harnessams/check_params.json.

    Each scenario gets its own tmp workspace (optional check_params.json +
    a project dir) and chdirs into the project — the config discovery walks
    up from the cwd, exactly like a gen script run.
    """
    scenarios = tempfile.mkdtemp(prefix="hams-chkpy-")
    base = os.getcwd()
    try:
        def enter(ws_name, params=None, raw=None):
            ws = Path(scenarios) / ws_name
            proj = ws / "proj"
            proj.mkdir(parents=True)
            if params is not None or raw is not None:
                cfg = ws / ".harnessams"
                cfg.mkdir()
                (cfg / "check_params.json").write_text(
                    raw if raw is not None else json.dumps(params), encoding="utf-8")
            os.chdir(proj)
            common_workspace._check_params_cache.clear()

        # 1. absent file -> built-in defaults (fail-open)
        enter("ws_defaults")
        t = "simulator lang=spectre\nR1 (a b) resistor r=0.05\n"
        assert any(e.startswith("rcl:") for e in errors_of(t)), errors_of(t)
        try:
            norm_length_si("l", 2.5e-6)
            raise AssertionError("bare length must raise with unit_check on")
        except CheckError:
            pass

        # 2. corrupted file -> per-key fail-open to the same defaults
        enter("ws_corrupt", raw="{not json")
        assert any(e.startswith("rcl:") for e in errors_of(t)), errors_of(t)

        # 3. widened rcl table from the config
        enter("ws_rcl", params={"rcl": {"r": [0.01, 1e7]}})
        assert errors_of(t) == [], errors_of(t)  # 0.05 now inside
        errs = [e for e in errors_of("simulator lang=spectre\nR1 (a b) resistor r=0.001\n")
                if e.startswith("rcl:")]
        assert len(errs) == 1 and "0.01" in errs[0] and "outside the ideal range" in errs[0], errs

        # 4. unit_check off -> the geometry unit pass goes silent and bare
        #    lengths pass through as base-unit floats
        enter("ws_unit", params={"unit_check": False})
        t_geo = ("simulator lang=spectre\nparameters w_val=@@w_val@@\n"
                 "M1 (d g s b) nmos w=w_val l=0.5\n")
        errs, warns = check_netlist_format(t_geo, parameters={"w_val": ["60"]})
        assert errs == [] and warns == [], (errs, warns)
        assert norm_length_si("l", 2.5e-6) == 2.5e-6

        # 5. mult table from the config (the active range rides into the message)
        enter("ws_mult", params={"mult": {"nf": [1, 300]}})
        t_mult = "simulator lang=spectre\nM1 (d g s b) nmos l=0.15u w=1u nf=200\n"
        assert errors_of(t_mult) == [], errors_of(t_mult)  # 200 now inside
        errs = [e for e in errors_of(
            "simulator lang=spectre\nM1 (d g s b) nmos l=0.15u w=1u nf=2000\n")
            if e.startswith("mult:")]
        assert len(errs) == 1 and "1..300" in errs[0], errs

        # 6. legacy keys (bias_check / strict_includes) are ignored on read
        enter("ws_legacy", params={"bias_check": True, "strict_includes": True,
                                   "rcl": {"r": [0.01, 1e7]}})
        assert errors_of(t) == [], errors_of(t)
        assert "bias_check" not in common_workspace.default_check_params()
    finally:
        os.chdir(base)
        common_workspace._check_params_cache.clear()
        shutil.rmtree(scenarios, ignore_errors=True)


def test_check_params_session_layer():
    """Per-session table (D-005): DSH_SESSION_ID -> sessions.json entry
    check_params overrides the project file per-key; every failure shape
    (no env, no entry, corrupt sessions.json, off-spec values) falls back
    to the project layer / defaults, fail-open.
    """
    scenarios = tempfile.mkdtemp(prefix="hams-chk-sess-")
    base = os.getcwd()
    sid = "session-aaaa1111-2222-3332-4444-555555555555"
    other = "session-bbbb1111-2222-3332-4444-555555555555"
    saved_env = {k: os.environ.get(k) for k in ("DSH_SESSION_ID",)}
    try:
        def enter(ws_name, project_params=None, sessions=None, raw_sessions=None):
            ws = Path(scenarios) / ws_name
            proj = ws / "proj"
            proj.mkdir(parents=True)
            cfg = ws / ".harnessams"
            cfg.mkdir()
            if project_params is not None:
                (cfg / "check_params.json").write_text(json.dumps(project_params), encoding="utf-8")
            if sessions is not None or raw_sessions is not None:
                (cfg / "sessions.json").write_text(
                    raw_sessions if raw_sessions is not None else json.dumps(sessions), encoding="utf-8")
            os.chdir(proj)
            common_workspace._check_params_cache.clear()

        t_narrow = "simulator lang=spectre\nR1 (a b) resistor r=0.05\n"  # inside widened, outside default
        t_nf = "simulator lang=spectre\nM1 (d g s b) nmos l=0.15u w=1u nf=200\n"

        # 1. session table widens r beyond BOTH the project file and defaults
        enter("ws_sess_wide", project_params={"rcl": {"r": [0.1, 1e6]}},
              sessions={"version": 2, "sessions": {sid: {
                  "display": "session", "reports": [], "dashboard_selected": None,
                  "check_params": {"rcl": {"r": [0.01, 1e7]}}}}})
        os.environ["DSH_SESSION_ID"] = sid
        assert errors_of(t_narrow) == [], errors_of(t_narrow)

        # 2. same workspace, DIFFERENT session (no own table) -> project layer
        os.environ["DSH_SESSION_ID"] = other
        assert any(e.startswith("rcl:") for e in errors_of(t_narrow)), errors_of(t_narrow)

        # 3. partial session table merges per-key (switch rides on top of the
        #    project r table)
        enter("ws_sess_partial", project_params={"rcl": {"r": [0.01, 1e7]}},
              sessions={"version": 2, "sessions": {sid: {
                  "check_params": {"unit_check": False}}}})
        os.environ["DSH_SESSION_ID"] = sid
        assert errors_of(t_narrow) == [], errors_of(t_narrow)
        t_geo = ("simulator lang=spectre\nparameters w_val=@@w_val@@\n"
                 "M1 (d g s b) nmos w=w_val l=0.5\n")
        errs, warns = check_netlist_format(t_geo, parameters={"w_val": ["60"]})
        assert errs == [] and warns == [], (errs, warns)

        # 4. corrupt sessions.json / off-spec session values -> project layer holds
        enter("ws_sess_corrupt", project_params={"rcl": {"r": [0.01, 1e7]}},
              raw_sessions="{not json")
        os.environ["DSH_SESSION_ID"] = sid
        assert errors_of(t_narrow) == [], errors_of(t_narrow)
        enter("ws_sess_offspec", project_params={"rcl": {"r": [0.01, 1e7]}},
              sessions={"version": 2, "sessions": {sid: {
                  "check_params": {"rcl": {"r": [9, 9]}}}}})
        os.environ["DSH_SESSION_ID"] = sid
        assert errors_of(t_narrow) == [], errors_of(t_narrow)  # bad pair ignored -> project r range

        # 5. no env at all -> project layer
        del os.environ["DSH_SESSION_ID"]
        assert errors_of(t_narrow) == [], errors_of(t_narrow)

        # 6. session mult table overrides the built-in default
        enter("ws_sess_mult",
              sessions={"version": 2, "sessions": {sid: {
                  "check_params": {"mult": {"nf": [1, 300]}}}}})
        os.environ["DSH_SESSION_ID"] = sid
        assert errors_of(t_nf) == [], errors_of(t_nf)
        os.environ["DSH_SESSION_ID"] = other  # no own table -> defaults, 200 > 128
        assert any(e.startswith("mult:") for e in errors_of(t_nf)), errors_of(t_nf)
    finally:
        for k, v in saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        os.chdir(base)
        common_workspace._check_params_cache.clear()
        shutil.rmtree(scenarios, ignore_errors=True)


def test_template_naming_family():
    """template*.scs family names pass silently (2026-11-21 relaxation).

    Only names outside the family warn now; the exact template.scs requirement
    moved to the batch uploader's declared-vs-uploaded match.
    """
    def naming(name):
        _, warns = check_netlist_format(GOOD, parameters=GOOD_PARAMS, template_name=name)
        return [w for w in warns if w.startswith("naming:")]

    assert naming("template.scs") == []
    assert naming("template_thorough.scs") == []
    assert naming("some/dir/template_pss.scs") == []
    assert naming("net.scs"), "outside-family name must still warn"
    assert naming(None) == []


def main():
    # Hermetic cwd: the default-behavior tests below must not pick up a real
    # harnessams-workspace sitting above this repo (its check_params.json would
    # change the defaults under test), so run them from an empty temp dir.
    clean = tempfile.mkdtemp(prefix="hams-chk-clean-")
    old_cwd = os.getcwd()
    common_workspace._check_params_cache.clear()
    os.chdir(clean)
    try:
        test_parse_ideal_value()
        test_in_range_passes()
        test_boundary_values_inclusive()
        test_out_of_range_literals()
        test_case_sensitive_milli_vs_mega()
        test_no_scientific_notation()
        test_undeclared_temperature_warns()
        test_placeholder_placement()
        test_sweep_param_presence()
        test_swept_name_reference_values()
        test_multiplier_gates()
        test_prefix_heuristic()
        test_geometry_rules()
        test_rcl_ranges_override()
        test_non_rcl_instances_untouched()
        test_check_params_config()
        test_check_params_session_layer()
        test_template_naming_family()
    finally:
        os.chdir(old_cwd)
        common_workspace._check_params_cache.clear()
        shutil.rmtree(clean, ignore_errors=True)
    print("spectre checks tests ok")


if __name__ == "__main__":
    main()
