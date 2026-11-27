# -*- coding: utf-8 -*-
"""Python-only coverage of the legacy gen-path companion library
(scripts/spectre/spectre_checks.py): the kwargs range overrides, the
chdir-based workspace config discovery, the DSH_SESSION_ID session layer,
the template*.scs naming family, and one clean-run smoke over the full GOOD
netlist. The shared netlist contract surface (language format, sweep
presence, RCL/mult value gates, geometry suffixes) is owned by the JS
engine's tests (tests/precheck.test.mjs) — the tool path never spawns this
library, so the mirrored cases are not duplicated here.
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
    norm_length_si,
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


def test_in_range_passes():
    # one clean-run smoke over the full device spread: RCL ranges, multipliers,
    # geometry suffixes, the language declaration — all silent together
    errors, warnings = check_netlist_format(GOOD, parameters=GOOD_PARAMS)
    assert errors == [], errors
    assert warnings == [], warnings


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
    finally:
        os.chdir(base)
        common_workspace._check_params_cache.clear()
        shutil.rmtree(scenarios, ignore_errors=True)


def test_check_params_session_layer():
    """Per-session table: DSH_SESSION_ID -> sessions.json entry
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
    """template*.scs family names pass silently.

    Only names outside the family warn; the exact template.scs requirement
    is the batch uploader's declared-vs-uploaded match.
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
        test_in_range_passes()
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
