# spectre-cli — pure command-line simulation & parameter sweeps

The operational entry page of the smart-spectre skill: running spectre headlessly, the assembly layer, the run-directory contract, the sweep worker and its config schema, the psf analysis scripts, and the netlist-level cascade assembly (cascade assembly and sweeping are CLI business; the netlist language itself is `spectre-grammar.md`). The `snp_*` S-parameter tools (`snp_read` / `snp_extract`) carry their own tool descriptions and are out of this skill's scope.

## Running spectre directly (no GUI)

A netlist runs headlessly with the raw CLI; psfascii raw output keeps the results plain-text parseable:

```bash
<spectre_bin> -64 netlist.scs +escchars +log spectre.out -format psfascii -raw netlist.scs.raw
```

Variant engines and threading mirror `spectre_args` in sweep_config.json (e.g. `++aps +mt -env ade`). Each analysis instance writes one raw file named after it — keep the `sp` instance name bare so S-parameter outputs land in `sp.sp` exactly where the parsers look (see `spectre-grammar.md`). For automated point sweeps use the sweep worker below instead of shell loops; for reading results with plain python see the analysis-scripts section.

## The assembly layer (`spectre_assemble`)

A project supplies `script/analysis/gen_*.py`; the tool runs it with the project root as cwd (`--out` forwarded, `HARNESSAMS_SIMULATOR=spectre` in the environment) and it writes the run-directory inputs: `template*.scs` + `sweep_config.json` pairs. The suite's project self-check ships a worked example (`gen_selfcheck_rc.py`, an ideal-component RC testbench); its low-pass pair is the minimal reference shape:

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

Before a netlist is accepted, the assembly-layer check library (also applied when you hand-write a template) rejects four families of problems — format, units, ideal RCL ranges, bias — the concrete rules are the syntax section of `spectre-grammar.md`. Check parameters resolve per session: built-in defaults <- the project's optional `.harnessams/check_params.json` <- the calling session's optional confirmed table (`.harnessams/sessions.json`); absent stores fail open.

## Run-directory contract (what the worker writes)

Per point `NN/`: `netlist.scs`, `netlist.scs.raw/` (psfascii; `sp.sp` for S-parameter analyses), `spectre.out`, `NN/point_meta.json` — key fields: `point_index, point_name, simulator, params, status (success|partial|failure|timeout|error), returncode, elapsed_sec, error_message, signals, raw_dir, command, started_at, finished_at`. Run-directory level: `sweep_progress.json` (rewritten every ~30 s and once at the end, atomic tmp+replace: `{status: running|completed|failed, total_points, completed, succeeded, failed, compute_node, simulator, parameters, points: [{index, point_name, status, elapsed_sec, params, error}], updated_at}`), `.worker_started` `{pid, host, started_at, total_points}`, `.compute_node` (plain hostname), `.simulator`, `errlog.txt` (startup/config/template/executable errors — never throws). These files are the ground truth for interpreting a run; the tools and scripts below consume them.

## Sweep worker (`spectre/spectre_sweep_worker.py`)

The worker runs Spectre point-by-point on the host that executes it. It is not coupled to any scheduler or channel: run it directly with python, or under whatever batch system the host provides.

### CLI

```
python ../scripts/spectre/spectre_sweep_worker.py --work-dir <run dir>
       [--config sweep_config.json] [--max-parallel 8] [--spectre-cmd spectre]
```

`--config` resolves relative to the work dir. `--max-parallel` and `--spectre-cmd` override the config. The spectre executable chain: `--spectre-cmd` > `spectre_cmd` in the config > `spectre` on PATH.

### sweep_config.json schema (omitted keys use these defaults)

| key | default | meaning |
|---|---|---|
| `parameters` | required | dict of lists — the sweep values |
| `sweep_mode` | `cross_product` | or `zip` (point count = the first list's length) |
| `spectre_cmd` | `spectre` | executable (absolute or bare for PATH lookup) |
| `spectre_args` | `[]` | extra tokens appended to the command |
| `output_format` | `psfascii` | parser requirement — keep psfascii |
| `netlist_template` | `template.scs` | must equal the template file in the run dir |
| `timeout_per_point` | `600` | seconds per point |
| `max_parallel` | `4` | worker thread-pool concurrency |
| `cshrc_path` | `null` | wrap as `csh -c "source <path>; <cmd>"` (cluster envs) |
| `metrics_required` | `[]` | signal names that must exist, else the point is partial |
| `signals_from` | `none` | `psf_info` reads non-swept `*.info` scalars into point_meta |
| `license_wait_extension_sec` | `120` | one-shot deadline extension on license wait |
| `raw_progress_extension_sec` | `120` | one-shot extension when the raw dir receives files |

### Command construction

```
<spectre_cmd> -64 <netlist> +escchars +log <log> -format psfascii -raw <raw> <spectre_args>
```

plus token-exact defaults when absent from `spectre_args`: `+lqtimeout 900`, `-maxw 5`, `-maxn 5`, `++aps` (not `+preset aps` — SPECTRE-132), `+mt`, `+logstatus`.

### Smart timeout and the global budget

Before a point is killed, two one-shot deadline extensions may fire: a `Waiting for available license` line in the log (license queue), and any file appearing in the raw dir (simulation in progress). The whole run carries a budget of `(timeout + license_ext + raw_ext) * total / max_parallel + 300 s`; points never started when the budget exhausts are written as explicit cancelled points (`status: "error"`, `error_message: "cancelled: global sweep budget exhausted before start"`).

### Exit codes and rerun semantics

`0` = all points success/partial; `1` = some point failed; `2` = startup / config / template / executable error (see `errlog.txt`). The worker always re-runs every combination and overwrites point dirs — there is no resume; guarding a directory against accidental re-runs is the caller's business.

## Analysis scripts (direct python usage)

The scripts tree (resolve `../scripts/...` against this skill's base directory) is self-contained: every script sets up its own imports, numpy is optional (`assets/numfallback.py` is the pure-stdlib fallback), and plain `python` runs them from anywhere. The `snp/` scripts belong to the `snp_*` tools' own descriptions and are not covered here.

### PSF parsing — `spectre/spectre_psf_parse.py`

```
python ../scripts/spectre/spectre_psf_parse.py --work-dir <run dir> [--out-dir DIR] [--signals a,b] [--format json|csv]
python ../scripts/spectre/spectre_psf_parse.py --raw-dir <one point's netlist.scs.raw> [same options]
```

Outputs land in `<target>/psf/` by default: per point `<NN>_psf.json` (swept analyses: signal -> swept curve with complex phasors and delta decompression) — `--format csv` adds the long form `point,signal,index,sweep_value,re,im` — plus `psf_index.json` (the point index and per-point errors). S-parameter `sp.sp` content flattens to `freq` + `s11..sNN` complex curves. Non-swept `*.info` files (DC operating points) parse to name/value scalars. Exit codes: `0` parsed at least one point, `1` nothing parsed, `2` usage/IO error. The parser reads psfascii only — the sweep must run Spectre with `-format psfascii` (the worker's default).

## Cascade netlist assembly (netlist-level stitching)

How to join multiple single-cell netlists into one cascade directly at the netlist level (no schematic). All simulations must be explicit about temperature (`temp=... tnom=...` in `simulatorOptions` and `temperature=...` in the `parameters` line — see `spectre-grammar.md`).

### 1. Wrap each cell as a `subckt` (device values hardcoded)

Take the cell's flat template netlist and:

- **Keep** every device instance (resistors, transistors, MOM caps — including structural elements such as a SimpleT cell's `Rshort (IN OUT) resistor r=0` bridge). These become the subckt body.
- **Drop** the testbench: `PORT0`, `PORT1`, `V1`/`V2`/`V3` (sources), `simulatorOptions`, `sp`/`sweeppss`, `modelParameter`/`element`/`save*` lines.
- **Replace each `@@param@@` placeholder with its literal best-point value** — no `parameters` line inside the subckt, no device-value variables.
- Ports are `AVSS IN OUT SW`, plus `SWB` iff the cell drives a complementary gate (SimpleT cells have no `SWB`).

```
subckt <Name> AVSS IN OUT SW [SWB]
    <device instances with literal values>
ends <Name>
```

### 2. Chain the subckts in series with INTER nodes

The node between stage *n* and stage *n+1* is `INTERn`; the first stage's `IN` is the cascade input, the last stage's `OUT` is the cascade output.

```
I1 (AVSS IN  INTER1  SW1 [SWB1]) <Cell1>
I2 (AVSS INTER1 INTER2 SW2 [SWB2]) <Cell2>
...
In (AVSS INTER(n-1) OUT SWn [SWBn]) <Celln>
```

`PORT0 (IN AVSS) port r=50 type=sine` / `PORT1 (OUT AVSS) port r=50 type=dc`; shared `V1 (AVSS 0) vsource dc=0`; per-cell control sources `VSWn (SWn AVSS) vsource dc=VDD*SWn` and (where the cell has `SWB`) `VSWBn (SWBn AVSS) vsource dc=VDD*(1-SWn)`. Copy the model `include` block unchanged from the cell templates; `sp sp ports=[PORT0 PORT1] start=1G stop=40G step=0.1G paramtype=yz`; `save OUT AVSS`.

### 3. Control-word convention: point index = binary value

For an *n*-cell cascade, run all `2^n` states as one cross-product sweep with `2^n <= 100`:

- `parameters temperature=... SWn=@@SWn@@ ... SW1=@@SW1@@` — **order the `parameters` dict from the last cell to the first** (`{"SW5":[...],"SW4":[...],...,"SW1":[...]}`). The cross-product then enumerates points in binary order: **point index = integer value of the control word**.
- The control word has **bit(n-1) (leftmost) = last cell, bit0 (rightmost) = first cell**. Point `16` means `10000` (only the last cell in attenuation), point `0` means all-reference, point `31` means `11111`.
- No post-rename needed: parsed curves and reports key every curve by point index and params — the control word is recovered from the point index's binary representation.

For the swept-PSS variant of this testbench see the swept-PSS section in `spectre-grammar.md`.
