# Analysis scripts — direct python usage

The scripts tree (resolve `../scripts/...` against this skill's base directory) is self-contained: every script sets up its own imports, numpy is optional (`assets/numfallback.py` is the pure-stdlib fallback), and plain `python` runs them from anywhere.

## PSF parsing — `spectre/spectre_psf_parse.py`

```
python ../scripts/spectre/spectre_psf_parse.py --work-dir <run dir> [--out-dir DIR] [--signals a,b] [--format json|csv]
python ../scripts/spectre/spectre_psf_parse.py --raw-dir <one point's netlist.scs.raw> [same options]
```

Outputs land in `<target>/psf/` by default: per point `<NN>_psf.json` (swept analyses: signal -> swept curve with complex phasors and delta decompression) — `--format csv` adds the long form `point,signal,index,sweep_value,re,im` — plus `psf_index.json` (the point index and per-point errors). S-parameter `sp.sp` content flattens to `freq`
+ `s11..sNN` complex curves. Non-swept `*.info` files (DC operating points)
parse to name/value scalars. Exit codes: `0` parsed at least one point, `1` nothing parsed, `2` usage/IO error.

The parser reads psfascii only — the sweep must run Spectre with `-format psfascii` (the worker's default).

## S-parameter scripts — `snp/snp_read.py` and `snp/snp_extract.py`

Read (format conversion):

```
python ../scripts/snp/snp_read.py --file <path> [--param S|Z|Y] [--out-dir DIR]
```

Accepts Touchstone `.s1p..sNp`, psfascii `sp.sp`, or a params CSV; writes `<stem>_params_<X>.csv` (lossless `%.17g` round trip) and `<stem>_read.json` (band statistics, reciprocity diagnostic, max |Sij|).

Extract (numerics):

```
python ../scripts/snp/snp_extract.py --file <params CSV from snp_read> --type <tl|coupled_tl|transformer|inductor> [port options]
```

Accepts ONLY the params CSV — raw `.sNp` / `sp.sp` inputs exit 2 with a convert-first hint. Exit `1` means the extraction ran but the result is unphysical: the JSON (with `summary` / `port_plan` / scored `candidates`) is still written — inspect it before retrying. The `snp_guide` tool returns the same flow in-chat.

## Interpreting run results

`NN/point_meta.json` (params/status/elapsed per point) and `sweep_progress.json` (aggregate + per-point list) are the ground truth of a run directory; see `worker-config.md` for the exact fields.
