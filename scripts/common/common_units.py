# -*- coding: utf-8 -*-
"""Assembly-layer unit normalization and bias checks for the Spectre channel.

Design contract:
- spectre lengths: the suffix is REQUIRED and preserved ("1.5u", "60n"),
  because a bare number in a Spectre netlist means metres (the unitless-value
  trap: a 6.0 ohm poly resistor becomes ~6 m long).
- frequencies: bare number = Hz; "10G" / "10Meg" / "100k" accepted.
- currents: bare number = A; p/n/u/m suffixes accepted.
- counts: non-negative integers only (bool does not count).
- bias voltages must satisfy |bias| < Vdd strictly.

Workspace switches (2026-11-10): <workspace>/.harnessams/check_params.json —
edited from the HarnessAMS task sidebar's check sub-view — can turn the unit
checks (unit_check) and the bias gate (bias_check) off for the whole workspace;
everything fails open to the built-in behaviour when the file is absent or
unreadable.

Pure standard library; imported by the check libraries and the demo gen scripts.
"""
import math
import os
import re
import sys

_FREQ_SUFFIX = {"G": 1e9, "M": 1e6, "k": 1e3, "meg": 1e6}  # input suffix -> Hz
_CUR_SUFFIX = {"p": 1e-12, "n": 1e-9, "u": 1e-6, "m": 1e-3}  # input suffix -> A


def _check_params():
    """Workspace check parameters via common_workspace.read_check_params.

    Lazy import so this module stays importable in isolation; every failure
    path falls back to the built-in defaults — fail-open by design.
    """
    try:
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if root not in sys.path:
            sys.path.insert(0, root)
        from common.common_workspace import read_check_params
        return read_check_params(os.getcwd())
    except Exception:
        return {}


def _unit_check_enabled():
    try:
        return bool(_check_params().get("unit_check", True))
    except Exception:
        return True


def _bias_check_enabled():
    try:
        return bool(_check_params().get("bias_check", True))
    except Exception:
        return True


def _finite_float(text):
    """float(text) when it parses to a finite number, else None."""
    try:
        v = float(text)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


class CheckError(ValueError):
    """Assembly-layer rejection; the message is a user-facing description."""


def norm_length_si(name, value, positive=True):
    """spectre length normalization: the u/n suffix is required and preserved.

    Returns a canonical suffixed string ("1.5u", "60n"). A bare number/string is
    rejected on purpose: Spectre would read it as metres. Workspace switch:
    unit_check=false (check_params.json) turns that rejection off — a bare
    number then passes through as the base-unit (metre) float.
    """
    m = None
    if isinstance(value, str):
        m = re.fullmatch(r"([0-9]*\.?[0-9]+)\s*([un])", value.strip(), re.I)
    if m:
        num = float(m.group(1))
        if positive and num <= 0:
            raise CheckError("%s: length must be positive, got %r" % (name, value))
        return "%s%s" % (fmt_num(num), m.group(2).lower())
    if not _unit_check_enabled():
        num = _finite_float(value)
        if num is not None:
            if positive and num <= 0:
                raise CheckError("%s: length must be positive, got %r" % (name, value))
            return num
    raise CheckError("%s: spectre length must be a u/n-suffixed string "
                     "(e.g. '1.5u'/'60n'), got %r" % (name, value))


def norm_freq(name, value, positive=True):
    """Frequency normalization: bare number = Hz; '10G' = 1e10; '10Meg'/'10M' = 1e7;
    '100k' = 1e5. Returns Hz."""
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise CheckError("%s: frequency must be a number or a G/M/k-suffixed string, got %r"
                         % (name, value))
    if isinstance(value, (int, float)):
        hz = float(value)
        src = repr(value)
    else:
        text = value.strip()
        if re.fullmatch(r"[+-]?[0-9]*\.?[0-9]+([eE][+-]?[0-9]+)?", text):
            hz = float(text)                      # bare numeric string = Hz
        else:
            m = re.fullmatch(r"([0-9]*\.?[0-9]+)\s*(meg|[gmk])", text, re.I)
            if m:
                suf = m.group(2)
                factor = _FREQ_SUFFIX["meg"] if suf.lower() == "meg" else _FREQ_SUFFIX[suf.upper()]
                hz = float(m.group(1)) * factor
            elif not _unit_check_enabled() and _finite_float(text) is not None:
                # unit checks off: any float-parseable text is taken at face
                # value (Hz) instead of enforcing the suffix whitelist
                hz = float(text)
            else:
                raise CheckError("%s: frequency accepts only a bare number (Hz) or a G/M/k suffix "
                                 "(e.g. '10G'/'10Meg'), got %r" % (name, value))
        src = text
    if positive and hz <= 0:
        raise CheckError("%s: frequency must be positive, got %s" % (name, src))
    return fmt_num(hz)


def norm_current(name, value, positive=True):
    """Current normalization: bare number = A; '50u' = 50e-6; '2m' = 2e-3. Returns A."""
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise CheckError("%s: current must be a number or a p/n/u/m-suffixed string, got %r"
                         % (name, value))
    if isinstance(value, (int, float)):
        amp = float(value)
    else:
        text = value.strip()
        m = re.fullmatch(r"([0-9]*\.?[0-9]+)\s*([pnum])(?:A)?", text, re.I)
        if m:
            amp = float(m.group(1)) * _CUR_SUFFIX[m.group(2).lower()]
        elif not _unit_check_enabled() and _finite_float(text) is not None:
            # unit checks off: any float-parseable text is taken at face value (A)
            amp = float(text)
        else:
            raise CheckError("%s: current accepts only a bare number (A) or a p/n/u/m suffix "
                             "(e.g. '50u'), got %r" % (name, value))
    if positive and amp <= 0:
        raise CheckError("%s: current must be positive, got %r" % (name, value))
    return fmt_num(amp)


def norm_count(name, value, minimum=1):
    """Count parameters (nf etc.): non-negative integers only (bool does not count)."""
    if isinstance(value, bool) or not isinstance(value, int):
        if isinstance(value, float) and value.is_integer():
            value = int(value)
        else:
            raise CheckError("%s: count must be a non-negative integer, got %r"
                             % (name, value))
    if value < minimum:
        raise CheckError("%s: count must be >= %s, got %s" % (name, minimum, value))
    return int(value)


def check_bias_lt_vdd(name, bias_v, vdd):
    """Bias voltage check: |bias| must stay strictly below Vdd (1e-12 float
    tolerance). Workspace switch: bias_check=false (check_params.json) turns
    the gate off — the value returns unchecked."""
    if not _bias_check_enabled():
        return bias_v
    if abs(bias_v) >= vdd - 1e-12:
        raise CheckError("%s: bias %s V violates |bias| < Vdd=%s V "
                         "(rejected at the assembly layer, not simulated)"
                         % (name, bias_v, vdd))
    return bias_v


def fmt_num(x):
    """Number formatting: integers drop the .0; floats keep enough significant digits."""
    if abs(x - round(x)) < 1e-12 and abs(x) < 1e15:
        return int(round(x))
    return float("%.10g" % x)
