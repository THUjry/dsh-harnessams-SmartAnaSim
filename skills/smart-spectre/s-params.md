# S-parameter flows

The chain is strictly two stages: **convert, then extract**.

1. `snp_read` (tool or `snp/snp_read.py`) converts Touchstone `.sNp`,
   Spectre psfascii `sp.sp` (kind preference S > Z > Y, per-port `PORT<k>:r`
   reference impedances honored) into the params CSV — the only input
   `snp_extract` accepts.
2. `snp_extract` (tool or `snp/snp_extract.py`) reduces the CSV to
   Z-parameters and extracts the device: `tl` / `coupled_tl` /
   `transformer` / `inductor`.

## Port model (defaults by file port count)

Single-ended means the cold terminal was already grounded in the EM/tb
setup (that side exposes ONE port); differential means both terminals are
ports.

| ports | inductor | transformer | tl | coupled_tl |
|---|---|---|---|---|
| 1 | single-ended | - | - | - |
| 2 | coil (1,2) diff | (1) SE + (2) SE | 2p SE | - |
| 3 | coil (1,2), ct=3 | coil1 (1,2) diff + coil2 (3) SE | - | - |
| 4 | - | coil1 (1,2) + coil2 (3,4) | 4p diff (1,2)+(3,4) | 4p SE |
| 5 | - | (1,2)+ct1=5, (3,4) | - | - |
| 6 | - | (1,2)+ct1=5, (3,4)+ct2=6 | - | - |
| 8 | - | - | - | 8p diff (1,2)(3,4)(5,6)(7,8) |

`coil1..coil4` / `coil` / `ct1` / `ct2` / `ct` remap the roles (one port per
side = single-ended, two = differential; for 4-port tl use `coil1/coil2` as
the near/far differential pairs). `--se` grounds ports during extraction —
a shorted-secondary leakage measurement is `--se 2` on a 2-port
transformer.

## Contracts that matter

- The effective port plan is always echoed (`port_plan` in the JSON and on
  stdout): confirm it before trusting the numbers.
- Missing or unphysical mappings trigger an automatic candidate search
  (winding swaps, tap positions, differential/SE role permutations — all 24
  role permutations for coupled_tl); the best plan is adopted and every
  scored candidate is reported.
- Exit 1 = unphysical-but-diagnosed: `ok=false` WITH `summary` +
  `port_plan` + `candidates[:8]` — that is the diagnosis, not a crash.
- The params CSV round trip is bit-exact (`%.17g`); converting twice is
  safe.
