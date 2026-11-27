# -*- coding: utf-8 -*-
"""Batch mechanics shared by every sweep worker (Spectre today).

Mechanisms:
- @@PARAM@@ template substitution (@@name@@ or @@name:format@@; unknown
  placeholders stay in place and are recorded in point_meta.unresolved_placeholders,
  except the temperature default below);
- temperature default (2026-11-25 ruling): an undeclared @@temperature@@ is
  substituted as 27 degC and recorded in point_meta.defaulted_params — a
  missing temperature never aborts the simulation;
- combination generation via itertools (cross_product by default, zip for
  equal-length lists);
- zero-padded point directory names (00, 01, ...; plain decimal from 100 on);
- crash-safe JSON writes (sibling .tmp + os.replace);
- sweep_progress.json aggregate with a uniform point/status shape (written on a
  30 s cadence while running and once at the end);
- .worker_started / .compute_node / .simulator markers and errlog.txt helper.

Simulator-specific behaviour (command line, output parsing, timeout policy)
stays in each worker.
"""
import itertools
import json
import os
import re
import sys

from common.common_workspace import atomic_write_json, utc_now, write_errlog

PARAM_RE = re.compile(r"@@(\w+)(?::([^@]*))?@@")
PROGRESS_PERIOD_SEC = 30

# Placeholders that resolve without a sweep entry (2026-11-25 ruling): an
# undeclared @@temperature@@ becomes 27 degC instead of literal text reaching
# spectre — a missing temperature is a warning (spectre_pre_check), never an
# aborted simulation. Other unknown placeholders stay unresolved.
DEFAULT_PARAMS = {"temperature": 27}


def generate_combinations(config):
    """Return (parameter names in config order, list of point parameter dicts).

    Raises ValueError with a user-facing message on a malformed config.
    """
    params = config.get("parameters")
    if not isinstance(params, dict) or not params:
        raise ValueError("parameters must be a non-empty object "
                         "(parameter name -> value list)")
    for k, v in params.items():
        if not isinstance(v, list) or not v:
            raise ValueError("parameters['%s'] must be a non-empty list" % k)
    names = list(params.keys())
    lists = [list(params[k]) for k in names]
    mode = config.get("sweep_mode", "cross_product")
    if mode == "zip":
        if len({len(values) for values in lists}) != 1:
            raise ValueError("sweep_mode=zip requires equal-length value lists")
        combos = [dict(zip(names, values)) for values in zip(*lists)]
    else:
        combos = [dict(zip(names, values)) for values in itertools.product(*lists)]
    return names, combos


def build_netlist(text, params):
    """Substitute @@PARAM@@ placeholders.

    Returns (netlist, unresolved names, defaulted names): an undeclared
    temperature falls back to DEFAULT_PARAMS (27 degC) and lands in the
    defaulted list; every other unknown placeholder stays in place and is
    recorded as unresolved (it reaches spectre as literal text).
    """
    unresolved = []
    defaulted = []

    def repl(m):
        name, fmt = m.group(1), m.group(2)
        if name not in params:
            if name in DEFAULT_PARAMS:
                defaulted.append(name)
                val = DEFAULT_PARAMS[name]
            else:
                unresolved.append(name)
                return m.group(0)
        else:
            val = params[name]
        try:
            return format(val, fmt) if fmt else str(val)
        except (ValueError, TypeError) as e:
            # degraded substitution is still str(val), but say so: a bad
            # format spec used to pass completely silently
            print("[sweep] warning: format spec %r on %s failed (%s); "
                  "substituting str() value" % (fmt, name, e), file=sys.stderr)
            return str(val)

    return PARAM_RE.sub(repl, text), unresolved, defaulted


def point_name(idx):
    """Two-digit point name below 100, plain decimal from 100 on."""
    return "%02d" % idx if idx < 100 else str(idx)


def init_worker_state(work_dir, total, node=None):
    """Write the startup markers a submission layer / poller can look for."""
    host = node or os.environ.get("COMPUTERNAME") or os.environ.get("HOSTNAME") or "local"
    atomic_write_json(os.path.join(work_dir, ".worker_started"), {
        "pid": os.getpid(),
        "host": host,
        "started_at": utc_now(),
        "total_points": total,
    })
    with open(os.path.join(work_dir, ".compute_node"), "w", encoding="utf-8") as f:
        f.write(host)


def write_simulator(work_dir, simulator):
    """Record which simulator produced this run directory (shared collect/report read it)."""
    with open(os.path.join(work_dir, ".simulator"), "w", encoding="utf-8",
              newline="\n") as f:
        f.write(simulator + "\n")


def read_simulator(work_dir):
    path = os.path.join(work_dir, ".simulator")
    if os.path.isfile(path):
        try:
            with open(path, encoding="utf-8-sig") as f:
                name = f.read().strip()
            if name:
                return name
        except OSError:
            pass
    return None


def write_progress(work_dir, names, combos, toolchain=None):
    """Aggregate every point_meta.json into sweep_progress.json.

    Returns the progress dict. Status is 'completed' when every point is
    success/partial, 'failed' when all points finished with a failure, else
    'running'. The optional toolchain dict (the worker's measured
    interpreter/simulator paths, D-006/D-003 telemetry) is carried verbatim:
    a green run's caller reads it back to pin what the job really ran under.
    """
    total = len(combos)
    points = []
    counts = {"success": 0, "partial": 0, "failure": 0,
              "timeout": 0, "error": 0, "pending": 0}
    for i in range(total):
        pdir = os.path.join(work_dir, point_name(i))
        meta_path = os.path.join(pdir, "point_meta.json")
        if os.path.isfile(meta_path):
            try:
                with open(meta_path, encoding="utf-8-sig") as f:
                    m = json.load(f)
                points.append({"index": i, "point_name": point_name(i),
                               "status": m.get("status"),
                               "elapsed_sec": m.get("elapsed_sec"),
                               "params": m.get("params"),
                               "error": m.get("error_message")})
                st = m.get("status") or "error"
                counts[st] = counts.get(st, 0) + 1
                continue
            except (OSError, ValueError):
                pass
        points.append({"index": i, "point_name": point_name(i),
                       "status": "pending", "params": combos[i], "error": None})
        counts["pending"] += 1
    done = total - counts["pending"]
    status = ("completed" if counts["success"] + counts["partial"] == total
              else "failed" if done == total else "running")
    node_file = os.path.join(work_dir, ".compute_node")
    node = "local"
    if os.path.isfile(node_file):
        try:
            with open(node_file, encoding="utf-8-sig") as f:
                node = f.read().strip() or "local"
        except OSError:
            pass
    prog = {
        "work_dir": work_dir,
        "status": status,
        "total_points": total,
        "completed": done,
        "succeeded": counts["success"],
        "failed": counts["failure"] + counts["timeout"] + counts["error"],
        "running": counts["pending"],
        "compute_node": node,
        "parameters": names,
        "simulator": read_simulator(work_dir),
        "points": points,
        "updated_at": utc_now(),
    }
    if toolchain:
        prog["toolchain"] = toolchain
    atomic_write_json(os.path.join(work_dir, "sweep_progress.json"), prog)
    return prog


__all__ = [
    "PARAM_RE", "PROGRESS_PERIOD_SEC",
    "DEFAULT_PARAMS", "generate_combinations",
    "build_netlist",
    "point_name", "init_worker_state", "write_simulator",
    "read_simulator", "write_progress", "utc_now",
    "atomic_write_json", "write_errlog",
]
