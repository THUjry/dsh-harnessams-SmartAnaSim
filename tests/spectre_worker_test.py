# -*- coding: utf-8 -*-
"""Spectre worker tests with a fake process layer (run as a plain script;
invoked by tests/spectre.test.mjs).

Usage: python -B tests/spectre_worker_test.py
"""
import io
import json
import os
import signal as real_signal
import subprocess as real_subprocess
import sys
import tempfile
import threading
import time
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

    def __init__(self, cmd, stdout=None, stderr=None, text=True, cwd=None,
                 start_new_session=False):
        self.cmd = cmd
        self.cwd = cwd
        self.start_new_session = start_new_session  # the csh-wrapped Spectre
        # runs in its own process group (POSIX; ignored on Windows)
        FakePopen.last_start_new_session = start_new_session
        self.pid = 4321
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
            # telemetry: sweep_progress.json carries the toolchain
            # the job really ran under — the self-check harvests it to pin the
            # project and remember the node (measurement, never a guess)
            tc = prog.get("toolchain")
            assert tc and tc.get("python") == sys.executable, tc
            assert tc.get("spectre") == sys.executable, tc
            assert isinstance(tc.get("node"), str) and tc["node"], tc
            # the numpy tri-state rides along (yes / fallback / no)
            assert tc.get("numpy") in ("yes", "no", "fallback"), tc
    finally:
        W.run_single_point = original_run
        W.resolve_spectre_cmd = original_resolve


def test_probe_numpy_state():
    # the tri-state the telemetry carries — "yes" (native import),
    # "fallback" (the staged numfallback asset imports), "no" (neither, e.g. a
    # python 2 shell). Never raises; a native numpy import must answer "yes".
    state = W.probe_numpy_state()
    assert state in ("yes", "no", "fallback"), state
    try:
        import numpy  # noqa: F401
        has_numpy = True
    except Exception:
        has_numpy = False
    if has_numpy:
        assert state == "yes", state


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


def test_timeout_kills_process_group():
    # the csh wrapper runs Spectre in a child process: a plain proc.kill()
    # only reaped csh, orphaning Spectre (and its license) — the timeout path
    # must kill the whole process group when the platform has killpg
    original = W.subprocess
    saved_killpg = getattr(os, "killpg", None)
    saved_getpgid = getattr(os, "getpgid", None)
    saved_sigkill = getattr(real_signal, "SIGKILL", None)
    killpg_calls = []
    try:
        W.subprocess = fake_subprocess()
        # inject a fake process-group API (Windows lacks killpg entirely; the
        # group-kill branch must be exercised the same way Linux runs it —
        # SIGKILL is injected with it for the same reason)
        os.killpg = lambda pgid, sig: killpg_calls.append((pgid, sig))
        os.getpgid = lambda pid: pid + 4242
        if not hasattr(real_signal, "SIGKILL"):
            real_signal.SIGKILL = 9
        with tempfile.TemporaryDirectory() as tmp:
            make_work(tmp)
            FakePopen.mode, FakePopen.rc, FakePopen.killed = "hang", 0, False
            res = W.run_single_point(tmp, 0, {"x": 1},
                                     base_config(timeout_per_point=0.01), sys.executable)
            assert res["status"] == "timeout", res
            assert killpg_calls, "timeout must kill the process GROUP"
            assert killpg_calls[0][1] == real_signal.SIGKILL, killpg_calls
            assert FakePopen.last_start_new_session, "csh-wrapped runs need start_new_session"
    finally:
        W.subprocess = original
        for mod, name, saved in ((os, "killpg", saved_killpg),
                                 (os, "getpgid", saved_getpgid),
                                 (real_signal, "SIGKILL", saved_sigkill)):
            if saved is None:
                if hasattr(mod, name):
                    delattr(mod, name)
            else:
                setattr(mod, name, saved)


def test_timeout_extensions_take_max():
    # both extensions granted in the same poll tick: the deadline is the MAX
    # of the two — the old plain second assignment wound the deadline back
    # down to the shorter raw extension
    original = W.subprocess
    try:
        W.subprocess = fake_subprocess()
        with tempfile.TemporaryDirectory() as tmp:
            make_work(tmp)
            pdir = os.path.join(tmp, "00")
            raw = os.path.join(pdir, "netlist.scs.raw")
            os.makedirs(raw)
            with open(os.path.join(raw, "progress"), "w", encoding="utf-8") as f:
                f.write("x")
            with open(os.path.join(pdir, "spectre.out"), "w", encoding="utf-8") as f:
                f.write("Waiting for available license\n")
            FakePopen.mode, FakePopen.rc, FakePopen.killed = "hang", 0, False
            cfg = base_config(timeout_per_point=0.01,
                              license_wait_extension_sec=3,
                              raw_progress_extension_sec=2)
            res = W.run_single_point(tmp, 0, {"x": 1}, cfg, sys.executable)
            assert res["status"] == "timeout", res
            assert res["license_wait_extended"] and res["raw_progress_extended"], res
            # first poll tick lands ~2s in: max deadline = 2s + 3s = 5s, so
            # the kill happens no earlier than that (the old code kept only
            # the 2s raw deadline and killed ~2s early)
            assert res["elapsed_sec"] >= 5.0, res
    finally:
        W.subprocess = original


def test_resolve_spectre_cmd_both_separators():
    # a forward-slash relative path ("bin/spectre") on Windows carries no
    # os.sep — it must still join against work_dir, never fall into the
    # which() lookup as a path fragment
    with tempfile.TemporaryDirectory() as tmp:
        got = W.resolve_spectre_cmd({}, tmp, "bin/spectre")
        assert got == os.path.normpath(os.path.join(tmp, "bin", "spectre")), got
        got2 = W.resolve_spectre_cmd({}, tmp, "spectre")
        assert got2 == (W.shutil.which("spectre") or "spectre"), got2


def test_progress_writes_during_a_long_single_point():
    # the progress cadence must also fire while one LONG point is still
    # running (as_completed only fired on completions, leaving
    # sweep_progress.json untouched for the point's whole runtime)
    original_run = W.run_single_point
    original_resolve = W.resolve_spectre_cmd
    original_period = W.PROGRESS_PERIOD_SEC
    try:
        W.PROGRESS_PERIOD_SEC = 1  # fast cadence for the test
        W.resolve_spectre_cmd = lambda *a, **k: sys.executable

        def slow_point(work_dir, idx, params, config, spectre_cmd):
            time.sleep(2.5)  # spans at least two 1 s periods
            meta = {"point_index": idx, "point_name": E.point_name(idx),
                    "simulator": "spectre", "params": dict(params),
                    "status": "success", "signals": {"m": idx}, "elapsed_sec": 2.5,
                    "returncode": 0, "error_message": None}
            pdir = os.path.join(work_dir, E.point_name(idx))
            os.makedirs(pdir, exist_ok=True)
            E.atomic_write_json(os.path.join(pdir, "point_meta.json"), meta)
            return meta

        W.run_single_point = slow_point
        with tempfile.TemporaryDirectory() as tmp:
            make_work(tmp)
            running_writes = []
            stop = threading.Event()

            def watcher():
                while not stop.is_set():
                    try:
                        with open(os.path.join(tmp, "sweep_progress.json"),
                                  encoding="utf-8") as f:
                            p = json.load(f)
                        if p.get("status") == "running":
                            running_writes.append(os.path.getmtime(
                                os.path.join(tmp, "sweep_progress.json")))
                    except (OSError, ValueError):
                        pass
                    time.sleep(0.2)

            t = threading.Thread(target=watcher, daemon=True)
            t.start()
            rc = W.main(["--work-dir", tmp, "--config", "sweep_config.json"])
            stop.set()
            t.join(timeout=2)
            assert rc == 0, rc
            assert len(set(running_writes)) >= 2, \
                ("progress must advance while a long point runs, saw %d distinct "
                 "running-state writes" % len(set(running_writes)))
    finally:
        W.run_single_point = original_run
        W.resolve_spectre_cmd = original_resolve
        W.PROGRESS_PERIOD_SEC = original_period


def test_main_rejects_zero_max_parallel():
    # explicit 0 is a startup error (both the --max-parallel flag and the
    # config value), never a silent fall-through into the default 4
    original_run = W.run_single_point
    original_resolve = W.resolve_spectre_cmd
    try:
        W.run_single_point = lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not run"))
        W.resolve_spectre_cmd = lambda *a, **k: sys.executable
        with tempfile.TemporaryDirectory() as tmp:
            make_work(tmp)
            rc = W.main(["--work-dir", tmp, "--max-parallel", "0"])
            assert rc == 2, rc
            assert os.path.isfile(os.path.join(tmp, "errlog.txt"))
        with tempfile.TemporaryDirectory() as tmp:
            make_work(tmp)
            cfg = base_config(max_parallel=0)
            with open(os.path.join(tmp, "sweep_config.json"), "w", encoding="utf-8") as f:
                json.dump(cfg, f)
            rc = W.main(["--work-dir", tmp])
            assert rc == 2, rc
            assert os.path.isfile(os.path.join(tmp, "errlog.txt"))
    finally:
        W.run_single_point = original_run
        W.resolve_spectre_cmd = original_resolve


def main():
    test_command_building()
    test_statuses()
    test_timeout_kills_process_group()
    test_timeout_extensions_take_max()
    test_resolve_spectre_cmd_both_separators()
    test_missing_template_and_signals()
    test_main_progress_and_markers()
    test_progress_writes_during_a_long_single_point()
    test_main_rejects_zero_max_parallel()
    test_main_startup_errors()
    print("spectre worker tests ok")


if __name__ == "__main__":
    main()
