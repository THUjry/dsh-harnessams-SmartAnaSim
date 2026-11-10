# HarnessAMS-SmartAnaSim

> Simulator plugin: Spectre channel (code-driven netlist assembly with hard PDK isolation) and S-parameter reading/extraction. Simulator-specific code only — the generic remote LSF channel (`batch_*`) and the sweep-result analysis tools (`sim_*`, HTML reports) live in the top-level `HarnessAMS` plugin. npm package `dsh-harnessams-smart-ana-sim`, row id `harnessams-smart-ana-sim`. Install paths and the compatibility matrix live in the top-level `HarnessAMS` repo.

## Tool surface (5)

| Family | Tool | Runs where |
|---|---|---|
| Spectre | `spectre_assemble`, `spectre_run_sweep`, `spectre_parse` | the host executing this plugin (local Spectre) |
| S-parameter | `snp_read`, `snp_extract` | local (tools only, no UI) |

A cluster run reaches these workers through the **top-level plugin's** generic `batch_*` tools: pick the `spectre/spectre_sweep_worker.py` worker (resolved under the workspace `sim_scripts_dir`, which points at this package's `scripts/` tree) and pass its arguments. Nothing here mentions Spectre from the batch side, and the Spectre tools never touch the network.

## Why the split

- `assemble` is simulator-specific: it writes `.scs` and checks `simulator lang=spectre`, placeholders, include paths, the unit-suffix rule (a bare geometry number means metres in Spectre) and ideal R/C/L value ranges.
- `run_sweep` is simulator-specific: a local Spectre process with psfascii output and smart timeouts (license wait / output-progress extensions).
- The generic batch channel and the data-contract analysis operations (collect / progress / report / trash) are **not** simulator details — they live in the top-level `dsh-harnessams` plugin.

## Repository layout

```
src/
  index.js                 entry: registers the 5 tools (plain JS, no build chain)
  tool.js                  parameter DSL -> valid JSON Schema compiler + validation
  py.js                    python spawn channel, script paths, error text
  projects.js              project gen_*.py discovery/execution (assembly bridge)
  tools/
    spectre.js  snp.js
scripts/
  common/    common_workspace.py common_units.py common_sweep_engine.py
  spectre/   spectre_sweep_worker.py spectre_checks.py spectre_psf_parse.py
  snp/       snp_common.py snp_read.py snp_extract.py
assets/numfallback.py      pure-stdlib numeric fallback for the snp_* scripts
tests/                     node:test suite + Python worker/PSF self-tests + fixtures
```

Every script filename carries its family prefix; scripts are plain files spawned by the Node channel (no build step, system Python only). Cross-directory imports add `scripts/` to `sys.path` and use `from common.… import …`.

## Workspace convention

A `harnessams.json` is discovered by walking upward (bare form, or inside `harnessams-workspace/`). Relative values resolve against the config file's directory. Recognized fields:

| Field | Used by |
|---|---|
| `pdk_lib` | Spectre assembly (include-path checks) |
| `spectre_cmd` | local Spectre worker (falls back to PATH) |
| `trash_dir`, `plotly_asset`, `remote_python`, `remote_scripts_dir`, `sim_scripts_dir` | consumed by the top-level `dsh-harnessams` plugin's analysis/batch/report tools (this package only supplies the `scripts/` tree that `sim_scripts_dir` points at) |

## Tool family toggles

Each simulator family registers only when its DSH plugin-config switch is not explicitly `false` (all default `true` — the full 5-tool surface). Change the config and cordis hot-swaps the plugin: the fiber restarts and the new selection registers immediately, no reboot.

> DSH (verified on 0.2.1-rc.2; re-checked against 0.2.2-rc.1 sources — the plugin manager still ships no client-side auto-render) does not auto-render a bundle's config: switches appear in the plugin manager only when the bundle ships a browser half registering the `plugins.row.config` slot (key `<package>#<row id>`). This package ships no browser half (deferred), so edit the booleans through the profile config layer; the `Config` export still drives defaults and the hot-swap server-side.

```json
{
  "spectre": true,
  "snp": true
}
```

The schema is `@deepseek-ai/schemastery` (a runtime `dependencies` entry, like `dsh-remote` — installed bundles do not get devDependencies; the official `tool-fs` plugin uses the same per-tool boolean-config pattern).

## Spectre channel

`spectre_run_sweep` is a *local* channel: it runs on the host executing the plugin. Use it when DSH runs on a simulation host; otherwise submit the same worker through the batch tools. The tool's overall timeout defaults to 1200 s (`timeout_sec` overrides it; per-point limits live in the sweep config).

Config contract (see `scripts/spectre/spectre_sweep_worker.py`):

```json
{
  "parameters": {"temperature": [65], "SW1": [0, 1]},
  "sweep_mode": "cross_product",
  "spectre_cmd": "spectre",
  "spectre_args": ["++aps", "+mt", "-env", "ade"],
  "output_format": "psfascii",
  "netlist_template": "template.scs",
  "timeout_per_point": 300,
  "max_parallel": 1,
  "metrics_required": [],
  "signals_from": "none",
  "license_wait_extension_sec": 120,
  "raw_progress_extension_sec": 120
}
```

- Each point writes `NN/netlist.scs`, `NN/netlist.scs.raw/` (psfascii), `NN/spectre.out` and `NN/point_meta.json`.
- Command defaults after token-exact dedup: `+lqtimeout 900`, `-maxw 5`, `-maxn 5`, `++aps` (not `+preset aps`, which triggers SPECTRE-132), `+mt`, `+logstatus`.
- Smart timeout: before killing, a `Waiting for available license` log line extends the deadline once, and files already in the raw directory extend it once (simulation in progress).
- `output_format` must stay `psfascii` for `spectre_parse` and for the snp_* S-parameter reader.
- `spectre_parse` turns raw PSF ASCII (`ac.ac`, `dc*.dc`, `tran*.tran`, `*.info`) into per-point JSON with Re/Im pairs plus an index; optional long CSV curves.

`spectre_checks.py` is the assembly-layer library for a project's gen script: `simulator lang=spectre`, forbidden SPICE directives, subckt/ends pairing, `@@PARAM@@` vs sweep parameters, the `template.scs` naming warning, relative include existence, the u/n-suffix geometry rule, and the ideal R/C/L value-range gate (R 0.1..1M ohm, C 1f..1u farad, L 0.1p..10n henry).

## Cluster runs (through the top-level plugin)

The generic remote batch channel (`batch_submit` / `batch_poll` / `batch_fetch`) and the sweep-result analysis tools (`sim_analysis` / `sim_progress` / `safe_trash`) belong to the top-level `dsh-harnessams` plugin. Typical remote Spectre round (see the HarnessAMS repo for the full channel contract — mirrors, live-session borrowing, the remote write fence and cluster gotchas):

```
batch_submit(run_dir="<proj>/sweep/Round1/top", machine_id="node1",
             remote_dir="HarnessAMS/Round1",
             worker="spectre/spectre_sweep_worker.py",     # resolved under sim_scripts_dir
             worker_args=["--config", "sweep_config.json", "--max-parallel", "8"],
             scheduler="lsf")                # or "direct" for a login-host foreground run
batch_poll(run_dir="...")                    # repeat until state == completed
batch_fetch(run_dir="...", includes=["point_meta.json",
             "*/netlist.scs.raw/sp.sp", "sweep_progress.json"])
```

The uploaded worker tree is this package's `scripts/` directory (the workspace `sim_scripts_dir`), so remote workers resolve `from common.common_sweep_engine import …` exactly like local ones.

## S-parameter reading and extraction (snp_read / snp_extract)

Inputs auto-detected: Touchstone v1 (`.s1p` .. `.sNp`), Spectre psfascii `sp.sp` (S/Y/Z paramtype), or a params CSV written by `snp_read` (lossless round trip). All math runs on Z-parameters (per-port reference impedances honored).

- `snp_read`: header info, band stats, reciprocity diagnostic, per-frequency S/Z/Y curve CSV.
- `snp_extract` by `--type`:
  - **transformer** (2/3/4/5/6 ports): L1/L2, Q1/Q2, k, two SRFs, Gmax.
  - **inductor** (1/2/3 ports): L, Q, SRF.
  - **tl** (2 ports, or 4 ports differential): Zc, gamma*l, theta; `--length-m`
    adds eps_eff and alpha per unit length.
  - **coupled_tl** (4 ports, or 8 ports differential): even/odd modal
    Z0e/Z0o/theta/k and a `theory_ok` flag (gate default 5.0e-2, calibrated at 4x
    a validated reference coupler; override with `--theory-gate`).

**Port model / mapping.** `coil`/`coil1`/`coil2` list a side's ports (one port = single-ended side, two = differential) and remap roles; `ct`/`ct1`/`ct2` name the center-tap port; `--se` grounds ports during extraction (V=0, Schur). Missing or unphysical mappings are auto-adjusted against physicality checks (L>0, |k|<=1,
Re(Z0) resistive-dominant, theta increasing, theta_e ~= theta_o) and the result
echoes a `port_plan` to confirm.

**Numeric backend.** numpy is the primary path; when missing, the bundled `assets/numfallback.py` pure-stdlib backend takes over automatically (`SNP_FORCE_FALLBACK=1` forces it; the two backends must agree, test-asserted).

## Reports

Sweep-result HTML reports are generated by the top-level plugin's `sim_report` tool (from `sweep/Round*/top/results.json`). S-parameter curve dashboards are not part of this round: use `spectre_parse` + `snp_*` for RF analysis.

## Validation

- `node --test` from the repo root (tool schema/registration, script parsing + naming, PSF parsing, Spectre worker statuses with a fake process layer, and the S-parameter regression suite; the batch-channel and report-injection tests moved to the HarnessAMS repo with the tools).
- node1 Spectre dual-mode acceptance through the plugin tools, no direct shell or Python (2026-10-31, Broadband4 Round1 template, 2 states at 65 C): `batch_submit(scheduler="direct")` ran on node1 (`compute_node=node1`, record `direct-1793437388485`) and `batch_submit(scheduler="lsf")` became Job **15929901** on node5; both returned 2/2 success. `batch_fetch` recovered both point metas and both `sp.sp` files (5135 lines each); the only difference in each pair is the PSF header `date`. `snp_read` on both files gives identical band/port/max|S|/reciprocity values, and the 1564 numeric CSV rows per point are identical. An earlier ssh-only acceptance used Job 15924601; it remains reproducible with `tests/manual/compare_spectre_runs.py <run-a> <run-b>`.

## What the package ships

`src/`, `scripts/{common,spectre,snp}` (9 `.py`), `assets/numfallback.py`, `cordis.patch.yml`, plus npm auto-included `package.json`, `README.md`, `LICENSE`. Deliberately not shipped: `tests/` (including `tests/data/` and the manual comparison helper) and any Plotly copy. `PUBLISH.md` holds the tarball checks.

## License

[MIT](LICENSE)
