# -*- coding: utf-8 -*-
"""HarnessAMS local ngspice batch sweep worker.

Shared batch mechanics (template substitution, combination generation, point
directories, progress, best points, markers) live in
scripts/common/common_sweep_engine.py.

ngspice specifics:
- command `ngspice_con -b <netlist>`; no license-wait semantics, a timeout kills
  the process -> status=timeout;
- meas results are parsed straight from stdout lines of the form `name = value`;
- concurrency via ThreadPoolExecutor (external processes; more robust than
  multiprocessing on Windows);
- bestPoints.json is updated per point when best_metric is configured: every
  better point hits the disk immediately, so a crash loses nothing.

Usage (inside a workspace, relative to the run directory):
  python <plugin scripts>/ngspice/ngspice_sweep_worker.py --work-dir sweep/Round1/top
         [--config sweep_config.json] [--max-parallel 4]

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
Exit codes: all points success/partial -> 0; otherwise 1; startup/config/template
errors -> 2 (errlog.txt).
"""
import argparse
import concurrent.futures
import json
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common.common_sweep_engine import (  # noqa: E402
    PROGRESS_PERIOD_SEC, BestPoints, atomic_write_json, build_netlist,
    generate_combinations, init_worker_state, point_name, utc_now,
    write_errlog, write_progress, write_simulator,
)
from common.common_workspace import find_workspace_config, resolve_path  # noqa: E402

_MEAS_RE = re.compile(r"^\s*([A-Za-z_]\w*)\s*=\s*([-+0-9.eE]+)\s*$")
_HARMLESS_WARN = ("iti: has no value", "is negative", "warning: eza0")

stop_flag = threading.Event()
print_lock = threading.Lock()


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
    name = point_name(idx)
    point_dir = os.path.join(work_dir, name)
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
        "point_name": name,
        "simulator": "ngspice",
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
        meta["error_message"] = "timeout after %ss (killed)" % timeout
    except OSError as e:
        meta["status"] = "error"
        meta["error_message"] = "spawn failed: %s" % e
    meta["elapsed_sec"] = round(time.monotonic() - started, 3)
    meta["finished_at"] = utc_now()
    atomic_write_json(os.path.join(point_dir, "point_meta.json"), meta)
    return meta


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


def main(argv=None):
    ap = argparse.ArgumentParser(description="HarnessAMS local ngspice batch sweep worker")
    ap.add_argument("--work-dir", required=True)
    ap.add_argument("--config", default="sweep_config.json")
    ap.add_argument("--max-parallel", type=int, default=None)
    args = ap.parse_args(argv)

    work_dir = os.path.abspath(args.work_dir)
    config_path = args.config if os.path.isabs(args.config) else os.path.join(work_dir, args.config)
    if not os.path.isdir(work_dir):
        print("work_dir does not exist: %s" % work_dir, file=sys.stderr)
        return 2
    try:
        with open(config_path, encoding="utf-8-sig") as f:
            config = json.load(f)
        names, combos = generate_combinations(config)
    except (OSError, ValueError) as e:
        write_errlog(work_dir, "config parse failed: %s" % e)
        print("config parse failed: %s" % e, file=sys.stderr)
        return 2

    template = os.path.join(work_dir, config.get("netlist_template", "template.spi"))
    if not os.path.isfile(template):
        write_errlog(work_dir, "netlist template missing: %s" % template)
        print("netlist template missing: %s" % template, file=sys.stderr)
        return 2

    ws, ws_dir = find_workspace_config(work_dir)
    cmd = config.get("ngspice_cmd")
    if cmd and not os.path.isabs(cmd):
        # sweep_config.json lives in work_dir; resolve its relative value there
        cmd = os.path.normpath(os.path.join(work_dir, cmd))
    if not cmd:
        cmd = (ws or {}).get("ngspice_cmd")
        if cmd and not os.path.isabs(cmd):
            # harnessams.json path fields resolve against the config file's directory
            cmd = resolve_path(cmd, ws_dir or work_dir)
    if not cmd or not os.path.isfile(cmd):
        write_errlog(work_dir, "ngspice executable not found: %s "
                               "(check config.ngspice_cmd or the workspace harnessams.json)" % cmd)
        print("ngspice executable not found: %s" % cmd, file=sys.stderr)
        return 2

    max_parallel = args.max_parallel or int(config.get("max_parallel", 4))
    best = BestPoints(work_dir, config.get("best_metric") or {})
    write_simulator(work_dir, "ngspice")
    init_worker_state(work_dir, len(combos))

    total = len(combos)
    gto = float(config.get("timeout_per_point", 120)) * total / max(1, max_parallel) + 300
    t0 = time.monotonic()
    print("[sweep] ngspice: %d points, max_parallel=%d, timeout/point=%ss, global budget=%.0fs"
          % (total, max_parallel, config.get("timeout_per_point", 120), gto))

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
                metas[i] = {"point_index": i, "point_name": point_name(i),
                            "simulator": "ngspice",
                            "params": combos[i], "status": "error",
                            "error_message": "worker exception: %s" % e, "signals": {}}
            best.update(metas[i])
            with print_lock:
                m = metas[i]
                print("[sweep] %s %s %ss" % (m["point_name"], m["status"],
                                            m.get("elapsed_sec")))
            if time.monotonic() > next_progress:
                write_progress(work_dir, names, combos)
                next_progress = time.monotonic() + PROGRESS_PERIOD_SEC
            if time.monotonic() - t0 > gto:
                stop_flag.set()
                print("[sweep] global budget %.0fs exhausted; winding down early "
                      "(unstarted points are not submitted)" % gto)
                for f2 in futures:
                    f2.cancel()
                break

    prog = write_progress(work_dir, names, combos)
    n_ok = sum(1 for m in metas if m and m.get("status") == "success")
    n_partial = sum(1 for m in metas if m and m.get("status") == "partial")
    print("[sweep] done: %d success / %d partial / %d failed in %.1fs"
          % (n_ok, n_partial, total - n_ok - n_partial, time.monotonic() - t0))
    if best.path and best.best:
        print("[sweep] best %s (%s) = %s @ point %s"
              % (best.name, best.mode, best.best["value"], best.best["point_name"]))
    return 0 if prog["status"] in ("completed",) else 1


if __name__ == "__main__":
    sys.exit(main())
