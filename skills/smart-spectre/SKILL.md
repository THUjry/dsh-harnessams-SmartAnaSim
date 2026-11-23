---
name: smart-spectre
description: >
  Spectre configuration reference: the template*.scs netlist format with
  @@PARAM@@ placeholders, the sweep_config.json contract, the assembly layer
  (spectre_assemble + the project's gen_*.py) with its check chain, and an
  index of companion pages (sweep worker, analysis scripts, S-parameters,
  cascade stitching, PSS variant). Trigger on spectre netlist format, sweep
  configuration, or running the bundled scripts directly.
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
