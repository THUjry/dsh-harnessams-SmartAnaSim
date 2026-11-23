# virtuoso-export — .oa -> netlist (si / ocean / cdlOut / plain_skill + round-trip)

Companion page of the `smart-spectre` root skill for the Virtuoso schematic
interaction face. Direction: everything that reads OUT of an OpenAccess
library — exporting a Spectre netlist from a schematic, the
tuning/diff round trip, and the ocean batching facts (CDS.log isolation)
that make headless ocean coexist with live GUI sessions. The tool face is
`gen_vsch_export` (schemes `si` / `ocean` / `cdlout` / `plain_skill`); this
page carries the recipes and pitfalls the tool text embeds.

## Route 1: `si -batch` (first choice)

`si` is an independent batch translator (no GUI, no Maestro, seconds to
start) — the first choice for export and for validation re-exports.

| si.env field | Meaning |
|---|---|
| `simLibName` / `simCellName` / `simViewName` | target library / cell / view (schematic) |
| `simSimulator` | `"spectre"` |
| `simViewList` / `simStopList` | netlisting view search order / stop views |
| `simNetlistHier` | hierarchical netlist `t` |

```sh
mkdir -p <run_dir> && cp <minted>/si.env <run_dir>/si.env && cd <run_dir>
si -batch -cdslib /path/to/cds.lib -command nl
# Spectre MUST use `nl`; `-command netlist` gives OSSHNL-510.
# HSPICE/auCdl use `-command netlist`. Product: <run_dir>/netlist (one file).
```

The product is a **pure circuit netlist**: no `parameters` line, no model
includes, no analysis statements (Maestro decks carry those).

> **The si.env CIW pitfall (the only point where this route is forced to
> touch a live Virtuoso)**: si.env is minted by the CIW call
> `simInitEnvWithArgs(runDir lib cell view "spectre" nil)` — minting needs
> a person / a live CIW. Once minted it is static text, reusable for the
> same (lib, cell, view, simulator) combo; afterwards `si -batch` is pure
> CLI. Hand-writing the fields is unverified (the official docs say don't).
> With no CIW at all: F1 (the user pastes the one-line mint command) or
> switch to the ocean route (log-isolated, no interlock).

## Route 2: headless `ocean` (no si.env)

```sh
mkdir -p <run_dir> && cd <run_dir>
cat > export.ocn <<'EOF'
simulator('spectre)
design("<lib>" "<cell>" "schematic")
createNetlist(?recreateAll t)
printf("EXPORT_DONE\n")
exit()
EOF
CDS_LOG_VERSION=pid CDS_LOG_PATH="$PWD" timeout -s KILL 600s \
  ocean -nograph -restore export.ocn -log "$PWD/CDS.log" > out.txt 2>&1 < /dev/null
```

**Exported content = the circuit part + whatever setup the session
declares.** Nothing is automatic: include lines need one
`modelFile(list("<models.scs>" "<section>"))` per include (a full corner
setup is easily 40+ lines), parameter lines need `desVar`, analyses need
`analysis` statements. With only `simulator` + `design` declared the
product is ~= a pure circuit netlist (same grade as si — exactly right for
diff/tuning round-trips); with modelFile/desVar/analysis filled in it is a
runnable deck (OSSHNL-109 auto-recovers). Corroboration that session state
matters: reference implementations deliberately guard against re-setting
the simulator, to avoid clobbering already-declared modelFile state.

## Route 3: `cdlOut` (CDL/LVS vocabulary)

`cdlOut` is a SKILL function (auCdl vocabulary), not a standalone binary —
it must run inside a Virtuoso process (open CIW or
`virtuoso -nograph -execute`). Caveat: auCdl simInfo renames devices vs
the spectre netlist (e.g. a `rppoly_rf` instance netlists as `rppolyl_rf`
plus a `model` property) — normalize the wording before diffing against a
spectre export.

## Route 4: plain SKILL text in an open CIW (zero startup)

OCEAN commands are SKILL evaluated in the live CIW (ADE/OCEAN loaded in
that session): pasting `simulator('spectre) design(...) createNetlist(?recreateAll t)`
into the user's already-open CIW exports with no si.env mint and no
headless startup. This is also the F1 fallback shape for every other
route: the user pastes the block, then copies the printed echo back.

## The round trip: tune, write back, re-export, diff

1. **Tune at the netlist level**: the si product is a pure circuit netlist —
   edit device-line parameters (`l`/`w`/`m`/resistance values...), never
   the structure or the device names. The tuned netlist doubles as the
   diff baseline (the expectation netlist); archive it.
2. **Write back** into a `Cell_tuned` copy per the CDF five-step recipe
   (`virtuoso-import.md`, "Parameter writeback").
3. **Validate by re-export**: `si`-export `Cell_tuned` and diff against the
   expectation netlist — parameter lines must match bit-for-bit, and
   connectivity/device names must be unchanged. The round trip is closed
   only when the diff is clean (si-to-si is pure-netlist wording, directly
   comparable); cdlOut wording differences normalize per route 3.

## ocean batching and the CDS.log lock (solved)

> Headless ocean batching is NOT blocked by live GUI sessions. The root
> cause was never a hardcoded log name but the default naming sharing the
> `~/CDS.log` pool: give every run its own log and multiple live GUI
> sessions plus concurrent ocean runs coexist (verified: two live GUI
> sessions + 4 concurrent ocean runs, zero lock warnings, all rc=0). The
> recipe = `CDS_LOG_VERSION=pid` + `CDS_LOG_PATH=<run dir>` +
> `-log <run dir>/CDS.log` (any ONE of the three alone already works).
> ocean still runs from a plain command line (a pure SKILL script needs no
> cds.lib); the lock is a `<name>.cdslck` sidecar file — never touch the
> GUI sessions' locks in the home directory.

### Official variable semantics (IC618 `doc/dfIIconfig/dfIIconfigStartup.html`)

| Variable | Semantics |
|---|---|
| `CDS_LOG_PATH` | colon-separated directories (preference order) for the log file; none existing/writable -> `$HOME` |
| `CDS_LOG_VERSION` | `sequential` appends CDS.log.1/.2...; `pid` appends the process id; default = each session overwrites |
| `CDS_LOG_TIMESTAMPS` | `1`/`True`/`Yes`/`t`/`y` = absolute per-line timestamps (same as `-logtime`); `2`/`rel` = relative |

Only `pid` was verified live; `sequential` untested.

### Measured behavior (18 batch runs, IC618, 2026-11-23)

- Default naming: a locked `~/CDS.log` rolls forward to `.1`, `.2`, ... and
  OVERWRITES the first free name (a stale 90 KB log was clobbered in the
  control run) — the roll length is untested.
- `-log` works written before OR after `-restore`; once it takes effect the
  run never touches `~/CDS.log*`.
- `CDS_LOG_VERSION=pid` alone: no shared-pool contact, but logs scatter in
  the home directory; `CDS_LOG_PATH=<run>` alone: home untouched.
- A stale `CDS.log.cdslck` left by a SIGKILL is silently reclaimed — no
  admin tool needed.
- The full trio, single run: ~53 s wall; 4 concurrent: 186-191 s each (a
  1-core cgroup quota spreads the startup cost — concurrency buys nothing
  on such login nodes).
- `-nograph` still needs a virtual X: each process starts its own Xvfb via
  `cdsXvfb-run` (no external DISPLAY; 4 concurrent runs had no display or
  TCP port conflicts).
- Do not poll with per-pid `ps` loops on quota-limited nodes (each `ps -p`
  walks thousands of /proc entries); filter per user with `ps -u` /
  `pgrep -u`.
- NFS clock skew can exceed 3 minutes vs the compute host — an mtime is
  not a reliable lock-age indicator; read the `.cdslck` content
  (`HostName` / `ProcessIdentifier` / `TimeEditLocked`) instead.

### `-restore` vs `-replay`

| Mode | Script lacks `exit()` | Script errors mid-way | stdout |
|---|---|---|---|
| `-restore` | runs on into the interactive prompt (the error branch parks there, holding the license) | aborts the script, parks | present (banner + SKILL output + error) |
| `-replay` | returns automatically | CONTINUES, exits rc=0 | empty — output only in `-log` |

Keep `-restore`. Success criterion: rc 0 AND no `*Error*` in CDS.log AND
the script's self-printed marker present — rc alone is unreliable. The
wrapper must carry `timeout -s KILL` (an erroring `-restore` script parks
ocean at a `>` prompt holding the license) and the script must end with
`exit()`.

### Reference-architecture note

The public reference implementations do NOT spawn a second DFII process
for ocean work: ocean commands are generated as SKILL code and evaluated
inside the user's live CIW; CDS.log is read incrementally from inside the
live session. Our headless route (isolated logs) is the batch-shaped
alternative when no live CIW can be tapped.
