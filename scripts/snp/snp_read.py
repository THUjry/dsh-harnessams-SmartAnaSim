# -*- coding: utf-8 -*-
"""snp_read.py -- parse an S-parameter file and dump per-frequency matrices.

Reads one of the three supported input formats (Touchstone .s1p..sNp, Spectre
psfascii sp output, or a params CSV written by this tool), prints a
summary to stdout, and writes next to the input file (or into --out-dir):

    <stem>_read.json         machine-readable header/summary
    <stem>_params_<X>.csv    long-format per-frequency matrices (X in S/Z/Y),
                             with a '#' metadata header so snp_extract.py can
                             read the CSV back losslessly.

Quick diagnostics are included: reciprocity error max|Xij-Xji|/max|Xij| and
max |Sij| (for S-kind dumps). This tool only reads; it extracts no device
parameters (use snp_extract.py for that).

Usage:
    python snp_read.py --file <path> [--param S|Z|Y] [--out-dir DIR]

Exit codes: 0 success, 2 input/usage error.
"""

import argparse
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import snp_common as sc  # noqa: E402


def _fmt_hz(f):
    if f >= 1e9:
        return "%.4g GHz" % (f / 1e9)
    if f >= 1e6:
        return "%.4g MHz" % (f / 1e6)
    if f >= 1e3:
        return "%.4g kHz" % (f / 1e3)
    return "%.4g Hz" % f


def main(argv=None):
    ap = argparse.ArgumentParser(description="parse an S-parameter file and dump S/Z/Y curves")
    ap.add_argument("--file", required=True, help="input .sNp / psfascii sp.sp / params CSV")
    ap.add_argument("--param", default="S", choices=["S", "Z", "Y"],
                    help="matrix kind to dump (default S)")
    ap.add_argument("--out-dir", default=None, help="output directory (default: beside input)")
    args = ap.parse_args(argv)

    try:
        data = sc.load_any(args.file)
    except sc.SnpError as e:
        print("[snp] error: %s" % e, file=sys.stderr)
        return 2

    out_dir = Path(args.out_dir) if args.out_dir else Path(args.file).resolve().parent
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        print("[snp] error: cannot create out dir: %s" % e, file=sys.stderr)
        return 2
    stem = Path(args.file).stem
    # strip a possible previous _params_X suffix so re-dumps do not stack
    for kind in ("S", "Z", "Y"):
        suf = "_params_%s" % kind
        if stem.endswith(suf):
            stem = stem[: -len(suf)]
    csv_path = out_dir / ("%s_params_%s.csv" % (stem, args.param))
    json_path = out_dir / ("%s_read.json" % stem)

    # convert to the requested kind (through Z)
    try:
        zd = sc.to_z(data)
        if args.param == "S":
            dump = sc.SNPData(zd.freq, [sc.z_to_s2(m, zd.z0) for m in zd.mats],
                              "S", zd.z0, zd.source, zd.fmt_desc, zd.warnings)
        elif args.param == "Y":
            dump = sc.z_to_y(zd)
        else:
            dump = zd
    except sc.SnpError as e:
        print("[snp] error: %s" % e, file=sys.stderr)
        return 2

    sc.write_params_csv(dump, csv_path)

    n = data.n_ports
    max_s = 0.0
    if dump.param == "S":
        for m in dump.mats:
            for row in m:
                for v in row:
                    max_s = max(max_s, abs(v))
    rec = dump.check_reciprocity()
    freq = dump.freq
    summary = {
        "ok": True,
        "source": str(Path(args.file).resolve()),
        "format": data.fmt_desc,
        "backend": sc.BACKEND,
        "n_ports": n,
        "n_freqs": len(freq),
        "freq_min_hz": min(freq) if freq else None,
        "freq_max_hz": max(freq) if freq else None,
        "param": args.param,
        "zref_ohm": dump.z0[:n] if n else [],
        "reciprocity_error": rec,
        "max_abs_S": max_s if dump.param == "S" else None,
        "csv_file": str(csv_path),
        "warnings": data.warnings + dump.warnings,
    }
    sc.write_json_atomic(json_path, summary)

    print("[snp] source: %s" % summary["source"])
    print("[snp] format: %s" % data.fmt_desc)
    print("[snp] ports=%d points=%d band=%s .. %s"
          % (n, len(freq), _fmt_hz(min(freq)) if freq else "-",
             _fmt_hz(max(freq)) if freq else "-"))
    if dump.param == "S":
        print("[snp] max|Sij| = %.4f" % max_s)
    print("[snp] reciprocity error max|Xij-Xji|/max|Xij| = %.3e" % rec)
    for w in summary["warnings"]:
        print("[snp] warning: %s" % w)
    print("[snp] wrote %s" % csv_path)
    print("[snp] wrote %s" % json_path)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception as e:  # keep the 0/2 exit-code contract intact
        print("[snp] error: unexpected failure: %s" % e, file=sys.stderr)
        sys.exit(2)
