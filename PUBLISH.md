# Publishing dsh-harnessams-smart-ana-sim

Checklist for publishing this package to npm. The repository root **is** the package;
run everything from here.

1. Version and records
   - Bump `version` in `package.json` (semver; the current line is 0.1.0).
   - Add a `CHANGELOG.md` entry for the new version.
   - Confirm `engines.dsh` still matches the verified DSH version. If the DSH version
     changed, re-run the minimal regression first and update the compatibility matrix in the
     top-level `HarnessAMS` repo (`docs/compatibility.md`).
2. Package content check (must-contain AND must-not-contain)
   - `npm pack --dry-run` (run from the repo root, which **is** the package). The tarball
     must contain exactly: `src/` (entry + tool/primary modules + `tools/`), `scripts/`
     (14 `.py`: six `common_*`, two `ngspice_*`, three `spectre_*`, three `snp_*`),
     `assets/numfallback.py`, `cordis.patch.yml`, plus the npm auto-included
     `package.json`, `README.md`, `LICENSE`.
   - Must NOT contain: `tests/` (including `tests/data/` and `tests/manual/`), any
     `__pycache__/` or `*.pyc` (the `files` negations enforce this — re-check after
     touching the whitelist), and `plotly.min.js` in any location (the single shared copy
     for the project lives inside `dsh-harnessams`; this package ships none).
3. Publish
   - `npm publish` from this directory.
4. Post-publish verification
   - `dsh plugin --profile web add dsh-harnessams-smart-ana-sim`, restart the profile
     (`dsh --profile web`), confirm the **14** tools register:
     `ngspice_assemble`, `ngspice_run_sweep`, `spectre_assemble`, `spectre_run_sweep`,
     `spectre_parse`, `bsub_submit`, `bsub_poll`, `bsub_fetch`, `sim_collect`,
     `sim_progress`, `sim_report`, `sim_trash`, `snp_read`, `snp_extract`.
   - ngspice smoke: assemble + run a one-point project, `sim_collect`, `sim_report`
     (the report header shows `Simulator: ngspice`).
   - Spectre smoke: on a host with Spectre, run a two-point psfascii sweep and
     `spectre_parse`; then verify `bsub_submit`/`bsub_poll`/`bsub_fetch` against a
     machine whose remote project has a live session (default includes plus
     `*/netlist.scs.raw/sp.sp`), and compare foreground vs bsub with
     `tests/manual/compare_spectre_runs.py` (the archive is kept as `fetch_<job>.tgz`;
     `bjobs` "No job found" with all point metas means completed).
   - Re-generate a sweep report from an existing workspace run: when a local seed
     source is discovered (an ancestor `assets/` or `src/assets/`, a sibling
     `dsh-harnessams` install, or the workspace `plotly_asset` field) the
     workspace-level `<workspace_root>/assets/plotly.min.js` appears and the HTML
     references it with a relative src (no `cdn.plot.ly` reference); a standalone
     install without any local source falls back to the CDN, which is expected,
     not a failure.
5. Aggregate bundle
   - Only after **both** plugin packages are published: bump and publish
     `dsh-harnessams-suite` from the top-level `HarnessAMS` repo (it pins the exact
     published versions).
