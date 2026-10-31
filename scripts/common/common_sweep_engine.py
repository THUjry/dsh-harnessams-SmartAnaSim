# -*- coding: utf-8 -*-
"""Batch mechanics shared by the ngspice and spectre sweep workers.

Mechanisms (identical for both simulators):
- @@PARAM@@ template substitution (@@name@@ or @@name:format@@; unknown
  placeholders stay in place and are recorded in point_meta.unresolved_placeholders);
- combination generation via itertools (cross_product by default, zip for
  equal-length lists);
- zero-padded point directory names (00, 01, ...; plain decimal from 100 on);
- crash-safe JSON writes (sibling .tmp + os.replace);
- sweep_progress.json aggregate with a uniform point/status shape (written on a
  30 s cadence while running and once at the end);
- bestPoints.json persisted as soon as a better point is found;
- .worker_started / .compute_node / .simulator markers and errlog.txt helper.

Simulator-specific behaviour (command line, output parsing, timeout policy)
stays in each worker.
"""
import itertools
import json
import os
import re

from common.common_workspace import atomic_write_json, utc_now, write_errlog

PARAM_RE = re.compile(r"@@(\w+)(?::([^@]*))?@@")
PROGRESS_PERIOD_SEC = 30


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
    """Substitute @@PARAM@@ placeholders; returns (netlist, unresolved names)."""
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

    return PARAM_RE.sub(repl, text), unresolved


def point_name(idx):
    """Two-digit point name below 100, plain decimal from 100 on."""
    return "%02d" % idx if idx < 100 else str(idx)


class BestPoints:
    """Per-point bestPoints.json updates (crash-safe: every better point is
    atomically written to disk immediately)."""

    def __init__(self, work_dir, spec):
        self.path = os.path.join(work_dir, "bestPoints.json")
        self.name = (spec or {}).get("name")
        self.mode = (spec or {}).get("mode", "max")
        self.best = None
        if not self.name:
            self.path = None
            return
        if os.path.isfile(self.path):
            try:
                with open(self.path, encoding="utf-8-sig") as f:
                    self.best = json.load(f)
            except (OSError, ValueError):
                self.best = None

    def update(self, meta):
        if not self.path or meta.get("status") not in ("success", "partial"):
            return
        val = (meta.get("signals") or {}).get(self.name)
        if val is None or isinstance(val, bool):
            return
        try:
            val = float(val)
        except (TypeError, ValueError):
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


def write_progress(work_dir, names, combos):
    """Aggregate every point_meta.json into sweep_progress.json.

    Returns the progress dict. Status is 'completed' when every point is
    success/partial, 'failed' when all points finished with a failure, else
    'running'.
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
    atomic_write_json(os.path.join(work_dir, "sweep_progress.json"), prog)
    return prog


def load_point_metas(work_dir, combos):
    """Read point_meta.json for every combination index (None where missing)."""
    metas = []
    for i in range(len(combos)):
        path = os.path.join(work_dir, point_name(i), "point_meta.json")
        try:
            with open(path, encoding="utf-8-sig") as f:
                metas.append(json.load(f))
        except (OSError, ValueError):
            metas.append(None)
    return metas


__all__ = [
    "PARAM_RE", "PROGRESS_PERIOD_SEC", "generate_combinations", "build_netlist",
    "point_name", "BestPoints", "init_worker_state", "write_simulator",
    "read_simulator", "write_progress", "load_point_metas", "utc_now",
    "atomic_write_json", "write_errlog",
]
