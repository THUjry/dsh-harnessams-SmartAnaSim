---
name: smart-spectre
description: >
  How to run Spectre simulations from the pure command line and how to sweep
  parameters — the spectre-cli page (direct runs, the write/pre-check layer,
  the sweep worker and sweep_config schema, the psf analysis scripts, cascade
  assembly) plus the spectre-grammar page for netlist syntax and per-analysis
  particularities (AC, sp with paramtype=yz, swept PSS), and two Virtuoso
  round-trip pages. Trigger on spectre netlist format, sweep configuration, or
  command-line simulation.
---

# smart-spectre — pure command-line Spectre simulation & parameter sweeps

This package is a pure tool set, usable on its own: `spectre_pre_check` validates hand-written run directories, the `snp_*` tools read S-parameters, `post_check` diagnoses run-directory data integrity on demand (when results look wrong, not as routine pipeline), and the sweep worker plus the analysis scripts under `scripts/` (a sibling of this skill's directory — resolve `../scripts/...` against this skill's base directory) run directly with plain `python`. The skill's scope is the command-line side: **how to run simulations without a GUI and how to sweep parameters**. Two content pages carry it, plus the Virtuoso round-trip pair:

- `spectre-cli.md` — the operational entry page: running spectre headlessly, the write/pre-check layer (hand-written run directories + `spectre_pre_check`), the run-directory contract, the sweep worker CLI and the full sweep_config.json schema, the psf analysis script, the post_check diagnostics, and the netlist-level cascade assembly (the `snp_*` tools carry their own descriptions and are out of scope)
- `spectre-grammar.md` — spectre syntax essentials with a section per analysis type: `@@PARAM@@` template rules, the Spectre-vs-SPICE rules and units, AC excitation, S-parameter `paramtype=yz` matrix output and result organization, the swept-PSS configuration and its pitfalls
- `virtuoso-import.md` — netlist -> OA: the round-trip charter and tool surface, spiceIn recipes (devmap six constructs, termOrder, place_only ladder), symbol + `<cell>_tb` generation, the CDF parameter-writeback five-step, F2 re-import
- `virtuoso-export.md` — .oa -> netlist: si / ocean / cdlOut / plain-SKILL routes, the tune-writeback-diff round trip, ocean batching with per-run CDS.log isolation

## Virtuoso schematic round-trip (pointer)

Schematic-level round-trip work (netlist <-> OpenAccess via the `gen_vsch_import` / `gen_vsch_export` tools) is outside the command-line scope and lives in the two companion pages above: `virtuoso-import.md` (netlist -> OA, the workflow charter, the tool surface and the fallback ladder) and `virtuoso-export.md` (OA -> netlist routes). The `gen_vsch_*` tools assemble ready-to-run text and never execute anything — every scheme also carries a `plain_skill` variant pasteable into an open CIW.

## Beyond this skill (external reference)

Runtime simulator knowledge that this skill deliberately does not duplicate lives in the open-source virtuoso-bridge-lite project's spectre skill (`https://github.com/Arcadia-1/virtuoso-bridge-lite/tree/main/skills/spectre`): simulation-mode / precision presets and their measured accuracy ordering (`spectre`/`aps`/`cx`/`ax`/`mx`/`lx`/`vx`), output-size control (save lists, strobing, output format), transient-noise (`tranNoise`) configuration, and Verilog-A-for-speedup experience rules. Reach for it the same way on any other option word or analysis type this skill does not cover; the runtime gotchas from the same source are already inlined in `spectre-cli.md`.
