# HarnessAMS-SmartAnaSim

> Simulator tools, pure and standalone — Spectre netlist pre-check (format/value validation for hand-written run directories), S-parameter reading/extraction, Virtuoso schematic interaction (command/SKILL-text assembly for the netlist <-> OA round trip), and on-demand post-run data-integrity diagnostics. No other plugin is required: the sweep worker and the analysis scripts under `scripts/` run with plain python, and the bundled `smart-spectre` skill documents the whole configuration surface (the HarnessAMS suite drives the same workers through its own managed batch channel, with the workspace `sim_scripts_dir` pointing at this package's `scripts/` tree). npm package `dsh-harnessams-smart-ana-sim`, row id `harnessams-smart-ana-sim`. Install paths and the compatibility matrix live in the top-level `HarnessAMS` repo.

## Tool surface (7)

| Family | Tool | Runs where |
|---|---|---|
| Spectre | `spectre_pre_check` | the host executing this plugin (pure JS checks; nothing executes, nothing is written) |
| S-parameter | `snp_guide` (always declared) | local (tools only, no UI) |
| S-parameter | `snp_read`, `snp_extract` (deferred, guide-gated) | local (tools only, no UI) |
| Virtuoso | `gen_vsch_import` | local (assembles ready-to-run command/SKILL text; never executes, no file artifacts) |
| Virtuoso | `gen_vsch_export` | local (assembles ready-to-run command/SKILL text; never executes, no file artifacts) |
| Post-run | `post_check` | local (collect-style diagnostics over a run dir; zero file artifacts) |

Sweep execution and result parsing are deliberately **script-face, not tools** (data processing is not tool-shaped; the managed scan flow lives in the HarnessAMS suite's `batch_*` channel). The sanctioned exception is `post_check`: on-demand data-integrity DIAGNOSIS is tool-shaped — call it when sweep results look wrong, empty or incomplete, not as part of the routine pipeline. The run directory (`template*.scs` + `sweep_config.json`) is hand-written following the `smart-spectre` template contract — `spectre_pre_check` validates it (the managed scan flow in the HarnessAMS suite runs the same check automatically before staging, with a review-then-waive escape); then the worker runs:
```
python <pkg>/scripts/spectre/spectre_sweep_worker.py --work-dir <run dir> [--max-parallel 8] [--spectre-cmd spectre]
python <pkg>/scripts/spectre/spectre_psf_parse.py  --work-dir <run dir> [--format csv]
```

run anywhere python exists (the worker needs Spectre on that host). The worker records its measured toolchain (its own interpreter, the resolved spectre, the hostname) into `sweep_progress.json` — the HarnessAMS self-check reads it back to pin the project's cluster toolchain (D-006/D-003). The bundled `smart-spectre` skill carries the full contracts — template rules, the `sweep_config.json` schema, smart timeouts, protocol files, the S-parameter chain, cascade netlist stitching, the PSS variant, and the Virtuoso schematic round-trip — as a root page plus named companion pages.

## Post-run data-integrity diagnostics (`post_check`)

Call `post_check` when sweep results look wrong, empty or incomplete — it is the collect-style inversion of the parser's strict rules: every point is scanned (bad points are reported, never skipped), every rule violation is caught and enumerated (not raised at the first offender), and the result is one classified JSON report (issue list with severity/check/point/file/key, per-check counts, per-point records, a capped issue list, coverage vs `sweep_progress.json`, errlog tails; zero file artifacts). Inputs: `work_dir` (a sweep run directory) or `raw_dir` (one point's raw dir). It checks coverage and status arithmetic, exactly-one `*.raw` per point, parseability through the soft entry, the axis rules (real, strictly increasing — violations enumerated exhaustively), per-signal vector rules (NaN delta gaps counted), `point_meta.signals` scalar rules (bool / non-finite), and parser-dropped VALUE lines. The JS side is pure orchestration; every check lives in `scripts/spectre/spectre_post_check.py` (argparse owns the exactly-one input rule).

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
- Activation is **one-way** per session (addition-only routes drop tool-removal; the set is never unset) and keyed by agent id string, so it survives in-process session restore (a reopened session gets a fresh Agent object with the same id). After a server restart the set is empty and the model self-heals by calling `snp_guide` again. A delegated subagent inherits an ancestor's activation through SEEDED links (`src/subagent.js`, D-003: the inherited history prefix already carries the guide call, so the declaration reflects it — the walk continues only while every child link is seeded); a non-seeded subagent reads the guide itself.

## Why this shape

- `spectre_pre_check` is simulator-specific: it validates `simulator lang=spectre`, `@@P@@` placeholder placement (parameters and include statements — the include path or `section=` value may sweep; `@@temperature@@` on a simulatorOptions line stays exempt), sweep-parameter presence, the unit-suffix rule (a bare geometry number means metres in Spectre), the ideal R/C/L value ranges, the nf range with its `nf_warn` warning threshold (default 50) and the default-rejected `m` (a same-line `// m-release` comment releases it, then 1..1000); name gates (reserved words from the merged official tables — all errors — and C-identifier name forms, integer/bang nets only, backslash escapes banned) and template size gates (warn above 10 KB, reject above 100 KB) — pure JS, so the submit path needs no local python.
- The sweep worker is a plain script by design: scanning methodology (submission, monitoring, result retrieval) is not simulator knowledge and does not live in this package.
- Data processing is not a tool: parsing and metrics run as scripts — locally, or on the host that holds the data — keeping the model in charge of the analysis. The single sanctioned exception is `post_check`: on-demand integrity diagnosis is tool-shaped (collect-style, report-only, zero artifacts), because the failure mode it serves is "the model is already stuck on weird data".

## Repository layout

```
src/
  index.js                 entry: registers the 7 tools + the smart-spectre skill
  tool.js                  parameter DSL -> valid JSON Schema compiler + validation
  skills.js                bundled-skill registration (resourceBase = the skill dir)
  py.js                    python spawn channel, script paths, error text
  precheck.js              the JS netlist pre-check engine (chain + families)
  tools/
    spectre.js  snp.js  vsch.js  post.js
skills/
  smart-spectre/           SKILL.md (generic format + index) and the companion
                           pages worker-config / analysis-scripts / s-params /
                           cascade / pss-variant / virtuoso-import /
                           virtuoso-export (the root names every page —
                           the skill loader does not enumerate the directory)
scripts/
  common/    common_workspace.py common_units.py common_sweep_engine.py
  spectre/   spectre_sweep_worker.py spectre_checks.py spectre_psf_parse.py
             spectre_post_check.py
  snp/       snp_common.py snp_read.py snp_extract.py
assets/numfallback.py      pure-stdlib numeric fallback for the snp_* scripts
tests/                     node:test suite + Python worker/PSF/post_check self-tests + fixtures
                           (see the Test map below)
```

Every script filename carries its family prefix; scripts are plain files spawned by the Node channel (no build step, system Python only). Cross-directory imports add `scripts/` to `sys.path` and use `from common.… import …`.

## Test map

Every functional area maps to its test files (file-level; the suites run with bare `node --test` from the repo root):

| Functional area | Tests |
|---|---|
| Plugin entry: 7-tool registration, JSON-Schema compilation, family toggles, deferred snp loading/activation/inheritance | `tests/entry.test.mjs` |
| `spectre_pre_check` engine (format/sweep/value families, check-params chain, session resolution) | `tests/precheck.test.mjs` |
| `snp_guide` / `snp_read` / `snp_extract` tool faces + numeric suites | `tests/snp.test.mjs` (mounting `tests/snp_common_test.py`, `tests/snp_extract_test.py`) |
| `gen_vsch_import` / `gen_vsch_export` scheme contracts | `tests/vsch.test.mjs` |
| `post_check` tool face + collect-all diagnostics | `tests/post.test.mjs` (mounting `tests/post_check_test.py`) |
| Python self-test mounts (worker, psf parse, netlist checks) + sessionEnv identity | `tests/spectre.test.mjs` (mounting `tests/spectre_worker_test.py`, `tests/psf_parse_test.py`, `tests/spectre_checks_test.py`) |
| Shipped-script census + family filename prefix convention | `tests/pipeline.test.mjs` |
| Subagent identity walk + byte-identity mirror guard | `tests/subagent.test.mjs` (the policy file is shared verbatim with the HarnessAMS repo) |

## Workspace convention

The family-level `harnessams.json` walker is retired (D-002): configuration lives per project at `<project>/.harnessams/workspace_config.json`, and nothing reads `harnessams.json` anymore. This package's remaining config face is `spectre_cmd`, resolved as the `--spectre-cmd` argument > the run's `sweep_config.json` `spectre_cmd` > `PATH`; PDK paths live in assembly-layer configuration only; the project's `workspace.sim_scripts_dir` is consumed by the HarnessAMS suite's batch channel — a fresh project records the package install location as its scripts root, a config-explicit project-local root heals lazily, and legacy projects keep their explicit values.

## Tool family toggles

Each family registers only when its DSH plugin-config switch is not explicitly `false` (all default `true` — the full 7-tool surface, guide included on the snp side). Change the config and cordis hot-swaps the plugin: the fiber restarts and the new selection registers immediately, no reboot.

> DSH (re-checked on 0.2.2-rc.5) does not auto-render a bundle's config: switches appear in the plugin manager only when the bundle ships a browser half registering the `plugins.row.config` slot (key `<package>#<row id>`). This package ships no browser half (deferred), so edit the booleans through the profile config layer; the `Config` export still drives defaults and the hot-swap server-side.

```json
{
  "spectre": true,
  "snp": true,
  "vsch": true,
  "post": true
}
```

The schema is `@deepseek-ai/schemastery` (a runtime `dependencies` entry — installed bundles do not get devDependencies; the official `tool-fs` plugin uses the same per-tool boolean-config pattern).

## Spectre channel

The run directory is hand-written (`template*.scs` with `@@P@@` placeholders on the parameters statement and on include lines (the include path or `section=` value may sweep — dspf extracts, corner sections; an include-only sweep needs no parameters statement), the circuit referencing parameters by name, SI-suffixed numbers only — no scientific notation, ideal R/C/L values as literals or parameter names, never expressions — `m` is rejected by default, each `m=` line needing a same-line `// m-release` comment (see the smart-spectre grammar page) — plus `sweep_config.json`; an omitted `temperature` gets the worker's 27 degC default. The `smart-spectre` skill documents the full template contract) and validated by `spectre_pre_check` (the managed scan flow in the HarnessAMS suite runs the same check automatically before staging, so a separate pre-run is a re-run, not a pipeline step). Name discipline and template size are gated too — reserved words on name slots (merged official tables) and non-identifier name forms are errors (net names may be integers or bang-suffixed; backslash escapes banned), a template above 10 KB warns and above 100 KB is rejected (split with include statements). Projects that prefer scripted assembly may still ship a `script/analysis/gen_*.py` (run it directly with the project root as cwd and `--out` forwarded; `spectre_checks.py` keeps the same contract for that optional path — the gen-script flow is legacy, no longer the recommended workflow). The sweep worker's config contract (see `scripts/spectre/spectre_sweep_worker.py` and the skill's `spectre-cli.md`):

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
- `spectre_psf_parse.py` turns raw PSF ASCII (`ac.ac`, `dc*.dc`, `tran*.tran`, `*.info`) into per-point JSON with Re/Im pairs plus an index; optional long CSV curves. It is a two-layer contract: the ENTRY is soft (every parse folds failures into a `PsfResult` — status `success|partial|failure|error`, data, errors, warnings, metadata; the batch CLI never stops on a bad point and returns the errors in the outputs), the ACCESSORS are hard (`scalar`/`vector`/`axis`/`frequency_hz` raise ValueError on rule violations for fail-loud consumers; `post_check` inverts them into collect-all diagnostics). Non-swept VALUE lines flatten one-level STRUCT payloads (`"M1" { "gm" .. "gds" .. }` -> `M1:gm`, `M1:gds`); lines that fit no shape become warnings instead of silent loss. S-parameter `sp.sp` sections flatten to `freq` + `s11..sNN` complex curves through `snp_common.parse_psfascii` (kind preference S > Z > Y; both trace spellings parse — concatenated below ten ports and the colon form `s1:12` from ten ports up — and the flattened keys use the colon form once a port index needs two digits; an sp analysis named other than bare `sp` is called out as a warning — keep the instance name bare), and `--raw-dir` may also point directly at a standalone `.sp` file (outputs land beside it). For S-parameter matrix CSV dumps and passive-device parameter extraction keep using the `snp_*` tools.

`spectre_checks.py` is the gen-script companion of `spectre_pre_check` (the JS engine in `src/precheck.js` is the submit-path authority; both faces enforce the same contract): `simulator lang=spectre`, forbidden SPICE directives, subckt/ends pairing, `@@PARAM@@` placement (parameters and include statements, `@@temperature@@` on a simulatorOptions line exempt), `@@P@@` <-> sweep-parameter presence, the `template*.scs` family naming warning (default `template.scs`; outside-family names warn — the declared-vs-uploaded match is the submitting channel's business), the u/n-suffix geometry rule, the ideal R/C/L value-range gate (R 0.1..1M ohm, C 1f..1u farad, L 0.1p..10n henry), the nf/m multiplier gates (nf positive integers in 1..128 with a warning at or above `nf_warn` — default 50; m rejected by default, a same-line `// m-release` comment releasing it, then 1..1000), the name gates (reserved-word collisions on instance/subckt/model/net/parameter name slots — merged official tables, all errors — and identifier name forms with integer/bang nets, backslash escapes banned) and the template size gates (10 KB warn / 100 KB reject). Workspace defaults (legacy `bias_check`/`strict_includes` keys are ignored on read — retired with their check families): `common_workspace.read_check_params` resolves the effective chain built-in defaults <- `.harnessams/check_params.json` (project fallback) <- the calling session's confirmed table in `.harnessams/sessions.json`, keyed by the shell-env built-in `DSH_SESSION_ID` (injected by the host into every model-run shell and passed through the tool shells via `exec.agent.id` — a delegated subagent rides its topmost user-visible ancestor's id, `src/subagent.js` D-003; runs without a session identity fall back to the project layer) — it supplies the default R/C/L and nf/m tables, the `nf_warn` threshold and the unit_check switch; every failure path falls back to the built-in constants (fail-open).

## S-parameter reading and extraction (snp_read / snp_extract)

These two tools are deferred and guide-gated (see "Deferred snp loading" above): call `snp_guide` first in a session that needs them.

The chain is strictly convert-then-extract: `snp_read` accepts Touchstone v1 (`.s1p` .. `.sNp`), Spectre psfascii `sp.sp` (S/Y/Z paramtype), or a params CSV, and writes the per-frequency S/Z/Y curve CSV (lossless `%.17g` round trip) plus a summary. `snp_extract` accepts **only that params CSV** — raw `.sNp` / `sp.sp` inputs exit 2 with a convert-first hint. All math runs on Z-parameters (per-port reference impedances honored).

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

- `node --test` from the repo root; the function-to-test map is the Test map section above.
- node1 Spectre dual-mode acceptance through the plugin tools, no direct shell or Python (Broadband4 Round1 template, direct + LSF runs, 2/2 success each; recovered point metas and `sp.sp` files differ only in the PSF header `date`, and the numeric CSV rows are identical) — reproducible with `tests/manual/compare_spectre_runs.py <run-a> <run-b>`.

## What the package ships

`src/`, `skills/smart-spectre/` (root page + seven companion pages), `scripts/{common,spectre,snp}` (10 `.py`), `assets/numfallback.py`, `cordis.patch.yml`, plus npm auto-included `package.json`, `README.md`, `LICENSE`. Deliberately not shipped: `tests/` (including `tests/data/` and the manual comparison helper) and any Plotly copy. `PUBLISH.md` holds the tarball checks.

## License

[MIT](LICENSE)
