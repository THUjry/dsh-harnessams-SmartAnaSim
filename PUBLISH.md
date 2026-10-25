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
     must contain exactly: `src/`, `scripts/` (all five pipeline `.py` files),
     `cordis.patch.yml`, plus the npm auto-included `package.json`, `README.md`, `LICENSE`.
   - Must NOT contain: `tests/`, any `__pycache__/` or `*.pyc` (the `files` negations
     enforce this — re-check after touching the whitelist), and `plotly.min.js` in any
     location (the single shared copy for the project lives inside `dsh-harnessams`;
     this package ships none).
3. Publish
   - `npm publish` from this directory.
4. Post-publish verification
   - `dsh plugin --profile web add dsh-harnessams-smart-ana-sim`, restart the profile
     (`dsh --profile web`), confirm the six `sim_*` tools register.
   - Re-generate a sweep report from an existing workspace run: when a local copy is
     discovered (an ancestor `assets/` or `src/assets/`, a sibling `dsh-harnessams`
     install, or the workspace `plotly_asset` field) it lands beside the report and the
     HTML has no `cdn.plot.ly` reference; a standalone install without any local copy
     falls back to the CDN, which is expected, not a failure.
5. Aggregate bundle
   - Only after **both** plugin packages are published: bump and publish
     `dsh-harnessams-suite` from the top-level `HarnessAMS` repo (it pins the exact
     published versions).
