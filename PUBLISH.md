# Publishing dsh-harnessams-smart-ana-sim

Checklist for publishing this package to npm. The repository root **is** the package; run everything from here.

1. Version and records
   - Bump `version` in `package.json` (semver; the current line is 0.1.0).
   - Add a `CHANGELOG.md` entry for the new version.
   - Confirm `engines.dsh` still matches the verified DSH version. If the DSH version changed, re-run the minimal regression first and update the compatibility matrix in the top-level `HarnessAMS` repo (`docs/compatibility.md`).
2. Package content check (must-contain AND must-not-contain)
   - `npm pack --dry-run` (run from the repo root, which **is** the package). The tarball must contain exactly: `src/` (entry + tool/py/precheck/skills modules + `tools/`), `skills/smart-spectre/` (root `SKILL.md` + the seven companion pages), `scripts/` (9 `.py`: three `common_*`, three `spectre_*`, three `snp_*`), `assets/numfallback.py`, `cordis.patch.yml`, plus the npm auto-included `package.json`, `README.md`, `LICENSE`.
   - Must NOT contain: `tests/` (including `tests/data/` and `tests/manual/`), any `__pycache__/` or `*.pyc` (the `files` negations enforce this — re-check after touching the whitelist), and `plotly.min.js` in any location (the single shared copy for the project lives inside `dsh-harnessams`; this package ships none).
3. Publish
   - `npm publish` from this directory.
4. Post-publish verification
   - `dsh plugin --profile web add dsh-harnessams-smart-ana-sim`, restart the profile (`dsh --profile web`), confirm the **6** tools register: `spectre_pre_check`, `gen_vsch_import`, `gen_vsch_export` and `snp_guide` are always declared; `snp_read` / `snp_extract` are deferred and appear only after a `snp_guide` call admits them for the session (check via a guide call, not the bare declaration list). Also confirm the `smart-spectre` skill appears in the session's available-skills catalog.
   - Spectre smoke: on a host with Spectre, hand-write a run directory (template.scs + sweep_config.json per the smart-spectre contract) and validate it with `spectre_pre_check`, then execute `scripts/spectre/spectre_sweep_worker.py` on it (2+ points, psfascii) and parse it with `scripts/spectre/spectre_psf_parse.py`; `snp_read` -> `snp_extract` on a resulting `sp.sp` exercises the S-parameter chain.
5. Aggregate bundle
   - Only after **both** plugin packages are published: bump and publish `dsh-harnessams-suite` from the top-level `HarnessAMS` repo (it pins the exact published versions).
