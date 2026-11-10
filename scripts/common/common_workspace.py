# -*- coding: utf-8 -*-
"""Workspace discovery and small shared helpers for the HarnessAMS pipeline scripts.

Convention (shared with the top-level plugin): a workspace `harnessams.json` is
discovered by walking upward from a start directory, either bare or inside a
`harnessams-workspace/` child. Relative path fields resolve against the config
file's own directory, so a checked-in example workspace works from any clone
location.
"""
import json
import math
import os
from datetime import datetime, timezone


def utc_now():
    """UTC timestamp used by every progress / metadata file (second precision)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def atomic_write_json(path, obj):
    """Write JSON through a sibling .tmp file, then os.replace (crash-safe)."""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, path)


def write_errlog(work_dir, message):
    """Append a timestamped line to <work_dir>/errlog.txt (never raises)."""
    try:
        os.makedirs(work_dir, exist_ok=True)
        with open(os.path.join(work_dir, "errlog.txt"), "a",
                  encoding="utf-8", newline="\n") as f:
            f.write("[%s] %s\n" % (utc_now(), message))
    except OSError:
        pass


def find_workspace_config(start_dir, max_levels=8):
    """Walk upward for harnessams.json (bare or inside harnessams-workspace/).

    Returns (config dict, config directory); (None, None) when not found.
    """
    d = os.path.abspath(start_dir)
    for _ in range(max_levels):
        for cand_dir in (d, os.path.join(d, "harnessams-workspace")):
            cand = os.path.join(cand_dir, "harnessams.json")
            if os.path.isfile(cand):
                try:
                    with open(cand, encoding="utf-8-sig") as f:
                        return json.load(f), cand_dir
                except (OSError, ValueError):
                    pass
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    return None, None


def resolve_path(value, base_dir):
    """Config path resolution: absolute values pass through, relative values
    resolve against the config file's directory."""
    if not value:
        return value
    if os.path.isabs(value):
        return value
    return os.path.normpath(os.path.join(base_dir, value))


def resolve_existing_file(value, base_dir, what):
    """Resolve a configured executable/path and require it to exist.

    Returns (absolute path or None, error message or None).
    """
    resolved = resolve_path(value, base_dir)
    if not resolved:
        return None, "%s is not configured" % what
    if not os.path.isfile(resolved):
        return None, "%s not found: %s" % (what, resolved)
    return resolved, None


# ---- check parameters (check_params.json) ----------------------------------
# Shape contract shared with the top-level plugin (HarnessAMS
# src/server/checkparams.js): {"rcl": {"r": [lo, hi], "c": [...], "l": [...]},
# "unit_check": bool, "bias_check": bool, "strict_includes": bool}. The task
# sidebar's check sub-view writes it under <workspace>/.harnessams/; the
# assembly check libraries read it as their default source of switches/bounds.
CHECK_PARAMS_DEFAULTS = {
    "rcl": {"r": (0.1, 1e6), "c": (1e-15, 1e-6), "l": (1e-13, 1e-8)},
    "unit_check": True,
    "bias_check": True,
    "strict_includes": False,
}

_check_params_cache = {}


def _valid_rcl_pair(value):
    """One [lo, hi] pair from an untrusted source: finite, positive, ordered."""
    if (isinstance(value, (list, tuple)) and len(value) == 2
            and all(isinstance(v, (int, float)) and not isinstance(v, bool)
                    and math.isfinite(v) and v > 0 for v in value)
            and value[0] < value[1]):
        return (float(value[0]), float(value[1]))
    return None


def default_check_params():
    """A fresh copy of the built-in defaults (rcl pairs as tuples)."""
    return {
        "rcl": dict(CHECK_PARAMS_DEFAULTS["rcl"]),
        "unit_check": CHECK_PARAMS_DEFAULTS["unit_check"],
        "bias_check": CHECK_PARAMS_DEFAULTS["bias_check"],
        "strict_includes": CHECK_PARAMS_DEFAULTS["strict_includes"],
    }


def read_check_params(start_dir):
    """Check parameters for the workspace tree above start_dir (fail-open).

    Walks up from start_dir (nearest wins, max 8 levels) looking for a
    .harnessams/check_params.json — this matches BOTH placement conventions:
    the HarnessAMS WebUI writes it at the DSH workspace root (next to
    tasks.json/project_settings.json, the parent of harnessams-workspace/),
    while a bare harnessams-workspace checkout serves it from its own root.
    Values merge over the defaults per-key, so a missing or corrupted file can
    never break an assembly run. Cached per resolved file path.
    """
    d = os.path.abspath(start_dir)
    for _ in range(8):
        for cand_dir in (d, os.path.join(d, "harnessams-workspace")):
            cand = os.path.join(cand_dir, ".harnessams", "check_params.json")
            if os.path.isfile(cand):
                cached = _check_params_cache.get(cand)
                if cached is not None:
                    return cached
                params = default_check_params()
                try:
                    with open(cand, encoding="utf-8-sig") as f:
                        raw = json.load(f)
                    if isinstance(raw, dict):
                        raw_rcl = raw.get("rcl")
                        if isinstance(raw_rcl, dict):
                            for kind in ("r", "c", "l"):
                                pair = _valid_rcl_pair(raw_rcl.get(kind))
                                if pair:
                                    params["rcl"][kind] = pair
                        for key in ("unit_check", "bias_check", "strict_includes"):
                            if isinstance(raw.get(key), bool):
                                params[key] = raw[key]
                except (OSError, ValueError):
                    pass
                _check_params_cache[cand] = params
                return params
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    return default_check_params()
