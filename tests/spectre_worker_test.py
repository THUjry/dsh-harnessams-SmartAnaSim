# -*- coding: utf-8 -*-
"""Spectre worker tests with a fake process layer (run as a plain script;
invoked by tests/spectre.test.mjs).

Usage: python -B tests/spectre_worker_test.py
"""
import io
import json
import os
import subprocess as real_subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from spectre import spectre_sweep_worker as W  # noqa: E402
from common import common_sweep_engine as E  # noqa: E402

TEMPLATE = "simulator lang=spectre\nparameters x=@@x@@\n"
INFO_FIXTURE = (ROOT / "tests" / "data" / "psf" / "point_ac" / "netlist.scs.raw"
                / "dcOp.info").read_text(encoding="utf-8")


class FakePopen:
    mode = "ok"          # ok | hang
    rc = 0
    stdout_text = ""
    stderr_text = ""
    killed = False

    def __init__(self, cmd, stdout=None, stderr=None, text=True, cwd=None):
        self.cmd = cmd
        self.cwd = cwd
        self.stdout = io.StringIO(FakePopen.stdout_text)
        self.stderr = io.StringIO(FakePopen.stderr_text)

    def poll(self):
        return None if FakePopen.mode == "hang" else FakePopen.rc

    def kill(self):
        FakePopen.killed = True


def fake_subprocess():
    return SimpleNamespace(Popen=FakePopen, PIPE=real_subprocess.PIPE,
                           STDOUT=real_subprocess.STDOUT, DEVNULL=real_subprocess.DEVNULL)


def base_config(**over):
    cfg = {
        "parameters": {"x": [1]},
        "sweep_mode": "cross_product",
        "netlist_template": "template.scs",
        "timeout_per_point": 1,
        "spectre_args": [],
        "signals_from": "none",
    }
    cfg.update(over)
    return cfg


def make_work(tmp, template=True):
    cfg = base_config()
    if template:
        with open(os.path.join(tmp, "template.scs"), "w", encoding="utf-8") as f:
            f.write(TEMPLATE)
    with open(os.path.join(tmp, "sweep_config.json"), "w", encoding="utf-8") as f:
        json.dump(cfg, f)
    return cfg


def test_command_building():
    cmd = W.build_spectre_cmd("spectre", [], "psfascii", "n.scs", "raw", "out")
    assert cmd[:3] == ["spectre", "-64", "n.scs"], cmd
    assert "+lqtimeout" in cmd and cmd[cmd.index("+lqtimeout") + 1] == "900", cmd
    assert cmd[cmd.index("-maxw") + 1] == "5", cmd
    assert cmd[cmd.index("-maxn") + 1] == "5", cmd
    assert "++aps" in cmd and "+mt" in cmd and cmd[-1] == "+logstatus", cmd
    assert "-format" in cmd and cmd[cmd.index("-format") + 1] == "psfascii", cmd

    tuned = W.build_spectre_cmd("spectre",
                                ["++aps", "+mt", "+lqtimeout=1000", "-maxw", "9", "+preset"],
                                "psfascii", "n.scs", "raw", "out")
    assert tuned.count("++aps") == 1, tuned
    assert tuned.count("-maxw") == 1, tuned
    assert tuned.count("+lqtimeout") == 0, tuned
    assert "+lqtimeout=1000" in tuned, tuned


def test_statuses():
    original = W.subprocess
    try:
        W.subprocess = fake_subprocess()
        with tempfile.TemporaryDirectory() as tmp:
            make_work(tmp)
            os.makedirs(os.path.join(tmp, "00", "netlist.scs.raw"))
            FakePopen.mode, FakePopen.rc = "ok", 0
            res = W.run_single_point(tmp, 0, {"x": 1}, base_config(), sys.executable)
            assert res["status"] == "success", res
            assert res["simulator"] == "spectre", res
            assert os.path.isfile(os.path.join(tmp, "00", "point_meta.json"))

        with tempfile.TemporaryDirectory() as tmp:
            make_work(tmp)
            os.makedirs(os.path.join(tmp, "00", "netlist.scs.raw"))
            FakePopen.mode, FakePopen.rc = "ok", 2
            res = W.run_single_point(tmp, 0, {"x": 1}, base_config(), sys.executable)
            assert res["status"] == "partial", res

        with tempfile.TemporaryDirectory() as tmp:
            make_work(tmp)
            FakePopen.mode, FakePopen.rc = "ok", 2
            res = W.run_single_point(tmp, 0, {"x": 1}, base_config(), sys.executable)
            assert res["status"] == "failure", res

        with tempfile.TemporaryDirectory() as tmp:
            make_work(tmp)
            FakePopen.mode, FakePopen.killed = "hang", False
            res = W.run_single_point(tmp, 0, {"x": 1},
                                     base_config(timeout_per_point=0.01), sys.executable)
            assert res["status"] == "timeout", res
            assert FakePopen.killed, "timeout must kill the process"
    finally:
        W.subprocess = original


def test_missing_template_and_signals():
    original = W.subprocess
    try:
        W.subprocess = fake_subprocess()
        FakePopen.mode, FakePopen.rc = "ok", 0
        with tempfile.TemporaryDirectory() as tmp:
            make_work(tmp, template=False)
            res = W.run_single_point(tmp, 0, {"x": 1}, base_config(), sys.executable)
            assert res["status"] == "error", res
            assert os.path.isfile(os.path.join(tmp, "errlog.txt"))
            assert os.path.isfile(os.path.join(tmp, "00", "point_meta.json"))

        with tempfile.TemporaryDirectory() as tmp:
            make_work(tmp)
            raw = os.path.join(tmp, "00", "netlist.scs.raw")
            os.makedirs(raw)
            with open(os.path.join(raw, "dcOp.info"), "w", encoding="utf-8") as f:
                f.write(INFO_FIXTURE)
            cfg = base_config(signals_from="psf_info",
                              metrics_required=["dcOp_M0:gm"])
            res = W.run_single_point(tmp, 0, {"x": 1}, cfg, sys.executable)
            assert res["status"] == "success", res
            assert abs(res["signals"]["dcOp_M0:gm"] - 1.906e-4) < 1e-12, res["signals"]
            assert res["signals_available"], res
    finally:
        W.subprocess = original


def test_main_progress_and_markers():
    def fake_point(work_dir, idx, params, config, spectre_cmd):
        meta = {
            "point_index": idx, "point_name": E.point_name(idx),
            "simulator": "spectre", "params": dict(params),
            "status": "success", "signals": {"m": idx}, "elapsed_sec": 0.1,
            "returncode": 0, "error_message": None,
        }
        pdir = os.path.join(work_dir, E.point_name(idx))
        os.makedirs(pdir, exist_ok=True)
        E.atomic_write_json(os.path.join(pdir, "point_meta.json"), meta)
        return meta

    original_run = W.run_single_point
    original_resolve = W.resolve_spectre_cmd
    try:
        W.run_single_point = fake_point
        W.resolve_spectre_cmd = lambda *a, **k: sys.executable
        with tempfile.TemporaryDirectory() as tmp:
            cfg = base_config()
            cfg["parameters"] = {"x": [0, 1]}
            cfg["best_metric"] = {"name": "m", "mode": "max"}
            with open(os.path.join(tmp, "template.scs"), "w", encoding="utf-8") as f:
                f.write(TEMPLATE)
            with open(os.path.join(tmp, "sweep_config.json"), "w", encoding="utf-8") as f:
                json.dump(cfg, f)
            rc = W.main(["--work-dir", tmp, "--config", "sweep_config.json", "--max-parallel", "2"])
            assert rc == 0, rc
            prog = json.load(open(os.path.join(tmp, "sweep_progress.json"), encoding="utf-8"))
            assert prog["status"] == "completed", prog
            assert prog["total_points"] == 2 and prog["succeeded"] == 2, prog
            assert prog["simulator"] == "spectre", prog
            assert open(os.path.join(tmp, ".simulator"), encoding="utf-8").read().strip() == "spectre"
            best = json.load(open(os.path.join(tmp, "bestPoints.json"), encoding="utf-8"))
            assert best["value"] == 1, best
    finally:
        W.run_single_point = original_run
        W.resolve_spectre_cmd = original_resolve


def test_main_startup_errors():
    original_run = W.run_single_point
    original_resolve = W.resolve_spectre_cmd
    original_which = W.shutil.which
    try:
        W.run_single_point = lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not run"))
        W.resolve_spectre_cmd = lambda *a, **k: "definitely-not-a-spectre"
        W.shutil.which = lambda name: None
        with tempfile.TemporaryDirectory() as tmp:
            make_work(tmp)
            rc = W.main(["--work-dir", tmp])
            assert rc == 2, rc
            assert os.path.isfile(os.path.join(tmp, "errlog.txt"))
        with tempfile.TemporaryDirectory() as tmp:
            make_work(tmp, template=False)
            cfg = base_config()
            with open(os.path.join(tmp, "sweep_config.json"), "w", encoding="utf-8") as f:
                json.dump(cfg, f)
            rc = W.main(["--work-dir", tmp])
            assert rc == 2, rc
    finally:
        W.run_single_point = original_run
        W.resolve_spectre_cmd = original_resolve
        W.shutil.which = original_which


def main():
    test_command_building()
    test_statuses()
    test_missing_template_and_signals()
    test_main_progress_and_markers()
    test_main_startup_errors()
    print("spectre worker tests ok")


if __name__ == "__main__":
    main()
