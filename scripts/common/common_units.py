# -*- coding: utf-8 -*-
"""Assembly-layer unit normalization and bias checks shared by both simulators.

Design contract:
- ngspice lengths: a bare number means um (the sky130 model library sets
  scale=1u, so netlists carry no u suffix); "96u" / "500n" are normalized to a
  bare um number.
- spectre lengths: the suffix is REQUIRED and preserved ("1.5u", "60n"),
  because a bare number in a Spectre netlist means metres (the unitless-value
  trap: a 6.0 ohm poly resistor becomes ~6 m long).
- frequencies: bare number = Hz; "10G" / "10Meg" / "100k" accepted.
- currents: bare number = A; p/n/u/m suffixes accepted.
- counts: non-negative integers only (bool does not count).
- bias voltages must satisfy |bias| < Vdd strictly.

Pure standard library; imported by the check libraries and the demo gen scripts.
"""
import re

_LEN_SUFFIX = {"u": 1.0, "n": 1e-3}          # input suffix -> um (n = 1e-3 um)
_FREQ_SUFFIX = {"G": 1e9, "M": 1e6, "k": 1e3, "meg": 1e6}  # input suffix -> Hz
_CUR_SUFFIX = {"p": 1e-12, "n": 1e-9, "u": 1e-6, "m": 1e-3}


class CheckError(ValueError):
    """Assembly-layer rejection; the message is a user-facing description."""


def norm_length(name, value, positive=True):
    """ngspice length normalization: bare number = um; '96u' = 96 um; '500n' = 0.5 um.
    Returns a bare um number (the value written verbatim into SPICE netlists)."""
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise CheckError("%s: length must be a number or a u/n-suffixed string, got %r"
                         % (name, value))
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
                raise CheckError("%s: length accepts only a bare number (um) or a u/n suffix "
                                 "(e.g. '96u'/'500n'), got %r" % (name, value))
            um = float(m.group(1)) * _LEN_SUFFIX[m.group(2).lower()]
        src = text
    if positive and um <= 0:
        raise CheckError("%s: length must be positive, got %s" % (name, src))
    if um > 1e5:
        raise CheckError("%s: length %s is outside the plausible range (>100 mm); "
                         "wrong units suspected" % (name, src))
    return fmt_num(um)


def norm_length_si(name, value, positive=True):
    """spectre length normalization: the u/n suffix is required and preserved.

    Returns a canonical suffixed string ("1.5u", "60n"). A bare number/string is
    rejected on purpose: Spectre would read it as metres.
    """
    if not isinstance(value, str):
        raise CheckError("%s: spectre length must be a u/n-suffixed string "
                         "(e.g. '1.5u'/'60n'), got %r" % (name, value))
    text = value.strip()
    m = re.fullmatch(r"([0-9]*\.?[0-9]+)\s*([un])", text, re.I)
    if not m:
        raise CheckError("%s: spectre length must be a u/n-suffixed string "
                         "(e.g. '1.5u'/'60n'), got %r" % (name, value))
    num = float(m.group(1))
    if positive and num <= 0:
        raise CheckError("%s: length must be positive, got %r" % (name, value))
    return "%s%s" % (fmt_num(num), m.group(2).lower())


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
            if not m:
                raise CheckError("%s: frequency accepts only a bare number (Hz) or a G/M/k suffix "
                                 "(e.g. '10G'/'10Meg'), got %r" % (name, value))
            suf = m.group(2)
            factor = _FREQ_SUFFIX["meg"] if suf.lower() == "meg" else _FREQ_SUFFIX[suf.upper()]
            hz = float(m.group(1)) * factor
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
        if not m:
            raise CheckError("%s: current accepts only a bare number (A) or a p/n/u/m suffix "
                             "(e.g. '50u'), got %r" % (name, value))
        amp = float(m.group(1)) * _CUR_SUFFIX[m.group(2).lower()]
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
    """Bias voltage check: |bias| must stay strictly below Vdd (1e-12 float tolerance)."""
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
