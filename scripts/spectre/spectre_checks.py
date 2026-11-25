# -*- coding: utf-8 -*-
"""HarnessAMS spectre netlist checks (Spectre .scs format + sweep config).

The gen-script companion of the spectre_pre_check tool (src/precheck.js is the
submit-path authority; this library keeps the optional gen_*.py face on the
same contract). Used by a project's gen_*.py before the netlist is written;
the unit helpers are re-exported for gen scripts.

Checks:
- the netlist must declare ``simulator lang=spectre``;
- SPICE-only directives (.control/.endc/.end/.lib/.option/.include/.param) are
  rejected;
- no scientific notation anywhere on a code line — netlists use SI suffixes
  (1f, 100M, 200u) or plain decimals, never exponent form (2026-11-25 ruling:
  no scale factor may hide in an exponent);
- subckt/ends counts pair;
- @@PARAM@@ placeholders live on the ``parameters`` statement ONLY (the sole
  exemption: @@temperature@@ on a simulatorOptions line); anywhere else is an
  error — the circuit references parameters by name;
- every swept parameter needs its @@P@@ placeholder in the template, and every
  placeholder its config entry (the worker only substitutes config parameters);
  an undeclared @@temperature@@ is a WARNING — the worker fills the default
  27 degC, so a missing temperature never aborts the simulation;
- ideal resistor/capacitor/inductor instance values must sit inside the
  plausibility ranges (R 0.1..1M ohm, C 1f..1u farad, L 0.1p..10n henry,
  inclusive). Literals and name references are checked (a name resolves to the
  swept values plus the template's declared default); expressions at R/C/L
  value positions are REJECTED (2026-11-25 ruling — the retired exemption let
  fF-scale scans slip an nF-tightened bound); the config-side prefix heuristic
  (C/R/L-initial names) covers parameters the circuit has not resolved
  precisely, with no expression exemption anymore. ``m``/``M`` follow Spectre
  case sensitivity (m=1e-3, M=1e6);
- multiplier attributes ``nf``/``m`` (device fingers, instance multiplier)
  must be positive integers inside the mult ranges;
- ``l|w|lr|wr|rl|rw`` swept name values must be u/n-suffixed strings, never
  bare numbers (Spectre reads a bare number as metres); literal bare geometry
  warns;
- the template name should be a ``template*.scs`` family name (warning).

Workspace defaults (2026-11-25 shape): <workspace>/.harnessams/
check_params.json — the same file the HarnessAMS task sidebar's check sub-view
writes — supplies the rcl/mult tables and the unit_check switch when the
caller does not override them; an absent or corrupted file falls back to the
built-in constants (fail-open). Legacy bias_check / strict_includes keys are
ignored on read.

Pure standard library.
"""
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common.common_units import (  # noqa: E402,F401  (re-exported for gen scripts)
    CheckError, fmt_num, norm_count, norm_freq, norm_length_si,
)

def _check_params():
    """Workspace check parameters (check_params.json), fail-open to defaults.

    The same file the HarnessAMS task sidebar's check sub-view edits; the
    scripts/ root inserted above makes the common package importable.
    """
    try:
        from common.common_workspace import read_check_params
        return read_check_params(os.getcwd())
    except Exception:
        return {}


_LENGTH_ATTRS = {"l", "w", "lr", "wr", "rl", "rw"}
_SPICE_DIRECTIVES = (".control", ".endc", ".end", ".lib", ".option",
                     ".include", ".param", ".model", ".tran", ".ac", ".dc")
_PLACEHOLDER = re.compile(r"@@(\w+)(?::[^@]*)?@@")
_PARAMETERS_STMT = re.compile(r"^parameters\s+(.+)$", re.I)
_SIMOPTS_STMT = re.compile(r"^simulatorOptions\b", re.I)
_PARAM_ASSIGN = re.compile(r"(\w+)\s*=\s*([^\s]+)")
# Ideal R/C/L instances: device name (case-insensitive) -> canonical value kind.
_RCL_DEVICE = {"resistor": "r", "res": "r", "capacitor": "c", "cap": "c",
               "inductor": "l", "ind": "l"}
# Ideal plausibility ranges (inclusive) for generated netlists: 0.1 ohm..1M ohm,
# 1 fF..1 uF, 0.1 pH..10 nH. Override via check_netlist_format(rcl_ranges=...);
# the workspace default comes from .harnessams/check_params.json (rcl table).
IDEAL_RCL_RANGES = {
    "r": (0.1, 1e6),     # ohm
    "c": (1e-15, 1e-6),  # farad
    "l": (1e-13, 1e-8),  # henry
}
# Multiplier plausibility ranges (inclusive): device fingers and the instance
# multiplier are counts. Override via check_netlist_format(mult_ranges=...);
# the workspace default comes from check_params.json (mult table).
MULT_RANGES = {
    "nf": (1, 128),
    "m": (1, 1000),
}
_IDEAL_RCL_RANGE_TEXT = {"r": "0.1..1M ohm", "c": "1f..1u farad", "l": "0.1p..10n henry"}
_RCL_UNIT = {"r": "ohm", "c": "farad", "l": "henry"}
_RCL_INSTANCE = re.compile(r"^\s*(\w+)\s*\(([^)]*)\)\s*([A-Za-z_]\w*)\s+(.*)$")
_RCL_ATTR_VALUE = re.compile(r"\b([rcl])\s*=\s*([^\s]+)")
_MULT_ATTR_VALUE = re.compile(r"\b(nf|m)\s*=\s*([^\s]+)")
_RCL_NUMBER = re.compile(r"([+-]?[0-9]*\.?[0-9]+(?:[eE][+-]?[0-9]+)?)([a-zA-Z]*)")
# Spectre suffixes are case-sensitive only for M (mega) vs m (milli); the rest
# pair upper/lower. 'meg' (any case) is tolerated as an alias for M.
_RCL_SUFFIX = {"T": 1e12, "G": 1e9, "M": 1e6, "K": 1e3, "k": 1e3, "m": 1e-3,
               "U": 1e-6, "u": 1e-6, "N": 1e-9, "n": 1e-9, "P": 1e-12,
               "p": 1e-12, "F": 1e-15, "f": 1e-15}
# Literal geometry without a unit suffix (the metres trap). The lookahead
# class includes '.' — without it the engine backtracks the number
# ('l=0.15u' would match as 'l=0' with '.' passing the lookahead).
_LITERAL_LENGTH = re.compile(r"\b(l|w|lr|wr|rl|rw)\s*=\s*"
                             r"([0-9]*\.?[0-9]+(?:[eE][+-]?[0-9]+)?)"
                             r"(?![0-9a-zA-Z_.])")
_GEOM_NAME_REF = re.compile(r"\b(l|w|lr|wr|rl|rw)\s*=\s*([A-Za-z_]\w*)\b", re.I)
_IDENTIFIER = re.compile(r"\b[A-Za-z_]\w*\b")
# Scientific notation anywhere on a code line (comments already stripped):
# number-shaped only — the lookbehind keeps identifier tails (a net named
# 'n1e5') out of the match, so '1e-15', '2.5e-4' and '1e+03' all flag.
_SCI_NOTATION = re.compile(r"(?<![A-Za-z0-9_.])\d+(?:\.\d+)?[eE][+-]?\d+")


def parse_ideal_value(text):
    """Parse an ideal R/C/L netlist value ('1k', '2.5u', '0.1', '1Meg') into the
    base unit (ohm/farad/henry). A bare number is the base unit — unlike geometry
    lengths there is no metres trap for ideal devices. Returns None when ``text``
    is not a plain literal (name, expression or unknown suffix), so the caller
    can skip it instead of guessing."""
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


def _range_text(kind, lo, hi):
    if (lo, hi) == IDEAL_RCL_RANGES[kind]:
        return _IDEAL_RCL_RANGE_TEXT[kind]
    return "%g..%g %s" % (lo, hi, _RCL_UNIT[kind])


def _mult_violation(value, lo, hi):
    """Why a multiplier value is bad, or None when it is a count in range."""
    if isinstance(value, bool):
        return "is not a positive integer (multipliers are counts)"
    if isinstance(value, int):
        num = value
    elif isinstance(value, float) and value.is_integer():
        num = int(value)
    elif isinstance(value, str) and re.fullmatch(r"[0-9]+", value.strip()):
        num = int(value.strip())
    else:
        return "is not a positive integer (multipliers are counts)"
    if not lo <= num <= hi:
        return "outside the multiplier range %d..%d" % (lo, hi)
    return None


def check_netlist_format(text, parameters=None, template_name="template.scs",
                         rcl_ranges=None, mult_ranges=None):
    """Return (errors, warnings) for a Spectre netlist template.

    parameters: the sweep_config.parameters mapping (optional; when given it is
    used for the placeholder-presence and value checks).
    rcl_ranges / mult_ranges: explicit overrides for the ideal R/C/L and nf/m
    ranges (default: the check_params.json tables, falling back to the
    built-in constants; kind -> (low, high) in the base unit).
    """
    errors = []
    warnings = []
    cfg = _check_params()
    unit_check = bool(cfg.get("unit_check", True))
    code = [(i + 1, s.strip()) for i, s in enumerate(text.splitlines())
            if s.strip() and not s.strip().startswith("//")]

    if not any(re.match(r"^simulator\s+lang\s*=\s*spectre\b", s, re.I) for _, s in code):
        errors.append("format: missing 'simulator lang=spectre' declaration")

    for n, s in code:
        low = s.lower()
        for directive in _SPICE_DIRECTIVES:
            if low == directive or low.startswith(directive + " "):
                errors.append("format: SPICE-only directive %r is not valid in a Spectre "
                              "netlist (line %d)" % (directive, n))
                break

    # Scientific notation is banned in netlists (2026-11-25 ruling): every
    # number is a plain decimal or carries an explicit SI suffix, so no scale
    # factor can hide in an exponent form (the retired c=(cpf*1e-15) idiom).
    for n, s in code:
        m = _SCI_NOTATION.search(s)
        if m:
            errors.append("format: scientific notation %r at line %d — netlists use SI "
                          "suffixes (1f, 100M, 200u) or plain decimals, never exponent form"
                          % (m.group(0), n))

    n_sub = sum(1 for _, s in code if re.match(r"^(inline\s+)?subckt\b", s, re.I))
    n_ends = sum(1 for _, s in code if re.match(r"^ends\b", s, re.I))
    if n_sub != n_ends:
        errors.append("format: subckt(%d) and ends(%d) counts do not pair" % (n_sub, n_ends))

    # @@ placement (2026-11-25 ruling): placeholders live on the parameters
    # statement; the single exemption is @@temperature@@ on a simulatorOptions
    # line (spectre does not reliably take a parameter reference there).
    for n, s in code:
        if "@@" not in s:
            continue
        names = _PLACEHOLDER.findall(s)
        if re.match(r"^parameters\b", s, re.I):
            continue
        if _SIMOPTS_STMT.match(s):
            bad = [x for x in names if x != "temperature"]
            if bad:
                errors.append("format: @@%s@@ outside the parameters statement (line %d) "
                              "— only @@temperature@@ is allowed on a simulatorOptions line"
                              % (bad[0], n))
            continue
        errors.append("format: %s placeholder outside the parameters statement (line %d) "
                      "— the circuit must reference parameters by name"
                      % (", ".join("@@%s@@" % x for x in names), n))

    # sweep-parameter presence: @@P@@ <-> config entry, both directions (the
    # worker only substitutes config parameters; anything else reaches spectre
    # as literal text).
    used = set()
    for _, s in code:
        used.update(_PLACEHOLDER.findall(s))
    if parameters is not None:
        declared = set(parameters.keys())
        # Temperature is the one placeholder that may stay undeclared: the
        # sweep worker fills the default 27 degC (2026-11-25 ruling — warn,
        # never block).
        missing = sorted(name for name in used - declared if name != "temperature")
        if missing:
            errors.append("params: template placeholders not declared in "
                          "sweep_config.parameters: %s" % ", ".join(missing))
        if "temperature" in used and "temperature" not in declared:
            warnings.append("params: @@temperature@@ is not declared in "
                            "sweep_config.parameters — the worker fills the "
                            "default 27 (degC)")
        absent = sorted(declared - used)
        if absent:
            errors.append("params: swept parameters without a @@P@@ placeholder in "
                          "the template: %s — the sweep cannot vary them" % ", ".join(absent))
        if declared and not any(re.match(r"^parameters\b", s, re.I) for _, s in code):
            warnings.append("format: no 'parameters' statement found although the sweep "
                            "declares parameters")
        circuit = "\n".join(s for _, s in code if not re.match(r"^parameters\b", s, re.I))
        for name in sorted(declared):
            if not re.search(r"\b%s\b" % re.escape(name), circuit):
                warnings.append("params: swept parameter %s is never referenced by the "
                                "circuit" % name)
    elif used:
        errors.append("params: the template carries @@P@@ placeholders but no sweep "
                      "parameters were given")

    # template-level 'parameters' statement (name -> literal default), used to
    # resolve parameter references in ideal R/C/L and multiplier values (last
    # statement wins)
    template_params = {}
    for _, s in code:
        pm = _PARAMETERS_STMT.match(s)
        if pm:
            for am in _PARAM_ASSIGN.finditer(pm.group(1)):
                template_params[am.group(1)] = am.group(2)

    # Names already checked precisely at an ideal-RCL value position — the
    # prefix heuristic below skips them to avoid duplicate findings.
    resolved_names = set()
    geom_checked = set()
    mult_checked = set()

    def _values_for(name):
        if parameters is not None and name in parameters:
            v = parameters[name]
            return v if isinstance(v, (list, tuple)) else [v]
        if name in template_params:
            return [template_params[name]]
        return []

    ranges = rcl_ranges if rcl_ranges is not None else (cfg.get("rcl") or IDEAL_RCL_RANGES)
    mults = mult_ranges if mult_ranges is not None else (cfg.get("mult") or MULT_RANGES)

    for n, s in code:
        if re.match(r"^parameters\b", s, re.I):
            continue
        im = _RCL_INSTANCE.match(s)
        if not im:
            continue
        kind = _RCL_DEVICE.get(im.group(3).lower())
        if kind is not None:
            # ideal resistor/capacitor/inductor: value range gate (inclusive
            # bounds). Literals and name references (swept values plus the
            # declared default) are handled; anything else — an expression —
            # is REJECTED (2026-11-25 ruling: the scale cannot be verified).
            # The range text follows the ACTIVE range so the message always
            # matches what was enforced.
            lo, hi = ranges[kind]
            range_text = _range_text(kind, lo, hi)
            for am in _RCL_ATTR_VALUE.finditer(s):
                attr, token = am.group(1), am.group(2).rstrip(",;")
                if attr != kind:
                    continue
                if token.startswith("@@"):
                    continue  # the placement pass above reports it
                num = parse_ideal_value(token)
                if num is not None:
                    # 0 is the legal degenerate element (r=0/l=0 short, c=0
                    # open — the 2026-11-25 r=0 ruling extended to every
                    # kind); the range applies above it
                    if num != 0 and not lo <= num <= hi:
                        errors.append("rcl: %s (%s) %s=%s (%g %s) outside the ideal range %s"
                                      % (im.group(1), im.group(3), attr, token, num,
                                         _RCL_UNIT[kind], range_text))
                    continue
                if re.fullmatch(r"[A-Za-z_]\w*", token):
                    resolved_names.add(token)
                    if token not in template_params:
                        errors.append("params: %s (%s) %s=%s references a parameter with "
                                      "no 'parameters' declaration"
                                      % (im.group(1), im.group(3), attr, token))
                        continue
                    for value in _values_for(token):
                        num = _ideal_number(value)
                        if num is not None and num != 0 and not lo <= num <= hi:
                            errors.append("rcl: %s (%s) %s=%s value %r outside the ideal "
                                          "range %s" % (im.group(1), im.group(3), attr,
                                                        token, value, range_text))
                    continue
                errors.append("rcl: %s (%s) %s=%s — expressions are not allowed at ideal "
                              "R/C/L value positions; use %s=<param> with suffixed sweep "
                              "values" % (im.group(1), im.group(3), attr, token, attr))
        else:
            # geometry on non-ideal-RCL lines: the metres trap. A bare number
            # means metres (warning); a swept name must carry u/n suffixes
            # (error) — the whole pass is gated by the unit_check switch.
            if unit_check:
                if _LITERAL_LENGTH.search(s):
                    warnings.append("units: literal geometry value without a unit suffix "
                                    "in %r (Spectre reads it as metres)" % s[:120])
                if parameters is not None:
                    for am in _GEOM_NAME_REF.finditer(s):
                        geom_checked.add(am.group(2))
                        vals = parameters.get(am.group(2))
                        if not isinstance(vals, (list, tuple)):
                            continue
                        for value in vals:
                            if not isinstance(value, str) or not re.fullmatch(
                                    r"[0-9]*\.?[0-9]+\s*[un]", value.strip(), re.I):
                                errors.append("units: %s=%s swept value %r must be a "
                                              "u/n-suffixed string (e.g. '1.5u'); a bare "
                                              "number means metres in Spectre"
                                              % (am.group(1).lower(), am.group(2), value))
                                break
        # multiplier gates (nf/m) apply to every instance line, ideal RCL
        # included; expressions are skipped (unknown scale).
        for am in _MULT_ATTR_VALUE.finditer(s):
            attr, token = am.group(1), am.group(2).rstrip(",;")
            lo, hi = mults[attr]
            if re.fullmatch(r"[0-9]+", token):
                if not lo <= int(token) <= hi:
                    errors.append("mult: %s %s=%s outside the multiplier range %d..%d"
                                  % (im.group(1), attr, token, lo, hi))
                continue
            if re.fullmatch(r"[0-9]*\.?[0-9]+(?:[eE][+-]?[0-9]+)?", token):
                errors.append("mult: %s %s=%s is not a positive integer (multipliers are "
                              "counts)" % (im.group(1), attr, token))
                continue
            if re.fullmatch(r"[A-Za-z_]\w*", token):
                if token not in template_params:
                    errors.append("params: %s %s=%s references a parameter with no "
                                  "'parameters' declaration" % (im.group(1), attr, token))
                    continue
                mult_checked.add(token)
                for value in _values_for(token):
                    bad = _mult_violation(value, lo, hi)
                    if bad:
                        errors.append("mult: %s=%s value %r %s" % (attr, token, value, bad))

    if parameters is not None:
        # exact-name config parameters nf / m
        for attr in ("nf", "m"):
            vals = parameters.get(attr)
            if vals is None or attr in mult_checked:
                continue
            lo, hi = mults[attr]
            for value in (vals if isinstance(vals, (list, tuple)) else [vals]):
                bad = _mult_violation(value, lo, hi)
                if bad:
                    errors.append("mult: swept parameter %s value %r %s" % (attr, value, bad))
        # prefix heuristic (the "find the C-prefixed parameter and check it as
        # a capacitor" net): applies to parameters the netlist has NOT resolved
        # precisely at a bare-name R/C/L value (those skip to avoid duplicate
        # findings). No expression exemption anymore (2026-11-25 ruling):
        # expressions at R/C/L value positions are errors, and a c/r/l-prefixed
        # config value is checkable as-is wherever it is used.
        for name in sorted(parameters.keys()):
            if not re.match(r"^[cCrRlL]", name):
                continue
            if (name in resolved_names or name in geom_checked
                    or name in _LENGTH_ATTRS):
                continue
            kind = name[0].lower()
            lo, hi = ranges[kind]
            range_text = _range_text(kind, lo, hi)
            vals = parameters[name]
            for value in (vals if isinstance(vals, (list, tuple)) else [vals]):
                num = _ideal_number(value)
                if num is not None and num != 0 and not lo <= num <= hi:
                    errors.append("rcl: swept parameter %s value %r outside the ideal %s "
                                  "range %s (name starts with '%s')"
                                  % (name, value, kind.upper(), range_text, name[0]))

    if template_name and not re.fullmatch(r"template[\w.-]*\.scs",
                                          os.path.basename(template_name)):
        warnings.append("naming: netlist_template should be a template*.scs family "
                        "name (default template.scs) matching the uploaded template file")
    return errors, warnings


def format_report(errors, warnings):
    """Human-readable multi-line report used by gen scripts."""
    out = []
    for e in errors:
        out.append("ERROR   " + e)
    for w in warnings:
        out.append("WARNING " + w)
    return "\n".join(out)
