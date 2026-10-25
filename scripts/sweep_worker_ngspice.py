# -*- coding: utf-8 -*-
"""HarnessAMS-SmartAnaSim local ngspice batch sweep skeleton.

Mechanisms:
- @@PARAM@@ template substitution (@@name@@ or @@name:format@@; unknown placeholders stay
  in place and are recorded in point_meta.unresolved_placeholders);
- combination generation via itertools (cross_product by default; zip for equal-length lists);
- per-point directories NN/ holding netlist.spi + ngspice.out + point_meta.json
  (atomic writes via a .tmp file + os.replace);
- aggregate progress file sweep_progress.json (written every 30s while running, plus once at the end);
- errlog.txt / .worker_started fallbacks.

ngspice specifics:
- command `ngspice_con -b <netlist>`; no license-wait semantics, a timeout kills the
  process -> status=timeout;
- meas results are parsed straight from stdout lines of the form `name = value`;
- concurrency via ThreadPoolExecutor (external processes; more robust than multiprocessing
  on Windows);
- bestPoints.json is updated per point when best_metric is configured: every better point
  hits the disk immediately, so a crash loses nothing (safety by default).

Usage (inside a workspace, relative to the run directory):
  python <plugin scripts>/sweep_worker_ngspice.py --work-dir sweep/Round1/top [--config sweep_config.json] [--max-parallel 4]

sweep_config.json contract:
  {
    "parameters":  { "<name>": [values...] },      // non-empty dict; each value a non-empty list
    "sweep_mode":  "cross_product" | "zip",        // default cross_product
    "ngspice_cmd": "<path to ngspice_con>",        // default: workspace harnessams.json; relative
                                                   //   values resolve against their config file's dir
    "ngspice_args": ["-b"],
    "netlist_template": "template.spi",
    "timeout_per_point": 120,
    "max_parallel": 4,
    "metrics_required": ["dc_gain_db", ...],       // success criterion; default: all stdout meas
    "best_metric":  {"name": "dc_gain_db", "mode": "max"|"min"}   // optional
  }

Workspace discovery: walks upward for harnessams.json (bare form or inside
harnessams-workspace/), max 6 levels.
Exit codes: all points success -> 0; otherwise 1; startup/config/template errors -> 2 (errlog.txt).
"""
import argparse
import concurrent.futures
import itertools
import json
import os
import re
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone

_PARAM_RE = re.compile(r"@@(\w+)(?::([^@]*))?@@")
_MEAS_RE = re.compile(r"^\s*([A-Za-z_]\w*)\s*=\s*([-+0-9.eE]+)\s*$")
PROGRESS_PERIOD_SEC = 30

stop_flag = threading.Event()
print_lock = threading.Lock()


def utc_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def atomic_write_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, path)


def write_errlog(work_dir, message):
    path = os.path.join(work_dir, "errlog.txt")
    with open(path, "a", encoding="utf-8", newline="\n") as f:
        f.write(f"[{utc_now()}] {message}\n")


def load_workspace_config(start_dir):
    """Walk upward for harnessams.json (bare form or inside harnessams-workspace/).
    Returns (config dict, config directory); ({}, None) when not found."""
    d = os.path.abspath(start_dir)
    for _ in range(6):
        for cand_dir in (d, os.path.join(d, "harnessams-workspace")):
            cand = os.path.join(cand_dir, "harnessams.json")
            if os.path.isfile(cand):
                try:
                    with open(cand, encoding="utf-8") as f:
                        return json.load(f), cand_dir
                except (OSError, ValueError):
                    pass
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    return {}, None


def generate_parameter_combinations(config):
    params = config.get("parameters")
    if not isinstance(params, dict) or not params:
        raise ValueError("sweep_config.parameters must be a non-empty object (parameter name -> value list)")
    for k, v in params.items():
        if not isinstance(v, list) or not v:
            raise ValueError(f"parameters['{k}'] must be a non-empty list")
    lists = [list(params[k]) for k in params]
    names = list(params.keys())
    mode = config.get("sweep_mode", "cross_product")
    if mode == "zip":
        if len({len(l) for l in lists}) != 1:
            raise ValueError("sweep_mode=zip requires equal-length value lists")
        combos = [dict(zip(names, vals)) for vals in zip(*lists)]
    else:
        combos = [dict(zip(names, vals)) for vals in itertools.product(*lists)]
    return names, combos


def build_netlist(text, params):
    unresolved = []
    def repl(m):
        name, fmt = m.group(1), m.group(2)
        if name not in params:
            unresolved.append(name)
            return m.group(0)
        val = params[name]
        try:
            return format(val, fmt) if fmt else str(val)
        except (ValueError, TypeError):
            return str(val)
    out = _PARAM_RE.sub(repl, text)
    return out, unresolved


def make_point_name(idx):
    return f"{idx:02d}" if idx < 100 else str(idx)


def parse_meas(stdout):
    signals = {}
    for line in stdout.splitlines():
        m = _MEAS_RE.match(line)
        if m:
            try:
                signals[m.group(1)] = float(m.group(2))
            except ValueError:
                pass
    return signals


def run_single_point(work_dir, idx, params, config, cmd):
    point_name = make_point_name(idx)
    point_dir = os.path.join(work_dir, point_name)
    os.makedirs(point_dir, exist_ok=True)
    netlist_path = os.path.join(point_dir, "netlist.spi")
    out_path = os.path.join(point_dir, "ngspice.out")

    template_path = os.path.join(work_dir, config.get("netlist_template", "template.spi"))
    with open(template_path, encoding="utf-8") as f:
        netlist, unresolved = build_netlist(f.read(), params)
    with open(netlist_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(netlist)

    argv = [cmd] + list(config.get("ngspice_args", ["-b"])) + [netlist_path]
    timeout = float(config.get("timeout_per_point", 120))
    started = time.monotonic()
    meta = {
        "point_index": idx,
        "point_name": point_name,
        "params": params,
        "status": "error",
        "returncode": None,
        "elapsed_sec": None,
        "error_message": None,
        "signals": {},
        "unresolved_placeholders": unresolved,
        "command": " ".join(argv),
        "started_at": utc_now(),
        "finished_at": None,
    }
    try:
        proc = subprocess.run(argv, capture_output=True, text=True,
                              encoding="utf-8", errors="replace",
                              timeout=timeout, cwd=point_dir)
        stdout = proc.stdout or ""
        with open(out_path, "w", encoding="utf-8", newline="\n") as f:
            f.write(stdout)
            if proc.stderr:
                f.write("\n--- stderr ---\n")
                f.write(proc.stderr)
        meta["returncode"] = proc.returncode
        meta["signals"] = parse_meas(stdout)
        required = config.get("metrics_required")
        if proc.returncode == 0:
            if required:
                missing = [k for k in required if k not in meta["signals"]]
                meta["status"] = "success" if not missing else "partial"
                if missing:
                    meta["error_message"] = "missing metrics: " + ", ".join(missing)
            else:
                meta["status"] = "success" if meta["signals"] else "partial"
        else:
            meta["status"] = "failure"
            meta["error_message"] = extract_error(stdout, proc.stderr)
    except subprocess.TimeoutExpired:
        meta["status"] = "timeout"
        meta["error_message"] = f"timeout after {timeout}s (killed)"
    except OSError as e:
        meta["status"] = "error"
        meta["error_message"] = f"spawn failed: {e}"
    meta["elapsed_sec"] = round(time.monotonic() - started, 3)
    meta["finished_at"] = utc_now()
    atomic_write_json(os.path.join(point_dir, "point_meta.json"), meta)
    return meta


_HARMLESS_WARN = ("iti: has no value", "is negative", "warning: eza0")


def extract_error(stdout, stderr):
    for text in (stderr or "", stdout or ""):
        for line in text.splitlines():
            s = line.strip()
            if not s:
                continue
            low = s.lower()
            if low.startswith("error") or "error:" in low or "failed" in low:
                if any(w in s for w in _HARMLESS_WARN):
                    continue
                return s[:300]
    return None


class BestPoints:
    """Per-point bestPoints.json updates (crash-safe: every better point is atomically
    written to disk immediately)."""

    def __init__(self, work_dir, spec):
        self.path = os.path.join(work_dir, "bestPoints.json")
        self.name = spec.get("name")
        self.mode = spec.get("mode", "max")
        self.best = None
        if not self.name:
            self.path = None
            return
        if os.path.isfile(self.path):
            try:
                with open(self.path, encoding="utf-8") as f:
                    self.best = json.load(f)
            except (OSError, ValueError):
                self.best = None

    def update(self, meta):
        if not self.path or meta.get("status") not in ("success", "partial"):
            return
        val = (meta.get("signals") or {}).get(self.name)
        if val is None:
            return
        if self.best is not None:
            cur = self.best.get("value")
            better = val > cur if self.mode == "max" else val < cur
            if not better:
                return
        self.best = {
            "metric": self.name,
            "mode": self.mode,
            "value": val,
            "point_index": meta["point_index"],
            "point_name": meta["point_name"],
            "params": meta["params"],
            "signals": meta["signals"],
            "updated_at": utc_now(),
        }
        atomic_write_json(self.path, self.best)


def write_aggregate_progress(work_dir, config, names, combos):
    total = len(combos)
    points = []
    counts = {"success": 0, "partial": 0, "failure": 0, "timeout": 0, "error": 0, "pending": 0}
    for i in range(total):
        pdir = os.path.join(work_dir, make_point_name(i))
        meta_path = os.path.join(pdir, "point_meta.json")
        if os.path.isfile(meta_path):
            try:
                with open(meta_path, encoding="utf-8") as f:
                    m = json.load(f)
                points.append({"index": i, "point_name": make_point_name(i),
                               "status": m.get("status"), "elapsed_sec": m.get("elapsed_sec"),
                               "params": m.get("params"), "error": m.get("error_message")})
                counts[m.get("status") or "error"] = counts.get(m.get("status") or "error", 0) + 1
                continue
            except (OSError, ValueError):
                pass
        points.append({"index": i, "point_name": make_point_name(i),
                       "status": "pending", "params": combos[i], "error": None})
        counts["pending"] += 1
    done = total - counts["pending"]
    status = ("completed" if counts["success"] + counts["partial"] == total
              else "failed" if done == total else "running")
    node_file = os.path.join(work_dir, ".compute_node")
    prog = {
        "work_dir": work_dir,
        "status": status,
        "total_points": total,
        "completed": done,
        "succeeded": counts["success"],
        "failed": counts["failure"] + counts["timeout"] + counts["error"],
        "running": counts["pending"],
        "compute_node": (open(node_file, encoding="utf-8").read().strip()
                         if os.path.isfile(node_file) else "local"),
        "parameters": names,
        "points": points,
        "updated_at": utc_now(),
    }
    atomic_write_json(os.path.join(work_dir, "sweep_progress.json"), prog)
    return prog


def main(argv=None):
    ap = argparse.ArgumentParser(description="HarnessAMS-SmartAnaSim local ngspice batch sweep skeleton")
    ap.add_argument("--work-dir", required=True)
    ap.add_argument("--config", default="sweep_config.json")
    ap.add_argument("--max-parallel", type=int, default=None)
    args = ap.parse_args(argv)

    work_dir = os.path.abspath(args.work_dir)
    config_path = args.config if os.path.isabs(args.config) else os.path.join(work_dir, args.config)
    if not os.path.isdir(work_dir):
        print(f"work_dir does not exist: {work_dir}", file=sys.stderr)
        return 2
    try:
        with open(config_path, encoding="utf-8") as f:
            config = json.load(f)
        names, combos = generate_parameter_combinations(config)
    except (OSError, ValueError) as e:
        write_errlog(work_dir, f"config parse failed: {e}")
        print(f"config parse failed: {e}", file=sys.stderr)
        return 2

    template = os.path.join(work_dir, config.get("netlist_template", "template.spi"))
    if not os.path.isfile(template):
        write_errlog(work_dir, f"netlist template missing: {template}")
        print(f"netlist template missing: {template}", file=sys.stderr)
        return 2

    ws, ws_dir = load_workspace_config(work_dir)
    cmd = config.get("ngspice_cmd")
    if cmd and not os.path.isabs(cmd):
        # sweep_config.json lives in work_dir; resolve its relative value there
        cmd = os.path.normpath(os.path.join(work_dir, cmd))
    if not cmd:
        cmd = ws.get("ngspice_cmd")
        if cmd and not os.path.isabs(cmd):
            # harnessams.json path fields resolve against the config file's directory
            cmd = os.path.normpath(os.path.join(ws_dir or work_dir, cmd))
    if not cmd or not os.path.isfile(cmd):
        write_errlog(work_dir, f"ngspice executable not found: {cmd} "
                               "(check config.ngspice_cmd or the workspace harnessams.json)")
        print(f"ngspice executable not found: {cmd}", file=sys.stderr)
        return 2

    max_parallel = args.max_parallel or int(config.get("max_parallel", 4))
    best = BestPoints(work_dir, config.get("best_metric") or {})
    with open(os.path.join(work_dir, ".worker_started"), "w", encoding="utf-8") as f:
        json.dump({"pid": os.getpid(), "host": os.environ.get("COMPUTERNAME", "local"),
                   "started_at": utc_now(), "total_points": len(combos)}, f, ensure_ascii=False)
    with open(os.path.join(work_dir, ".compute_node"), "w", encoding="utf-8") as f:
        f.write("local")

    total = len(combos)
    gto = float(config.get("timeout_per_point", 120)) * total / max(1, max_parallel) + 300
    t0 = time.monotonic()
    print(f"[sweep] {total} points, max_parallel={max_parallel}, timeout/point="
          f"{config.get('timeout_per_point', 120)}s, global budget={gto:.0f}s")

    def runner(i):
        return run_single_point(work_dir, i, combos[i], config, cmd)

    metas = [None] * total
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_parallel) as pool:
        futures = {pool.submit(runner, i): i for i in range(total)}
        next_progress = time.monotonic() + PROGRESS_PERIOD_SEC
        for fut in concurrent.futures.as_completed(futures):
            i = futures[fut]
            try:
                metas[i] = fut.result()
            except Exception as e:  # a single-point exception must not take down the batch
                metas[i] = {"point_index": i, "point_name": make_point_name(i),
                            "params": combos[i], "status": "error",
                            "error_message": f"worker exception: {e}", "signals": {}}
            best.update(metas[i])
            with print_lock:
                m = metas[i]
                print(f"[sweep] {m['point_name']} {m['status']} {m.get('elapsed_sec')}s")
            if time.monotonic() > next_progress:
                write_aggregate_progress(work_dir, config, names, combos)
                next_progress = time.monotonic() + PROGRESS_PERIOD_SEC
            if time.monotonic() - t0 > gto:
                stop_flag.set()
                print(f"[sweep] global budget {gto:.0f}s exhausted; winding down early "
                      f"(unstarted points are not submitted)")
                for f2 in futures:
                    f2.cancel()
                break

    prog = write_aggregate_progress(work_dir, config, names, combos)
    n_ok = sum(1 for m in metas if m and m.get("status") == "success")
    n_partial = sum(1 for m in metas if m and m.get("status") == "partial")
    print(f"[sweep] done: {n_ok} success / {n_partial} partial / {total - n_ok - n_partial} failed"
          f" in {time.monotonic() - t0:.1f}s")
    if best.path and best.best:
        print(f"[sweep] best {best.name} ({best.mode}) = {best.best['value']}"
              f" @ point {best.best['point_name']}")
    return 0 if n_ok + n_partial == total else 1


if __name__ == "__main__":
    sys.exit(main())
