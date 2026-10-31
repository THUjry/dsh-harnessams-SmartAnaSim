# -*- coding: utf-8 -*-
"""HarnessAMS ngspice assembly-layer checks (netlist format), plus the unit/bias
helpers re-exported for the demo gen scripts.

Contract:
- Length parameters accept a bare number (um; the sky130 model library sets
  scale=1u, so netlists carry no u suffix) or a string with a u/n suffix
  (normalized to a bare um number);
- Frequency parameters accept a bare number (Hz) or a G/M/k-suffixed string;
- Count parameters (nf etc.) must be non-negative integers;
- Bias voltages must satisfy |bias| < Vdd strictly.

Netlist format checks (sky130 + ngspice pitfalls):
- MOS device instances must use the X prefix (sky130 models are subcircuits);
- .lib lines must precede .control;
- .control/.endc and .subckt/.ends pairing;
- the netlist must end with .end;
- files referenced by .include / .lib must exist;
- MC .option seed must precede .lib.

Pure standard library. Imported by each project's gen_*.py assembly script.
"""
import os
import re
import sys
from pathlib import Path

# scripts/ on sys.path so the shared units module is importable whether this file
# is imported by a demo script or executed directly.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common.common_units import (  # noqa: E402,F401  (re-exported for gen scripts)
    CheckError, check_bias_lt_vdd, fmt_num, norm_count, norm_current,
    norm_freq, norm_length, norm_length_si,
)


def check_netlist_format(text, pdk_lib_path=None):
    """Netlist format check: returns a list of errors (empty = pass)."""
    errors = []
    lines = text.splitlines()
    stripped = [ln.strip() for ln in stripped_no_comment(lines)]

    ctrl_i = find_first(stripped, ".control")
    endc_i = find_first(stripped, ".endc")
    if ctrl_i == -1 and endc_i == -1:
        pass  # no control block is legal too (pure .dc/.ac card style)
    elif ctrl_i == -1 or endc_i == -1 or endc_i < ctrl_i:
        errors.append("format: .control/.endc not paired")

    n_sub = sum(1 for ln in stripped if ln.lower().startswith(".subckt"))
    n_ends = sum(1 for ln in stripped if ln.lower().startswith(".ends"))
    if n_sub != n_ends:
        errors.append("format: .subckt(%d) and .ends(%d) counts do not pair"
                      % (n_sub, n_ends))

    if not any(ln.lower().startswith(".end") and not ln.lower().startswith(".ends")
               and not ln.lower().startswith(".endc") for ln in stripped):
        errors.append("format: missing .end terminator")

    lib_i = find_first(stripped, ".lib")
    if lib_i != -1 and ctrl_i != -1 and lib_i > ctrl_i:
        errors.append("format: .lib model line must precede .control")
    for i, ln in enumerate(stripped):
        if ln.lower().startswith(".option") and "seed" in ln.lower() and lib_i != -1 and i > lib_i:
            errors.append("format: MC .option seed must precede .lib")
            break

    mos_re = re.compile(r"^\s*M\w+\s+\S+", re.I)
    card_area_end = ctrl_i if ctrl_i != -1 else len(stripped)
    if any(mos_re.match(ln) for ln in stripped[:card_area_end]):
        errors.append("format: sky130 MOS instances must use the X prefix "
                      "(an M prefix resolves to built-in BSIM models and finds no model)")

    for ln in stripped:
        low = ln.lower()
        if low.startswith(".include") or low.startswith(".lib "):
            m = re.match(r'\.(?:include|lib)\s+"([^"]+)"', ln)
            if not m:
                m = re.match(r"\.(?:include|lib)\s+(\S+)", ln)
            if m:
                path = m.group(1)
                if pdk_lib_path and os.path.normcase(os.path.abspath(path)) == \
                        os.path.normcase(os.path.abspath(pdk_lib_path)):
                    continue  # the model library's existence is guaranteed by the workspace config
                if not os.path.isfile(path):
                    errors.append("format: referenced file does not exist: %s" % path)
    return errors


def stripped_no_comment(lines):
    out = []
    for ln in lines:
        s = ln.strip()
        if not s or s.startswith("*") or s.startswith("$") or s.startswith(";"):
            continue
        out.append(ln)
    return out


def find_first(stripped_lines, token):
    for i, ln in enumerate(stripped_lines):
        if ln.lower().startswith(token.lower()):
            return i
    return -1
