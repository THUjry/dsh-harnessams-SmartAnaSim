# -*- coding: utf-8 -*-
"""post_check collect-style diagnostics tests (plain script; invoked by
tests/post.test.mjs). Fixture work dirs mix a clean point (the shipped real
AC raw dir) with deliberately broken points; the assertions pin the
collect-all inversion: every point scanned, every violation enumerated,
classification and caps.
Usage: python -B tests/post_check_test.py
"""
import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from spectre import spectre_post_check as PC  # noqa: E402

DATA = ROOT / "tests" / "data" / "psf" / "point_ac" / "netlist.scs.raw"

# A swept file whose axis goes 1e9 -> 9e8 -> 1.2e9 (one non-increasing pair)
# and whose only signal first appears at step 2 (one NaN delta gap at index 0).
BAD_SWEPT = """TYPE
sweep
SWEEP
"freq" "F"
1.000000e+09
9.000000e+08
1.200000e+09
TRACE
"VOUT" "V"
END
VALUE
"freq" 1.000000e+09
"freq" 9.000000e+08
"VOUT" (1.000000e+00 1.000000e-01)
"freq" 1.200000e+09
END
"""

# A non-swept info file with one dropped (unparsable) VALUE line.
BAD_INFO = """TYPE
dc
VALUE
"K1" 2.5
"WEIRD" [1 2 3]
END
"""


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def _write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(obj, f)
        f.write("\n")


def _clean_raw(dst):
    os.makedirs(dst, exist_ok=True)
    for name in ("ac.ac", "dcOp.info"):
        shutil.copyfile(str(DATA / name), os.path.join(dst, name))


def run_and_parse(**kwargs):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = PC.run(**kwargs)
    return rc, json.loads(buf.getvalue())


def _build_work_dir(tmp):
    """00 clean / 01 failed meta + escape + bad swept / 02 no raw dir / 03 two
    raw dirs / 04 dropped-line info + bad meta signals; progress mismatches
    and an errlog on top."""
    # 00: the clean reference point
    _clean_raw(os.path.join(tmp, "00", "netlist.scs.raw"))
    _write_json(os.path.join(tmp, "00", "point_meta.json"),
                {"status": "success", "signals": {"vth": 0.45},
                 "raw_dir": os.path.join(tmp, "00", "netlist.scs.raw")})
    # 01: failed status, raw_dir escaping the point dir (the target exists
    # locally — that is what makes it foreign data), non-monotonic axis,
    # NaN delta gap
    raw01 = os.path.join(tmp, "01", "netlist.scs.raw")
    _write(os.path.join(raw01, "ac.ac"), BAD_SWEPT)
    os.makedirs(os.path.join(tmp, "elsewhere"))
    _write_json(os.path.join(tmp, "01", "point_meta.json"),
                {"status": "failure", "error_message": "Spectre crashed: ERROR (x): boom",
                 "raw_dir": "../elsewhere"})
    # 02: point dir with no raw dir and no point_meta.json at all
    os.makedirs(os.path.join(tmp, "02"))
    # 03: two raw dirs -> exactly-one violation, first is checked; an sp
    # analysis named sp1 (not bare sp) is called out; a remote-style raw_dir
    # (fetched copy origin) is deliberately NOT an escape warning
    _clean_raw(os.path.join(tmp, "03", "netlist.scs.raw"))
    _write(os.path.join(tmp, "03", "netlist.scs.raw", "sp1.sp"), "TYPE\nsp\n")
    _clean_raw(os.path.join(tmp, "03", "other.raw"))
    _write_json(os.path.join(tmp, "03", "point_meta.json"),
                {"status": "success",
                 "raw_dir": "/home/user/sim/run/03/netlist.scs.raw"})
    # 04: dropped VALUE line + bool / non-finite values in point_meta.signals
    raw04 = os.path.join(tmp, "04", "netlist.scs.raw")
    _write(os.path.join(raw04, "dcOp.info"), BAD_INFO)
    _write_json(os.path.join(tmp, "04", "point_meta.json"),
                {"status": "success",
                 "signals": {"ok_v": 1.0, "bool_v": True, "inf_v": float("inf")}})
    # run-level: progress disagrees with the disk, errlog present
    _write_json(os.path.join(tmp, "sweep_progress.json"),
                {"status": "completed", "total_points": 9, "completed": 6,
                 "succeeded": 4, "failed": 1,
                 "points": [{"index": 0, "status": "success"},
                            {"index": 1, "status": "failure"},
                            {"index": 8, "status": "success"}]})
    _write(os.path.join(tmp, "errlog.txt"), "startup error: no license\n")
    return tmp


def _issues_of(report, check):
    return [i for i in report["issues"] if i["check"] == check]


def test_work_dir_collect_all():
    with tempfile.TemporaryDirectory() as tmp0:
        tmp = _build_work_dir(tmp0)
        rc, report = run_and_parse(work_dir=tmp)
    assert rc == 0, rc
    assert report["mode"] == "work-dir"
    assert report["ok"] is False and report["status"] == "failure"
    assert report["points"]["present"] == 5 and report["points"]["expected"] == 9
    assert report["points"]["status_counts"] == {"success": 3, "failure": 1}
    assert len(report["point_records"]) == 5, report["point_records"]
    # every violation enumerated: 16 issues, 6 error + 10 warn, 0 truncated
    assert report["issues_truncated"] == 0, report["issues_truncated"]
    assert len(report["issues"]) == 16, json.dumps(report["issues"], indent=2)
    assert len([i for i in report["issues"] if i["severity"] == "error"]) == 6
    assert len([i for i in report["issues"] if i["severity"] == "warn"]) == 10

    # point 01: failed status + escaped raw_dir + axis rule + NaN gap
    ps = _issues_of(report, "point_status")
    assert len(ps) == 1 and ps[0]["point"] == "01" and "boom" in ps[0]["detail"]
    esc = _issues_of(report, "raw_dir_escape")
    assert len(esc) == 1 and esc[0]["point"] == "01" and "outside" in esc[0]["detail"]
    ax = _issues_of(report, "axis_rule")
    assert len(ax) == 1 and ax[0]["point"] == "01" and ax[0]["key"] == "ac_freq"
    assert "strictly increasing" in ax[0]["detail"] and "non-increasing pair(s)" in ax[0]["detail"]
    assert "values[1]" in ax[0]["detail"], ax[0]["detail"]
    sv = _issues_of(report, "signal_value")
    assert len(sv) == 1 and sv[0]["point"] == "01" and sv[0]["key"] == "ac_VOUT"
    assert "NaN delta gaps" in sv[0]["detail"] and sv[0]["severity"] == "warn"

    # point 02: no raw dir + missing point_meta
    rd = _issues_of(report, "raw_dir_count")
    assert len(rd) == 2, rd
    by_point = {i["point"]: i for i in rd}
    assert "no *.raw directory" in by_point["02"]["detail"]
    assert "expected exactly one raw dir, found 2" in by_point["03"]["detail"]
    pm = _issues_of(report, "point_meta")
    assert len(pm) == 1 and pm[0]["point"] == "02" and "missing" in pm[0]["detail"]

    # points 03+04: the sp1 naming call-out, the dropped VALUE line and the
    # bool / non-finite meta signals
    pw = _issues_of(report, "parse_warning")
    assert len(pw) == 2, pw
    assert any(i["point"] == "03" and "sp1.sp is not read" in i["detail"] for i in pw)
    assert any(i["point"] == "04" and "unparsed VALUE line" in i["detail"] for i in pw)
    pms = _issues_of(report, "point_meta_signal")
    assert sorted(i["key"] for i in pms) == ["bool_v", "inf_v"], pms
    assert any("is a bool" in i["detail"] for i in pms)
    assert any("not finite" in i["detail"] for i in pms)

    # run-level: progress disagreement (4) + errlog (1)
    prog = _issues_of(report, "progress")
    assert len(prog) == 4, prog
    assert any("total_points=9" in i["detail"] for i in prog)
    assert any("completed=6" in i["detail"] for i in prog)
    assert any("declared but absent on disk: 08" in i["detail"] for i in prog)
    assert any("on disk but not declared" in i["detail"] for i in prog)
    assert report["errlog_tail"] == "startup error: no license"
    assert _issues_of(report, "errlog") and _issues_of(report, "errlog")[0]["severity"] == "warn"

    # classification and summary
    assert len(report["checks_summary"]) == 10, report["checks_summary"]
    assert "16 issue(s)" in report["summary"] and "6 error" in report["summary"]


def test_issue_cap():
    with tempfile.TemporaryDirectory() as tmp0:
        tmp = _build_work_dir(tmp0)
        rc, report = run_and_parse(work_dir=tmp, max_issues=3)
    assert rc == 0
    assert len(report["issues"]) == 3 and report["issues_truncated"] == 13
    assert "13 more truncated" in report["summary"]


def test_clean_work_dir():
    with tempfile.TemporaryDirectory() as tmp:
        _clean_raw(os.path.join(tmp, "00", "netlist.scs.raw"))
        _write_json(os.path.join(tmp, "00", "point_meta.json"), {"status": "success"})
        _write_json(os.path.join(tmp, "sweep_progress.json"),
                    {"status": "completed", "total_points": 1, "completed": 1,
                     "succeeded": 1, "failed": 0, "points": [{"index": 0}]})
        rc, report = run_and_parse(work_dir=tmp)
    assert rc == 0, rc
    assert report["ok"] is True and report["status"] == "success", report["issues"]
    assert report["issues"] == [] and report["points"]["present"] == 1


def test_raw_dir_mode():
    with tempfile.TemporaryDirectory() as tmp:
        raw = os.path.join(tmp, "netlist.scs.raw")
        _clean_raw(raw)
        rc, report = run_and_parse(raw_dir=raw)
    assert rc == 0, rc
    assert report["mode"] == "raw-dir" and report["ok"] is True
    assert report["points"]["present"] == 1
    assert report["point_records"][0]["n_signals"] == 5, report["point_records"]

    with tempfile.TemporaryDirectory() as tmp:
        raw = os.path.join(tmp, "00", "netlist.scs.raw")
        _clean_raw(raw)
        rc, report = run_and_parse(raw_dir=raw)
    assert rc == 0 and report["ok"] is True, report["issues"]


def test_missing_dirs_exit_2():
    rc, report = run_and_parse(work_dir="no-such-dir")
    assert rc == 2 and report["status"] == "error" and report["ok"] is False
    rc, report = run_and_parse(raw_dir="no-such-dir")
    assert rc == 2 and report["status"] == "error"


def test_argparse_owns_exactly_one():
    for argv in ([], ["--work-dir", "x", "--raw-dir", "y"]):
        try:
            PC.main(argv)
        except SystemExit as exc:
            assert exc.code == 2, exc.code
        else:
            raise AssertionError("argparse must exit 2 for %r" % argv)


def main():
    test_work_dir_collect_all()
    test_issue_cap()
    test_clean_work_dir()
    test_raw_dir_mode()
    test_missing_dirs_exit_2()
    test_argparse_owns_exactly_one()
    print("post_check tests ok")


if __name__ == "__main__":
    main()
