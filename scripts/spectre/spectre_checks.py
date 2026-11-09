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


def check_netlist_format(text, parameters=None, template_name="template.scs",
                         strict_includes=False, base_dir=None):
    """Return (errors, warnings) for a Spectre netlist template.

    parameters: the sweep_config.parameters mapping (optional; when given it is
    used for the placeholder/unit checks).
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

        # unit suffix contract for geometry attributes
        for attr, name in _INSTANCE_VALUE.findall(text):
            if attr.lower() not in _LENGTH_ATTRS:
                continue
            for value in parameters.get(name, []):
                if not isinstance(value, str) or not re.fullmatch(
                        r"[0-9]*\.?[0-9]+\s*[un]", value.strip(), re.I):
                    errors.append("units: %s=%@@ ... value %r must be a u/n-suffixed string "
                                  "(e.g. '1.5u'); a bare number means metres in Spectre"
                                  % (attr, value))
                    break

    for s in stripped:
        if _LITERAL_LENGTH.search(s):
            warnings.append("units: literal geometry value without a unit suffix in %r "
                            "(Spectre reads it as metres)" % s[:120])

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
