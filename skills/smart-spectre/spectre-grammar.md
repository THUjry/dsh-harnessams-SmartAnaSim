# spectre-grammar — spectre syntax essentials

Netlist-language rules the templates and analyses rely on: placeholder discipline, the Spectre-vs-SPICE rules, units, and a section per analysis type spelling out its particularities — AC excitation, S-parameter `paramtype=yz` matrix output and result organization, the swept-PSS configuration and its pitfalls. The operational side — running, sweeping, parsing — lives in `spectre-cli.md`.

## Template placeholder rules (`@@PARAM@@` in the `parameters` line only)

The run directory holds one template netlist and one `sweep_config.json`. The template name is any `template*.scs` family member (default `template.scs`; e.g. `template_thorough.scs` for the PSS variant) — **the name must equal `sweep_config.netlist_template`**.

The worker's regex `@@(\w+)@@` does simple string substitution — no expression evaluation. Parameters defined in the `parameters` line become Spectre variables referenced **anywhere** in instance lines with Spectre-native expression syntax:

```
parameters temperature=@@temperature@@ SW=@@SW@@
...
V2 (SW AVSS) vsource dc=1.2*SW type=dc          // Spectre references SW directly
V3 (SWB AVSS) vsource dc=1.2*(1-SW) type=dc     // Spectre evaluates the expression
```

Only the `parameters` line needs `@@...@@` placeholders. `@@(\w+)@@` matches `[a-zA-Z0-9_]+` only — `@@1-SW@@` will not resolve; write `1-SW` in the instance line instead. `spectre_pre_check` enforces this placement: `@@...@@` anywhere outside the `parameters` statement is an error, with the single exemption of `@@temperature@@` on a `simulatorOptions` line (the shipped sky130 templates carry it there — spectre does not reliably take a parameter reference in `simulatorOptions`). An undeclared `@@temperature@@` is a WARNING, not an error: the sweep worker substitutes the default 27 degC, so a netlist missing its temperature entry never fails the run.

## Spectre vs SPICE rules (enforced by spectre_pre_check)

- `simulator lang=spectre` must lead the netlist; SPICE-only directives are rejected: `.control` `.lib` `.include` `.param` `.model` `.tran` `.ac` `.dc`; the text is LF-only and pure ASCII
- no scientific notation anywhere in the netlist — every number is a plain decimal or carries an explicit SI suffix (`1f`, `100M`, `200u`); `1e-15`-style exponents hide the scale and are rejected
- `subckt`/`ends` must balance; Spectre-native statements carry no leading dot
- template file names must match the `template*.scs` family; `@@P@@` placement (parameters statement only, `@@temperature@@` on a simulatorOptions line exempt) and placeholder/config presence (both directions; undeclared `@@temperature@@` stays a warning — worker default 27 degC) are rejected when violated
- unit suffixes — geometry values (`l` `w` `lr` `wr`...) must carry u/n suffixes; frequencies G/M/k; counts are non-negative integers
- `m`/`M` are case-sensitive per Spectre: `1Meg`, never `1M`
- ideal RCL value positions accept a literal or a bare parameter name only — expressions (`c=(cpf*1f)` and the retired `c=(cpf*1e-15)`) are rejected: reference the parameter (`c=cpf`) and put the scale in suffixed sweep values (`"56f"`). Ranges: R 0.1..1e6 ohm, C 1f..1uF, L 0.1p..10nH (literals, name-referenced swept values, and C/R/L-initial config names); r/c/l = 0 is the legal degenerate element (short / open)
- multiplier ranges — `nf` (device fingers) and `m` (instance multiplier) must be positive integers: nf 1..128, m 1..1000

## Analysis statements — shared rules

- **Each analysis instance writes one psfascii raw file named after it** (under the point's `netlist.scs.raw/`): keep the `sp` instance name bare (`sp sp start=... stop=...`) so S-parameter outputs land in `sp.sp` — an instance name like `sp1` writes `sp1.sp`, which the parse path does not pick up.
- **Explicit temperature always**: `temp=... tnom=...` in `simulatorOptions` and `temperature=...` in the `parameters` line. When the sweep config omits `temperature`, the worker fills `temp=27` (degC) and the pre-check warns — the run never fails on a missing temperature.
- **`save` controls what is written**: `save vout` (single nets) or `save OUT AVSS` (cascade style); unsaved signals do not reach the raw file.

## AC analysis (`ac`)

Small-signal analysis around the DC operating point: `ac ac start=1k stop=100M dec=50` (`dec=N` per decade; `step=` for linear grids). The excitation is the source's small-signal magnitude — spectre `vsource` has **no `ac=` parameter**: set `mag=1` on the source (`V1 (vin 0) vsource dc=0 mag=1`) and read the response as a complex phasor per frequency.

## S-parameter analysis (`sp`)

Statement shape (cascade testbench style): `sp sp ports=[PORT0 PORT1] start=1G stop=40G step=0.1G paramtype=yz`.

- **`paramtype=yz` emits Y- and Z-parameter results alongside S** — one `sp` run therefore yields the S, Y and Z matrices together, no re-run needed; leave it off when only S is wanted.
- Ports are `PORT0 ... port r=50 type=sine` / `PORT1 ... port r=50 type=dc` elements; the `ports=[...]` list fixes the port ordering of the matrices.
- **Result organization**: the psfascii `sp.sp` holds one frequency vector plus the flattened matrix traces (`s11..sNN` complex curves; with `paramtype=yz` the Y/Z traces join them) — per point `NN/` of a sweep, under `netlist.scs.raw/`. From ten ports up spectre switches the trace NAME to the colon spelling (`s1:12`); the parser accepts both, and the flattened output keys follow the same rule — concatenated while every port index is a single digit, colon once either index needs two digits.

## Swept PSS (`sweeppss` + `pss`, the `template_thorough.scs` variant)

Large-signal periodic steady state with an input-power sweep, used for IP1dB-style characterization. Same subckts and chaining as the cascade testbench (see the cascade section in `spectre-cli.md`), but `parameters ... SW1=0 ... SWn=0 pin=@@pin@@ fin=@@fin@@`; `PORT0 ... type=sine freq=fin dbm=pin`; replace the `sp` analysis with:

```
sweeppss sweep param=pin start=-20 stop=30 step=1 {
 pss pss flexbalance=yes oversamplefactor=1 fund=fin harms=10 errpreset=moderate tstab=0 annotate=status
}
```

- **The `pss` knobs**: `fund=fin` (fundamental, tracks the swept `fin`), `harms=10` (harmonics kept), `errpreset=moderate` (accuracy preset), `tstab=0` (transient stabilization runs before PSS starts), `flexbalance=yes` and `oversamplefactor=1` (sampling/balance robustness), `annotate=status` (progress printed to the log — with a plain `pss` this is the only usable progress signal).
- The `sweeppss` wrapper sweeps `param=pin` over **-20..+30 dBm** so high-linearity cells (IP1dB > 20 dBm) are fully characterized, not just reported as a lower bound; `fin` takes one point per target frequency (e.g. `["5G","10G","20G","30G"]`).

**Pitfalls (both required together):**

- **Never write `dbm=@@pin@@` on the PORT0 line.** The placeholder substitutes a literal, freezing the port power so the sweep has no effect. PORT0 must reference the swept parameter as `dbm=pin`.
- **Never remove `pin` from the `parameters` line.** `sweeppss sweep param=pin` needs `pin` as a defined parameter, or Spectre errors out (SFE-1997). Keep `pin=@@pin@@` in the parameters line (config `pin:[0]`).
