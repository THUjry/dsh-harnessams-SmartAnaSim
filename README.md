# HarnessAMS-SmartAnaSim

> Simulator tools, pure and standalone — Spectre netlist assembly (template generation + configuration checks), S-parameter reading/extraction, and Virtuoso schematic interaction (command/SKILL-text assembly for the netlist <-> OA round trip). No other plugin is required: the sweep worker and the analysis scripts under `scripts/` run with plain python, and the bundled `smart-spectre` skill documents the whole configuration surface (the HarnessAMS suite drives the same workers through its own managed batch channel, with the workspace `sim_scripts_dir` pointing at this package's `scripts/` tree). npm package `dsh-harnessams-smart-ana-sim`, row id `harnessams-smart-ana-sim`. Install paths and the compatibility matrix live in the top-level `HarnessAMS` repo.

## Tool surface (6)

| Family | Tool | Runs where |
|---|---|---|
| Spectre | `spectre_assemble` | the host executing this plugin (assembly + checks) |
| S-parameter | `snp_guide` (always declared) | local (tools only, no UI) |
| S-parameter | `snp_read`, `snp_extract` (deferred, guide-gated) | local (tools only, no UI) |
| Virtuoso | `gen_vsch_import` | local (assembles ready-to-run command/SKILL text; never executes, no file artifacts) |
| Virtuoso | `gen_vsch_export` | local (assembles ready-to-run command/SKILL text; never executes, no file artifacts) |

Sweep execution and result parsing are deliberately **script-face, not tools** (2026-11-23 ruling: data processing is not tool-shaped; the managed scan flow lives in the HarnessAMS suite's `batch_*` channel). `spectre_assemble` produces the run directory, then:

```
python <pkg>/scripts/spectre/spectre_sweep_worker.py --work-dir <run dir> [--max-parallel 8] [--spectre-cmd spectre]
python <pkg>/scripts/spectre/spectre_psf_parse.py  --work-dir <run dir> [--format csv]
```

run anywhere python exists (the worker needs Spectre on that host). The worker records its measured toolchain (its own interpreter, the resolved spectre, the hostname) into `sweep_progress.json` — the HarnessAMS self-check reads it back to pin the project's cluster toolchain (D-016/D-017). The bundled `smart-spectre` skill carries the full contracts — template rules, the `sweep_config.json` schema, smart timeouts, protocol files, the S-parameter chain, cascade netlist stitching, the PSS variant, and the Virtuoso schematic round-trip — as a root page plus named companion pages.

## Virtuoso schematic interaction (`gen_vsch_import` / `gen_vsch_export`)

These two tools assemble ready-to-run TEXT for the netlist <-> OpenAccess round trip and never execute anything or write files — the cds.lib layout, install roots and license hosts are environment-specific, so execution belongs to the caller (the model via its own shell, or the user through a pasted block). Each returns structured schemes to try in order, or one scheme when requested by name:

- `gen_vsch_import` — `param` (spiceIn `-param` parameter file; `+PLACE_ONLY` label connectivity by default), `cli` (standalone spiceIn flags, full place and route, battle-tested), `plain_skill` (self-contained Cadence SKILL text: conn2Sch + symbol + `<cell>_tb`, pasteable into an open CIW).
- `gen_vsch_export` — `si` (`si -batch -command nl`, needs a one-time CIW-minted si.env), `ocean` (headless with per-run CDS.log isolation, no si.env), `cdlout` (CDL/LVS vocabulary via a SKILL carrier), `plain_skill` (OCEAN/SKILL text for an open CIW — zero startup).

When a headless carrier fails, the skill blocks double as the F1 fallback: paste them verbatim into the user's already-open CIW and have the echo copied back. The recipes and pitfalls (devmap six constructs, termOrder matching, place_only ladder, si.env mint, CDS.log isolation, the CDF parameter-writeback five-step) live in the skill pages `virtuoso-import.md` / `virtuoso-export.md`.

> **Wording rule**: in this repo, "SKILL language" / "SKILL script" always means the Cadence SKILL language (the language itself — not necessarily a file on disk); lowercase `skill` or the bare word means a DSH plugin skill like `smart-spectre`.

## Deferred snp loading (guide-gated)

`snp_read` and `snp_extract` register with `deferLoading: true` plus a `when` admission predicate, so they stay out of the model-facing declarations until the always-resident `snp_guide` tool is called (its execute marks the calling agent and returns the usage flow):

- On routes whose model declares `toolUpdate` (e.g. the default `deepseek-flash`), the loop appends the two tools via `tool-addition` — the KV-cache prefix is preserved; the declaration tokens are paid only by sessions that actually use the family.
- On routes without `toolUpdate`, `deferLoading` is stripped by the route: the `when` gating still keeps the tools out of spectre-only sessions, but activation resends the full declaration list (one-time prefix break at that step).
- Activation is **one-way** per session (addition-only routes drop tool-removal; the set is never unset) and keyed by agent id string, so it survives in-process session restore (a reopened session gets a fresh Agent object with the same id). After a server restart the set is empty and the model self-heals by calling `snp_guide` again; sub-agents are separate agents and call the guide themselves.

## Why this shape

- `spectre_assemble` is simulator-specific: it writes `.scs` and checks `simulator lang=spectre`, placeholders, include paths, the unit-suffix rule (a bare geometry number means metres in Spectre) and ideal R/C/L value ranges.
- The sweep worker is a plain script by design: scanning methodology (submission, monitoring, result retrieval) is not simulator knowledge and does not live in this package.
- Data processing is not a tool: parsing and metrics run as scripts — locally, or on the host that holds the data — keeping the model in charge of the analysis.

## Repository layout

```
src/
  index.js                 entry: registers the 6 tools + the smart-spectre skill
  tool.js                  parameter DSL -> valid JSON Schema compiler + validation
  skills.js                bundled-skill registration (resourceBase = the skill dir)
  py.js                    python spawn channel, script paths, error text
  projects.js              project gen_*.py discovery/execution (assembly bridge)
  tools/
    spectre.js  snp.js  vsch.js
skills/
  smart-spectre/           SKILL.md (generic format + index) and the companion
                           pages worker-config / analysis-scripts / s-params /
                           cascade / pss-variant / virtuoso-import /
                           virtuoso-export (the root names every page —
                           the skill loader does not enumerate the directory)
scripts/
  common/    common_workspace.py common_units.py common_sweep_engine.py
  spectre/   spectre_sweep_worker.py spectre_checks.py spectre_psf_parse.py
  snp/       snp_common.py snp_read.py snp_extract.py
assets/numfallback.py      pure-stdlib numeric fallback for the snp_* scripts
tests/                     node:test suite + Python worker/PSF self-tests + fixtures
```

Every script filename carries its family prefix; scripts are plain files spawned by the Node channel (no build step, system Python only). Cross-directory imports add `scripts/` to `sys.path` and use `from common.… import …`.

## Workspace convention

The family-level `harnessams.json` walker is retired (2026-11-19 D-004): configuration now lives per project at `<project>/.harnessams/workspace_config.json`, and nothing reads `harnessams.json` anymore. This package's former config consumption:

| Former field | Now |
|---|---|
| `spectre_cmd` | the `--spectre-cmd` argument > the run's `sweep_config.json` `spectre_cmd` > `PATH` |
| `pdk_lib` | never consumed here — PDK paths live in assembly-layer configuration only |
| `sim_scripts_dir` | the project's `workspace_config.json` `workspace.sim_scripts_dir` (consumed by the HarnessAMS suite's batch channel; fresh projects stage their own copy under `.harnessams/scripts` + sibling `assets/` — D-015 — and legacy projects keep pointing at this package's `scripts/` tree) |
| `trash_dir` / `plotly_asset` / `remote_python` / `remote_scripts_dir` | retired with the family file |

## Tool family toggles

Each family registers only when its DSH plugin-config switch is not explicitly `false` (all default `true` — the full 6-tool surface, guide included on the snp side). Change the config and cordis hot-swaps the plugin: the fiber restarts and the new selection registers immediately, no reboot.

> DSH (verified on 0.2.1-rc.2, re-checked on 0.2.2-rc.1 sources and live on 0.2.2-rc.3 — the plugin manager still ships no client-side auto-render) does not auto-render a bundle's config: switches appear in the plugin manager only when the bundle ships a browser half registering the `plugins.row.config` slot (key `<package>#<row id>`). This package ships no browser half (deferred), so edit the booleans through the profile config layer; the `Config` export still drives defaults and the hot-swap server-side.

```json
{
  "spectre": true,
  "snp": true,
  "vsch": true
}
```

The schema is `@deepseek-ai/schemastery` (a runtime `dependencies` entry — installed bundles do not get devDependencies; the official `tool-fs` plugin uses the same per-tool boolean-config pattern).

## Spectre channel

`spectre_assemble` runs the project's `script/analysis/gen_*.py` with the project root as cwd (`--out` forwarded, `HARNESSAMS_SIMULATOR=spectre` in the environment) to emit `template*.scs` + `sweep_config.json` pairs. The sweep worker's config contract (see `scripts/spectre/spectre_sweep_worker.py` and the skill's `spectre-cli.md`):

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
- Smart timeout: before killing, a `Waiting for available license` log line extends the deadline once, and files appearing in the raw directory extend it once (simulation in progress).
- `output_format` must stay `psfascii` for the parser and for the snp_* S-parameter reader.
- `spectre_psf_parse.py` turns raw PSF ASCII (`ac.ac`, `dc*.dc`, `tran*.tran`, `*.info`) into per-point JSON with Re/Im pairs plus an index; optional long CSV curves. S-parameter `sp.sp` sections flatten to `freq` + `s11..sNN` complex curves through `snp_common.parse_psfascii` (kind preference S > Z > Y), and `--raw-dir` may also point directly at a standalone `.sp` file (outputs land beside it). For S-parameter matrix CSV dumps and passive-device parameter extraction keep using the `snp_*` tools.

`spectre_checks.py` is the assembly-layer library for a project's gen script: `simulator lang=spectre`, forbidden SPICE directives, subckt/ends pairing, `@@PARAM@@` vs sweep parameters, the `template*.scs` family naming warning (default `template.scs`; outside-family names warn — the declared-vs-uploaded match is the submitting channel's business), relative include existence, the u/n-suffix geometry rule, and the ideal R/C/L value-range gate (R 0.1..1M ohm, C 1f..1u farad, L 0.1p..10n henry). Workspace defaults (2026-11-10, re-shaped by D-005 2026-11-19): `common_workspace.read_check_params` resolves the effective chain built-in defaults <- `.harnessams/check_params.json` (project fallback) <- the calling session's confirmed table in `.harnessams/sessions.json`, keyed by the shell-env built-in `DSH_SESSION_ID` (injected by the host into every model-run shell and passed through the tool shells via `exec.agent.id`; runs without a session identity fall back to the project layer) — it supplies the default R/C/L ranges, the strict-includes switch and the unit_check switch for `check_netlist_format`, and `common_units` consults it for the unit_check/bias_check gates; every failure path falls back to the built-in constants (fail-open).

## S-parameter reading and extraction (snp_read / snp_extract)

These two tools are deferred and guide-gated (see "Deferred snp loading" above): call `snp_guide` first in a session that needs them.

The chain is strictly convert-then-extract: `snp_read` accepts Touchstone v1 (`.s1p` .. `.sNp`), Spectre psfascii `sp.sp` (S/Y/Z paramtype), or a params CSV, and writes the per-frequency S/Z/Y curve CSV (lossless `%.17g` round trip) plus a summary. `snp_extract` (2026-11-23) accepts **only that params CSV** — raw `.sNp` / `sp.sp` inputs exit 2 with a convert-first hint. All math runs on Z-parameters (per-port reference impedances honored).

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
echoes a `port_plan` to confirm. An unphysical result exits 1 with the JSON
(summary/port_plan/candidates) still written — that is the diagnosis, not a crash.

**Numeric backend.** numpy is the primary path; when missing, the bundled `assets/numfallback.py` pure-stdlib backend takes over automatically (`SNP_FORCE_FALLBACK=1` forces it; the two backends must agree, test-asserted).

## Validation

- `node --test` from the repo root (tool schema/registration + the bundled skill, script parsing + naming, PSF parsing, Spectre worker statuses with a fake process layer, and the S-parameter regression suite).
- node1 Spectre dual-mode acceptance through the plugin tools, no direct shell or Python (2026-10-31, Broadband4 Round1 template, 2 states at 65 C): a direct-mode run on node1 (`compute_node=node1`, record `direct-1793437388485`) and an LSF run became Job **15929901** on node5; both returned 2/2 success. Both point metas and both `sp.sp` files were recovered (5135 lines each); the only difference in each pair is the PSF header `date`. `snp_read` on both files gives identical band/port/max|S|/reciprocity values, and the 1564 numeric CSV rows per point are identical. An earlier ssh-only acceptance used Job 15924601; it remains reproducible with `tests/manual/compare_spectre_runs.py <run-a> <run-b>`.

## What the package ships

`src/`, `skills/smart-spectre/` (root page + seven companion pages), `scripts/{common,spectre,snp}` (9 `.py`), `assets/numfallback.py`, `cordis.patch.yml`, plus npm auto-included `package.json`, `README.md`, `LICENSE`. Deliberately not shipped: `tests/` (including `tests/data/` and the manual comparison helper) and any Plotly copy. `PUBLISH.md` holds the tarball checks.

## License

[MIT](LICENSE)
