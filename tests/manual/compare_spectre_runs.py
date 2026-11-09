# -*- coding: utf-8 -*-
"""Compare two Spectre run directories (foreground vs batch verification helper).

Usage:
  python -B tests/manual/compare_spectre_runs.py <run-a> <run-b> [--tol 1e-9]

Checks, per point directory NN:
- point_meta.json: status, params, and numeric signals (relative tolerance tol;
  timing fields, commands and paths are ignored);
- netlist.scs.raw/sp.sp: every line except the PSF HEADER ``date`` line must be
  byte-identical (Spectre writes the wall-clock date there; the numeric payload is
  otherwise deterministic for the same version/options).

Exit code 0 when equal, 1 on any mismatch, 2 on usage/IO error. This is a manual
acceptance helper, not shipped with the package.
"""
import argparse
import json
import math
import os
import sys


def point_names(run_dir):
    out = []
    for name in sorted(os.listdir(run_dir)):
        p = os.path.join(run_dir, name)
        if os.path.isdir(p) and name.isdigit() and os.path.isfile(os.path.join(p, "point_meta.json")):
            out.append(name)
    return out


def load_meta(path):
    with open(path, encoding="utf-8-sig") as f:
        return json.load(f)


def close(a, b, tol):
    if isinstance(a, bool) or isinstance(b, bool):
        return a == b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        if math.isnan(a) and math.isnan(b):
            return True
        return abs(a - b) <= max(tol, tol * max(abs(a), abs(b)))
    return a == b


def compare_meta(a, b, tol, errors):
    for key in ("status", "params"):
        if a.get(key) != b.get(key):
            errors.append("point %s: %s differs: %r vs %r" % (a.get("point_name"), key, a.get(key), b.get(key)))
    sa, sb = a.get("signals") or {}, b.get("signals") or {}
    for key in sorted(set(sa) | set(sb)):
        if key not in sa or key not in sb:
            errors.append("point %s: signal %s present on one side only" % (a.get("point_name"), key))
        elif not close(sa[key], sb[key], tol):
            errors.append("point %s: signal %s differs: %r vs %r"
                          % (a.get("point_name"), key, sa[key], sb[key]))


def compare_sp(a_path, b_path, errors, label):
    if not os.path.isfile(a_path) or not os.path.isfile(b_path):
        errors.append("%s: sp.sp missing on one side" % label)
        return 0
    with open(a_path, encoding="utf-8", errors="replace") as f:
        a_lines = f.read().splitlines()
    with open(b_path, encoding="utf-8", errors="replace") as f:
        b_lines = f.read().splitlines()
    if len(a_lines) != len(b_lines):
        errors.append("%s: sp.sp line count %d vs %d" % (label, len(a_lines), len(b_lines)))
        return 0
    diffs = 0
    for i, (la, lb) in enumerate(zip(a_lines, b_lines), start=1):
        if la == lb:
            continue
        if la.startswith('"date"') and lb.startswith('"date"'):
            continue
        diffs += 1
        if diffs <= 3:
            errors.append("%s: sp.sp line %d differs: %r vs %r" % (label, i, la, lb))
    if diffs:
        errors.append("%s: sp.sp %d differing line(s) besides the header date" % (label, diffs))
    return diffs


def main(argv=None):
    ap = argparse.ArgumentParser(description="compare two Spectre run directories")
    ap.add_argument("run_a")
    ap.add_argument("run_b")
    ap.add_argument("--tol", type=float, default=1e-9)
    args = ap.parse_args(argv)
    a_dir, b_dir = os.path.abspath(args.run_a), os.path.abspath(args.run_b)
    for d in (a_dir, b_dir):
        if not os.path.isdir(d):
            print("not a directory: %s" % d, file=sys.stderr)
            return 2

    a_points, b_points = point_names(a_dir), point_names(b_dir)
    errors = []
    if a_points != b_points:
        errors.append("point sets differ: %s vs %s" % (a_points, b_points))
    sp_diffs = 0
    for point in a_points:
        if point not in b_points:
            continue
        compare_meta(load_meta(os.path.join(a_dir, point, "point_meta.json")),
                     load_meta(os.path.join(b_dir, point, "point_meta.json")),
                     args.tol, errors)
        sp_diffs += compare_sp(
            os.path.join(a_dir, point, "netlist.scs.raw", "sp.sp"),
            os.path.join(b_dir, point, "netlist.scs.raw", "sp.sp"),
            errors, "point %s" % point)

    if errors:
        for e in errors:
            print("FAIL " + e)
        print("FAIL: %d mismatch(es) across %d point(s)" % (len(errors), len(a_points)))
        return 1
    print("OK: %d point(s); sp.sp identical except the PSF header date" % len(a_points))
    return 0


if __name__ == "__main__":
    sys.exit(main())
