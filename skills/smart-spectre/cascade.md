# Cascade netlist assembly (netlist-level stitching)

How to join multiple single-cell netlists into one cascade directly at the
netlist level (no schematic). All simulations must be explicit about
temperature (`temp=... tnom=...` in `simulatorOptions` and `temperature=...`
in the `parameters` line).

## 1. Wrap each cell as a `subckt` (device values hardcoded)

Take the cell's flat template netlist and:

- **Keep** every device instance (resistors, transistors, MOM caps —
  including structural elements such as a SimpleT cell's
  `Rshort (IN OUT) resistor r=0` bridge). These become the subckt body.
- **Drop** the testbench: `PORT0`, `PORT1`, `V1`/`V2`/`V3` (sources),
  `simulatorOptions`, `sp`/`sweeppss`, `modelParameter`/`element`/`save*`
  lines.
- **Replace each `@@param@@` placeholder with its literal best-point
  value** — no `parameters` line inside the subckt, no device-value
  variables.
- Ports are `AVSS IN OUT SW`, plus `SWB` iff the cell drives a complementary
  gate (SimpleT cells have no `SWB`).

```
subckt <Name> AVSS IN OUT SW [SWB]
    <device instances with literal values>
ends <Name>
```

## 2. Chain the subckts in series with INTER nodes

The node between stage *n* and stage *n+1* is `INTERn`; the first stage's
`IN` is the cascade input, the last stage's `OUT` is the cascade output.

```
I1 (AVSS IN  INTER1  SW1 [SWB1]) <Cell1>
I2 (AVSS INTER1 INTER2 SW2 [SWB2]) <Cell2>
...
In (AVSS INTER(n-1) OUT SWn [SWBn]) <Celln>
```

`PORT0 (IN AVSS) port r=50 type=sine` / `PORT1 (OUT AVSS) port r=50
type=dc`; shared `V1 (AVSS 0) vsource dc=0`; per-cell control sources
`VSWn (SWn AVSS) vsource dc=VDD*SWn` and (where the cell has `SWB`)
`VSWBn (SWBn AVSS) vsource dc=VDD*(1-SWn)`. Copy the model `include` block
unchanged from the cell templates; `sp sp ports=[PORT0 PORT1] start=1G
stop=40G step=0.1G paramtype=yz`; `save OUT AVSS`.

## 3. Control-word convention: point index = binary value

For an *n*-cell cascade, run all `2^n` states as one cross-product sweep
with `2^n <= 100`:

- `parameters temperature=... SWn=@@SWn@@ ... SW1=@@SW1@@` — **order the
  `parameters` dict from the last cell to the first**
  (`{"SW5":[...],"SW4":[...],...,"SW1":[...]}`). The cross-product then
  enumerates points in binary order: **point index = integer value of the
  control word**.
- The control word has **bit(n-1) (leftmost) = last cell, bit0 (rightmost)
  = first cell**. Point `16` means `10000` (only the last cell in
  attenuation), point `0` means all-reference, point `31` means `11111`.
- No post-rename needed: parsed curves and reports key every curve by point
  index and params — the control word is recovered from the point index's
  binary representation.

For the swept-PSS variant of this testbench see `pss-variant.md`.
