# -*- coding: utf-8 -*-
"""HarnessAMS spectre assembly-layer checks (Spectre .scs format + sweep config).

Used by a project's gen_*.py before the netlist is written. The unit and bias
helpers are re-exported for the gen scripts.

Checks:
- the netlist must declare ``simulator lang=spectre``;
- SPICE-only directives (.control/.endc/.end/.lib/.option/.include/.param) are
  rejected;
- subckt/ends counts pair;
- every ``@@PARAM@@`` placeholder must be declared in the sweep parameters, and
  every swept parameter should be used at least once (warning);
- ``l|w|lr|wr|rl|rw=@@P@@`` values must be u/n-suffixed strings, never bare
  numbers (Spectre reads a bare number as metres);
- ideal resistor/capacitor/inductor instance values (``resistor``/``res`` r=,
  ``capacitor``/``cap`` c=, ``inductor``/``ind`` l=) must sit inside the ideal
  plausibility ranges R 0.1 ohm..1M ohm, C 1f..1u farad, L 0.1p..10n henry
  (inclusive); literals, ``@@P@@`` swept values and template ``parameters``
  references are all checked. ``m``/``M`` follow Spectre case sensitivity
  (m=1e-3, M=1e6); a bare number is the base unit (no metres trap here);
- relative include/dspf_include files must exist; absolute include paths cannot
  be verified locally (warning unless strict_includes);
- the uploaded template name should be ``template.scs`` (warning; the batch
  uploader promotes this to an error).

Pure standard library.
"""
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common.common_units import (  # noqa: E402,F401  (re-exported for gen scripts)
    CheckError, check_bias_lt_vdd, fmt_num, norm_count, norm_current,
    norm_freq, norm_length_si,
)

_LENGTH_ATTRS = {"l", "w", "lr", "wr", "rl", "rw"}
_SPICE_DIRECTIVES = (".control", ".endc", ".end", ".lib", ".option",
                     ".include", ".param", ".model", ".tran", ".ac", ".dc")
_PLACEHOLDER = re.compile(r"@@(\w+)(?::[^@]*)?@@")
_INSTANCE_VALUE = re.compile(r"\b(\w+)\s*=\s*@@(\w+)(?::[^@]*)?@@")
_LITERAL_LENGTH = re.compile(r"\b(l|w|lr|wr|rl|rw)\s*=\s*([0-9]*\.?[0-9]+(?:[eE][+-]?[0-9]+)?)"
                             r"(?![0-9a-zA-Z_])")
_INCLUDE = re.compile(r'^\s*(?:dspf_include|include)\s+"([^"]+)"')

# Ideal R/C/L instances: device name (case-insensitive) -> canonical value kind.
_RCL_DEVICE = {"resistor": "r", "res": "r", "capacitor": "c", "cap": "c",
               "inductor": "l", "ind": "l"}
# Ideal plausibility ranges (inclusive) for generated netlists: 0.1 ohm..1M ohm,
# 1 fF..1 uF, 0.1 pH..10 nH. Override via check_netlist_format(rcl_ranges=...).
IDEAL_RCL_RANGES = {
    "r": (0.1, 1e6),     # ohm
    "c": (1e-15, 1e-6),  # farad
    "l": (1e-13, 1e-8),  # henry
}
_IDEAL_RCL_RANGE_TEXT = {"r": "0.1..1M ohm", "c": "1f..1u farad", "l": "0.1p..10n henry"}
_RCL_UNIT = {"r": "ohm", "c": "farad", "l": "henry"}
_RCL_INSTANCE = re.compile(r"^\s*(\w+)\s*\(([^)]*)\)\s*([A-Za-z_]\w*)\s+(.*)$")
_RCL_ATTR_VALUE = re.compile(r"\b([rcl])\s*=\s*([^\s]+)")
_RCL_NUMBER = re.compile(r"([+-]?[0-9]*\.?[0-9]+(?:[eE][+-]?[0-9]+)?)([a-zA-Z]*)")
# Spectre suffixes are case-sensitive only for M (mega) vs m (milli); the rest
# pair upper/lower. 'meg' (any case) is tolerated as an alias for M.
_RCL_SUFFIX = {"T": 1e12, "G": 1e9, "M": 1e6, "K": 1e3, "k": 1e3, "m": 1e-3,
               "U": 1e-6, "u": 1e-6, "N": 1e-9, "n": 1e-9, "P": 1e-12,
               "p": 1e-12, "F": 1e-15, "f": 1e-15}
_PARAMETERS_STMT = re.compile(r"^parameters\s+(.+)$", re.I)
_PARAM_ASSIGN = re.compile(r"(\w+)\s*=\s*([^\s]+)")


def parse_ideal_value(text):
    """Parse an ideal R/C/L netlist value ('1k', '2.5u', '0.1', '1Meg') into the
    base unit (ohm/farad/henry). A bare number is the base unit — unlike geometry
    lengths there is no metres trap for ideal devices. Returns None when ``text``
    is not a plain literal (placeholder, expression or unknown suffix), so the
    caller can skip it instead of guessing."""
    token = text.strip().rstrip(",;")
    m = _RCL_NUMBER.fullmatch(token)
    if not m:
        return None
    suffix = m.group(2)
    if not suffix:
        return float(m.group(1))
    if suffix.lower() == "meg":
        factor = 1e6
    else:
        factor = _RCL_SUFFIX.get(suffix)
        if factor is None:
            return None
    return float(m.group(1)) * factor


def _ideal_number(value):
    """Base-unit number for a sweep-config value: strings parse as SI literals,
    numbers are taken as-is (bare = base unit); bool/other types are uncheckable."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        return parse_ideal_value(value)
    return None


def check_netlist_format(text, parameters=None, template_name="template.scs",
                         strict_includes=False, base_dir=None, rcl_ranges=None):
    """Return (errors, warnings) for a Spectre netlist template.

    parameters: the sweep_config.parameters mapping (optional; when given it is
    used for the placeholder/unit checks).
    rcl_ranges: override for the ideal R/C/L plausibility ranges
    (default IDEAL_RCL_RANGES; kind -> (low, high) in the base unit).
    """
    errors = []
    warnings = []
    lines = text.splitlines()
    stripped = []
    for raw in lines:
        s = raw.strip()
        # Spectre comments: // and /* ... */ (block comments are rare in
        # generated templates; line comments are what the assembly scripts emit)
        if not s or s.startswith("//"):
            continue
        stripped.append(s)

    if not any(re.match(r"^simulator\s+lang\s*=\s*spectre\b", s) for s in stripped):
        errors.append("format: missing 'simulator lang=spectre' declaration")

    for i, s in enumerate(stripped, start=1):
        low = s.lower()
        for directive in _SPICE_DIRECTIVES:
            if low == directive or low.startswith(directive + " "):
                errors.append("format: SPICE-only directive %r is not valid in a Spectre "
                              "netlist (line %d)" % (directive, i))
                break

    n_sub = sum(1 for s in stripped if re.match(r"^(inline\s+)?subckt\b", s, re.I))
    n_ends = sum(1 for s in stripped if re.match(r"^ends\b", s, re.I))
    if n_sub != n_ends:
        errors.append("format: subckt(%d) and ends(%d) counts do not pair" % (n_sub, n_ends))

    used = set(_PLACEHOLDER.findall(text))
    if parameters is not None:
        declared = set(parameters.keys())
        missing = sorted(used - declared)
        unused = sorted(declared - used)
        if missing:
            errors.append("parameters: template placeholders not declared in "
                          "sweep_config.parameters: %s" % ", ".join(missing))
        if unused:
            warnings.append("parameters: swept parameters not used by the template: %s"
                            % ", ".join(unused))
        if not any(re.match(r"^parameters\b", s) for s in stripped):
            warnings.append("format: no 'parameters' statement found although the sweep "
                            "declares parameters")

    # template-level 'parameters' statement (name -> literal default), used to
    # resolve parameter references in ideal R/C/L values (last statement wins)
    template_params = {}
    for s in stripped:
        pm = _PARAMETERS_STMT.match(s)
        if pm:
            for am in _PARAM_ASSIGN.finditer(pm.group(1)):
                template_params[am.group(1)] = am.group(2)

    ranges = IDEAL_RCL_RANGES if rcl_ranges is None else rcl_ranges
    for s in stripped:
        im = _RCL_INSTANCE.match(s)
        kind = _RCL_DEVICE.get(im.group(3).lower()) if im else None
        if kind is None:
            # unit suffix contract for geometry attributes (bare number = metres)
            if parameters is not None:
                for attr, name in _INSTANCE_VALUE.findall(s):
                    if attr.lower() not in _LENGTH_ATTRS:
                        continue
                    for value in parameters.get(name, []):
                        if not isinstance(value, str) or not re.fullmatch(
                                r"[0-9]*\.?[0-9]+\s*[un]", value.strip(), re.I):
                            errors.append("units: %s=@@%s@@ swept value %r must be a u/n-suffixed "
                                          "string (e.g. '1.5u'); a bare number means metres in "
                                          "Spectre" % (attr, name, value))
                            break
            if _LITERAL_LENGTH.search(s):
                warnings.append("units: literal geometry value without a unit suffix in %r "
                                "(Spectre reads it as metres)" % s[:120])
            continue

        # ideal resistor/capacitor/inductor: value range gate (inclusive bounds).
        # Checks literals, @@P@@ swept values and template parameters references;
        # non-literal forms (expressions, unknown names) are skipped, not guessed.
        lo, hi = ranges[kind]
        range_text = _IDEAL_RCL_RANGE_TEXT[kind]
        for am in _RCL_ATTR_VALUE.finditer(im.group(4)):
            attr, token = am.group(1), am.group(2)
            if attr != kind:
                continue
            pm = _PLACEHOLDER.fullmatch(token)
            if pm is not None:
                if parameters is None:
                    continue
                vals = parameters.get(pm.group(1), [])
                if not isinstance(vals, (list, tuple)):
                    vals = [vals]
                for value in vals:
                    num = _ideal_number(value)
                    if num is not None and not lo <= num <= hi:
                        errors.append("rcl: %s (%s) %s=@@%s@@ swept value %r outside the "
                                      "ideal range %s" % (im.group(1), im.group(3), attr,
                                                          pm.group(1), value, range_text))
                continue
            num = parse_ideal_value(token)
            if num is not None:
                if not lo <= num <= hi:
                    errors.append("rcl: %s (%s) %s=%s (%g %s) outside the ideal range %s"
                                  % (im.group(1), im.group(3), attr, token, num,
                                     _RCL_UNIT[kind], range_text))
            elif token in template_params:
                num = parse_ideal_value(template_params[token])
                if num is not None and not lo <= num <= hi:
                    errors.append("rcl: %s (%s) %s=%s (parameters %s=%s) outside the "
                                  "ideal range %s" % (im.group(1), im.group(3), attr, token,
                                                      token, template_params[token],
                                                      range_text))

    if template_name and os.path.basename(template_name) != "template.scs":
        warnings.append("naming: netlist_template should be 'template.scs' "
                        "(the remote batch uploader always names it template.scs)")

    for s in stripped:
        m = _INCLUDE.match(s)
        if not m:
            continue
        path = m.group(1)
        if os.path.isabs(path):
            if strict_includes:
                errors.append("include: absolute path cannot be verified locally: %s" % path)
            else:
                warnings.append("include: absolute path is not verifiable locally: %s" % path)
            continue
        candidate = path if base_dir is None else os.path.join(base_dir, path)
        if not os.path.isfile(candidate):
            errors.append("include: relative include does not exist: %s" % path)
    return errors, warnings


def format_report(errors, warnings):
    """Human-readable multi-line report used by gen scripts."""
    out = []
    for e in errors:
        out.append("ERROR   " + e)
    for w in warnings:
        out.append("WARNING " + w)
    return "\n".join(out)
