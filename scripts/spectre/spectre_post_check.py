# -*- coding: utf-8 -*-
"""Collect-style post-simulation data-integrity diagnostics (the post_check tool body).

On-demand: call it when sweep results look wrong, empty or incomplete — it is
NOT part of the routine pipeline (parsing and metrics stay script-face). Given
a sweep run directory (--work-dir) or a single point's raw directory
(--raw-dir), it scans EVERY point (bad points are reported, never skipped) and
inverts the parser's strict accessor rules into collect-all diagnostics: the
same rules (exact keys, non-empty, finite, real, strictly increasing, exactly
one raw dir per point) but every violation is caught and enumerated into a
classified, aggregated issue list instead of raising at the first offender.

Checks:
- coverage: sweep_progress.json vs the NN point dirs on disk (total_points,
  declared-vs-present indexes, completed == succeeded + failed);
- per point: exactly one *.raw directory; point_meta.json presence, status
  census and non-success error tails; scalar rules over point_meta.signals;
  raw_dir path containment;
- per raw dir: the parser's soft entry folds parse errors into the report;
  per swept file the axis rules (real, strictly increasing — enumerated
  exhaustively, not just the first violation) and per-signal vector rules
  (non-empty, finite; NaN delta gaps counted separately from non-finite
  values); signal length vs the axis length; scalar rules over merged
  non-vector values (bool / non-finite);
- errlog.txt presence with a bounded tail.

Output: ONE JSON report on stdout (zero file artifacts). Exit codes: 0 the
checks ran (issues ride in the report), 2 usage/IO error.
"""
import argparse
import json
import math
import os
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from spectre.spectre_psf_parse import (  # noqa: E402
    axis,
    parse_psf_ascii_directory,
    point_names,
    scalar,
    vector,
)

MAX_META_TAIL = 300
MAX_ERRLOG_TAIL = 500


class Report(object):
    """Issue collector: caps the list, counts per check category and per point."""

    def __init__(self, max_issues):
        self.max_issues = max_issues
        self.issues = []
        self.truncated = 0
        self.by_check = {}
        self.by_point = {}

    def add(self, severity, check, detail, point=None, file=None, key=None):
        self.by_check.setdefault(check, {"error": 0, "warn": 0})
        self.by_check[check][severity] = self.by_check[check].get(severity, 0) + 1
        if point is not None:
            self.by_point[point] = self.by_point.get(point, 0) + 1
        if len(self.issues) >= self.max_issues:
            self.truncated += 1
            return
        rec = {"severity": severity, "check": check, "detail": detail}
        if point is not None:
            rec["point"] = point
        if file is not None:
            rec["file"] = file
        if key is not None:
            rec["key"] = key
        self.issues.append(rec)


def _load_json(path):
    """(data, None) or (None, error-text) — soft, never raises."""
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f), None
    except OSError as exc:
        return None, "cannot read %s: %s" % (path, exc)
    except ValueError as exc:
        return None, "invalid JSON in %s: %s" % (path, exc)


def _tail_text(path, limit):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        return ""
    text = text.strip()
    return text[-limit:] if len(text) > limit else text


def _nan_inf_bad_counts(values):
    """(nan_count, inf_count, bad_count): missing delta gaps vs corrupt values
    vs non-numeric items (bool / string / None)."""
    nan_n = inf_n = bad_n = 0
    for v in values:
        if isinstance(v, bool) or not isinstance(v, (int, float, complex)):
            bad_n += 1
            continue
        c = complex(v)
        real_bad = (math.isnan(c.real) or math.isnan(c.imag)
                    or math.isinf(c.real) or math.isinf(c.imag))
        if not real_bad:
            continue
        if math.isinf(c.real) or math.isinf(c.imag):
            inf_n += 1
        else:
            nan_n += 1
    return nan_n, inf_n, bad_n


def _axis_exhaustive(values):
    """Count EVERY axis violation (the collect-all companion to the accessor's
    first-error raise): non-real values and non-increasing adjacent pairs."""
    non_real = 0
    for v in values:
        try:
            c = complex(v)
        except (TypeError, ValueError):
            continue
        if c.imag != 0:
            non_real += 1
    non_mono = 0
    first = None
    for i in range(len(values) - 1):
        try:
            a = complex(values[i]).real
            b = complex(values[i + 1]).real
        except (TypeError, ValueError):
            continue
        if math.isfinite(a) and math.isfinite(b) and b <= a:
            non_mono += 1
            if first is None:
                first = (i, a, b)
    parts = []
    if non_mono:
        parts.append("%d non-increasing pair(s) (first: values[%d]=%r <= values[%d]=%r)"
                     % (non_mono, first[0] + 1, first[2], first[0], first[1]))
    if non_real:
        parts.append("%d non-real value(s)" % non_real)
    return "; ".join(parts)


def _check_raw_data(report, raw_dir, point):
    """Parse one raw dir through the soft entry and collect every rule
    violation. Returns (merged_data, parse_status, n_signals)."""
    res = parse_psf_ascii_directory(raw_dir)
    for err in res.errors:
        report.add("error", "parse", err, point=point)
    for warn in res.warnings:
        report.add("warn", "parse_warning", warn, point=point)
    merged = res.data

    for frec in res.metadata.get("files", []):
        fname = frec.get("name")
        prefix = frec.get("prefix") or ""
        axis_key = None
        if frec.get("sweep_var"):
            axis_key = prefix + frec["sweep_var"]
        axis_len = None
        if axis_key:
            try:
                axis(merged, axis_key)
            except ValueError as exc:
                detail = str(exc)
                extra = _axis_exhaustive(merged.get(axis_key) or [])
                if extra:
                    detail = "%s; exhaustive: %s" % (detail, extra)
                report.add("error", "axis_rule", detail, point=point, file=fname, key=axis_key)
            if isinstance(merged.get(axis_key), list):
                axis_len = len(merged[axis_key])
        # the file record carries unprefixed signal names; merged keys carry
        # the analysis prefix (ac_/dc_) — join them before every lookup
        for sig in frec.get("signals", []):
            if prefix + sig == axis_key:
                continue
            sig_key = prefix + sig
            values = merged.get(sig_key)
            if not isinstance(values, list):
                continue  # scalar values are covered by the merged scalar sweep
            if axis_len is not None and len(values) != axis_len:
                report.add("error", "length_mismatch",
                           "signal %r length %d != axis %r length %d"
                           % (sig_key, len(values), axis_key, axis_len),
                           point=point, file=fname, key=sig_key)
            try:
                vector(merged, sig_key)
            except ValueError as exc:
                nan_n, inf_n, bad_n = _nan_inf_bad_counts(values)
                parts = [str(exc)]
                if nan_n:
                    parts.append("%d missing value(s) (NaN delta gaps)" % nan_n)
                if inf_n:
                    parts.append("%d non-finite value(s)" % inf_n)
                if bad_n:
                    parts.append("%d non-numeric value(s)" % bad_n)
                severity = "warn" if (nan_n and not inf_n and not bad_n) else "error"
                report.add(severity, "signal_value", "; ".join(parts),
                           point=point, file=fname, key=sig_key)

    # scalar rules over the merged non-vector values (strings are legitimate
    # scalars; bool and non-finite numbers are the diagnostic targets)
    for key in sorted(merged):
        value = merged[key]
        if isinstance(value, list) or isinstance(value, str):
            continue
        try:
            scalar(merged, key)
        except ValueError as exc:
            report.add("error", "scalar_value", str(exc), point=point, key=key)

    return merged, res.status, len(merged)


def _check_point_meta(report, work_dir, point, require=True):
    """point_meta.json checks: presence, status census, error tails, scalar
    rules over its signals, raw_dir path containment."""
    pdir = os.path.realpath(os.path.join(work_dir, point))
    meta_path = os.path.join(pdir, "point_meta.json")
    if not os.path.isfile(meta_path):
        if require:
            report.add("error", "point_meta", "point_meta.json missing", point=point)
        return None
    pm, err = _load_json(meta_path)
    if err:
        report.add("error", "point_meta", err, point=point)
        return None
    status = pm.get("status")
    if status is not None and status != "success":
        severity = "warn" if status == "partial" else "error"
        tail = (pm.get("error_message") or "")[:MAX_META_TAIL]
        report.add(severity, "point_status", "status=%s%s"
                   % (status, (": " + tail) if tail else ""), point=point)
    signals = pm.get("signals")
    if isinstance(signals, dict):
        for key in sorted(signals):
            try:
                scalar(signals, key)
            except ValueError as exc:
                report.add("error", "point_meta_signal", str(exc), point=point, key=key)
    raw = pm.get("raw_dir")
    if isinstance(raw, str) and raw:
        contained = False
        outside = []
        for base in (pdir, os.path.realpath(work_dir)):
            resolved = os.path.realpath(raw if os.path.isabs(raw) else os.path.join(base, raw))
            if resolved == pdir or resolved.startswith(pdir + os.sep):
                contained = True
            else:
                outside.append(resolved)
        # a non-local raw_dir (a fetched copy keeps the remote origin path)
        # is not an integrity problem; a LOCAL path that escapes the point
        # directory points at foreign data and is
        if not contained and any(os.path.exists(p) for p in outside):
            report.add("warn", "raw_dir_escape",
                       "raw_dir %r resolves outside the point directory" % raw, point=point)
    return pm


def _index_to_dir(index):
    return str(index) if index >= 100 else "%02d" % index


def _check_progress(report, work_dir, points):
    """sweep_progress.json vs disk: total_points, declared indexes, arithmetic."""
    path = os.path.join(work_dir, "sweep_progress.json")
    if not os.path.isfile(path):
        report.add("warn", "progress",
                   "sweep_progress.json missing (direct CLI run without the worker?)")
        return None
    prog, err = _load_json(path)
    if err:
        report.add("error", "progress", err)
        return None
    total = prog.get("total_points")
    if isinstance(total, int) and total != len(points):
        report.add("warn", "progress",
                   "total_points=%d but %d NN point dir(s) on disk" % (total, len(points)))
    completed, succeeded, failed = prog.get("completed"), prog.get("succeeded"), prog.get("failed")
    if all(isinstance(v, int) for v in (completed, succeeded, failed)):
        if completed != succeeded + failed:
            report.add("warn", "progress",
                       "completed=%d != succeeded(%d) + failed(%d)"
                       % (completed, succeeded, failed))
    declared = set()
    for p in prog.get("points") or []:
        index = p.get("index") if isinstance(p, dict) else None
        if isinstance(index, int):
            declared.add(_index_to_dir(index))
    if declared:
        missing = sorted(declared - set(points))
        extra = sorted(set(points) - declared)
        if missing:
            report.add("warn", "progress",
                       "declared but absent on disk: %s" % ", ".join(missing[:20]))
        if extra:
            report.add("warn", "progress",
                       "on disk but not declared: %s" % ", ".join(extra[:20]))
    return prog


def _raw_dirs_of(pdir):
    if not os.path.isdir(pdir):
        return []
    return sorted(n for n in os.listdir(pdir)
                  if os.path.isdir(os.path.join(pdir, n)) and n.endswith(".raw"))


def _check_work_dir(work_dir, report):
    # the shared point-naming rule (spectre_psf_parse.point_names — the single
    # implementation; numeric order, so a 100-point run lists 11 before 100)
    points = point_names(work_dir)
    prog = _check_progress(report, work_dir, points)
    status_counts = {}
    point_records = []
    for point in points:
        pdir = os.path.join(work_dir, point)
        raw_dirs = _raw_dirs_of(pdir)
        if not raw_dirs:
            report.add("error", "raw_dir_count", "no *.raw directory", point=point)
        elif len(raw_dirs) > 1:
            report.add("warn", "raw_dir_count",
                       "expected exactly one raw dir, found %d: %s (checking the first)"
                       % (len(raw_dirs), ", ".join(raw_dirs)), point=point)
        pm = _check_point_meta(report, work_dir, point)
        pm_status = (pm or {}).get("status")
        if pm_status:
            status_counts[pm_status] = status_counts.get(pm_status, 0) + 1
        parse_status, n_signals = None, 0
        if raw_dirs:
            _, parse_status, n_signals = _check_raw_data(report, os.path.join(pdir, raw_dirs[0]), point)
        point_records.append({"point": point, "raw_dirs": len(raw_dirs),
                              "point_meta_status": pm_status,
                              "parse_status": parse_status, "n_signals": n_signals,
                              "issues": report.by_point.get(point, 0)})
    errlog_tail = None
    errlog_path = os.path.join(work_dir, "errlog.txt")
    if os.path.isfile(errlog_path):
        errlog_tail = _tail_text(errlog_path, MAX_ERRLOG_TAIL)
        report.add("warn", "errlog",
                   "errlog.txt present (startup/config/template errors): %s" % errlog_tail)
    total = prog.get("total_points") if isinstance(prog, dict) else None
    if not isinstance(total, int):
        total = len(points)
    census = {"expected": total, "present": len(points),
              "status_counts": status_counts}
    return census, point_records, errlog_tail


def _check_single_point(raw_dir, report):
    point = os.path.basename(os.path.dirname(os.path.abspath(raw_dir))) or "point"
    _, parse_status, n_signals = _check_raw_data(report, raw_dir, point)
    parent = os.path.dirname(os.path.abspath(raw_dir))
    _check_point_meta(report, parent, os.path.basename(parent) or ".", require=False)
    census = {"expected": 1, "present": 1, "status_counts": {}}
    point_records = [{"point": point, "raw_dirs": 1, "point_meta_status": None,
                      "parse_status": parse_status, "n_signals": n_signals,
                      "issues": report.by_point.get(point, 0)}]
    return census, point_records, None


def run(work_dir=None, raw_dir=None, max_issues=25):
    started = time.monotonic()
    report = Report(max_issues)
    if work_dir:
        mode, target = "work-dir", os.path.abspath(work_dir)
        if not os.path.isdir(target):
            print(json.dumps({"ok": False, "status": "error", "mode": mode, "target": target,
                              "issues": [{"severity": "error", "check": "io",
                                          "detail": "work dir does not exist: %s" % target}],
                              "summary": "work dir does not exist"}))
            return 2
        census, point_records, errlog_tail = _check_work_dir(target, report)
    else:
        mode, target = "raw-dir", os.path.abspath(raw_dir)
        if not os.path.isdir(target):
            print(json.dumps({"ok": False, "status": "error", "mode": mode, "target": target,
                              "issues": [{"severity": "error", "check": "io",
                                          "detail": "raw dir does not exist: %s" % target}],
                              "summary": "raw dir does not exist"}))
            return 2
        census, point_records, errlog_tail = _check_single_point(target, report)

    errors = [i for i in report.issues if i["severity"] == "error"]
    warns = [i for i in report.issues if i["severity"] == "warn"]
    ok = not report.issues and not report.truncated
    status = "success" if ok else ("failure" if errors else "partial")
    counts = ", ".join("%s=%d" % kv for kv in sorted((census["status_counts"] or {}).items()))
    summary = "%d point(s) scanned%s; %d issue(s) (%d error, %d warn) across %d check category(ies)%s" % (
        census["present"], (" (%s)" % counts) if counts else "",
        len(report.issues) + report.truncated, len(errors), len(warns),
        len(report.by_check), ("; %d more truncated" % report.truncated) if report.truncated else "")
    payload = {
        "ok": ok,
        "status": status,
        "mode": mode,
        "target": target,
        "duration_ms": round((time.monotonic() - started) * 1000, 1),
        "points": census,
        "point_records": point_records,
        "checks_summary": report.by_check,
        "issues": report.issues,
        "issues_truncated": report.truncated,
        "summary": summary,
    }
    if errlog_tail:
        payload["errlog_tail"] = errlog_tail
    print(json.dumps(payload, indent=2, ensure_ascii=True))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="collect-style simulation-data integrity diagnostics")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--work-dir", help="a sweep run directory holding NN/ point dirs + sweep_progress.json")
    src.add_argument("--raw-dir", help="one point's raw output directory (netlist.scs.raw)")
    ap.add_argument("--max-issues", type=int, default=25,
                    help="issue list cap before truncation counting (default 25)")
    args = ap.parse_args(argv)
    return run(work_dir=args.work_dir, raw_dir=args.raw_dir, max_issues=args.max_issues)


if __name__ == "__main__":
    sys.exit(main())
