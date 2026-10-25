# -*- coding: utf-8 -*-
"""Aggregate every point_meta.json in a sweep run directory into results.json
(per-point metrics plus summary statistics).

Usage:
  python <plugin scripts>/collect_results.py --work-dir sweep/Round1/top [--out results.json]
Default output: <work-dir>/results.json. Exit code: 0 when success/partial points exist, else 1.
"""
import argparse
import json
import math
import os
import re
import sys


def load_points(work_dir):
    points = []
    for name in sorted(os.listdir(work_dir)):
        if not re.fullmatch(r"\d{2,}", name):
            continue
        meta_path = os.path.join(work_dir, name, "point_meta.json")
        if not os.path.isfile(meta_path):
            continue
        try:
            with open(meta_path, encoding="utf-8") as f:
                m = json.load(f)
        except (OSError, ValueError):
            continue
        points.append({
            "index": m.get("point_index", int(name)),
            "point_name": m.get("point_name", name),
            "params": m.get("params"),
            "status": m.get("status"),
            "returncode": m.get("returncode"),
            "elapsed_sec": m.get("elapsed_sec"),
            "metrics": m.get("signals") or {},
            "error": m.get("error_message"),
        })
    return points


def summarize(points):
    ok = [p for p in points if p["status"] in ("success", "partial")]
    metric_names = sorted({k for p in ok for k in p["metrics"]})
    summary = {}
    for k in metric_names:
        vals = [p["metrics"][k] for p in ok if isinstance(p["metrics"].get(k), (int, float))
                and math.isfinite(p["metrics"][k])]
        if not vals:
            continue
        summary[k] = {"min": min(vals), "max": max(vals),
                      "mean": sum(vals) / len(vals), "n": len(vals)}
    return summary


def main(argv=None):
    ap = argparse.ArgumentParser(description="Collect sweep results into results.json")
    ap.add_argument("--work-dir", required=True)
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)
    work_dir = os.path.abspath(args.work_dir)
    points = load_points(work_dir)
    if not points:
        print(f"no point_meta.json found: {work_dir}", file=sys.stderr)
        return 1
    result = {
        "work_dir": work_dir,
        "n_points": len(points),
        "n_success": sum(1 for p in points if p["status"] == "success"),
        "n_partial": sum(1 for p in points if p["status"] == "partial"),
        "n_failed": sum(1 for p in points if p["status"] not in ("success", "partial")),
        "points": points,
        "summary": summarize(points),
        "best_points": None,
    }
    best_path = os.path.join(work_dir, "bestPoints.json")
    if os.path.isfile(best_path):
        try:
            with open(best_path, encoding="utf-8") as f:
                result["best_points"] = json.load(f)
        except (OSError, ValueError):
            pass
    out = os.path.abspath(args.out) if args.out else os.path.join(work_dir, "results.json")
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
        f.write("\n")
    print(f"[collect] {result['n_success']} success / {result['n_partial']} partial / "
          f"{result['n_failed']} failed -> {out}")
    for k, v in result["summary"].items():
        print(f"  {k}: min={v['min']:.6g} max={v['max']:.6g} mean={v['mean']:.6g} (n={v['n']})")
    return 0 if result["n_success"] + result["n_partial"] > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
