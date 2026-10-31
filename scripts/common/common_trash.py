# -*- coding: utf-8 -*-
"""HarnessAMS trash tool (safety by default: deletion never destroys).

Moves the given paths into the workspace trash directory (<timestamp>_<name>/ entries) and
prints the new location; --restore brings an entry back. The trash directory comes from the
workspace harnessams.json trash_dir field when present (relative values resolve against the
config file's directory), defaulting to <workspace>/.trash. The workspace is found by walking
upward for harnessams.json (bare form or inside harnessams-workspace/).

Usage:
  python <plugin scripts>/common/common_trash.py <path>...                       # move into the trash
  python <plugin scripts>/common/common_trash.py --list                          # list trash contents
  python <plugin scripts>/common/common_trash.py --restore <trash-name> <dest>   # restore an entry
  python <plugin scripts>/common/common_trash.py --purge <trash-name>            # really destroy (explicit; no --all)
"""
import argparse
import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common.common_workspace import find_workspace_config, resolve_path  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser(description="trash: deletion never destroys by default")
    ap.add_argument("paths", nargs="*", help="paths to move into the trash")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--restore", nargs=2, metavar=("TRASH_NAME", "DEST_DIR"))
    ap.add_argument("--purge", metavar="TRASH_NAME")
    args = ap.parse_args(argv)

    first = args.paths[0] if args.paths else "."
    cfg, cfg_dir = find_workspace_config(first if os.path.exists(first)
                                         else os.path.dirname(os.path.abspath(first)))
    if cfg is None:
        print("workspace not found (no harnessams.json walking upward); "
              "trash location cannot be determined", file=sys.stderr)
        return 2
    trash = resolve_path(cfg.get("trash_dir"), cfg_dir) or os.path.join(cfg_dir, ".trash")

    if args.list:
        if not os.path.isdir(trash):
            print("[]")
            return 0
        items = []
        for name in sorted(os.listdir(trash)):
            p = os.path.join(trash, name)
            info = os.path.join(p, ".trash_meta.json")
            meta = {}
            if os.path.isfile(info):
                try:
                    with open(info, encoding="utf-8") as f:
                        meta = json.load(f)
                except (OSError, ValueError):
                    pass
            items.append({"name": name, "original_path": meta.get("original_path"),
                          "trashed_at": meta.get("trashed_at"), "size_bytes": tree_size(p)})
        print(json.dumps(items, indent=2, ensure_ascii=False))
        return 0

    if args.restore:
        name, dest = args.restore
        src = os.path.join(trash, name)
        if not os.path.isdir(src):
            print(f"no such trash entry: {name}", file=sys.stderr)
            return 1
        os.makedirs(dest, exist_ok=True)
        target = os.path.join(dest, name.split("_", 1)[-1] if "_" in name else name)
        shutil.move(src, target)
        print(f"restored -> {target}")
        return 0

    if args.purge:
        src = os.path.join(trash, args.purge)
        if not os.path.isdir(src):
            print(f"no such trash entry: {args.purge}", file=sys.stderr)
            return 1
        shutil.rmtree(src)
        print(f"purged {args.purge}")
        return 0

    if not args.paths:
        ap.print_usage()
        return 2

    os.makedirs(trash, exist_ok=True)
    for p in args.paths:
        ap_path = os.path.abspath(p)
        if not os.path.exists(ap_path):
            print(f"path does not exist: {ap_path}", file=sys.stderr)
            return 1
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        dest = os.path.join(trash, f"{stamp}_{os.path.basename(ap_path.rstrip('/\\'))}")
        while os.path.exists(dest):
            dest += "_x"
        shutil.move(ap_path, dest)
        with open(os.path.join(dest, ".trash_meta.json"), "w", encoding="utf-8", newline="\n") as f:
            json.dump({"original_path": ap_path, "trashed_at": stamp}, f, ensure_ascii=False)
        print(f"trashed {ap_path} -> {dest}")
    return 0


def tree_size(path):
    if os.path.isfile(path):
        return os.path.getsize(path)
    total = 0
    for r, _, files in os.walk(path):
        for fn in files:
            try:
                total += os.path.getsize(os.path.join(r, fn))
            except OSError:
                pass
    return total


if __name__ == "__main__":
    sys.exit(main())
