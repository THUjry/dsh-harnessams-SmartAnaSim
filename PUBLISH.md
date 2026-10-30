# Publishing dsh-harnessams-smart-ana-sim

Checklist for publishing this package to npm. The repository root **is** the package;
run everything from here.

1. Version and records
   - Bump `version` in `package.json` (semver).
   - Add a `CHANGELOG.md` entry for the new version.
   - Confirm `engines.dsh` still matches the verified DSH version. If the DSH version
     changed, re-run the minimal regression first and update the compatibility matrix in the
     top-level `HarnessAMS` repo (`docs/compatibility.md`).
2. Package content check (must-contain AND must-not-contain)
   - `npm pack --dry-run` (run from the repo root, which **is** the package). The tarball
     must contain exactly: `src/`, `scripts/` (all eight `.py` files: five pipeline +
     `snp_common.py` / `snp_read.py` / `snp_extract.py`), `assets/numfallback.py`,
     `cordis.patch.yml`, plus the npm auto-included `package.json`, `README.md`, `LICENSE`.
   - Must NOT contain: `tests/`, any `__pycache__/` or `*.pyc` (the `files` negations
     enforce this — re-check after touching the whitelist), and `plotly.min.js` in any
     location (the single shared copy for the project lives inside `dsh-harnessams`;
     this package ships none).
3. Publish
   - `npm publish` from this directory.
4. Post-publish verification
   - `dsh plugin --profile web add dsh-harnessams-smart-ana-sim`, restart the profile
     (`dsh --profile web`), confirm the eight tools register (six `sim_*` plus
     `snp_read` / `snp_extract`).
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
