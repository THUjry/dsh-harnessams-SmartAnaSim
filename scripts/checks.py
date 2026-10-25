# -*- coding: utf-8 -*-
"""HarnessAMS-SmartAnaSim assembly-layer check library (netlist format / parameter dimensions / bias < Vdd).

Design contract:
- Length parameters accept a bare number (um; the model library sets scale=1u, so netlists
  carry no u suffix) or a string with a u/n suffix (normalized to a bare um number);
- Frequency parameters accept a bare number (Hz) or a string with a G/M/k suffix (normalized to Hz);
- Count parameters (nf etc.) must be non-negative integers;
- Bias voltages must satisfy |bias| < Vdd strictly; violations are rejected at the assembly
  layer and never reach the simulator;
- Netlist format checks: X-prefix MOS instances, .lib line placement, .control/.endc pairing,
  .end terminator, .include file existence, and MC .option seed ordering.

Pure standard library. Called by each demo project's gen_*.py assembly script.
"""
import os
import re

_LEN_SUFFIX = {"u": 1.0, "n": 1e-3}          # input suffix -> um (n = 1e-3 um)
_FREQ_SUFFIX = {"G": 1e9, "M": 1e6, "k": 1e3, "meg": 1e6}  # input suffix -> Hz


class CheckError(ValueError):
    """Assembly-layer rejection; the message is a user-facing description."""


def norm_length(name, value, positive=True):
    """Length normalization: bare number = um; '96u' = 96 um; '500n' = 0.5 um.
    Returns a bare um number (the value written verbatim into netlists)."""
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise CheckError(f"{name}: length must be a number or a u/n-suffixed string, got {value!r}")
    if isinstance(value, (int, float)):
        um = float(value)
        src = repr(value)
    else:
        text = value.strip()
        if re.fullmatch(r"[+-]?[0-9]*\.?[0-9]+([eE][+-]?[0-9]+)?", text):
            um = float(text)                      # bare numeric string = um
        else:
            m = re.fullmatch(r"([0-9]*\.?[0-9]+)\s*([un])", text, re.I)
            if not m:
                raise CheckError(f"{name}: length accepts only a bare number (um) or a u/n suffix "
                                 f"(e.g. '96u'/'500n'), got {value!r}")
            um = float(m.group(1)) * _LEN_SUFFIX[m.group(2).lower()]
        src = text
    if positive and um <= 0:
        raise CheckError(f"{name}: length must be positive, got {src}")
    if um > 1e5:
        raise CheckError(f"{name}: length {src} is outside the plausible range (>100 mm); wrong units suspected")
    # netlist-internal unit is um without a suffix (sky130 combined_models sets scale=1u)
    return _fmt_num(um)


def norm_freq(name, value, positive=True):
    """Frequency normalization: bare number = Hz; '10G' = 1e10; '10Meg'/'10M' = 1e7; '100k' = 1e5.
    Returns Hz."""
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise CheckError(f"{name}: frequency must be a number or a G/M/k-suffixed string, got {value!r}")
    if isinstance(value, (int, float)):
        hz = float(value)
        src = repr(value)
    else:
        text = value.strip()
        if re.fullmatch(r"[+-]?[0-9]*\.?[0-9]+([eE][+-]?[0-9]+)?", text):
            hz = float(text)                      # bare numeric string = Hz
        else:
            m = re.fullmatch(r"([0-9]*\.?[0-9]+)\s*(meg|[gmk])", text, re.I)
            if not m:
                raise CheckError(f"{name}: frequency accepts only a bare number (Hz) or a G/M/k suffix "
                                 f"(e.g. '10G'/'10Meg'), got {value!r}")
            suf = m.group(2)
            factor = _FREQ_SUFFIX["meg"] if suf.lower() == "meg" else _FREQ_SUFFIX[suf.upper()]
            hz = float(m.group(1)) * factor
        src = text
    if positive and hz <= 0:
        raise CheckError(f"{name}: frequency must be positive, got {src}")
    return _fmt_num(hz)


def norm_current(name, value, positive=True):
    """Current normalization: bare number = A; '50u' = 50e-6; '2m' = 2e-3. Returns A."""
    _CUR_SUFFIX = {"p": 1e-12, "n": 1e-9, "u": 1e-6, "m": 1e-3}
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise CheckError(f"{name}: current must be a number or a p/n/u/m-suffixed string, got {value!r}")
    if isinstance(value, (int, float)):
        amp = float(value)
    else:
        text = value.strip()
        m = re.fullmatch(r"([0-9]*\.?[0-9]+)\s*([pnum])(?:A)?", text, re.I)
        if not m:
            raise CheckError(f"{name}: current accepts only a bare number (A) or a p/n/u/m suffix "
                             f"(e.g. '50u'), got {value!r}")
        amp = float(m.group(1)) * _CUR_SUFFIX[m.group(2).lower()]
    if positive and amp <= 0:
        raise CheckError(f"{name}: current must be positive, got {value!r}")
    return _fmt_num(amp)


def norm_count(name, value, minimum=1):
    """Count parameters (nf etc.): non-negative integers only (bool does not count)."""
    if isinstance(value, bool) or not isinstance(value, int):
        if isinstance(value, float) and value.is_integer():
            value = int(value)
        else:
            raise CheckError(f"{name}: count must be a non-negative integer, got {value!r}")
    if value < minimum:
        raise CheckError(f"{name}: count must be >= {minimum}, got {value}")
    return int(value)


def check_bias_lt_vdd(name, bias_v, vdd):
    """Bias voltage check: |bias| must stay strictly below Vdd (1e-12 float tolerance)."""
    if abs(bias_v) >= vdd - 1e-12:
        raise CheckError(f"{name}: bias {bias_v} V violates |bias| < Vdd={vdd} V "
                         f"(rejected at the assembly layer, not simulated)")
    return bias_v


def check_netlist_format(text, pdk_lib_path=None):
    """Netlist format check: returns a list of errors (empty = pass).

    Covers the known sky130 + ngspice pitfalls:
    - MOS device instances must use the X prefix (sky130 models are subcircuits);
    - .lib lines must precede .control;
    - .control/.endc and .subckt/.ends pairing;
    - the netlist must end with .end;
    - files referenced by .include / .lib must exist;
    - MC .option seed must precede .lib.
    """
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
        errors.append(f"format: .subckt({n_sub}) and .ends({n_ends}) counts do not pair")

    if not any(ln.lower().startswith(".end") and not ln.lower().startswith(".ends")
               and not ln.lower().startswith(".endc") for ln in stripped):
        errors.append("format: missing .end terminator")

    lib_i = find_first(stripped, ".lib")
    if lib_i != -1 and ctrl_i != -1 and lib_i > ctrl_i:
        errors.append("format: .lib model line must precede .control")
    seed_i = find_first(stripped, ".option")
    for i, ln in enumerate(stripped):
        if ln.lower().startswith(".option") and "seed" in ln.lower() and lib_i != -1 and i > lib_i:
            errors.append("format: MC .option seed must precede .lib")
            break

    mos_re = re.compile(r"^\s*M\w+\s+\S+", re.I)
    card_area_end = ctrl_i if ctrl_i != -1 else len(stripped)  # MOS instance lines can only appear in the card area before .control
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
                    errors.append(f"format: referenced file does not exist: {path}")
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


def _fmt_num(x):
    """Number formatting: integers drop the .0; floats keep enough significant digits."""
    if abs(x - round(x)) < 1e-12 and abs(x) < 1e15:
        return int(round(x))
    return float(f"{x:.10g}")
