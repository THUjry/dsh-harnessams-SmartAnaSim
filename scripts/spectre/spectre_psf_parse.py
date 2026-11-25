# -*- coding: utf-8 -*-
"""Spectre PSF ASCII result parser — a two-layer contract (worker helper + CLI).

Layer 1, the ENTRY, is soft: parse_psf_ascii_file / parse_psf_ascii_directory
fold every failure (missing file, read error, empty file, unrecognized layout,
per-point errors in batch mode) into a PsfResult (status / data / errors /
warnings / metadata) instead of raising, and the batch CLI never stops on a bad
point — errors are returned to the caller. Layer 2, the ACCESSORS, are hard:
scalar / vector / axis / frequency_hz raise ValueError the moment a value
breaks its rule (exact key, non-empty, finite, real, strictly increasing) for
fail-loud downstream consumers; diagnostics invert that by catching every
violation and collecting (see spectre_post_check.py).

Mechanics (Spectre psfascii output):
- section scan: HEADER / TYPE / SWEEP / TRACE / VALUE / END;
- swept analyses (transient, DC sweep, AC): GROUP -> signal-name mapping,
  complex phasors, and Spectre's delta compression (later steps only carry
  changed signals; missing values fill with NaN);
- non-swept data (DC operating-point ``*.info``): name/value scalars, plus
  one-level STRUCT flattening — a VALUE line whose payload is a quoted-member
  sequence (``"M0" { "gm" 1e-3 "gds" 2e-5 }`` or the bare sequence) flattens
  to ``M0:gm``-style keys; lines that fit no shape stay as warnings;
- S-parameter ``sp.sp``: delegated to ``snp.snp_common.parse_psfascii`` (the
  single S-parameter psfascii parser; kind preference S > Z > Y) and flattened
  to ``freq`` plus ``s11..sNN`` complex curves.

The parser never reads binary PSF: run Spectre with ``-format psfascii`` (the
worker's ``output_format`` default).

CLI:
  python spectre_psf_parse.py --raw-dir <one point raw dir | a standalone .sp file>
          [--out-dir DIR] [--signals a,b] [--format json|csv]
  python spectre_psf_parse.py --work-dir <run dir> [same options]

Outputs per point: <out>/<point>_psf.json (payload: point, raw_dir, status,
errors, warnings, signals) plus a long CSV with --format csv; the summary
<out>/psf_index.json covers EVERY point (failed points included, with their
errors). Exit codes: 0 = at least one point yielded data (per-point errors
ride in the outputs), 1 = nothing parsed, 2 = usage/IO error.
"""
import argparse
import json
import math
import os
import re
import sys
import time
from pathlib import Path

# The scripts tree ships and travels as one unit, so the sibling snp/ package
# is always resolvable next to this file — the same sys.path convention the
# sweep worker uses for common./spectre. imports.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
try:
    from snp.snp_common import parse_psfascii as _snp_parse_psfascii  # noqa: E402
except ImportError:  # pragma: no cover - a split tree was never a layout
    _snp_parse_psfascii = None

_MISSING = math.nan
_SECTIONS = ("HEADER", "TYPE", "SWEEP", "TRACE", "VALUE", "END")
_NUM = r"[-+0-9.eE]+"

# ---------------------------------------------------------------------------
# Layer 1: the entry — a soft-failure result contract (never raises on data)
# ---------------------------------------------------------------------------

STATUS_SUCCESS = "success"    # data present, nothing folded
STATUS_PARTIAL = "partial"    # data present, but errors and/or warnings folded
STATUS_FAILURE = "failure"    # parsed, yet no data (empty file / no signals)
STATUS_ERROR = "error"        # could not even read (missing file / OSError)


class PsfResult(object):
    """Unified parse result: the soft-failure contract carries everything the
    caller needs — the data that did parse, the failures that did not fold
    away, and where they came from. Timing is NOT part of the contract (it
    belongs to the execution layer); metadata holds source/kind/sections."""

    def __init__(self, status, data=None, errors=None, warnings=None, metadata=None):
        self.status = status
        self.data = data if data is not None else {}
        self.errors = errors if errors is not None else []
        self.warnings = warnings if warnings is not None else []
        self.metadata = metadata if metadata is not None else {}

    @property
    def ok(self):
        return self.status == STATUS_SUCCESS


def _scan_raw_root(raw_dir):
    """Resolve the directory that actually holds PSF files.

    Spectre sometimes nests the output inside ``raw_dir / raw_dir.name``.
    """
    if not os.path.isdir(raw_dir):
        return raw_dir
    inner = os.path.join(raw_dir, os.path.basename(os.path.normpath(raw_dir)))
    if os.path.isdir(inner):
        has_psf = (any(f.endswith(".dc") for f in os.listdir(inner))
                   or any(f.endswith(".info") for f in os.listdir(inner))
                   or os.path.isfile(os.path.join(inner, "logFile")))
        if has_psf:
            return inner
    for child in sorted(os.listdir(raw_dir)):
        p = os.path.join(raw_dir, child)
        if os.path.isdir(p) and (any(f.endswith(".dc") for f in os.listdir(p))
                                 or any(f.endswith(".info") for f in os.listdir(p))):
            return p
    return raw_dir


def parse_psf_ascii_file(path):
    """Parse one PSF ASCII file into a PsfResult (soft: never raises)."""
    meta = {"source": str(path)}
    if not os.path.isfile(path):
        return PsfResult(STATUS_ERROR, errors=["PSF ASCII file not found: %s" % path], metadata=meta)
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            content = f.read()
    except OSError as exc:
        return PsfResult(STATUS_ERROR, errors=["cannot read %s: %s" % (path, exc)], metadata=meta)
    if not content.strip():
        return PsfResult(STATUS_FAILURE, errors=["file is empty: %s" % path], metadata=meta)
    res = _parse_psf_ascii_content(content)
    res.metadata["source"] = str(path)
    if res.status == STATUS_SUCCESS and not res.data:
        res.status = STATUS_FAILURE
        res.errors.append("no values parsed: %s" % path)
    return res


def _parse_sp_curves(path):
    """Flatten one Spectre psfascii S-parameter file into curve signals.

    Delegates to snp.snp_common.parse_psfascii (the single sp parser: kind
    preference S > Z > Y, per-port reference impedances) and flattens the
    per-frequency NxN matrices into trace-style signals ``s11..sNN`` plus the
    ``freq`` sweep axis. Soft: delegation failures fold into the result.
    """
    meta = {"source": str(path), "kind": "sp", "sweep_var": "freq"}
    if _snp_parse_psfascii is None:
        return PsfResult(STATUS_ERROR, errors=["snp.snp_common unavailable: %s" % path], metadata=meta)
    try:
        sp = _snp_parse_psfascii(path)
    except Exception as exc:
        return PsfResult(STATUS_ERROR, errors=["cannot parse S-parameter file %s: %s" % (path, exc)], metadata=meta)
    kind = (sp.param or "S").lower()
    out = {"freq": list(sp.freq)}
    for i in range(sp.n_ports):
        for j in range(sp.n_ports):
            out["%s%d%d" % (kind, i + 1, j + 1)] = [m[i][j] for m in sp.mats]
    return PsfResult(STATUS_SUCCESS, data=out, metadata=meta)


def parse_psf_ascii_directory(raw_dir):
    """Merge every PSF ASCII analysis in one raw directory into one PsfResult.

    Soft: a file that fails folds into errors (with its source path) and never
    stops the merge. Transient signals keep their own names, DC signals are
    prefixed ``dc_``, AC signals ``ac_``, S-parameter ``sp.sp`` traces become
    ``freq`` + ``s11..sNN`` complex curves, and ``*.info`` scalars are prefixed
    with the info file stem. metadata['files'] records per-file facts (name,
    prefix, kind, sweep_var, status, n_signals) for diagnostics.
    """
    meta = {"source": str(raw_dir)}
    if not os.path.isdir(raw_dir):
        return PsfResult(STATUS_ERROR, errors=["raw directory does not exist: %s" % raw_dir], metadata=meta)
    raw_dir = _scan_raw_root(raw_dir)
    merged = {}
    errors = []
    warnings = []
    files = []

    def _absorb(res, prefix=""):
        rec = {"name": os.path.basename(res.metadata.get("source", "?")),
               "prefix": prefix,
               "kind": res.metadata.get("kind"),
               "sweep_var": res.metadata.get("sweep_var"),
               "status": res.status,
               "n_signals": len(res.data),
               "signals": sorted(res.data.keys())[:200]}
        files.append(rec)
        src = res.metadata.get("source", "?")
        if res.status in (STATUS_ERROR, STATUS_FAILURE):
            errors.append("%s: %s" % (src, "; ".join(res.errors) or "no data"))
        for w in res.warnings:
            warnings.append("%s: %s" % (src, w))
        for k, v in res.data.items():
            merged[prefix + k] = v

    for candidate in ("tran.tran.tran", "tran.tran"):
        path = os.path.join(raw_dir, candidate)
        if os.path.isfile(path):
            _absorb(parse_psf_ascii_file(path))
            break
    for candidate in ("dc.dc", "dcOp.dc", "spectre.dc"):
        path = os.path.join(raw_dir, candidate)
        if os.path.isfile(path):
            _absorb(parse_psf_ascii_file(path), "dc_")
            break
    for candidate in ("ac.ac", "ac.ac.ac"):
        path = os.path.join(raw_dir, candidate)
        if os.path.isfile(path):
            _absorb(parse_psf_ascii_file(path), "ac_")
            break

    sp_path = os.path.join(raw_dir, "sp.sp")
    if os.path.isfile(sp_path):
        _absorb(_parse_sp_curves(sp_path))

    try:
        listing = sorted(os.listdir(raw_dir))
    except OSError as exc:
        errors.append("cannot list %s: %s" % (raw_dir, exc))
        listing = []
    for name in listing:
        if name.endswith(".sp") and name != "sp.sp":
            warnings.append("S-parameter file %s is not read (the parser reads sp.sp; "
                            "keep the sp analysis instance name bare)" % name)
            continue
        if not name.endswith(".info"):
            continue
        _absorb(parse_psf_ascii_file(os.path.join(raw_dir, name)),
                os.path.splitext(name)[0].replace(".", "_") + "_")

    if merged and (errors or warnings):
        status = STATUS_PARTIAL
    elif merged:
        status = STATUS_SUCCESS
    else:
        status = STATUS_FAILURE
        if not errors:
            errors.append("no PSF ASCII data in %s (binary PSF? use output_format=psfascii)" % raw_dir)
    meta["files"] = files
    return PsfResult(status, data=merged, errors=errors, warnings=warnings, metadata=meta)


def scalar_signals(raw_dir, max_keys=500):
    """Finite numeric scalars from the ``*.info`` files of a raw directory.

    Returns {prefixed_name: float}; capped for point_meta size. Used by the
    worker when sweep_config.signals_from == "psf_info". Flattened STRUCT
    members flow in with the rest; strings, complex members and non-finite
    values stay out. Never raises.
    """
    out = {}
    try:
        if not os.path.isdir(raw_dir):
            return {}
        raw_dir = _scan_raw_root(raw_dir)
        for name in sorted(os.listdir(raw_dir)):
            if not name.endswith(".info"):
                continue
            res = parse_psf_ascii_file(os.path.join(raw_dir, name))
            prefix = os.path.splitext(name)[0].replace(".", "_")
            for k, v in res.data.items():
                if isinstance(v, bool) or not isinstance(v, (int, float)):
                    continue
                if not math.isfinite(v):
                    continue
                out["%s_%s" % (prefix, k)] = v
                if len(out) >= max_keys:
                    return out
    except OSError:
        return {}
    return out


def _parse_psf_ascii_content(content):
    lines = content.splitlines()
    n = len(lines)
    sections = {}
    for i, line in enumerate(lines):
        s = line.strip()
        if s in _SECTIONS:
            sections[s] = i
    if "VALUE" not in sections:
        return PsfResult(STATUS_FAILURE,
                         errors=["no VALUE section (not PSF ASCII?)"],
                         metadata={"sections": sorted(sections)})
    if "SWEEP" in sections:
        res = _parse_swept(lines, n, sections)
    else:
        res = _parse_non_swept(lines, n, sections)
    res.metadata["sections"] = sorted(sections)
    if "HEADER" in sections:
        hi = sections["HEADER"] + 1
        if hi < n and lines[hi].strip():
            res.metadata["psf_header"] = lines[hi].strip()
    return res


def _parse_swept(lines, n, sections):
    sweep_var = ""
    sweep_start = sections["SWEEP"] + 1
    sweep_end = sections.get("TRACE", sections.get("VALUE", n))
    for i in range(sweep_start, sweep_end):
        s = lines[i].strip()
        if not s or s in ("TRACE", "VALUE", "END"):
            break
        m = re.match(r'"([^"]+)"', s)
        if m:
            sweep_var = m.group(1)
            break
    if not sweep_var:
        return PsfResult(STATUS_FAILURE, errors=["SWEEP section declares no variable name"],
                         metadata={"kind": "swept"})

    trace_names = []
    group_to_name = {}
    current_group = None
    if "TRACE" in sections:
        trace_start = sections["TRACE"] + 1
        trace_end = sections.get("VALUE", n)
        for i in range(trace_start, trace_end):
            s = lines[i].strip()
            if not s or s in ("VALUE", "END"):
                break
            m_group = re.match(r'"(\s*\d+)"\s+GROUP\s+\d+', s)
            if m_group:
                current_group = m_group.group(1)
                continue
            m_sig = re.match(r'"([^"]+)"\s+"[^"]*"', s)
            if m_sig:
                sig = m_sig.group(1)
                trace_names.append(sig)
                if current_group is not None:
                    group_to_name[current_group] = sig
                current_group = None
                continue
            m = re.match(r'"([^"]+)"', s)
            if m:
                trace_names.append(m.group(1))

    raw_entries = []
    value_start = sections["VALUE"] + 1
    value_end = sections.get("END", n)
    for i in range(value_start, value_end):
        s = lines[i].strip()
        if not s or s == "END":
            break
        m_c = re.match(r'"([^"]+)"\s+\(\s*(%s)\s+(%s)\s*\)' % (_NUM, _NUM), s)
        if m_c:
            raw_key = m_c.group(1)
            sig_name = group_to_name.get(raw_key, raw_key)
            try:
                val = complex(float(m_c.group(2)), float(m_c.group(3)))
            except ValueError:
                continue
            raw_entries.append((sig_name, val))
            continue
        m = re.match(r'"([^"]+)"\s+(\S+)', s)
        if not m:
            continue
        raw_key = m.group(1)
        if raw_key == sweep_var:
            try:
                raw_entries.append((None, float(m.group(2))))
            except ValueError:
                continue
        else:
            sig_name = group_to_name.get(raw_key, raw_key)
            try:
                raw_entries.append((sig_name, float(m.group(2))))
            except ValueError:
                continue

    sweep_values = []
    state = {}
    series = {name: [] for name in trace_names}
    for key, value in raw_entries:
        if key is None:
            if sweep_values:
                for name in trace_names:
                    series[name].append(state.get(name, _MISSING))
            sweep_values.append(value)
        else:
            state[key] = value
    if sweep_values:
        for name in trace_names:
            series[name].append(state.get(name, _MISSING))

    data = {sweep_var: sweep_values}
    data.update(series)
    warnings = []
    if not trace_names:
        warnings.append("SWEEP file declares no traces (only the sweep axis)")
    status = STATUS_SUCCESS if data else STATUS_FAILURE
    if data and warnings:
        status = STATUS_PARTIAL
    return PsfResult(status, data=data, warnings=warnings,
                     metadata={"kind": "swept", "sweep_var": sweep_var})


# Non-swept VALUE line shapes, full-line anchored so a struct-ish line never
# half-matches into a scalar (the unit-annotated form is the real Spectre
# operating-point shape: "M0:gm" "S" 1.906000e-04).
_RE_SCALAR_UNIT = re.compile(r'^"([^"]+)"\s+"([^"]+)"\s+(%s)$' % _NUM)
_RE_SCALAR = re.compile(r'^"([^"]+)"\s+(%s)$' % _NUM)
_RE_STRING = re.compile(r'^"([^"]+)"\s+"([^"]+)"$')
_RE_KEY_TAIL = re.compile(r'^"([^"]+)"\s+(.+)$', re.S)
_STRUCT_MEMBER = re.compile(r'"([^"]+)"\s+(%s)' % _NUM)


def _flatten_struct_value(line):
    """One-level STRUCT flattening for a non-swept VALUE line.

    Accepts a braced member sequence (``"M0" { "gm" 1e-3 "gds" 2e-5 }``) or a
    bare multi-member sequence (``"M0" "gm" 1e-3 "gds" 2e-5``) and returns
    (key, [(member, value), ...]); a single bare member is left to the
    scalar-with-unit shape above (the two are indistinguishable), so it
    returns None there. Returns None whenever the tail is not a clean
    quoted-member/number sequence — those lines become warnings.
    """
    m = _RE_KEY_TAIL.match(line)
    if not m:
        return None
    key, rest = m.group(1), m.group(2).strip()
    braced = rest.startswith("{") and rest.endswith("}")
    if braced:
        rest = rest[1:-1].strip()
    members = []
    while rest:
        rest = rest.lstrip()
        if not rest:
            break
        m_pair = _STRUCT_MEMBER.match(rest)
        if not m_pair:
            return None
        try:
            value = float(m_pair.group(2))
        except ValueError:
            return None
        members.append((m_pair.group(1), value))
        rest = rest[m_pair.end():]
    if not members:
        return None
    if not braced and len(members) == 1:
        return None
    return key, members


def _parse_non_swept(lines, n, sections):
    data = {}
    warnings = []
    value_start = sections["VALUE"] + 1
    value_end = sections.get("END", n)
    for i in range(value_start, value_end):
        s = lines[i].strip()
        if not s or s == "END":
            break
        m_t = _RE_SCALAR_UNIT.match(s)
        if m_t:
            try:
                data[m_t.group(1)] = float(m_t.group(3))
                continue
            except ValueError:
                pass
        m_n = _RE_SCALAR.match(s)
        if m_n:
            try:
                data[m_n.group(1)] = float(m_n.group(2))
                continue
            except ValueError:
                pass
        m_s = _RE_STRING.match(s)
        if m_s:
            data[m_s.group(1)] = m_s.group(2)
            continue
        flat = _flatten_struct_value(s)
        if flat is not None:
            key, members = flat
            for sub, value in members:
                data["%s:%s" % (key, sub)] = value
            continue
        warnings.append("unparsed VALUE line %d: %s" % (i + 1, s[:160]))
    status = STATUS_SUCCESS if data and not warnings else (
        STATUS_PARTIAL if data else STATUS_FAILURE)
    return PsfResult(status, data=data, warnings=warnings, metadata={"kind": "non-swept"})


# ---------------------------------------------------------------------------
# Layer 2: strict accessors — hard failure at value-retrieval time
# ---------------------------------------------------------------------------

def _key_sample(data, key):
    names = sorted(data.keys())
    sample = ", ".join(names[:8])
    if len(names) > 8:
        sample += ", ... (%d total)" % len(names)
    return "key %r not found (%d keys: %s)" % (key, len(names), sample)


def read_psf_ascii(result):
    """Contract -> data conversion for fail-loud consumers: a not-ok result
    raises ValueError carrying every folded parser error."""
    if not getattr(result, "ok", False):
        detail = "; ".join(result.errors) or "no data"
        raise ValueError("PSF parse not ok (%s): %s" % (result.status, detail))
    return result.data


def scalar(data, key):
    """Exact key -> plain finite number (bool excluded; complex/list/tuple rejected)."""
    if key not in data:
        raise ValueError(_key_sample(data, key))
    value = data[key]
    if isinstance(value, bool):
        raise ValueError("key %r is a bool, expected a number" % key)
    if isinstance(value, (complex, list, tuple)):
        raise ValueError("key %r is a %s, expected a scalar number" % (key, type(value).__name__))
    if not isinstance(value, (int, float)):
        raise ValueError("key %r is a %s, expected a number" % (key, type(value).__name__))
    if not math.isfinite(value):
        raise ValueError("key %r is not finite: %r" % (key, value))
    return value


def vector(data, key):
    """Exact key -> non-empty list of finite numbers (as complex, bool excluded)."""
    if key not in data:
        raise ValueError(_key_sample(data, key))
    values = data[key]
    if not isinstance(values, list):
        raise ValueError("key %r is a %s, expected a list" % (key, type(values).__name__))
    if not values:
        raise ValueError("key %r is an empty list" % key)
    out = []
    for i, v in enumerate(values):
        if isinstance(v, bool) or not isinstance(v, (int, float, complex)):
            raise ValueError("key %r [%d] is a %s, expected a number" % (key, i, type(v).__name__))
        c = complex(v)
        if not (math.isfinite(c.real) and math.isfinite(c.imag)):
            raise ValueError("key %r [%d] is not finite: %r" % (key, i, v))
        out.append(c)
    return out


def axis(data, key):
    """A real, strictly increasing sweep axis: vector + realness + monotonicity."""
    values = vector(data, key)
    for i, c in enumerate(values):
        if c.imag != 0:
            raise ValueError("key %r [%d] must be real, imaginary part %r" % (key, i, c.imag))
    for i in range(len(values) - 1):
        if values[i + 1].real <= values[i].real:
            raise ValueError("key %r must be strictly increasing: values[%d]=%r <= values[%d]=%r"
                             % (key, i + 1, values[i + 1].real, i, values[i].real))
    return [c.real for c in values]


def frequency_hz(data):
    """The 'freq' axis with the full axis rule set (vector + real + monotone)."""
    return axis(data, "freq")


# ---------------------------------------------------------------------------
# Output encoding + CLI
# ---------------------------------------------------------------------------

def _encode_values(values):
    """JSON-safe values: complex pairs [re, im], plain numbers, or null for NaN."""
    has_complex = any(isinstance(v, complex) for v in values)
    out = []
    for v in values:
        if isinstance(v, complex):
            out.append([v.real, v.imag])
        elif isinstance(v, float) and not math.isfinite(v):
            out.append(None)
        else:
            out.append(v)
    return {"complex": has_complex, "values": out}


def _select_signals(data, wanted):
    if not wanted:
        return data
    keep = set(wanted)
    return {k: v for k, v in data.items() if k in keep}


def _write_point(point, raw_dir, res, out_dir, fmt, wanted=None):
    data = _select_signals(res.data, wanted)
    payload = {
        "point": point,
        "raw_dir": raw_dir,
        "status": res.status,
        "errors": list(res.errors),
        "warnings": list(res.warnings),
        "signals": {},
    }
    for name, values in data.items():
        if isinstance(values, list):
            payload["signals"][name] = _encode_values(values)
        else:
            payload["signals"][name] = values
    stem = "%s_psf" % point
    os.makedirs(out_dir, exist_ok=True)
    json_path = os.path.join(out_dir, stem + ".json")
    with open(json_path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(payload, f, indent=2, ensure_ascii=True, allow_nan=False)
        f.write("\n")
    csv_path = None
    if fmt == "csv":
        csv_path = os.path.join(out_dir, stem + ".csv")
        with open(csv_path, "w", encoding="utf-8", newline="\n") as f:
            f.write("point,signal,index,sweep_value,re,im\n")
            for name, values in data.items():
                if not isinstance(values, list):
                    continue
                # the sweep variable itself is emitted as a column, not a row
                for idx, v in enumerate(values):
                    sweep_v = data.get("time", data.get("freq", []))
                    x = sweep_v[idx] if idx < len(sweep_v) else ""
                    if isinstance(v, complex):
                        f.write("%s,%s,%d,%s,%r,%r\n" % (point, name, idx, x, v.real, v.imag))
                    elif isinstance(v, float) and math.isfinite(v):
                        f.write("%s,%s,%d,%s,%r,\n" % (point, name, idx, x, v))
    return json_path, csv_path, len(payload["signals"])


def _point_raw_dir(run_dir, point):
    pdir = os.path.join(run_dir, point)
    candidate = os.path.join(pdir, "netlist.scs.raw")
    if os.path.isdir(candidate):
        return candidate
    if os.path.isdir(pdir):
        for name in sorted(os.listdir(pdir)):
            p = os.path.join(pdir, name)
            if os.path.isdir(p) and name.endswith(".raw"):
                return p
    return None


def _point_names(run_dir):
    return sorted(n for n in os.listdir(run_dir)
                  if re.fullmatch(r"\d{2,}", n) and os.path.isdir(os.path.join(run_dir, n)))


def main(argv=None):
    started = time.monotonic()
    ap = argparse.ArgumentParser(description="parse Spectre psfascii raw output into JSON/CSV curves")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--raw-dir", help="one point's raw output directory (netlist.scs.raw), or a standalone .sp psfascii file")
    src.add_argument("--work-dir", help="a run directory holding NN/netlist.scs.raw")
    ap.add_argument("--out-dir", default=None,
                    help="output directory (default: <raw-dir>/psf, <work-dir>/psf, or beside a standalone .sp file)")
    ap.add_argument("--signals", default=None, help="comma list of signal names to keep")
    ap.add_argument("--format", default="json", choices=["json", "csv"],
                    help="also write per-point long CSV curves (json is always written)")
    args = ap.parse_args(argv)
    wanted = [s.strip() for s in (args.signals or "").split(",") if s.strip()]

    if args.raw_dir:
        raw_dir = os.path.abspath(args.raw_dir)
        if os.path.isfile(raw_dir):
            # a standalone S-parameter psfascii file: outputs land beside it
            out_dir = os.path.abspath(args.out_dir) if args.out_dir else os.path.dirname(raw_dir)
            res = _parse_sp_curves(raw_dir)
            point = os.path.splitext(os.path.basename(raw_dir))[0]
            json_path, csv_path, n = _write_point(point, raw_dir, res, out_dir, args.format, wanted)
            print("[psf] %s: %d signal(s) [%s] -> %s%s"
                  % (point, n, res.status, json_path, (" + " + csv_path) if csv_path else ""))
            return 0 if res.data else 1
        if not os.path.isdir(raw_dir):
            print("raw dir does not exist or is not a directory: %s" % raw_dir, file=sys.stderr)
            return 2
        out_dir = os.path.abspath(args.out_dir) if args.out_dir else os.path.join(raw_dir, "psf")
        res = parse_psf_ascii_directory(raw_dir)
        point = os.path.basename(os.path.dirname(raw_dir)) or "point"
        json_path, csv_path, n = _write_point(point, raw_dir, res, out_dir, args.format, wanted)
        print("[psf] %s: %d signal(s) [%s] -> %s%s"
              % (point, n, res.status, json_path, (" + " + csv_path) if csv_path else ""))
        if res.errors:
            for e in res.errors:
                print("[psf]   error: %s" % e, file=sys.stderr)
        return 0 if res.data else 1

    run_dir = os.path.abspath(args.work_dir)
    if not os.path.isdir(run_dir):
        print("work dir does not exist: %s" % run_dir, file=sys.stderr)
        return 2
    out_dir = os.path.abspath(args.out_dir) if args.out_dir else os.path.join(run_dir, "psf")
    points = _point_names(run_dir)
    if not points:
        print("no point directories (NN) in %s" % run_dir, file=sys.stderr)
        return 1
    index = {"work_dir": run_dir, "duration_ms": round((time.monotonic() - started) * 1000, 1),
             "points": [], "errors": []}
    parsed = 0
    for point in points:
        raw_dir = _point_raw_dir(run_dir, point)
        if not raw_dir:
            index["errors"].append({"point": point, "error": "no *.raw directory"})
            index["points"].append({"point": point, "raw_dir": None, "status": "error",
                                    "errors": ["no *.raw directory"], "warnings": [],
                                    "json": None, "csv": None, "n_signals": 0, "signals": []})
            print("[psf] %s: no *.raw directory" % point, file=sys.stderr)
            continue
        res = parse_psf_ascii_directory(raw_dir)
        data = _select_signals(res.data, wanted)
        json_path, csv_path, n = _write_point(point, raw_dir, res, out_dir, args.format, wanted)
        index["points"].append({"point": point, "raw_dir": raw_dir, "status": res.status,
                                "errors": list(res.errors), "warnings": list(res.warnings),
                                "json": json_path, "csv": csv_path,
                                "n_signals": n, "signals": sorted(data.keys())})
        if res.data:
            parsed += 1
        print("[psf] %s: %d signal(s) [%s]%s"
              % (point, n, res.status,
                 (" (" + "; ".join(res.errors[:3]) + ")") if res.errors else ""))
    index_path = os.path.join(out_dir, "psf_index.json")
    os.makedirs(out_dir, exist_ok=True)
    with open(index_path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(index, f, indent=2, ensure_ascii=True)
        f.write("\n")
    print("[psf] %d/%d point(s) parsed -> %s%s (%.1f ms)"
          % (parsed, len(points), index_path,
             (" (%d error point(s))" % len(index["errors"])) if index["errors"] else "",
             (time.monotonic() - started) * 1000))
    return 0 if parsed else 1


if __name__ == "__main__":
    sys.exit(main())
