# -*- coding: utf-8 -*-
"""ngspice worker tests with a fake process layer (run as a plain script;
invoked by tests/ngspice.test.mjs).

Usage: python -B tests/ngspice_worker_test.py
"""
import json
import os
import subprocess as real_subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from common import common_sweep_engine as E  # noqa: E402
from ngspice import ngspice_sweep_worker as W  # noqa: E402

TEMPLATE = ".param x=@@x@@\n.end\n"


def base_config(**over):
    cfg = {
        "parameters": {"x": [1]},
        "sweep_mode": "cross_product",
        "netlist_template": "template.spi",
        "timeout_per_point": 1,
        "ngspice_args": ["-b"],
    }
    cfg.update(over)
    return cfg


def make_work(tmp, template=True, config=None):
    cfg = config or base_config()
    if template:
        with open(os.path.join(tmp, "template.spi"), "w", encoding="utf-8") as f:
            f.write(TEMPLATE)
    with open(os.path.join(tmp, "sweep_config.json"), "w", encoding="utf-8") as f:
        json.dump(cfg, f)
    return cfg


def fake_layer(returncode=0, stdout="dc_gain_db = 40.5\n", stderr="", timeout=False):
    def run(argv, **kwargs):
        if timeout:
            raise real_subprocess.TimeoutExpired(argv, kwargs.get("timeout", 1))
        return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)
    return SimpleNamespace(run=run, TimeoutExpired=real_subprocess.TimeoutExpired)


def test_engine_helpers():
    cfg = {"parameters": {"a": [1, 2], "b": [3]}}
    names, combos = E.generate_combinations(cfg)
    assert names == ["a", "b"] and combos == [{"a": 1, "b": 3}, {"a": 2, "b": 3}], combos
    cfg = {"parameters": {"a": [1, 2], "b": [3, 4]}, "sweep_mode": "zip"}
    _, combos = E.generate_combinations(cfg)
    assert combos == [{"a": 1, "b": 3}, {"a": 2, "b": 4}], combos
    try:
        E.generate_combinations({"parameters": {}})
    except ValueError:
        pass
    else:
        raise AssertionError("empty parameters must be rejected")
    text, unresolved = E.build_netlist("l=@@l@@ w=@@w:g@@ n=@@missing@@", {"l": 1.5, "w": 2})
    assert text == "l=1.5 w=2 n=@@missing@@", text
    assert unresolved == ["missing"], unresolved
    assert E.point_name(3) == "03" and E.point_name(100) == "100"


def test_point_statuses():
    original = W.subprocess
    try:
        with tempfile.TemporaryDirectory() as tmp:
            make_work(tmp)
            W.subprocess = fake_layer()
            res = W.run_single_point(tmp, 0, {"x": 1}, base_config(), sys.executable)
            assert res["status"] == "success", res
            assert res["signals"]["dc_gain_db"] == 40.5, res
            assert res["simulator"] == "ngspice", res
            assert os.path.isfile(os.path.join(tmp, "00", "point_meta.json"))

        with tempfile.TemporaryDirectory() as tmp:
            make_work(tmp)
            W.subprocess = fake_layer()
            res = W.run_single_point(tmp, 0, {"x": 1},
                                     base_config(metrics_required=["missing_metric"]),
                                     sys.executable)
            assert res["status"] == "partial", res
            assert "missing metrics" in res["error_message"], res

        with tempfile.TemporaryDirectory() as tmp:
            make_work(tmp)
            W.subprocess = fake_layer(returncode=2, stdout="Error: broken netlist\n")
            res = W.run_single_point(tmp, 0, {"x": 1}, base_config(), sys.executable)
            assert res["status"] == "failure", res
            assert "broken netlist" in res["error_message"], res

        with tempfile.TemporaryDirectory() as tmp:
            make_work(tmp)
            W.subprocess = fake_layer(timeout=True)
            res = W.run_single_point(tmp, 0, {"x": 1}, base_config(), sys.executable)
            assert res["status"] == "timeout", res
    finally:
        W.subprocess = original


def test_main_progress_and_markers():
    def fake_point(work_dir, idx, params, config, cmd):
        meta = {
            "point_index": idx, "point_name": E.point_name(idx),
            "simulator": "ngspice", "params": dict(params),
            "status": "success", "signals": {"gain": 40 + idx}, "elapsed_sec": 0.1,
            "returncode": 0, "error_message": None,
        }
        pdir = os.path.join(work_dir, E.point_name(idx))
        os.makedirs(pdir, exist_ok=True)
        E.atomic_write_json(os.path.join(pdir, "point_meta.json"), meta)
        return meta

    original_run = W.run_single_point
    try:
        W.run_single_point = fake_point
        with tempfile.TemporaryDirectory() as tmp:
            cfg = base_config(ngspice_cmd=sys.executable)
            cfg["parameters"] = {"x": [0, 1]}
            cfg["best_metric"] = {"name": "gain", "mode": "max"}
            make_work(tmp, config=cfg)
            rc = W.main(["--work-dir", tmp, "--config", "sweep_config.json", "--max-parallel", "2"])
            assert rc == 0, rc
            prog = json.load(open(os.path.join(tmp, "sweep_progress.json"), encoding="utf-8"))
            assert prog["status"] == "completed" and prog["total_points"] == 2, prog
            assert prog["simulator"] == "ngspice", prog
            assert open(os.path.join(tmp, ".simulator"), encoding="utf-8").read().strip() == "ngspice"
            best = json.load(open(os.path.join(tmp, "bestPoints.json"), encoding="utf-8"))
            assert best["value"] == 41, best
    finally:
        W.run_single_point = original_run


def test_main_startup_errors():
    with tempfile.TemporaryDirectory() as tmp:
        make_work(tmp, template=False)
        rc = W.main(["--work-dir", tmp])
        assert rc == 2, rc
        assert os.path.isfile(os.path.join(tmp, "errlog.txt"))
    with tempfile.TemporaryDirectory() as tmp:
        make_work(tmp, config=base_config(ngspice_cmd="definitely-not-ngspice"))
        rc = W.main(["--work-dir", tmp])
        assert rc == 2, rc
        assert os.path.isfile(os.path.join(tmp, "errlog.txt"))


def main():
    test_engine_helpers()
    test_point_statuses()
    test_main_progress_and_markers()
    test_main_startup_errors()
    print("ngspice worker tests ok")


if __name__ == "__main__":
    main()
