# HarnessAMS-SmartAnaSim

> Simulation plugin: local ngspice channel + code-driven netlist assembly (hard PDK isolation) + sweep-result HTML reports.
> npm package `dsh-harnessams-smart-ana-sim`, row id `harnessams-smart-ana-sim`. Install paths and the compatibility matrix live in the top-level `HarnessAMS` repo.

## Scope (stage 1)

1. Local ngspice channel (the remote channel is deferred, along with remote spectre/VerilogA validation — decision of 2026-10-25);
2. Built-in netlist format checks: a broken netlist never reaches the simulator;
3. Sweep-result HTML reports: the loop does not close without them; embeddable in the top-level console. Generated reports load `plotly.min.js` from a single workspace-level copy (`<workspace_root>/assets/plotly.min.js`, seeded on demand by the generator from a discoverable local source — an ancestor `assets/` or `src/assets/` directory, a sibling `dsh-harnessams` install under `node_modules/`, or the workspace `plotly_asset` field; the master copy lives inside the `dsh-harnessams` package in the top-level HarnessAMS repo) and fall back to the Plotly CDN when no local source exists;
4. Netlists are assembled by code, hard-isolating the PDK: user code never touches PDK files — the assembly layer is the only channel;
5. Input type/dimension checks: lengths accept only u/n, frequencies only G/M/k, counters must be integers; violations are rejected at the assembly layer;
6. Bias-voltage range checks: every bias must stay below Vdd; violations are rejected at the assembly layer;
7. Safety by default: deletion moves paths to a trash folder instead of destroying them; the best point is persisted to `bestPoints.json` as it is found.

## Repository layout

```
src/index.js           plugin entry (plain JS, no build chain; registers the eight tools:
                       sim_assemble / sim_run_sweep / sim_collect / sim_progress / sim_report /
                       sim_trash + snp_read / snp_extract)
scripts/               pipeline scripts, spawned as fresh python processes:
                         sweep_worker_ngspice.py  local ngspice batch sweep skeleton
                         checks.py                assembly-layer checks
                         collect_results.py       results.json aggregation
                         gen_sweep_report.py      interactive HTML report (workspace-level shared Plotly)
                         trash.py                 safety-by-default trash
                         snp_common.py            S-parameter I/O + math library (loaders, conversions,
                                                  port reductions; shared by both snp_* scripts)
                         snp_read.py              parse an S-parameter file, dump S/Z/Y curves + diagnostics
                         snp_extract.py           device parameter extraction (transformer / inductor /
                                                  tl / coupled_tl) with port auto-adjustment
assets/
  numfallback.py       pure-stdlib numeric fallback backend (used automatically when
                       the system python has no numpy)
tests/                 node:test suite (metadata + mock ctx registration + compile smoke +
                       snp synthetic round-trips and real-data regression); run: node --test
tests/data/            anonymized real EM data used by the snp regression tests (see its README)
cordis.patch.yml       bundle patch (insert row)
CHANGELOG.md / SECURITY.md / PUBLISH.md
```

## S-parameter reading and extraction (snp_read / snp_extract)

Two tools (added 2026-10-30, tools-only by design — no UI), backed by the scripts
above. Inputs auto-detected: Touchstone v1 (`.s1p` .. `.sNp`), Spectre psfascii
`sp.sp` (S/Y/Z paramtype), or a params CSV previously written by `snp_read`
(lossless round trip). All math runs on Z-parameters (per-port reference
impedances honored).

- `snp_read`: header info, band stats, reciprocity diagnostic, per-frequency
  S/Z/Y curve CSV.
- `snp_extract` by `--type`:
  - **transformer** (2/3/4/5/6 ports): L1 = Im(Z11)/w, L2 = Im(Z22)/w,
    Q1/Q2, k = Im(Z21)/sqrt(Im(Z11) Im(Z22)), two SRFs (first + -> - zeros of
    Im(Z11)/Im(Z22)), Gmax (ADE gmax). No M, no turns ratio.
  - **inductor** (1/2/3 ports): L, Q, SRF only.
  - **tl** (2 ports, or 4 ports differential): Zc = sqrt(B/C) (Re>0 branch),
    gamma*l from the ABCD eigenvalue, theta unwrapped; `--length-m` adds
    eps_eff and alpha per unit length.
  - **coupled_tl** (4 ports, or 8 ports differential): even/odd modal
    Z0e/Z0o/theta/k and a `theory_ok` 0/1 flag (relative modal off-block
    residual vs a gate defaulting to 4x a validated reference coupler:
    1.46 ohm at 94 GHz on max|Zm| ~ 116 ohm -> gate 5.0e-2; override with
    `--theory-gate`).

**Port model.** "Single-ended" means the cold terminal was already grounded in
the EM setup, so that side exposes one port fewer; "differential" means both
terminals are ports. `coil`/`coil1`/`coil2` list a side's ports (one port =
single-ended side, two = differential) and remap roles when the file order
differs; `ct`/`ct1`/`ct2` name the center-tap port (taps float unless
grounded); `--se` grounds given ports during extraction (V=0, Schur) —
grounding one terminal of a differential side gives a single-ended view, and
`--se 2` on a 2-port transformer is the shorted-secondary leakage measurement.
Default topologies by port count (the write-script convention): transformer
2:(1)+(2), 3:(1,2)+(3), 4:(1,2)+(3,4), 5:(1,2)+ct1=5+(3,4), 6:+ct2=6; inductor
1:(1), 2:(1,2), 3:(1,2)+ct=3; tl 4p pairs (1,2)+(3,4); coupled_tl 8p pairs
(1,2)(3,4)(5,6)(7,8). Missing or unphysical mappings are auto-adjusted
(candidates scored on physicality: L>0, |k|<=1, Re(Z0)>0 resistive-dominant,
theta increasing, theta_e ~= theta_o) and the result echoes a `port_plan`
(confirm it before trusting the numbers).

**Numeric backend.** numpy is the primary path (the user's system python is
expected to provide it); when numpy is missing the bundled
`assets/numfallback.py` pure-stdlib backend takes over automatically (slower,
identical results; force it with `SNP_FORCE_FALLBACK=1` for testing).

## What the package ships (besides the JS)

- `scripts/` — the plain-Python pipeline scripts above: they are the functional body
  of the plugin (every tool invocation spawns a fresh `python <script>` process), so they
  ship with the tarball and take effect on edit without any rebuild;
- `assets/numfallback.py` — the pure-stdlib numeric fallback for the snp_* scripts
  (auto-used when the system python lacks numpy);
- `cordis.patch.yml` — the bundle patch (insert row);
- npm auto-included files: `package.json`, `README.md`, `LICENSE`.

Deliberately NOT shipped: `plotly.min.js` (the master copy lives inside the
`dsh-harnessams` package; reports reference the workspace-level copy the generator
seeds from it, or fall back to the CDN) and `tests/`. `PUBLISH.md` holds the
must-contain / must-not-contain tarball checks.

## Workspace convention

The pipeline locates a workspace `harnessams.json` by walking upward from the run directory
(bare form, or inside `harnessams-workspace/`). The config provides `ngspice_cmd`, `pdk_lib`,
`trash_dir`, `sim_scripts_dir`, and optionally `plotly_asset` (a local plotly.min.js usable
as the seed source for the workspace-level report asset); relative values resolve against
the config file's directory.

## Simulation environment

- ngspice-47 Windows x64 portable: shipped as a local copy with the project; copy the whole directory to install, invoke `bin\ngspice_con.exe -b`. A ready-to-run bundle (ngspice + sky130 + offline report asset + these pipeline scripts) ships in the top-level repo under `LocalDesignExample/`;
- sky130: fossi-foundation latest `combined_models\sky130.lib.spice` (the **continuous** models — the frozen google build fails to parse under ngspice-47, and the binned top-level models impose a w<=100 um limit; MOS instances use the X prefix with bare um values).

## Status

Implemented (M1–M2 milestone scope), smoke-tested against a verified sky130 OTA baseline (9-point PVT batch, zero deviation from reference values). The batch skeleton draws on [Arcadia-1/virtuoso-bridge-lite](https://github.com/Arcadia-1/virtuoso-bridge-lite) (`@@PARAM@@` templating + per-point `point_meta.json` + progress file); the dashboard follows the hard rules of its report generator, adapted to ngspice output.

## License

[MIT](LICENSE)
