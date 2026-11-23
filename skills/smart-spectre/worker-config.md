# Sweep worker configuration (`spectre/spectre_sweep_worker.py`)

The worker runs Spectre point-by-point on the host that executes it. It is
not coupled to any scheduler or channel: run it directly with python, or
under whatever batch system the host provides.

## CLI

```
python ../scripts/spectre/spectre_sweep_worker.py --work-dir <run dir>
       [--config sweep_config.json] [--max-parallel 8] [--spectre-cmd spectre]
```

`--config` resolves relative to the work dir. `--max-parallel` and
`--spectre-cmd` override the config. The spectre executable chain:
`--spectre-cmd` > `spectre_cmd` in the config > `spectre` on PATH.

## sweep_config.json schema (omitted keys use these defaults)

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

## Command construction

```
<spectre_cmd> -64 <netlist> +escchars +log <log> -format psfascii -raw <raw> <spectre_args>
```

plus token-exact defaults when absent from `spectre_args`: `+lqtimeout 900`,
`-maxw 5`, `-maxn 5`, `++aps` (not `+preset aps` — SPECTRE-132), `+mt`,
`+logstatus`.

## Smart timeout and the global budget

Before a point is killed, two one-shot deadline extensions may fire: a
`Waiting for available license` line in the log (license queue), and any
file appearing in the raw dir (simulation in progress). The whole run
carries a budget of `(timeout + license_ext + raw_ext) * total /
max_parallel + 300 s`; points never started when the budget exhausts are
written as explicit cancelled points (`status: "error"`,
`error_message: "cancelled: global sweep budget exhausted before start"`).

## Protocol files

- `sweep_progress.json` — rewritten every ~30 s and once at the end (atomic
  tmp+replace): `{status: running|completed|failed, total_points, completed,
  succeeded, failed, compute_node, simulator, parameters, points: [{index,
  point_name, status, elapsed_sec, params, error}], updated_at}`
- `NN/point_meta.json` — key fields: `point_index, point_name, simulator,
  params, status (success|partial|failure|timeout|error), returncode,
  elapsed_sec, error_message, signals, raw_dir, command, started_at,
  finished_at`
- `.worker_started` `{pid, host, started_at, total_points}`, `.compute_node`
  (plain hostname), `.simulator` — run-level markers
- `errlog.txt` — startup/config/template/executable errors (never throws)

## Exit codes and rerun semantics

`0` = all points success/partial; `1` = some point failed; `2` = startup /
config / template / executable error (see `errlog.txt`). The worker always
re-runs every combination and overwrites point dirs — there is no resume;
guarding a directory against accidental re-runs is the caller's business.
