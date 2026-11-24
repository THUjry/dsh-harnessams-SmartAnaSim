---
name: smart-spectre
description: >
  Spectre configuration reference: the template*.scs netlist format with
  @@PARAM@@ placeholders, the sweep_config.json contract, the assembly layer
  (spectre_assemble + the project's gen_*.py) with its check chain, the
  Virtuoso schematic round-trip (gen_vsch_import / gen_vsch_export recipes,
  spiceIn / si / ocean pitfalls), and an index of companion pages. Trigger
  on spectre netlist format, sweep configuration, Virtuoso schematic
  import/export, or running the bundled scripts directly.
---

# smart-spectre — Spectre configuration reference

This package is a pure tool set, usable on its own: `spectre_assemble`
generates and checks sweep inputs, the `snp_*` tools read S-parameters, and
the sweep worker plus the analysis scripts under `scripts/` (a sibling of
this skill's directory — resolve `../scripts/...` against this skill's base
directory) run directly with plain `python`. This page carries the GENERIC
netlist and configuration format; specialized topics live in companion
pages — read the one you need, on demand:

- `worker-config.md` — the sweep_config.json schema with defaults, the sweep
  worker CLI, its smart timeouts and global budget, the run-directory
  protocol files and exit codes
- `analysis-scripts.md` — running the psf parser and the snp_* scripts
  directly with python, and their output contracts
- `s-params.md` — the S-parameter chain (sp.sp / .sNp -> snp_read params
  CSV -> snp_extract), the port model and the unphysical-result contract
- `cascade.md` — stitching multiple cell netlists into one cascade testbench
  at the netlist level (subckt wrapping, control-word sweep order)
- `pss-variant.md` — the PSS / IP1dB sweep variant (`template_thorough.scs`)
  and its two mandatory pitfalls
- `virtuoso-import.md` — netlist -> OA: spiceIn recipes (devmap six
  constructs, termOrder, place_only ladder), symbol + `<cell>_tb`
  generation, the CDF parameter-writeback five-step, F2 re-import
- `virtuoso-export.md` — .oa -> netlist: si / ocean / cdlOut / plain-SKILL
  routes, the tune-writeback-diff round trip, ocean batching with per-run
  CDS.log isolation

## Template rules

### Any `template*.scs` name; `@@PARAM@@` in the `parameters` line only

The run directory holds one template netlist and one `sweep_config.json`.
The template name is any `template*.scs` family member (default
`template.scs`; e.g. `template_thorough.scs` for the PSS variant) — **the
name must equal `sweep_config.netlist_template`**.

The worker's regex `@@(\w+)@@` does simple string substitution — no
expression evaluation. Parameters defined in the `parameters` line become
Spectre variables referenced **anywhere** in instance lines with
Spectre-native expression syntax:

```
parameters temperature=@@temperature@@ SW=@@SW@@
...
V2 (SW AVSS) vsource dc=1.2*SW type=dc          // Spectre references SW directly
V3 (SWB AVSS) vsource dc=1.2*(1-SW) type=dc     // Spectre evaluates the expression
```

Only the `parameters` line needs `@@...@@` placeholders. `@@(\w+)@@` matches
`[a-zA-Z0-9_]+` only — `@@1-SW@@` will not resolve; write `1-SW` in the
instance line instead.

## The assembly layer (`spectre_assemble`)

A project supplies `script/analysis/gen_*.py`; the tool runs it with the
project root as cwd (`--out` forwarded, `HARNESSAMS_SIMULATOR=spectre` in
the environment) and it writes the run-directory inputs: `template*.scs` +
`sweep_config.json` pairs. The suite's project self-check ships a worked
example (`gen_selfcheck_rc.py`, an ideal-component RC testbench); its
low-pass pair is the minimal reference shape:

```
// RC low-pass (ideal components, no PDK)
simulator lang=spectre
parameters r_val=1k c_val=1n
V1 (vin 0) vsource dc=0 mag=1
R1 (vin vout) resistor r=@@r_val@@
C1 (vout 0) capacitor c=@@c_val@@
save vout
ac ac start=1e+03 stop=1e+08 dec=50
```

```json
{
  "parameters": { "r_val": ["1k", "2k", "5k"], "c_val": ["1n", "2n", "5n"] },
  "sweep_mode": "cross_product",
  "output_format": "psfascii",
  "netlist_template": "template.scs",
  "timeout_per_point": 120,
  "max_parallel": 4
}
```

Before a netlist is accepted, the assembly-layer check library (also applied
when you hand-write a template) rejects four families of problems:

1. **format** — missing `simulator lang=spectre`; SPICE-only directives
   (`.control` `.lib` `.include` `.param` `.model` `.tran` `.ac` `.dc`);
   unbalanced `subckt`/`ends`; placeholder declared/used mismatch; `include`
   of a missing relative file; non-`template*.scs` template names;
2. **units** — geometry values (`l` `w` `lr` `wr`...) must carry u/n
   suffixes; frequencies G/M/k; currents p/n/u/m; counts non-negative
   integers;
3. **ideal RCL ranges** — R 0.1..1e6 ohm, C 1f..1uF, L 0.1p..10nH
   (m/M is case-sensitive per Spectre: `1Meg`, never `1M`);
4. **bias** — |bias| < Vdd.

Check parameters resolve per session: built-in defaults <- the project's
optional `.harnessams/check_params.json` <- the calling session's optional
confirmed table (`.harnessams/sessions.json`); absent stores fail open.

## Run-directory contract (what the worker writes)

Per point `NN/`: `netlist.scs`, `netlist.scs.raw/` (psfascii; `sp.sp` for
S-parameter analyses), `spectre.out`, `point_meta.json`. Run-directory
level: `sweep_progress.json` (rewritten every ~30 s and at the end),
`.worker_started`, `.compute_node`, `.simulator`, `errlog.txt`. The full
schema, defaults and timeout mechanics: `worker-config.md`.

## Virtuoso schematic round-trip

The `gen_vsch_*` tools assemble ready-to-run text for the netlist <->
OpenAccess round trip (they never execute anything and write no files —
cds.lib layouts and install roots are environment-specific, so execution
belongs to the caller; every scheme also carries a `plain_skill` variant:
self-contained Cadence SKILL text pasteable into an open CIW). Workflow
charter:

| Phase | Who / what | What happens |
|---|---|---|
| 1 | human + export tool | draw the schematic and hand the cell path over; export the netlist headlessly (ocean). For a process never run before: run one minimal no-parameter simulation first and hand its run_dir/log over — that configures the required model sections and simulation setup (afterwards learnable from past projects) |
| 2 | agent | build the testbench and tune at the NETLIST level; get results |
| 3 | tool | display results as HTML |
| 4 | SKILL text | back-annotate device/net names and key parameters into the schematic and form the tb (plain_skill schemes + the writeback recipe in `virtuoso-import.md`) |
| 5 | human | finalize and configure the final tb manually; run it and double-align against the pre-import design: netlist AND simulation results |

Division of labor: agent-orchestrated where flexibility is needed,
tool-constrained where strictness is needed. Model sections and simulator
setup come ONLY from the include lines of user-provided simulation logs
(`input.scs` / run_dir artifacts) — never from browsing PDK or
model-library directories.

Tool surface (IC618-verified) — lightweight CLI first:

| Tool | Direction | Note |
|---|---|---|
| `si` | schematic -> netlist | batch translator, no GUI, no Maestro; first choice for export and validation re-exports |
| `spiceIn` | netlist -> schematic | independent binary; the F2 re-import channel |
| `virtuoso -nograph` (stdin REPL) | SKILL carrier | pipe the SKILL text in (`timeout -s KILL ... virtuoso -nograph -log x.log < s.il`; no `-execute` flag exists); slow start, self-contained log, no interlock; judge by printed markers, not rc |
| `ocean -restore` | SKILL carrier / export | writeback alternate carrier; `createNetlist` is the si.env-free export (log isolation required — see `virtuoso-export.md`) |
| `cdlOut` (SKILL function) | schematic -> CDL | LVS/CDL vocabulary only; rides one of the two carriers |

(`spiceOut` does not exist — the installation's `spice*` set is `spiceIn`
alone.) Hard boundary: Cadence has no SKILL interpreter independent of a
Virtuoso process — parameter writeback is the only round-trip step that
cannot stay on a lightweight CLI.

Fallback ladder when the background carrier fails: **F1** = hand the
self-contained SKILL block to the user for their already-open CIW (zero
new startup cost; they paste the printed echo back); **F2** = re-import
the tuned netlist as `<cell>_tuned` through spiceIn (all-CLI; ASG-placed
graphics, connectivity and parameters guaranteed by the netlist itself).

Deep recipes and pitfalls: `virtuoso-import.md` / `virtuoso-export.md`.
