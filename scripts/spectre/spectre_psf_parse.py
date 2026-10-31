# -*- coding: utf-8 -*-
"""Spectre PSF ASCII result parser (worker helper + standalone CLI).

Mechanisms (Spectre psfascii output):
- section scan: HEADER / TYPE / SWEEP / TRACE / VALUE / END;
- swept analyses (transient, DC sweep, AC): GROUP -> signal-name mapping, complex
  phasor values, and Spectre's delta compression (later steps only carry changed
  signals; missing values are filled with NaN);
- non-swept data (DC operating-point ``*.info`` files): name/value scalars.

The parser never reads binary PSF: run Spectre with ``-format psfascii`` (the
worker's ``output_format`` default) so ``netlist.scs.raw/sp.sp`` and the PSF
files are plain text.

CLI:
  python spectre_psf_parse.py --raw-dir <one point raw dir>   [--out-dir DIR]
          [--signals a,b] [--format json|csv]
  python spectre_psf_parse.py --work-dir <run dir>             [--out-dir DIR]
          [--signals a,b] [--format json|csv]

Outputs per point: <out>/<point>_psf.json (or .csv for the swept curves) and a
summary <out>/psf_index.json. Exit codes: 0 parsed at least one point,
1 nothing parsed, 2 usage/IO error.
"""
import argparse
import json
import math
import os
import re
import sys

_MISSING = math.nan
_SECTIONS = ("HEADER", "TYPE", "SWEEP", "TRACE", "VALUE", "END")


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
    """Parse one PSF ASCII file into {signal: [values]} (or scalars)."""
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            content = f.read()
    except OSError:
        return {}
    if not content.strip():
        return {}
    return _parse_psf_ascii_content(content)


def parse_psf_ascii_directory(raw_dir):
    """Merge every PSF ASCII analysis in one raw directory into {signal: values}.

    Transient signals keep their own names, DC signals are prefixed ``dc_``, AC
    signals ``ac_``, and ``*.info`` scalars are prefixed with the info file stem.
    """
    if not os.path.isdir(raw_dir):
        return {}
    raw_dir = _scan_raw_root(raw_dir)
    merged = {}

    for candidate in ("tran.tran.tran", "tran.tran"):
        path = os.path.join(raw_dir, candidate)
        if os.path.isfile(path):
            data = parse_psf_ascii_file(path)
            if data:
                merged.update(data)
            break

    for candidate in ("dc.dc", "dcOp.dc", "spectre.dc"):
        path = os.path.join(raw_dir, candidate)
        if os.path.isfile(path):
            data = parse_psf_ascii_file(path)
            if data:
                for k, v in data.items():
                    merged["dc_" + k] = v
            break

    for candidate in ("ac.ac", "ac.ac.ac"):
        path = os.path.join(raw_dir, candidate)
        if os.path.isfile(path):
            data = parse_psf_ascii_file(path)
            if data:
                for k, v in data.items():
                    merged["ac_" + k] = v
            break

    for name in sorted(os.listdir(raw_dir)):
        if not name.endswith(".info"):
            continue
        data = parse_psf_ascii_file(os.path.join(raw_dir, name))
        if not data:
            continue
        prefix = os.path.splitext(name)[0].replace(".", "_")
        for k, v in data.items():
            merged["%s_%s" % (prefix, k)] = v
    return merged


def scalar_signals(raw_dir, max_keys=500):
    """Numeric scalars from the ``*.info`` files of a raw directory.

    Returns {prefixed_name: float}; capped for point_meta size. Used by the
    worker when sweep_config.signals_from == "psf_info".
    """
    if not os.path.isdir(raw_dir):
        return {}
    raw_dir = _scan_raw_root(raw_dir)
    out = {}
    for name in sorted(os.listdir(raw_dir)):
        if not name.endswith(".info"):
            continue
        data = parse_psf_ascii_file(os.path.join(raw_dir, name))
        prefix = os.path.splitext(name)[0].replace(".", "_")
        for k, v in data.items():
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                continue
            if not math.isfinite(v):
                continue
            out["%s_%s" % (prefix, k)] = v
            if len(out) >= max_keys:
                return out
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
        return {}
    if "SWEEP" in sections:
        return _parse_swept(lines, n, sections)
    return _parse_non_swept(lines, n, sections)


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
        return {}

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
        m_c = re.match(r'"([^"]+)"\s+\(\s*([-+0-9.eE]+)\s+([-+0-9.eE]+)\s*\)', s)
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
    return data


def _parse_non_swept(lines, n, sections):
    data = {}
    value_start = sections["VALUE"] + 1
    value_end = sections.get("END", n)
    for i in range(value_start, value_end):
        s = lines[i].strip()
        if not s or s == "END":
            break
        m_t = re.match(r'^"([^"]+)"\s+(?:"[^"]+"\s+)([-+0-9.eE]+)', s)
        if m_t:
            try:
                data[m_t.group(1)] = float(m_t.group(2))
            except ValueError:
                pass
            continue
        m_n = re.match(r'^"([^"]+)"\s+([-+0-9.eE]+)', s)
        if m_n:
            try:
                data[m_n.group(1)] = float(m_n.group(2))
            except ValueError:
                pass
            continue
        m = re.match(r'^"([^"]+)"\s+(\S+)', s)
        if m:
            name = m.group(1)
            raw = m.group(2)
            try:
                data[name] = float(raw)
            except ValueError:
                data[name] = raw.strip('"')
    return data


# ---------------------------------------------------------------------------
# Output encoding
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


def _write_point(point, raw_dir, data, out_dir, fmt):
    payload = {"point": point, "raw_dir": raw_dir, "signals": {}}
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
    ap = argparse.ArgumentParser(description="parse Spectre psfascii raw output into JSON/CSV curves")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--raw-dir", help="one point's raw output directory (netlist.scs.raw)")
    src.add_argument("--work-dir", help="a run directory holding NN/netlist.scs.raw")
    ap.add_argument("--out-dir", default=None,
                    help="output directory (default: <raw-dir>/psf or <work-dir>/psf)")
    ap.add_argument("--signals", default=None, help="comma list of signal names to keep")
    ap.add_argument("--format", default="json", choices=["json", "csv"],
                    help="also write per-point long CSV curves (json is always written)")
    args = ap.parse_args(argv)
    wanted = [s.strip() for s in (args.signals or "").split(",") if s.strip()]

    if args.raw_dir:
        raw_dir = os.path.abspath(args.raw_dir)
        if not os.path.isdir(raw_dir):
            print("raw dir does not exist: %s" % raw_dir, file=sys.stderr)
            return 2
        out_dir = os.path.abspath(args.out_dir) if args.out_dir else os.path.join(raw_dir, "psf")
        data = _select_signals(parse_psf_ascii_directory(raw_dir), wanted)
        if not data:
            print("no PSF ASCII data parsed in %s (binary PSF? use output_format=psfascii)"
                  % raw_dir, file=sys.stderr)
            return 1
        point = os.path.basename(os.path.dirname(raw_dir)) or "point"
        json_path, csv_path, n = _write_point(point, raw_dir, data, out_dir, args.format)
        print("[psf] %s: %d signal(s) -> %s%s"
              % (point, n, json_path, (" + " + csv_path) if csv_path else ""))
        return 0

    run_dir = os.path.abspath(args.work_dir)
    if not os.path.isdir(run_dir):
        print("work dir does not exist: %s" % run_dir, file=sys.stderr)
        return 2
    out_dir = os.path.abspath(args.out_dir) if args.out_dir else os.path.join(run_dir, "psf")
    points = _point_names(run_dir)
    if not points:
        print("no point directories (NN) in %s" % run_dir, file=sys.stderr)
        return 1
    index = {"work_dir": run_dir, "points": [], "errors": []}
    parsed = 0
    for point in points:
        raw_dir = _point_raw_dir(run_dir, point)
        if not raw_dir:
            index["errors"].append({"point": point, "error": "no *.raw directory"})
            continue
        data = _select_signals(parse_psf_ascii_directory(raw_dir), wanted)
        if not data:
            index["errors"].append({"point": point, "error": "no PSF ASCII data"})
            continue
        json_path, csv_path, n = _write_point(point, raw_dir, data, out_dir, args.format)
        index["points"].append({"point": point, "raw_dir": raw_dir,
                                "json": json_path, "csv": csv_path,
                                "n_signals": n, "signals": sorted(data.keys())})
        parsed += 1
    index_path = os.path.join(out_dir, "psf_index.json")
    os.makedirs(out_dir, exist_ok=True)
    with open(index_path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(index, f, indent=2, ensure_ascii=True)
        f.write("\n")
    print("[psf] %d/%d point(s) parsed -> %s%s"
          % (parsed, len(points), index_path,
             (" (%d error(s))" % len(index["errors"])) if index["errors"] else ""))
    return 0 if parsed else 1


if __name__ == "__main__":
    sys.exit(main())
