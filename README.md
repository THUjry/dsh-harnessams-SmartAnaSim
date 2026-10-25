# HarnessAMS-SmartAnaSim

> Simulation plugin: local ngspice channel + code-driven netlist assembly (hard PDK isolation) + sweep-result HTML reports.
> npm package `dsh-harnessams-smart-ana-sim`, row id `harnessams-smart-ana-sim`. Install paths and the compatibility matrix live in the top-level `HarnessAMS` repo.

## Scope (stage 1)

1. Local ngspice channel (the remote channel is deferred, along with remote spectre/VerilogA validation — decision of 2026-10-25);
2. Built-in netlist format checks: a broken netlist never reaches the simulator;
3. Sweep-result HTML reports: the loop does not close without them; embeddable in the top-level console. Generated reports use a local `plotly.min.js` when one is discoverable (an ancestor `assets/` or `src/assets/` directory, a sibling `dsh-harnessams` install under `node_modules/`, or the workspace `plotly_asset` field; the single shared copy lives inside the `dsh-harnessams` package in the top-level HarnessAMS repo) and fall back to the Plotly CDN otherwise;
4. Netlists are assembled by code, hard-isolating the PDK: user code never touches PDK files — the assembly layer is the only channel;
5. Input type/dimension checks: lengths accept only u/n, frequencies only G/M/k, counters must be integers; violations are rejected at the assembly layer;
6. Bias-voltage range checks: every bias must stay below Vdd; violations are rejected at the assembly layer;
7. Safety by default: deletion moves paths to a trash folder instead of destroying them; the best point is persisted to `bestPoints.json` as it is found.

## Repository layout

```
src/index.js           plugin entry (plain JS, no build chain; registers the six sim_* tools)
scripts/               pipeline scripts, spawned as fresh python processes:
                         sweep_worker_ngspice.py  local ngspice batch sweep skeleton
                         checks.py                assembly-layer checks
                         collect_results.py       results.json aggregation
                         gen_sweep_report.py      interactive HTML report (local-first Plotly)
                         trash.py                 safety-by-default trash
tests/                 starter unit tests (node:test + a python compile smoke; run: node --test)
cordis.patch.yml       bundle patch (insert row)
CHANGELOG.md / SECURITY.md / PUBLISH.md
```

## What the package ships (besides the JS)

- `scripts/` — the five plain-Python pipeline scripts above: they are the functional body
  of the plugin (every tool invocation spawns a fresh `python <script>` process), so they
  ship with the tarball and take effect on edit without any rebuild;
- `cordis.patch.yml` — the bundle patch (insert row);
- npm auto-included files: `package.json`, `README.md`, `LICENSE`.

Deliberately NOT shipped: `plotly.min.js` (the single shared copy for the project lives in
the `dsh-harnessams` package; reports find it via the discovery chain or fall back to the
CDN) and `tests/`. `PUBLISH.md` holds the must-contain / must-not-contain tarball checks.

## Workspace convention

The pipeline locates a workspace `harnessams.json` by walking upward from the run directory
(bare form, or inside `harnessams-workspace/`). The config provides `ngspice_cmd`, `pdk_lib`,
`trash_dir`, `sim_scripts_dir`, and optionally `plotly_asset` (a local plotly.min.js for
offline reports); relative values resolve against the config file's directory.

## Simulation environment

- ngspice-47 Windows x64 portable: shipped as a local copy with the project; copy the whole directory to install, invoke `bin\ngspice_con.exe -b`. A ready-to-run bundle (ngspice + sky130 + offline report asset + these pipeline scripts) ships in the top-level repo under `LocalDesignExample/`;
- sky130: fossi-foundation latest `combined_models\sky130.lib.spice` (the **continuous** models — the frozen google build fails to parse under ngspice-47, and the binned top-level models impose a w<=100 um limit; MOS instances use the X prefix with bare um values).

## Status

Implemented (M1–M2 milestone scope), smoke-tested against a verified sky130 OTA baseline (9-point PVT batch, zero deviation from reference values). The batch skeleton draws on [Arcadia-1/virtuoso-bridge-lite](https://github.com/Arcadia-1/virtuoso-bridge-lite) (`@@PARAM@@` templating + per-point `point_meta.json` + progress file); the dashboard follows the hard rules of its report generator, adapted to ngspice output.

## License

[MIT](LICENSE)
