# -*- coding: utf-8 -*-
"""Small shared helpers for the HarnessAMS pipeline scripts.

The family-level `harnessams.json` discovery is retired (2026-11-19, D-010):
config is per-project now (<project>/.harnessams/workspace_config.json on the
plugin side), and the only consumer the family walker had left — the sweep
worker's spectre_cmd lookup — takes the executable from --spectre-cmd,
sweep_config.spectre_cmd, or PATH. What remains here: timestamps, atomic
writes, the errlog helper, and the per-session check-parameter chain below.
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


# ---- check parameters (check_params.json) ----------------------------------
# Shape contract shared with the top-level plugin (HarnessAMS
# src/server/checkparams.js) and the pre-check engine (SmartAnaSim
# src/precheck.js): {"rcl": {"r": [lo, hi], "c": [...], "l": [...]},
# "mult": {"nf": [lo, hi], "m": [...]}, "unit_check": bool}. The task
# sidebar's check sub-view writes it under <workspace>/.harnessams/; the check
# faces (spectre_pre_check tool, spectre_checks.py) read it as their default
# source of bounds/switches. Legacy bias_check / strict_includes keys are
# ignored on read (removed 2026-11-25 with their check families).
CHECK_PARAMS_DEFAULTS = {
    "rcl": {"r": (0.1, 1e6), "c": (1e-15, 1e-6), "l": (1e-13, 1e-8)},
    "mult": {"nf": (1, 128), "m": (1, 1000)},
    "unit_check": True,
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


def _valid_mult_pair(value):
    """One [lo, hi] multiplier pair: positive integers, ordered."""
    if (isinstance(value, (list, tuple)) and len(value) == 2
            and all(isinstance(v, int) and not isinstance(v, bool) and v > 0
                    for v in value)
            and value[0] < value[1]):
        return (int(value[0]), int(value[1]))
    return None


def default_check_params():
    """A fresh copy of the built-in defaults (rcl/mult pairs as tuples)."""
    return {
        "rcl": dict(CHECK_PARAMS_DEFAULTS["rcl"]),
        "mult": dict(CHECK_PARAMS_DEFAULTS["mult"]),
        "unit_check": CHECK_PARAMS_DEFAULTS["unit_check"],
    }


def _merge_check_layer(params, raw):
    """One layer (project file or session table) merged over params, per key,
    fail-open. Unknown keys are ignored."""
    if not isinstance(raw, dict):
        return
    raw_rcl = raw.get("rcl")
    if isinstance(raw_rcl, dict):
        for kind in ("r", "c", "l"):
            pair = _valid_rcl_pair(raw_rcl.get(kind))
            if pair:
                params["rcl"][kind] = pair
    raw_mult = raw.get("mult")
    if isinstance(raw_mult, dict):
        for kind in ("nf", "m"):
            pair = _valid_mult_pair(raw_mult.get(kind))
            if pair:
                params["mult"][kind] = pair
    if isinstance(raw.get("unit_check"), bool):
        params["unit_check"] = raw["unit_check"]


def _read_project_params(cand):
    """Project layer: .harnessams/check_params.json merged over defaults (cached)."""
    cached = _check_params_cache.get(cand)
    if cached is not None:
        return cached
    params = default_check_params()
    try:
        with open(cand, encoding="utf-8-sig") as f:
            raw = json.load(f)
        _merge_check_layer(params, raw)
    except (OSError, ValueError):
        pass
    _check_params_cache[cand] = params
    return params


def _apply_session_params(sessions_path, session_id, params):
    """Session layer: the calling session's confirmed table (sessions.json,
    HarnessAMS D-009 shape {sessions: {<sid>: {check_params: …}}}) merged
    per-key over the project layer. Fail-open: a missing/corrupt file or a
    session without its own table leaves the project layer untouched."""
    cache_key = (sessions_path, session_id)
    cached = _check_params_cache.get(cache_key)
    if cached is not None:
        return cached
    out = {"rcl": dict(params["rcl"]), "mult": dict(params["mult"]),
           "unit_check": params["unit_check"]}
    try:
        with open(sessions_path, encoding="utf-8-sig") as f:
            raw = json.load(f)
        sessions = raw.get("sessions") if isinstance(raw, dict) else None
        entry = sessions.get(session_id) if isinstance(sessions, dict) else None
        sess = entry.get("check_params") if isinstance(entry, dict) else None
        _merge_check_layer(out, sess)
    except (OSError, ValueError):
        pass
    _check_params_cache[cache_key] = out
    return out


def read_check_params(start_dir):
    """Effective check parameters for the workspace tree above start_dir
    (fail-open). Resolution chain (HarnessAMS D-009, per-session params):

        built-in defaults  <-  .harnessams/check_params.json (project layer)
                           <-  the CALLING session's confirmed table in
                               .harnessams/sessions.json (session layer, only
                               when the DSH_SESSION_ID env var names a session
                               with its own table)

    DSH_SESSION_ID reaches this process through two channels: the host's
    dsh-shell-env built-in (model-run bash/pwsh calls) or the SmartAnaSim tool
    shell passing exec.agent.id (which IS the SessionId). Walks up from
    start_dir (nearest wins, max 8 levels) looking for a .harnessams store —
    this matches BOTH placement conventions: the DSH workspace root (the
    parent of harnessams-workspace/) and a bare harnessams-workspace checkout.
    Values merge per-key, so a missing or corrupted file can never break an
    assembly run. Cached per resolved file path (+ session id for the top
    layer); each tool call runs in a fresh python process, so the cache never
    outlives a run.
    """
    session_id = os.environ.get("DSH_SESSION_ID") or None
    d = os.path.abspath(start_dir)
    for _ in range(8):
        for cand_dir in (d, os.path.join(d, "harnessams-workspace")):
            store = os.path.join(cand_dir, ".harnessams")
            if not os.path.isdir(store):
                continue
            params = _read_project_params(os.path.join(store, "check_params.json"))
            if session_id:
                sessions_path = os.path.join(store, "sessions.json")
                if os.path.isfile(sessions_path):
                    params = _apply_session_params(sessions_path, session_id, params)
            return params
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    return default_check_params()
