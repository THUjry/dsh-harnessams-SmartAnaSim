# spectre-grammar — spectre syntax essentials

Netlist-language rules the templates and analyses rely on: placeholder discipline, the Spectre-vs-SPICE rules, units, and a section per analysis type spelling out its particularities — AC excitation, S-parameter `paramtype=yz` matrix output and result organization, the swept-PSS configuration and its pitfalls. The operational side — running, sweeping, parsing — lives in `spectre-cli.md`.

## Template placeholder rules (`@@PARAM@@` on the `parameters` and `include` lines)

The run directory holds one template netlist and one `sweep_config.json`. The template name is any `template*.scs` family member (default `template.scs`; e.g. `template_thorough.scs` for the PSS variant) — **the name must equal `sweep_config.netlist_template`**.

The worker's regex `@@(\w+)@@` does simple string substitution — no expression evaluation. Parameters defined in the `parameters` line become Spectre variables referenced **anywhere** in instance lines with Spectre-native expression syntax:

```
parameters temperature=@@temperature@@ SW=@@SW@@
...
V2 (SW AVSS) vsource dc=1.2*SW type=dc          // Spectre references SW directly
V3 (SWB AVSS) vsource dc=1.2*(1-SW) type=dc     // Spectre evaluates the expression
```

Only the `parameters` line needs `@@...@@` placeholders. `@@(\w+)@@` matches `[a-zA-Z0-9_]+` only — `@@1-SW@@` will not resolve; write `1-SW` in the instance line instead. `spectre_pre_check` enforces this placement: `@@...@@` anywhere outside the `parameters` and `include` statements is an error, with the single exemption of `@@temperature@@` on a `simulatorOptions` line (the shipped sky130 templates carry it there — spectre does not reliably take a parameter reference in `simulatorOptions`). An undeclared `@@temperature@@` is a WARNING, not an error: the sweep worker substitutes the default 27 degC, so a netlist missing its temperature entry never fails the run.

`include` statements are the placement's second home: the placeholder may sit in the path string or the `section=` value — `include "corner_@@corner@@.scs"` or `include "sky130.scs" section=@@corner@@` — so a sweep can swap the included file per point (dspf extracts, corner sections). The worker substitutes text-wide, so the placeholder resolves there exactly like a parameters-line one; a sweep that only varies include text needs no `parameters` statement at all.

## Spectre vs SPICE rules (enforced by spectre_pre_check)

- `simulator lang=spectre` must lead the netlist; SPICE-only directives are rejected: `.control` `.lib` `.include` `.param` `.model` `.tran` `.ac` `.dc`; the text is LF-only and pure ASCII
- no scientific notation anywhere in the netlist — every number is a plain decimal or carries an explicit SI suffix (`1f`, `100M`, `200u`); `1e-15`-style exponents hide the scale and are rejected
- `subckt`/`ends` must balance; Spectre-native statements carry no leading dot
- template file names must match the `template*.scs` family; `@@P@@` placement (parameters and include statements, `@@temperature@@` on a simulatorOptions line exempt) and placeholder/config presence (both directions; undeclared `@@temperature@@` stays a warning — worker default 27 degC) are rejected when violated
- unit suffixes — geometry values (`l` `w` `lr` `wr`...) must carry u/n suffixes; frequencies G/M/k; counts are non-negative integers
- `m`/`M` are case-sensitive per Spectre: `1Meg`, never `1M`
- ideal RCL value positions accept a literal or a bare parameter name only — expressions (`c=(cpf*1f)` and the retired `c=(cpf*1e-15)`) are rejected: reference the parameter (`c=cpf`) and put the scale in suffixed sweep values (`"56f"`). Ranges: R 0.1..1e6 ohm, C 1f..1uF, L 0.1p..10nH (literals, name-referenced swept values, and C/R/L-initial config names); r/c/l = 0 is the legal degenerate element (short / open)
- multiplier gates — `nf` (device fingers) must be a positive integer in 1..128, with a WARNING at or above the `nf_warn` threshold (default 50, check_params); `m` (instance multiplier) is REJECTED by default — a `// m-release` comment on the m= line releases it, and the positive-integer range 1..1000 then applies (the m-semantics section below explains why)
- name discipline — reserved words and name forms: instance/subckt/model/net/parameter names colliding with the merged official reserved lists (netlist keywords incl. prot/unprot, constants, math functions, reserved parameters, analogmodel, known analysis/primitive names) are errors; names must match `^[A-Za-z_][A-Za-z0-9_]*$` — net names additionally `^[0-9]+$` or a bang suffix — and the backslash escape is banned outright (see the reserved-words section)
- template size gates — above 10 KB the template warns (move large circuit definitions into included files), above 100 KB the pre-check rejects it outright (split the netlist with include statements)

## Reserved words and name forms

The merged official reserved-word tables own the name space, and every collision is an error — no tiers: netlist keywords (39, plus the prot/unprot abbreviations), built-in math/physics constants, math functions, `analogmodel` and the known analysis and primitive names on instance/subckt/model name slots; constants, math functions and the User-Guide keyword subset on net names (`in`/`out`/`input`/`output`/`invisible`/`unprotect`/`visible` stay legal as net names — the manuals carve them out); the reserved parameters `temp tnom scale scalem freq time` must never be declared (a `parameters` statement and a sweep-config key are both declarations). The analysis statement's own name slot (`ac ac`, `sp sp`) is convention, not a violation — analysis statements carry no parenthesized net list, so they never reach the name extraction; the instance master slot (the primitive/model reference after the net list) names an external library object, not a hand-written name, and is not checked either.

Name forms follow the official identifier rules: `^[A-Za-z_][A-Za-z0-9_]*$` everywhere, with net names additionally accepting a pure integer (`0`, `07` — leading zeros significant) or a bang suffix (`vdd!`, the global-net idiom). Unescaped `$`, `@`, `-`, blanks, tabs or punctuation are errors, and the backslash escape (`\2N2222` — the official legacy-compat back door, "use these features only when necessary") is banned outright here: rename instead. Boundary notes: an escaped name never reaches the value gates (the instance regex requires a plain leading identifier), and a name containing an escaped blank cannot be represented under whitespace tokenization — moot while escapes are banned.

## The instance multiplier `m` — tiered semantics, rejected by default

`m` is an instance-level generic parameter, not a model parameter: syntactically any instance line accepts `m=`, but whether the result actually scales depends on who implements the scaling — a netlist whose `m=` lines pass the check is not a netlist whose results scale by m. That gap is why `spectre_pre_check` rejects every `m=` by default (`m=1` included): add a `// m-release` comment on the same line as each verified `m=` to release it, and the 1..1000 range gate then applies.

- **Spectre primitives** (`resistor`/`capacitor`/`inductor`, MOS, BJT, diode, JFET): the simulator scales — R → R/m (**divided**, not multiplied), C → C·m, L → L/m; currents, gm, gds and junction capacitances ×m; noise current density ×√m (noise power ×m).
- **`subckt` instances** (most PDK devices are a subckt wrapper): the simulator replicates the whole subckt m times at elaboration — every device and node inside is copied, internal nodes renamed.
- **Verilog-A / CMI custom models**: `m` is a reserved formal name — a module declaring it is ignored with a warning ("reserved for the subcircuit multiplicity factor"), and the model scales only if its author implemented it. Writing `m=` here guarantees nothing.
- **Sources and `tline`** (`vsource`/`isource`/`port`): `m` is meaningless (parallel voltage sources are physically undefined) — do not rely on it.

Three traps worth remembering:

- **`m` multiplies down the hierarchy**: a parent subckt `m=2` around inner devices with `m=3` gives an effective 6 — and `info what=inst` reports only an instance's own m, never the accumulated one, so audit the netlist hierarchy for the total.
- **`m` ≠ `nf`**: `nf` divides one device into fingers (per-finger parasitics, Rg distribution, per-finger junction geometry — AD/AS/PD/PS are per-finger values); `m` parallels whole devices with internal parasitics unchanged. In RF work they are not interchangeable: `nf` for Rg and switching-figure trade-offs, `m` for total width and drive.
- **Monte-Carlo mismatch samples once per (subckt) instance**: `m=4` gets one mismatch offset shared by all four copies (fully correlated), while four independent instances draw four independent samples — replacing N parallel devices with `m=N` underestimates σ.

Self-checks: `spectre -h <primitive>` for the parameter table; an `m=2`-vs-two-parallel-instances `.op` comparison (currents, gm, Cgg must differ by exactly 2× — a mismatch means the device or the PDK's CDF output is broken); `Tools → CDF → Edit` — simInfo `instParameters` must contain `m` (old PDK conversions silently emit `m=1` no matter the schematic multiplier), and a subckt that multiplies internally stacks with an outer `m` — don't double-count.

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
