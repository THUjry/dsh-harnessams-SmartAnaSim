# tests/data — anonymized real EM regression data

Real electromagnetic-extracted passive device data used by the snp regression tests (`tests/snp.test.mjs`). **Anonymized on import**: files are renamed and identifying header lines (dates, design strings, tool version stamps) are scrubbed or replaced with neutral values; do not use these files to identify any project, process or design. Do not add non-anonymized data here.

| file | what it is | role in tests |
|---|---|---|
| `coupled_em_01.s4p` | 4-port coupled-line structure, Touchstone RI, 0-150 GHz | coupled_tl regression on the touchstone path (auto port-mapping + values vs reference key results) |
| `coupled_ref_01.sp.sp` | 4-port coupled-line structure, Spectre psfascii (S/Y/Z), 50-150 GHz | coupled_tl regression on the psfascii path; also the calibration source of the theory gate (relative off-block residual at 94 GHz ~ 1.25e-2; gate = 4x) |
| `five_port_em_01.s5p` | 5-port device, Touchstone MA (GHz) | parser smoke: header handling, 5-port row-major layout, reciprocity check |

Expected values asserted in the tests come from an independently implemented reference extractor; the two agree to numerical noise on all three files.
