# HarnessAMS-SmartAnaSim

> Simulation plugin: ngspice and Spectre channels (code-driven netlist assembly with
> hard PDK isolation), a generic remote LSF channel through dsh-remote, S-parameter
> reading/extraction, and sweep-result HTML reports.
> npm package `dsh-harnessams-smart-ana-sim`, row id `harnessams-smart-ana-sim`. Install
> paths and the compatibility matrix live in the top-level `HarnessAMS` repo.

## Tool surface (14)

| Family | Tool | Runs where |
|---|---|---|
| ngspice | `ngspice_assemble`, `ngspice_run_sweep` | local ngspice (portable build) |
| Spectre | `spectre_assemble`, `spectre_run_sweep`, `spectre_parse` | the host executing this plugin (local Spectre) |
| bsub (generic LSF) | `bsub_submit`, `bsub_poll`, `bsub_fetch` | remote cluster through dsh-remote; no simulator knowledge |
| S-parameter | `snp_read`, `snp_extract` | local (tools only, no UI) |
| shared | `sim_collect`, `sim_progress`, `sim_report`, `sim_trash` | local, simulator-agnostic |

The generic bsub channel is how a Spectre run reaches a cluster: pick the
`spectre/spectre_sweep_worker.py` worker and pass its arguments. Nothing in
`bsub_*` mentions Spectre, and the Spectre tools never touch the network.

## Why the split

- `assemble` is simulator-specific: ngspice writes a SPICE deck and checks
  `.control`/`.lib`/X-prefix rules; Spectre writes `.scs` and checks
  `simulator lang=spectre`, placeholders, include paths and the unit-suffix rule
  (a bare geometry number means metres in Spectre).
- `run_sweep` is simulator-specific: a local ngspice subprocess with stdout
  measurements vs. a local Spectre process with psfascii output and smart
  timeouts (license wait / output-progress extensions).
- `collect` / `progress` / `report` / `trash` are data-contract operations and
  stay shared as `sim_*`.
- The remote channel is infrastructure, not a simulator detail, so it is a
  separate generic `bsub_*` family.

## Repository layout

```
src/
  index.js                 entry: registers the 14 tools (plain JS, no build chain)
  tool.js                  parameter DSL -> valid JSON Schema compiler + validation
  py.js                    python spawn channel, script paths, error text
  projects.js              project gen_*.py discovery/execution (assembly bridge)
  remote.js                dsh-remote shell: rw_machines/rw_exec/rw_upload/rw_download,
                           mirror + live-session borrowing, result-text parsing
  bsub.js                  generic LSF engine: submit command, job record, poll parse,
                           fetch archive/extract, script manifest
  tools/
    ngspice.js  spectre.js  bsub.js  shared.js  snp.js
scripts/
  common/    common_workspace.py common_units.py common_sweep_engine.py
             common_collect_results.py common_gen_sweep_report.py common_trash.py
  ngspice/   ngspice_sweep_worker.py ngspice_checks.py
  spectre/   spectre_sweep_worker.py spectre_checks.py spectre_psf_parse.py
  snp/       snp_common.py snp_read.py snp_extract.py
assets/numfallback.py      pure-stdlib numeric fallback for the snp_* scripts
tests/                     node:test suite + Python worker/PSF self-tests + fixtures
```

Every script filename carries its family prefix; scripts are plain files spawned
by the Node channel (no build step, system Python only). Cross-directory imports
add `scripts/` to `sys.path` and use `from common.… import …`.

## Workspace convention

A `harnessams.json` is discovered by walking upward (bare form, or inside
`harnessams-workspace/`). Relative values resolve against the config file's
directory. Recognized fields:

| Field | Used by |
|---|---|
| `ngspice_cmd`, `pdk_lib`, `trash_dir` | ngspice worker / assembly / trash |
| `spectre_cmd` | local Spectre worker (falls back to PATH) |
| `remote_python`, `remote_scripts_dir` | generic bsub defaults (both optional; remote absolute values stay opaque) |
| `plotly_asset` | report Plotly seeding fallback |

## ngspice channel

1. `ngspice_assemble(project_dir)` runs the project's `script/analysis/gen_*.py`
   (`design_params.json` -> `template.spi` + `sweep_config.json` + metadata).
   Format, dimension and bias<Vdd checks reject at the assembly layer.
2. `ngspice_run_sweep(run_dir)` substitutes `@@PARAM@@` per point, runs
   `ngspice_con -b`, parses `name = value` measurements, and writes
   `NN/point_meta.json`, `sweep_progress.json` (30 s cadence), crash-safe
   `bestPoints.json`, `errlog.txt`, `.worker_started` and `.simulator`.

The sweep config contract is documented in
`scripts/ngspice/ngspice_sweep_worker.py` (`ngspice_cmd`, `ngspice_args`,
`netlist_template`, `timeout_per_point`, `max_parallel`, `metrics_required`,
`best_metric`). The batch engine (combinations, template substitution, progress,
best points) is shared with Spectre.

## Spectre channel

`spectre_run_sweep` is a *local* channel: it runs on the host executing the
plugin. Use it when DSH runs on a simulation host; otherwise submit the same
worker through the bsub tools.

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
  "best_metric": {"name": "...", "mode": "max"},
  "signals_from": "none",
  "license_wait_extension_sec": 120,
  "raw_progress_extension_sec": 120
}
```

- Each point writes `NN/netlist.scs`, `NN/netlist.scs.raw/` (psfascii),
  `NN/spectre.out` and `NN/point_meta.json`.
- Command defaults after token-exact dedup: `+lqtimeout 900`, `-maxw 5`,
  `-maxn 5`, `++aps` (not `+preset aps`, which triggers SPECTRE-132), `+mt`,
  `+logstatus`.
- Smart timeout: before killing, a `Waiting for available license` log line
  extends the deadline once, and files already in the raw directory extend it
  once (simulation in progress).
- `output_format` must stay `psfascii` for `spectre_parse` and for the snp_* S-
  parameter reader.
- `spectre_parse` turns raw PSF ASCII (`ac.ac`, `dc*.dc`, `tran*.tran`,
  `*.info`) into per-point JSON with Re/Im pairs plus an index; optional long
  CSV curves.

`spectre_checks.py` is the assembly-layer library for a project's gen script:
`simulator lang=spectre`, forbidden SPICE directives, subckt/ends pairing,
`@@PARAM@@` vs sweep parameters, the `template.scs` naming warning, relative
include existence, and the u/n-suffix geometry rule.

## Generic bsub channel (remote LSF)

Prerequisites: dsh-remote installed in the profile; the target machine has a
local mirror and a **live session in its remote project** (a DSH workspace
inside `$DSH_HOME/remote-workspaces/<host>-<user>-<port>/<ws>`); LSF `bsub`
available; the batch directory inside the mirror's `remotePath` (the remote
write fence). The plugin consumes only `rw_machines`, `rw_exec`, `rw_upload` and
`rw_download`; it never opens its own SSH channel.

1. `bsub_submit` uploads `scripts/` (relative layout, into
   `<remotePath>/.harnessams/scripts` by default) and the run directory's
   top-level files, then submits:
   ```
   cd <remote_dir> && bsub -q <queue> -J <job> -o bsub.out -e bsub.err \
     <remote_python> <scripts>/<worker> ...worker_args --work-dir <remote_dir>
   ```
   The template is always uploaded as **`template.scs`** (AsymATT convention), so
   `sweep_config.json` must use `"netlist_template": "template.scs"`. The tool
   writes `<run_dir>/.bsub_job.json` and verifies `bjobs` / `errlog.txt` /
   `.worker_started` after submission.
2. `bsub_poll` reads one marker-delimited remote command: `bjobs` state,
   `point_meta.json` count, `sweep_progress.json` summary and the errlog tail.
   A finished job that left the queue is `completed` when all point metas exist
   (LSF's `No job found` is healthy/complete, not an error).
3. `bsub_fetch` tars the requested includes on the remote (`point_meta.json`,
   `sweep_progress.json`, `errlog.txt`, `bsub.out/err` by default; add
   `*/netlist.scs.raw/sp.sp` for Spectre), downloads `fetch_<job>.tgz` and
   extracts it into the local run directory. If `tar` is unavailable it falls
   back to a bounded per-file download. The archive is kept.

Example: submit the Spectre worker and fetch S-parameters.

```
bsub_submit(run_dir="<proj>/sweep/Round1/top", machine_id="node1",
            remote_dir="HarnessAMS/Round1",
            worker="spectre/spectre_sweep_worker.py",
            worker_args=["--config", "sweep_config.json", "--max-parallel", "8"])
bsub_poll(run_dir="...")                     # repeat until state == completed
bsub_fetch(run_dir="...", includes=["point_meta.json",
            "*/netlist.scs.raw/sp.sp", "sweep_progress.json"])
```

Cluster gotchas (verified): use the full remote Python path (a bare `python3`
may resolve to Calibre's Python); `bjobs` empty right after a fast batch is
normal; `+mt` is harmless when LSF allocates one core; keep the remote directory
inside the mirror's `remotePath` or the write fence rejects the upload.

## S-parameter reading and extraction (snp_read / snp_extract)

Inputs auto-detected: Touchstone v1 (`.s1p` .. `.sNp`), Spectre psfascii
`sp.sp` (S/Y/Z paramtype), or a params CSV previously written by `snp_read`
(lossless round trip). All math runs on Z-parameters (per-port reference
impedances honored).

- `snp_read`: header info, band stats, reciprocity diagnostic, per-frequency
  S/Z/Y curve CSV.
- `snp_extract` by `--type`:
  - **transformer** (2/3/4/5/6 ports): L1/L2, Q1/Q2, k, two SRFs, Gmax.
  - **inductor** (1/2/3 ports): L, Q, SRF.
  - **tl** (2 ports, or 4 ports differential): Zc, gamma*l, theta; `--length-m`
    adds eps_eff and alpha per unit length.
  - **coupled_tl** (4 ports, or 8 ports differential): even/odd modal
    Z0e/Z0o/theta/k and a `theory_ok` flag (gate default 5.0e-2, calibrated at 4x
    a validated reference coupler; override with `--theory-gate`).

**Port model / mapping.** `coil`/`coil1`/`coil2` list a side's ports (one port =
single-ended side, two = differential) and remap roles; `ct`/`ct1`/`ct2` name the
center-tap port; `--se` grounds ports during extraction (V=0, Schur). Missing or
unphysical mappings are auto-adjusted against physicality checks (L>0, |k|<=1,
Re(Z0) resistive-dominant, theta increasing, theta_e ~= theta_o) and the result
echoes a `port_plan` to confirm.

**Numeric backend.** numpy is the primary path; when missing, the bundled
`assets/numfallback.py` pure-stdlib backend takes over automatically
(`SNP_FORCE_FALLBACK=1` forces it; the two backends must agree, test-asserted).

## Reports

`sim_report(project_dir)` renders `sweep/Round*/top/results.json` into
`Analysis/<project>_report.html`: summary cards, interactive Plotly metric
charts, per-point table, best point. Plotly comes from a single workspace-level
copy (`<workspace_root>/assets/plotly.min.js`, seeded on demand from an ancestor
`assets/` / `src/assets/` / an installed `dsh-harnessams` package, or the
workspace `plotly_asset` field); the CDN is the fallback. The Simulator header
label comes from `results.json.simulator` (written by `sim_collect`). S-parameter
curve dashboards are not part of this round: use `spectre_parse` + `snp_*` for
RF analysis.

## Validation

- `node --test` from the repo root: 38 passing tests (tool schema/registration,
  script parsing + naming, PSF parsing, ngspice and Spectre worker statuses with
  fake process layers, bsub channel against a mocked dsh-remote tool shell, and
  the existing S-parameter regression suite).
- ngspice demo regression: a single-point rerun of demo1 (tt, 1.62 V, -40 C)
  reproduced the recorded metrics bit-for-bit (`dc_gain_db` 42.8323,
  `ugb_hz` 147379000, `phase_margin_deg` 71.6164, `noise` 2.787523e-05).
- node1 Spectre dual-mode acceptance (2026-10-31, Broadband4 Round1 template,
  2 states at 65 C): foreground run on node1 and LSF job 15924601 both returned
  2/2 success; `point_meta` status/params/signals matched, and each `sp.sp` was
  byte-identical across all 5135 lines except the PSF header `date`. Reproduce
  with `tests/manual/compare_spectre_runs.py <run-a> <run-b>`.

## What the package ships

`src/`, `scripts/{common,ngspice,spectre,snp}` (14 `.py`), `assets/numfallback.py`,
`cordis.patch.yml`, plus npm auto-included `package.json`, `README.md`, `LICENSE`.
Deliberately not shipped: `tests/` (including `tests/data/` and the manual
comparison helper) and any Plotly copy. `PUBLISH.md` holds the tarball checks.

## License

[MIT](LICENSE)
