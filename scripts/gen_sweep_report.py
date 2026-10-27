# -*- coding: utf-8 -*-
"""HarnessAMS sweep-result HTML report generator (generic ngspice sweep version).

Mechanisms and hard rules:
- Four sections: (1) summary metric cards, (2) interactive metric charts (Plotly),
  (3) per-point detail table, (4) best point;
- Round selector: MANIFEST + data/Round<N>.js loaded via <script> tags (no fetch under
  file://, so script injection is the only reliable channel);
- var ROUND_DATA (never const: round switching re-declares and overwrites);
- ?v= cache-bust (data is regenerated in place; prevents stale browser caches);
- main HTML written as utf-8-sig (prevents single-byte-locale misreads), data JS as
  plain utf-8 (json.dumps emits ASCII only);
- Plotly events re-bound on every buildReport (newPlot clears handlers); native DOM
  handlers bound exactly once;
- only el.on(...) is used - Plotly.on and el.removeAllListeners are absent or destructive
  in Plotly 3.0.1;
- table and chart rendering are isolated in per-section try/catch so one failing section
  cannot freeze the page;
- robust num()/show() formatters (null / strings / missing values all render '-').

Plotly loading uses a single workspace-level copy (no per-report duplication): the
generator seeds <workspace_root>/assets/plotly.min.js (workspace root = the directory
holding harnessams.json) from the best local source -- an explicit --plotly path, an
assets/plotly.min.js found in the script's ancestor directories (package-local assets/,
the shipped dsh-harnessams assets/, an installed node_modules sibling), or the workspace
harnessams.json plotly_asset field -- refreshing it when the size differs, and every
report references it with a workspace-relative src, so reports open offline. The CDN is
the fallback when no local source exists; --plotly-cdn forces it.

Input: --project <demo project dir> (containing sweep/Round*/top/results.json + cell_meta.json)
Output: <project>/Analysis/<project>_report.html + <project>/Analysis/data/Round<N>.js
Usage: python gen_sweep_report.py --project <workspace>/<demo> [--round Round1] [--plotly <path>] [--plotly-cdn]
"""
import argparse
import json
import os
import re
import shutil
import sys
from datetime import datetime, timezone

PLOTLY_CDN = "https://cdn.plot.ly/plotly-3.0.1.min.js"

HEAD = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>__CASCADE__ - sweep report</title>
<script src="__PLOTLY__"></script>
<script>window.Plotly || document.write('<script src="/harnessams/assets/plotly.min.js"><\\/script>');</script>
<style>
  body { font-family: "Segoe UI", system-ui, sans-serif; margin: 0 24px 48px; color: #1c2733; }
  h1 { font-size: 22px; margin: 18px 0 4px; }
  h2 { font-size: 18px; margin: 26px 0 8px; border-left: 4px solid #2b6cb0; padding-left: 8px; }
  .meta { color: #5a6b7b; font-size: 13px; margin-bottom: 12px; }
  .selector-bar { display: flex; gap: 16px; align-items: center; margin: 10px 0; }
  select { padding: 4px 8px; font-size: 14px; }
  .cards { display: flex; flex-wrap: wrap; gap: 12px; margin: 10px 0; }
  .card { border: 1px solid #d7e0e8; border-radius: 8px; padding: 10px 16px; min-width: 200px; background: #f8fafc; }
  .card .k { font-size: 13px; color: #5a6b7b; }
  .card .v { font-size: 18px; font-weight: 600; margin-top: 2px; }
  .chart { width: 100%; height: 480px; margin: 8px 0 20px; }
  table { border-collapse: collapse; width: 100%; font-size: 13px; }
  th, td { border: 1px solid #d7e0e8; padding: 4px 8px; text-align: right; }
  th { background: #eef3f8; position: sticky; top: 0; }
  td.l, th.l { text-align: left; }
  tr.ok td { background: #ffffff; }
  tr.bad td { background: #fde8e8; }
  tr.sel td { background: #fff3c4 !important; }
  #info-bar { position: sticky; bottom: 0; background: #2b6cb0; color: #fff; padding: 6px 12px;
              border-radius: 6px; display: none; font-size: 13px; cursor: pointer; margin-top: 10px; }
  .legend-item { display: inline-block; margin: 0 10px 6px 0; font-size: 13px; cursor: pointer;
                 padding: 2px 8px; border-radius: 4px; border: 1px solid transparent; }
  .legend-item.selected { border-color: #2b6cb0; background: #e8f0fa; }
</style>
</head>
<body>
<h1>__CASCADE__ - sweep report</h1>
<div class="meta">Generated __NOW__ | PDK: __PDK__ | Simulator: ngspice (local)</div>
<div class="selector-bar">Round: <select id="round-select"></select><span id="round-info" class="meta"></span></div>
"""

BODY_TOP = """
<h2>1. Summary metrics</h2>
<div class="cards" id="summary-cards"></div>
<h2>2. Metric charts (click a legend item / curve to highlight its detail row)</h2>
<div id="legend-grid"></div>
<div id="chart-metrics"></div>
<h2>3. Per-point details</h2>
<div style="max-height: 420px; overflow: auto;">
<table id="points-table"><thead id="points-head"></thead><tbody id="points-body"></tbody></table>
</div>
<h2>4. Best point (bestPoints.json)</h2>
<div class="cards" id="best-card"></div>
<div id="info-bar"></div>
"""

FOOT_JS = r"""
<script>
const MANIFEST = { project: "__CASCADE__", rounds: [__ROUNDS__] };
var ROUND_DATA = null;                       // hard rule: var, not const (round switching re-declares and overwrites)

const CHART_DIVS = ["chart-metrics"];

function num(v, n) {                         // hard rule: robust formatter
  return (typeof v === "number" && isFinite(v)) ? v.toFixed(n === undefined ? 4 : n) : "-";
}
function show(v) { return (v === null || v === undefined || v === "") ? "-" : String(v); }
function fmtVal(v) {
  if (typeof v !== "number" || !isFinite(v)) return show(v);
  const a = Math.abs(v);
  if (a !== 0 && (a < 1e-3 || a >= 1e6)) return v.toExponential(3);
  return num(v, 4).replace(/\.?0+$/, "");
}

function ctrlColor(i, n) {                   // HSL blue-to-red gradient
  const h = 240 - 240 * (n <= 1 ? 0 : i / (n - 1));
  return "hsl(" + Math.round(h) + ",70%,45%)";
}

function populateSelect() {
  const sel = document.getElementById("round-select");
  sel.innerHTML = "";
  for (const r of MANIFEST.rounds) {
    const opt = document.createElement("option");
    opt.value = r.file; opt.textContent = r.title + " - " + r.n_points + " points";
    sel.appendChild(opt);
  }
  sel.onchange = function () { loadRound(sel.value); };
}

function loadRound(file) {                   // hard rule: script injection + ?v= cache-bust
  const old = document.getElementById("round-data-script");
  if (old) old.remove();
  const el = document.createElement("script");
  el.id = "round-data-script";
  el.src = file + "?v=" + Date.now();
  el.onload = function () { buildReport(ROUND_DATA); };
  el.onerror = function () {
    document.getElementById("round-info").textContent = "data load failed: " + file;
  };
  document.head.appendChild(el);
}

function buildReport(RD) {
  if (!RD) return;
  document.getElementById("round-info").textContent =
    "Status: " + RD.n_success + " success / " + RD.n_failed + " failed";
  try { buildSummary(RD); } catch (e) { console.warn("summary", e); }   // hard rule: per-section isolation
  try { buildLegend(RD); } catch (e) { console.warn("legend", e); }
  try { buildCharts(RD); } catch (e) { console.warn("charts", e); }
  try { buildTable(RD); } catch (e) { console.warn("table", e); }
  try { buildBest(RD); } catch (e) { console.warn("best", e); }
  bind(RD);                                  // hard rule: re-bind Plotly events after every buildReport
}

function buildSummary(RD) {
  const cards = document.getElementById("summary-cards");
  cards.innerHTML = "";
  for (const [k, s] of Object.entries(RD.summary || {})) {
    const c = document.createElement("div");
    c.className = "card";
    c.innerHTML = '<div class="k">' + show(k) + '</div>' +
      '<div class="v">min ' + fmtVal(s.min) + ' / max ' + fmtVal(s.max) + '</div>' +
      '<div class="k">mean ' + fmtVal(s.mean) + ' (n=' + s.n + ')</div>';
    cards.appendChild(c);
  }
}

function buildLegend(RD) {
  const lg = document.getElementById("legend-grid");
  lg.innerHTML = "";
  RD.group_values.forEach(function (v, i) {
    const el = document.createElement("span");
    el.className = "legend-item";
    el.dataset.value = String(v);
    el.style.color = ctrlColor(i, RD.group_values.length);
    el.textContent = RD.group_by + " = " + show(v);
    lg.appendChild(el);
  });
}

function buildCharts(RD) {
  const host = document.getElementById("chart-metrics");
  host.innerHTML = "";
  const metrics = RD.metric_names;
  const rows = Math.ceil(metrics.length / 2);
  for (let i = 0; i < metrics.length; i++) {
    const div = document.createElement("div");
    div.className = "chart";
    div.id = "chart-" + i;
    if (metrics.length > 1) { div.style.height = "360px"; }
    host.appendChild(div);
  }
  metrics.forEach(function (mk, mi) {
    const traces = [];
    RD.group_values.forEach(function (gv, gi) {
      const pts = RD.points.filter(function (p) { return String(p.params[RD.group_by]) === String(gv); });
      traces.push({
        x: pts.map(function (p) { return p.index; }),
        y: pts.map(function (p) { return (p.metrics && typeof p.metrics[mk] === "number") ? p.metrics[mk] : null; }),
        mode: "lines+markers", name: RD.group_by + "=" + show(gv),
        line: { width: 2, color: ctrlColor(gi, RD.group_values.length) },
        marker: { size: 7, opacity: 0.4, color: ctrlColor(gi, RD.group_values.length) },
        connectgaps: false,
        hovertemplate: "point %{x}<br>" + mk + " = %{y}<extra>" + RD.group_by + "=" + show(gv) + "</extra>",
      });
    });
    Plotly.newPlot("chart-" + mi, traces, baseLayout(mk), { displayModeBar: false });
  });
}

function baseLayout(ylabel) {
  return {
    title: { text: ylabel, font: { size: 20 } },
    xaxis: { title: { text: "sweep point index", font: { size: 16 } }, tickfont: { size: 16 }, dtick: 1 },
    yaxis: { title: { text: ylabel, font: { size: 16 } }, tickfont: { size: 16 } },
    height: 480, margin: { t: 48, b: 60, l: 76, r: 40 },
    clickmode: "event", hovermode: "closest", showlegend: true,
  };
}

function buildTable(RD) {
  const head = document.getElementById("points-head");
  const body = document.getElementById("points-body");
  const cols = ["index"].concat(RD.param_names, RD.metric_names, ["status", "elapsed_s"]);
  head.innerHTML = "<tr>" + cols.map(function (c) {
    return '<th class="l">' + show(c) + "</th>";
  }).join("") + "</tr>";
  body.innerHTML = "";
  RD.points.forEach(function (p) {
    const tr = document.createElement("tr");
    tr.className = (p.status === "success" || p.status === "partial") ? "ok" : "bad";
    tr.dataset.index = String(p.index);
    let html = "<td>" + p.index + "</td>";
    RD.param_names.forEach(function (k) { html += '<td class="l">' + show(p.params ? p.params[k] : "-") + "</td>"; });
    RD.metric_names.forEach(function (k) { html += "<td>" + fmtVal(p.metrics ? p.metrics[k] : null) + "</td>"; });
    html += "<td>" + show(p.status) + "</td><td>" + num(p.elapsed_sec, 2) + "</td>";
    tr.innerHTML = html;
    body.appendChild(tr);
  });
}

function buildBest(RD) {
  const card = document.getElementById("best-card");
  card.innerHTML = "";
  const b = RD.best_points;
  const c = document.createElement("div");
  c.className = "card";
  if (b && b.metric) {
    c.innerHTML = '<div class="k">best ' + show(b.metric) + " (" + show(b.mode) + ")</div>" +
      '<div class="v">' + fmtVal(b.value) + " @ point " + show(b.point_name) + "</div>" +
      '<div class="k">' + show(JSON.stringify(b.params)) + "</div>";
  } else {
    c.innerHTML = '<div class="k">no bestPoints.json (best_metric not configured, or no successful point)</div>';
  }
  card.appendChild(c);
}

let bindDone = false;
let selIndex = null;

function highlight(idx) {                    // cross-highlight: charts <-> detail table
  const rows = document.querySelectorAll("#points-body tr");
  rows.forEach(function (r) { r.classList.toggle("sel", r.dataset.index === String(idx)); });
  const bar = document.getElementById("info-bar");
  const p = ROUND_DATA.points.find(function (q) { return String(q.index) === String(idx); });
  if (p) {
    bar.style.display = "block";
    bar.textContent = "point " + p.index + " | " + JSON.stringify(p.params) +
      " | " + JSON.stringify(p.metrics);
  } else { bar.style.display = "none"; }
  selIndex = idx;
}

function resetAll() { highlight(null); }

function bind(RD) {                          // hard rule: rebind after newPlot; el.on only
  RD.metric_names.forEach(function (mk, mi) {
    const el = document.getElementById("chart-" + mi);
    el.on("plotly_click", function (evt) {
      if (evt.points && evt.points[0]) { highlight(evt.points[0].x); }
    });
    el.on("plotly_deselect", resetAll);
  });
  if (!bindDone) {                           // hard rule: native DOM handlers bound exactly once
    document.getElementById("info-bar").onclick = resetAll;
    document.getElementById("legend-grid").addEventListener("click", function (e) {
      const t = e.target.closest(".legend-item");
      if (!t) return;
      const v = t.dataset.value;
      const p = ROUND_DATA.points.find(function (q) { return String(q.params[ROUND_DATA.group_by]) === v; });
      if (p) highlight(selIndex === p.index ? null : p.index);
    });
    bindDone = true;
  }
}

populateSelect();
if (MANIFEST.rounds.length) { loadRound(MANIFEST.rounds[0].file); }
</script>
</body>
</html>
"""


def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_workspace_config(start_dir):
    """Walk upward for harnessams.json (bare form or inside harnessams-workspace/).
    Returns (config dict, config directory) or (None, None)."""
    d = os.path.abspath(start_dir)
    for _ in range(6):
        for cand_dir in (d, os.path.join(d, "harnessams-workspace")):
            cand = os.path.join(cand_dir, "harnessams.json")
            if os.path.isfile(cand):
                try:
                    with open(cand, encoding="utf-8") as f:
                        return json.load(f), cand_dir
                except (OSError, ValueError):
                    pass
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    return None, None


def resolve_plotly_src(force_cdn, out_dir, project, plotly_arg=None):
    """Workspace-level shared Plotly copy. The single copy lives at
    <workspace_root>/assets/plotly.min.js (workspace root = the directory holding
    harnessams.json). Seed source search order:
    1. an explicit --plotly path (seed override);
    2. the script's ancestor directories, each probed for four candidates:
       assets/plotly.min.js (package-local assets/), src/assets/plotly.min.js (the
       shipped copy inside the dsh-harnessams package), node_modules/dsh-harnessams/
       assets/plotly.min.js (an installed sibling package), and dsh-harnessams/
       assets/plotly.min.js (an ancestor that is a node_modules directory);
    3. the workspace harnessams.json plotly_asset field (relative values resolve
       against the config file's directory).
    A missing or size-stale workspace copy is (re-)seeded from the found source, and
    the report references it with a workspace-relative src (always same-drive, so it
    opens offline; --out on another drive falls back to a copy beside the report).
    With no local source at all the CDN is the fallback; --plotly-cdn forces it."""
    if force_cdn:
        return PLOTLY_CDN
    cfg, cfg_dir = load_workspace_config(project)
    if cfg_dir is None:
        return PLOTLY_CDN
    ws_asset = os.path.join(cfg_dir, "assets", "plotly.min.js")
    seed = plotly_arg
    if not seed:
        d = os.path.dirname(os.path.abspath(__file__))
        for _ in range(5):
            for cand in (os.path.join(d, "assets", "plotly.min.js"),
                         os.path.join(d, "src", "assets", "plotly.min.js"),
                         os.path.join(d, "node_modules", "dsh-harnessams",
                                      "assets", "plotly.min.js"),
                         os.path.join(d, "dsh-harnessams",
                                      "assets", "plotly.min.js")):
                if os.path.isfile(cand):
                    seed = cand
                    break
            if seed:
                break
            parent = os.path.dirname(d)
            if parent == d:
                break
            d = parent
    if not seed:
        field = cfg.get("plotly_asset") if cfg else None
        if field:
            seed = field if os.path.isabs(field) else os.path.normpath(
                os.path.join(cfg_dir, field))
    seed_ok = bool(seed) and os.path.isfile(seed)
    if os.path.isfile(ws_asset):
        if seed_ok and os.path.getsize(ws_asset) != os.path.getsize(seed):
            shutil.copyfile(seed, ws_asset)      # size mismatch = plotly version bump, refresh
    elif seed_ok:
        os.makedirs(os.path.dirname(ws_asset), exist_ok=True)
        shutil.copyfile(seed, ws_asset)
    else:
        return PLOTLY_CDN
    try:
        return os.path.relpath(ws_asset, out_dir).replace(os.sep, "/")
    except ValueError:
        # --out on a different drive than the workspace: no relative route, keep a sibling copy
        shutil.copyfile(ws_asset, os.path.join(out_dir, "plotly.min.js"))
        return "plotly.min.js"


def find_rounds(project_dir, only=None):
    sweep_root = os.path.join(project_dir, "sweep")
    rounds = []
    if not os.path.isdir(sweep_root):
        return rounds
    for name in sorted(os.listdir(sweep_root)):
        if not re.fullmatch(r"Round\d+", name):
            continue
        if only and name != only:
            continue
        top = os.path.join(sweep_root, name, "top")
        res = os.path.join(top, "results.json")
        if os.path.isfile(res):
            rounds.append((name, top, res))
    return rounds


def build_round_data(name, top, results_path):
    res = load_json(results_path)
    meta_path = os.path.join(top, "cell_meta.json")
    meta = load_json(meta_path) if os.path.isfile(meta_path) else {}
    points = res.get("points", [])
    param_names, metric_names = [], []
    if points:
        p0 = points[0]
        param_names = list((p0.get("params") or {}).keys())
        metric_names = list((p0.get("metrics") or {}).keys())
    group_by = None
    group_values = []
    for k in param_names:
        vals = []
        for p in points:
            v = (p.get("params") or {}).get(k)
            if v not in vals:
                vals.append(v)
        if len(vals) > 1 and group_by is None:
            group_by, group_values = k, vals
    if group_by is None and param_names:
        group_by, group_values = param_names[0], [(points[0].get("params") or {}).get(param_names[0])]
    return {
        "round": name,
        "testbench": meta.get("testbench") or meta.get("cell_name", ""),
        "pdk": (meta.get("pdk") or {}).get("name", ""),
        "group_by": group_by or "",
        "group_values": group_values,
        "param_names": param_names,
        "metric_names": metric_names,
        "n_points": res.get("n_points", len(points)),
        "n_success": res.get("n_success", 0),
        "n_partial": res.get("n_partial", 0),
        "n_failed": res.get("n_failed", 0),
        "summary": res.get("summary") or {},
        "best_points": res.get("best_points"),
        "points": points,
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description="ngspice sweep HTML report generator")
    ap.add_argument("--project", required=True)
    ap.add_argument("--round", default=None)
    ap.add_argument("--out", default=None)
    ap.add_argument("--plotly", default=None,
                    help="explicit path to a local plotly.min.js (overrides discovery)")
    ap.add_argument("--plotly-cdn", action="store_true",
                    help="force the Plotly CDN instead of a local plotly.min.js")
    args = ap.parse_args(argv)

    project = os.path.abspath(args.project)
    rounds = find_rounds(project, args.round)
    if not rounds:
        print(f"no round with a results.json found ({os.path.join(project, 'sweep', 'Round*')})",
              file=sys.stderr)
        return 1
    out_dir = os.path.abspath(args.out) if args.out else os.path.join(project, "Analysis")
    data_dir = os.path.join(out_dir, "data")
    os.makedirs(data_dir, exist_ok=True)

    entries = []
    pdk_name = ""
    for name, top, res_path in rounds:
        rd = build_round_data(name, top, res_path)
        pdk_name = pdk_name or rd["pdk"]
        js_name = f"Round{name.replace('Round', '')}.js"
        # hard rule: data JS is plain utf-8 without BOM, json.dumps all-ASCII, one var line
        payload = "var ROUND_DATA = " + json.dumps(rd, separators=(",", ":"), ensure_ascii=True) + ";"
        with open(os.path.join(data_dir, js_name), "w", encoding="utf-8", newline="\n") as f:
            f.write(payload)
        entries.append({"id": name, "title": f"{name} - {rd['testbench']}",
                        "file": f"data/{js_name}", "n_points": rd["n_points"]})

    project_name = os.path.basename(project.rstrip("/\\"))
    rounds_js = ",".join(
        "{id:" + json.dumps(e["id"]) + ",title:" + json.dumps(e["title"]) +
        ",file:" + json.dumps(e["file"]) + ",n_points:" + str(e["n_points"]) + "}"
        for e in entries)
    plotly_src = resolve_plotly_src(args.plotly_cdn, out_dir, project, args.plotly)
    html = (HEAD
            .replace("__CASCADE__", project_name)
            .replace("__PLOTLY__", plotly_src)
            .replace("__NOW__", datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"))
            .replace("__PDK__", pdk_name or "n/a"))
    html += BODY_TOP
    html += (FOOT_JS
             .replace("__CASCADE__", project_name)
             .replace("__ROUNDS__", rounds_js))
    # hard rule: main HTML is utf-8-sig (BOM prevents single-byte-locale misreads)
    report_path = os.path.join(out_dir, f"{project_name}_report.html")
    with open(report_path, "w", encoding="utf-8-sig", newline="\n") as f:
        f.write(html)
    print(f"[report] {len(entries)} round(s) -> {report_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
