This directory holds small hand-written Spectre psfascii (PSF ASCII) fixtures used
by tests/psf_parse_test.py and tests/spectre_worker_test.py.

point_ac/netlist.scs.raw/
  ac.ac      swept AC data: complex VOUT, three frequency points, and a delta-
             compressed middle step that carries no VOUT value (the parser must
             back-fill NaN between snapshots)
  dcOp.info  non-swept operating-point scalars (fed to signals_from=psf_info)

bad/netlist.scs.raw/notes.txt has no parseable PSF data (error-path fixture).
