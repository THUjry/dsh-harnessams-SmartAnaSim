# Changelog

All notable changes to this project are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow semver.

## [Unreleased]

### Changed

- snp family is now guide-gated with deferred loading: `snp_read` / `snp_extract` register with `deferLoading: true` plus a `when` admission predicate, so they stay out of the model-facing declarations until the new always-resident `snp_guide` tool is called (its execute marks the calling agent and returns the usage flow). On `toolUpdate` routes (deepseek-flash default) activation rides `tool-addition` and preserves the KV-cache prefix (~745 declaration tokens saved for spectre-only sessions); on routes without it the `when` gating still applies and activation resends the declarations once. Activation is one-way per session, keyed by agent id (survives in-process session restore; server restart self-heals via a guide re-call; sub-agents call the guide themselves). `src/tool.js` forwards def-level `deferLoading` / `when` through the local compiler. Tests 31 → 33 (deferred-registration shape + guide activation semantics).
- Wire-verified on a live 0.2.2-rc.3 dev-mount instance with deepseek-flash (2026-11-18): first request declares 48 tools without snp_*; after the guide call the declaration set grows to 50 with two `defer_loading` entries plus exactly one `tool-addition` pair, and cacheReadTokens never drops at the addition step (12928 → 24192 → 24320 → …). In-process session reopen keeps activation; after a server restart a direct `snp_read` call fails with `unknown tool` (the loop writes a `tool-removal` pair and rebases once — the documented addition-only boundary) before a guide re-call heals it with a fresh addition pair. The same run surfaced that the registry validates execute() results against `output.schema`: a string-returning guide needs an explicit string-schema output override (fixed in this release).

## [0.1.0] - 2026-11-10

Initial release (stage 1 scope).

### Added

- DSH plugin entry `dsh-harnessams-smart-ana-sim` (row id `harnessams-smart-ana-sim`) exposing five tools: `spectre_assemble`, `spectre_run_sweep`, `spectre_parse`, `snp_read`, `snp_extract`. Source under `src/` (plain JS, no build chain); the generic remote batch channel and the sweep-result analysis tools belong to the top-level `dsh-harnessams` plugin — this package stays simulator-specific.
- Spectre channel: code-driven netlist assembly with hard PDK isolation — `.scs` format checks (placeholders, include paths, unit-suffix rule, ideal R/C/L value ranges, bias < Vdd), `@@PARAM@@` templating, per-point `point_meta.json` + `sweep_progress.json` progress, psfascii output with smart license/output timeout extensions, and PSF ASCII parsing (swept/delta/complex plus non-swept `.info` scalars). Overall sweep timeout defaults to 1200 s.
- S-parameter tools (local, tools-only by design — no UI):
  - `snp_read` — parse Touchstone v1 (.s1p..sNp) / Spectre psfascii sp.sp (S/Y/Z paramtype) / params CSV (round-trippable); dump per-frequency S/Z/Y curve CSV with a metadata header, plus header info, band stats, reciprocity diagnostic and max |Sij|.
  - `snp_extract` — device parameter extraction from the same inputs: transformer (L1/L2/Q1/Q2/k, two SRFs, Gmax; 2/3/4/5/6 ports incl. center taps), inductor (L/Q/SRF), tl (Zc, gamma*l, theta; 2-port or 4-port differential), coupled_tl (modal Z0e/Z0o/theta/k, theory gate on the off-block residual). Missing or unphysical port mappings are auto-adjusted against physicality checks; every result echoes a `port_plan` for confirmation.
- Plain-Python pipeline shipped under `scripts/{common,spectre,snp}/` (no build step, system Python only): the family workers plus `common_sweep_engine.py` / `common_units.py` / `common_workspace.py`, `spectre_psf_parse.py`, `spectre_checks.py`, `snp_common.py` / `snp_read.py` / `snp_extract.py`. Numeric backend is numpy with the bundled pure-stdlib `assets/numfallback.py` fallback (forced by `SNP_FORCE_FALLBACK=1`).
- Family toggles via the schemastery `Config` (`spectre` / `snp` booleans, default `true`): `apply(ctx, config)` registers only the enabled families; changes hot-swap the plugin.
- Plotly delivery: reports reference a single workspace-level `<workspace_root>/assets/plotly.min.js` seeded on demand (ancestor-probed candidates or the workspace `plotly_asset` field); the CDN is the fallback. The master copy lives in the top-level `dsh-harnessams` package — this package intentionally ships none (decision D-013 in the top-level repo).
- Workspace convention: `harnessams.json` is discovered by walking upward (bare form, or inside `harnessams-workspace/`); relative path fields resolve against the config file's directory; `trash_dir` is honored when present.
- Tests under `tests/` (`node --test` from the repo root): tool schema/registration, script parsing + naming, PSF parsing, Spectre worker statuses with a fake process layer, the S-parameter regression suite (synthetic round-trip fixtures plus anonymized real EM data), and the RCL range-check scenarios.
