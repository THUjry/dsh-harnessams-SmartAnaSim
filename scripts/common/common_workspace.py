# -*- coding: utf-8 -*-
"""Workspace discovery and small shared helpers for the HarnessAMS pipeline scripts.

Convention (shared with the top-level plugin): a workspace `harnessams.json` is
discovered by walking upward from a start directory, either bare or inside a
`harnessams-workspace/` child. Relative path fields resolve against the config
file's own directory, so a checked-in example workspace works from any clone
location.
"""
import json
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
