# -*- coding: utf-8 -*-
"""HarnessAMS local Spectre batch sweep worker.

Runs Spectre point-by-point on the host that executes this script (a Linux
simulation host, or a Windows host with Spectre on PATH). It is NOT coupled to
any remote channel: use the generic bsub tools to submit this script to an LSF
queue when Spectre lives on a cluster login/compute node.

Mechanisms (via scripts/common/common_sweep_engine.py):
- @@PARAM@@ template substitution and combination generation;
- per-point directory NN/ holding netlist.scs, netlist.scs.raw/, spectre.out and
  point_meta.json (atomic writes);
- sweep_progress.json (30 s cadence),
  .worker_started / .compute_node / .simulator markers and errlog.txt.

Spectre specifics:
- command: <spectre_cmd> -64 <netlist> +escchars +log <log> -format <fmt>
  -raw <raw> [spectre_args], then token-exact defaults +lqtimeout 900,
  -maxw 5, -maxn 5, ++aps, +mt, +logstatus. ++aps is used instead of
  "+preset aps" (SPECTRE-132);
- smart timeout: before killing, a log line "Waiting for available license"
  extends the deadline once (license queue) and any file in the raw directory
  extends it once (simulation in progress);
- psfascii output (output_format, default psfascii) is required for the PSF
  parser; sp.sp stays the S-parameter artifact read by the snp_* tools.

Usage:
  python <plugin scripts>/spectre/spectre_sweep_worker.py --work-dir sweep/Round1/top
         [--config sweep_config.json] [--max-parallel 8] [--spectre-cmd spectre]
  python <plugin scripts>/spectre/spectre_sweep_worker.py --work-dir DIR --self-test

sweep_config.json contract:
  {
    "parameters": { "<name>": [values...] },
    "sweep_mode": "cross_product" | "zip",
    "spectre_cmd": "spectre",
    "spectre_args": ["++aps", "+mt", "-env", "ade"],
    "output_format": "psfascii",
    "netlist_template": "template.scs",
    "timeout_per_point": 600,
    "max_parallel": 8,
    "cshrc_path": null,
    "metrics_required": [],
    "signals_from": "none" | "psf_info",
    "license_wait_extension_sec": 120,
    "raw_progress_extension_sec": 120
  }

Exit codes: 0 all points success/partial; 1 some point failed; 2 startup/config/
template/executable error (errlog.txt).
"""
import argparse
import concurrent.futures
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common.common_sweep_engine import (  # noqa: E402
    PROGRESS_PERIOD_SEC, atomic_write_json, build_netlist,
    generate_combinations, init_worker_state, point_name, utc_now,
    write_errlog, write_progress, write_simulator,
)
from common.common_workspace import find_workspace_config, resolve_path  # noqa: E402
from spectre.spectre_psf_parse import parse_psf_ascii_file, scalar_signals  # noqa: E402

print_lock = threading.Lock()


def _has_flag(args, flag):
    for a in args:
        a = str(a)
        if a == flag or a.startswith(flag + "="):
            return True
    return False


def build_spectre_cmd(spectre_cmd, spectre_args, output_format, netlist_path,
                      raw_dir, log_file):
    """Assemble the Spectre command line (token-exact default dedup)."""
    args = [str(a) for a in (spectre_args or [])]
    cmd = [spectre_cmd, "-64", netlist_path, "+escchars"]
    if log_file:
        cmd.extend(["+log", log_file])
    if output_format:
        cmd.extend(["-format", output_format])
    if raw_dir:
        cmd.extend(["-raw", raw_dir])
    cmd.extend(args)
    if not _has_flag(args, "+lqtimeout"):
        cmd.extend(["+lqtimeout", "900"])
    if not _has_flag(args, "-maxw"):
        cmd.extend(["-maxw", "5"])
    if not _has_flag(args, "-maxn"):
        cmd.extend(["-maxn", "5"])
    if not _has_flag(args, "++aps") and not _has_flag(args, "+preset"):
        cmd.append("++aps")
    if not _has_flag(args, "+mt"):
        cmd.append("+mt")
    cmd.append("+logstatus")
    return cmd


def scan_signals(raw_dir):
    """Names of PSF signals available for this point (bounded, diagnostic only)."""
    if not os.path.isdir(raw_dir):
        return []
    names = set()
    try:
        for name in sorted(os.listdir(raw_dir)):
            path = os.path.join(raw_dir, name)
            if not os.path.isfile(path):
                continue
            if name.endswith(".info"):
                names.update(parse_psf_ascii_file(path).keys())
            elif name.endswith((".tran", ".dc", ".ac")):
                names.add(re.sub(r"\.(tran|dc|ac)+$", "", name))
    except OSError:
        pass
    return sorted(names)[:200]


def _extract_err(text):
    for pat in (r"ERROR\s*\([^)]*\):\s*(.*)", r"Fatal\s+error:\s*(.*)", r"Error:\s*(.*)"):
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            return m.group(0).strip()[:500]
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    return lines[-1][:500] if lines else "Unknown error"


def _drain(stream, buf):
    try:
        for line in iter(stream.readline, ""):
            buf.append(line)
    finally:
        try:
            stream.close()
        except Exception:
            pass


def run_single_point(work_dir, idx, params, config, spectre_cmd):
    name = point_name(idx)
    pdir = os.path.join(work_dir, name)
    os.makedirs(pdir, exist_ok=True)
    template = os.path.join(work_dir, config.get("netlist_template", "template.scs"))
    result = {
        "point_index": idx,
        "point_name": name,
        "simulator": "spectre",
        "params": dict(params),
        "status": "error",
        "returncode": -1,
        "elapsed_sec": 0,
        "error_message": None,
        "signals": {},
        "signals_available": [],
        "unresolved_placeholders": [],
        "raw_dir": None,
        "command": "",
        "license_wait_extended": False,
        "raw_progress_extended": False,
        "started_at": utc_now(),
        "finished_at": None,
    }
    try:
        with open(template, encoding="utf-8") as f:
            text, unresolved = build_netlist(f.read(), params)
    except FileNotFoundError:
        msg = "netlist template not found: %s" % template
        write_errlog(work_dir, msg)
        result["error_message"] = msg
        result["finished_at"] = utc_now()
        atomic_write_json(os.path.join(pdir, "point_meta.json"), result)
        return result
    result["unresolved_placeholders"] = unresolved
    netlist_path = os.path.join(pdir, "netlist.scs")
    with open(netlist_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)

    raw_dir = os.path.join(pdir, "netlist.scs.raw")
    log_file = os.path.join(pdir, "spectre.out")
    cmd = build_spectre_cmd(
        spectre_cmd=spectre_cmd,
        spectre_args=config.get("spectre_args"),
        output_format=config.get("output_format", "psfascii"),
        netlist_path=netlist_path,
        raw_dir=raw_dir,
        log_file=log_file,
    )
    result["command"] = " ".join(cmd)
    result["raw_dir"] = raw_dir
    timeout = float(config.get("timeout_per_point", 600))
    lic_ext = float(config.get("license_wait_extension_sec", 120))
    raw_ext = float(config.get("raw_progress_extension_sec", 120))
    cshrc = config.get("cshrc_path")

    started = time.monotonic()
    try:
        if cshrc and os.path.isfile(cshrc):
            proc = subprocess.Popen(["csh", "-c", "source %s; %s" % (cshrc, " ".join(cmd))],
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    text=True, cwd=pdir)
        else:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    text=True, cwd=pdir)
        out_buf, err_buf = [], []
        t_out = threading.Thread(target=_drain, args=(proc.stdout, out_buf), daemon=True)
        t_err = threading.Thread(target=_drain, args=(proc.stderr, err_buf), daemon=True)
        t_out.start()
        t_err.start()
        effective_timeout = timeout
        while True:
            retcode = proc.poll()
            elapsed = time.monotonic() - started
            if retcode is not None:
                t_out.join(timeout=10)
                t_err.join(timeout=10)
                result["elapsed_sec"] = round(elapsed, 2)
                result["returncode"] = retcode
                stderr = "".join(err_buf)
                raw_ok = os.path.isdir(raw_dir)
                if retcode == 0:
                    result["status"] = "success"
                elif raw_ok:
                    result["status"] = "partial"
                    result["error_message"] = _extract_err(stderr)
                else:
                    result["status"] = "failure"
                    result["error_message"] = _extract_err(stderr or "".join(out_buf))
                if raw_ok:
                    result["signals_available"] = scan_signals(raw_dir)
                break
            if elapsed > effective_timeout:
                active = False
                if os.path.isfile(log_file):
                    try:
                        with open(log_file, encoding="utf-8", errors="replace") as f:
                            log_text = f.read()
                    except OSError:
                        log_text = ""
                    if "Waiting for available license" in log_text and not result["license_wait_extended"]:
                        effective_timeout = elapsed + lic_ext
                        result["license_wait_extended"] = True
                        active = True
                if os.path.isdir(raw_dir) and os.listdir(raw_dir) and not result["raw_progress_extended"]:
                    effective_timeout = elapsed + raw_ext
                    result["raw_progress_extended"] = True
                    active = True
                if not active:
                    try:
                        proc.kill()
                    except OSError:
                        pass
                    result["status"] = "timeout"
                    result["elapsed_sec"] = round(elapsed, 2)
                    result["error_message"] = (
                        "Timeout after %.0fs (configured %ss, no output, no license wait)"
                        % (elapsed, timeout))
                    result["returncode"] = -1
                    break
            time.sleep(2)
    except FileNotFoundError:
        result["status"] = "error"
        result["error_message"] = "Spectre not found: %s" % spectre_cmd
    except Exception:
        result["status"] = "error"
        result["error_message"] = traceback.format_exc()
    result["elapsed_sec"] = result.get("elapsed_sec") or round(time.monotonic() - started, 2)

    if config.get("signals_from") == "psf_info" and os.path.isdir(raw_dir):
        try:
            result["signals"] = scalar_signals(raw_dir)
        except Exception:
            result["signals"] = {}
    required = config.get("metrics_required") or []
    if required and result["status"] in ("success", "partial"):
        missing = [k for k in required if k not in result["signals"]]
        if missing:
            result["status"] = "partial"
            result["error_message"] = "missing metrics: " + ", ".join(missing)
    result["finished_at"] = utc_now()
    atomic_write_json(os.path.join(pdir, "point_meta.json"), result)
    return result


def resolve_spectre_cmd(config, work_dir, ws, ws_dir, override=None):
    cmd = override or config.get("spectre_cmd")
    if cmd and not os.path.isabs(cmd) and os.sep in cmd:
        cmd = os.path.normpath(os.path.join(work_dir, cmd))
    if not cmd:
        cmd = (ws or {}).get("spectre_cmd")
        if cmd and not os.path.isabs(cmd) and os.sep in cmd:
            cmd = resolve_path(cmd, ws_dir or work_dir)
    if not cmd:
        cmd = "spectre"
    if not os.path.isabs(cmd) and os.sep not in cmd:
        found = shutil.which(cmd)
        return found or cmd
    return cmd


def main(argv=None):
    ap = argparse.ArgumentParser(description="HarnessAMS local Spectre batch sweep worker")
    ap.add_argument("--work-dir", required=True)
    ap.add_argument("--config", default="sweep_config.json")
    ap.add_argument("--max-parallel", type=int, default=None)
    ap.add_argument("--spectre-cmd", default=None)
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

    template = os.path.join(work_dir, config.get("netlist_template", "template.scs"))
    if not os.path.isfile(template):
        msg = "netlist template not found: %s (config netlist_template=%r)" % (
            template, config.get("netlist_template", "template.scs"))
        write_errlog(work_dir, msg)
        print(msg, file=sys.stderr)
        return 2

    ws, ws_dir = find_workspace_config(work_dir)
    spectre_cmd = resolve_spectre_cmd(config, work_dir, ws, ws_dir, args.spectre_cmd)
    probe = spectre_cmd if os.path.isfile(spectre_cmd) else shutil.which(spectre_cmd)
    if not probe:
        msg = ("Spectre executable not found: %r (set sweep_config.spectre_cmd, the "
               "workspace spectre_cmd, or --spectre-cmd; remote runs use the bsub tools)"
               % spectre_cmd)
        write_errlog(work_dir, msg)
        print(msg, file=sys.stderr)
        return 2
    spectre_cmd = probe

    max_parallel = args.max_parallel or int(config.get("max_parallel", 4))
    write_simulator(work_dir, "spectre")
    init_worker_state(work_dir, len(combos))

    total = len(combos)
    gto = float(config.get("timeout_per_point", 600)) * total / max(1, max_parallel) + 300
    t0 = time.monotonic()
    print("[sweep] spectre: %d points, max_parallel=%d, timeout/point=%ss, global budget=%.0fs"
          % (total, max_parallel, config.get("timeout_per_point", 600), gto))

    def runner(i):
        return run_single_point(work_dir, i, combos[i], config, spectre_cmd)

    metas = [None] * total
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_parallel) as pool:
        futures = {pool.submit(runner, i): i for i in range(total)}
        next_progress = time.monotonic() + PROGRESS_PERIOD_SEC
        for fut in concurrent.futures.as_completed(futures):
            i = futures[fut]
            try:
                metas[i] = fut.result()
            except Exception as e:
                metas[i] = {"point_index": i, "point_name": point_name(i),
                            "simulator": "spectre", "params": combos[i],
                            "status": "error", "elapsed_sec": 0,
                            "error_message": "worker exception: %s" % e,
                            "signals": {}}
            with print_lock:
                m = metas[i]
                print("[sweep] %s %s %ss" % (m["point_name"], m["status"],
                                            m.get("elapsed_sec")))
            if time.monotonic() > next_progress:
                write_progress(work_dir, names, combos)
                next_progress = time.monotonic() + PROGRESS_PERIOD_SEC
            if time.monotonic() - t0 > gto:
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
    return 0 if prog["status"] == "completed" else 1


if __name__ == "__main__":
    sys.exit(main())
