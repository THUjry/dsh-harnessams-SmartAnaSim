# PSS / IP1dB variant (`template_thorough.scs`)

Same subckts and chaining as `cascade.md`, but: `parameters ... SW1=0 ...
SWn=0 pin=@@pin@@ fin=@@fin@@`; `PORT0 ... type=sine freq=fin dbm=pin`;
replace the `sp` analysis with:

```
sweeppss sweep param=pin start=-20 stop=30 step=1 {
 pss pss flexbalance=yes oversamplefactor=1 fund=fin harms=10 errpreset=moderate tstab=0 annotate=status
}
```

The pin sweep covers **-20..+30 dBm** so high-linearity cells (IP1dB > 20
dBm) are fully characterized, not just reported as a lower bound. `fin`
takes one point per target frequency (e.g. `["5G","10G","20G","30G"]`).

**Pitfalls (both required together):**

- **Never write `dbm=@@pin@@` on the PORT0 line.** The placeholder
  substitutes a literal, freezing the port power so the sweep has no
  effect. PORT0 must reference the swept parameter as `dbm=pin`.
- **Never remove `pin` from the `parameters` line.** `sweeppss sweep
  param=pin` needs `pin` as a defined parameter, or Spectre errors out
  (SFE-1997). Keep `pin=@@pin@@` in the parameters line (config
  `pin:[0]`).
