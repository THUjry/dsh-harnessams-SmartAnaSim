# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow semver.

## [0.1.0] - 2026-10-25

First release (stage 1 scope; not yet published to npm).

### Added

- DSH plugin entry `dsh-harnessams-smart-ana-sim` (row id `harnessams-smart-ana-sim`) exposing
  six tools: `sim_assemble`, `sim_run_sweep`, `sim_collect`, `sim_progress`, `sim_report`,
  `sim_trash`. Source under `src/` (plain JS, no build chain).
- Plain-Python pipeline shipped under `scripts/` (no build step, system Python only):
  - `sweep_worker_ngspice.py` — local ngspice batch sweep skeleton: `@@PARAM@@` templating,
    per-point directories with atomic `point_meta.json`, `sweep_progress.json` (30 s cadence),
    crash-safe `bestPoints.json` persistence, ThreadPoolExecutor concurrency.
  - `checks.py` — assembly-layer checks (netlist format / parameter dimensions / bias < Vdd).
  - `collect_results.py` — per-round aggregation into `results.json` with summary statistics.
  - `gen_sweep_report.py` — self-contained interactive HTML report (Plotly, four sections,
    round selector); local-first Plotly with a single shared copy (see below).
  - `trash.py` — safety-by-default trash (list / restore / purge).
- Plotly resolution, single shared copy: reports use a local `plotly.min.js` found via an
  explicit `--plotly` path, three ancestor-probed candidates (`assets/`,
  `src/assets/`, `node_modules/dsh-harnessams/assets/`), or the workspace
  `harnessams.json` `plotly_asset` field; the CDN is the fallback (`--plotly-cdn` forces it).
  The single copy for this project lives inside the `dsh-harnessams` package in the
  top-level HarnessAMS repo (`src/assets/plotly.min.js`) and ships with its tarball —
  this package intentionally ships none.
- Workspace convention: `harnessams.json` is discovered by walking upward (bare form, or
  inside `harnessams-workspace/`); relative path fields resolve against the config file's
  directory; `trash_dir` is honored when present.
- Starter tests under `tests/` (`node --test` from the repo root): plugin metadata + tool registration
  via mock ctx, plus a compile smoke over the pipeline scripts.
