# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow semver.

## [Unreleased]

### Added

- S-parameter local reading and computation (2026-10-30, tools-only by design — no UI);
  tool surface grows from six to eight:
  - `snp_read` — parse Touchstone v1 (.s1p..sNp) / Spectre psfascii sp.sp (S/Y/Z
    paramtype) / snp_read params CSV (round-trippable); dump per-frequency S/Z/Y
    curve CSV with a metadata header, plus header info, band stats, reciprocity
    diagnostic and max |Sij|.
  - `snp_extract` — device parameter extraction from the same inputs:
    transformer (L1/L2/Q1/Q2/k from Z, two SRFs, Gmax; 2/3/4/5/6 ports incl.
    center taps), inductor (L/Q/SRF; 1/2/3 ports), tl (Zc, gamma*l, theta,
    optional eps_eff/alpha via length; 2 ports or 4-port differential),
    coupled_tl (even/odd modal Z0e/Z0o/theta/k, f(theta=90), and a theory_ok
    0/1 flag on the relative modal off-block residual; 4 ports or 8-port
    differential). Port model: single-ended sides are one port (cold end
    grounded in the EM setup), differential sides two; `coil*` remap sides,
    `ct*` name taps, `--se` grounds ports during extraction (incl. the
    shorted-secondary leakage view). Missing or unphysical port mappings are
    auto-adjusted against physicality checks and every result echoes a
    `port_plan` for confirmation.
  - Scripts: `scripts/snp_common.py` (loaders, conversions, port reductions),
    `scripts/snp_read.py`, `scripts/snp_extract.py`; numeric backend is numpy
    with the bundled `assets/numfallback.py` pure-stdlib fallback when the
    system python lacks numpy (forced by `SNP_FORCE_FALLBACK=1` for testing).
  - Tests: synthetic round-trip fixtures (`tests/synth_helper.py`) and
    anonymized real EM regression data (`tests/data/`: EMX .s4p, psfascii
    sp.sp, 5-port .s5p) whose extracted values reproduce the reference
    implementation's key results bit-for-bit; `node --test` 22/22.

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
    round selector); workspace-level shared Plotly (see below).
  - `trash.py` — safety-by-default trash (list / restore / purge).
- Plotly delivery, single workspace-level copy: the generator seeds
  `<workspace_root>/assets/plotly.min.js` from an explicit `--plotly` path, four
  ancestor-probed candidates (`assets/`, `src/assets/`,
  `node_modules/dsh-harnessams/assets/`, `dsh-harnessams/assets/`), or the workspace
  `harnessams.json` `plotly_asset` field (refreshing on size mismatch), and every
  report references it with a workspace-relative `src`; the CDN is the fallback
  (`--plotly-cdn` forces it). The master copy lives inside the `dsh-harnessams` package
  in the top-level HarnessAMS repo (`src/assets/plotly.min.js`) and ships with its
  tarball — this package intentionally ships none. Decision record: top-level repo
  `docs/decisions.md` (D-001).
- Workspace convention: `harnessams.json` is discovered by walking upward (bare form, or
  inside `harnessams-workspace/`); relative path fields resolve against the config file's
  directory; `trash_dir` is honored when present.
- Starter tests under `tests/` (`node --test` from the repo root): plugin metadata + tool registration
  via mock ctx, plus a compile smoke over the pipeline scripts.
