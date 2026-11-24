# virtuoso-import — netlist -> OA (spiceIn recipes, symbol/tb, parameter writeback)

Companion page of the `smart-spectre` root skill for the Virtuoso schematic interaction face. Direction: everything that writes INTO an OpenAccess library — importing a netlist as a schematic, generating the symbol and the `<cell>_tb` testbench skeleton, writing tuned parameters back into a schematic copy, and re-importing a tuned netlist (F2). The tool face is `gen_vsch_import` (schemes `param` / `cli` / `plain_skill`); this page carries the recipes and pitfalls the tool text embeds, plus the parameter-writeback recipe (no tool scheme — compose the Cadence SKILL text yourself, guided here).

## spiceIn primer

`spiceIn` is an independent IC618 binary (no Virtuoso process, no GUI, seconds to start): netlist -> OA schematic, with ASG automatic placement and routing on by default at the CLI level (the GUI form defaults to OFF; `envSetVal("spiceIn" "asgMode" 'boolean t)` turns it on there).

> **Hard rule: never run spiceIn through SKILL `system()`** — it starts an > internal Virtuoso process that deadlocks the CIW. Always run it from a > shell/SSH. This matters doubly when orchestrating from inside a CIW-side > daemon: route the call out to a real shell.

The run directory must hold a `cds.lib` that defines the output library and every referenced library. spiceIn does NOT overwrite an existing same-name cell — re-import under a new name (e.g. `<cell>_tuned`) or delete the old cell first.

## Matching: the four requirements and the devmap

spiceIn matches a netlist device to a schematic cell only when ALL four hold: (1) the cell name matches (via `devSelect`), (2) a `symbol` view exists, (3) the terminal count is equal, and (4) a termOrder source exists — CDF simInfo or a devmap `termOrder`. There is **no concept of a default termOrder**: a self-made cell without CDF simInfo fails with SPICEIN-24 ("symbol not found") even though the cell exists. The fix is an explicit devmap `termOrder`; the authoritative pin order is the `.subckt` header of the dspf/spf source. Netlist nodes map by position (e.g. gate nets `SWB` and `SW` both landing on pin `SW`; a supply named `AVDDV` landing on `AVDD`).

devmap constructs (six): `devSelect` / `propMatch` / `propMap` / `addProp` / `termOrder` / `inhTerms`. Official sample: `<install>/tools/dfII/samples/spiceIn/dev.map`; reference doc `doc/transrefOA/chap3.html`. A worked devmap (PDK model-subckt names mapped onto one cell plus an `addProp model` property, self-made switch cells carrying an explicit termOrder):

```
-- Device map for a TSMC 65 nm PDK import
devSelect := rppolys_rf rppoly_rf
addProp := model rppolys_rf

devSelect := rppolyl_rf rppoly_rf
addProp := model rppolyl_rf

devSelect := SWmos_lvt_2u4f SWmos_lvt_2u4f
termOrder := OUT IN SW AVSS AVDD

devSelect := SWmos_lvt_2u32f SWmos_lvt_2u32f
termOrder := OUT IN SW AVSS AVDD
```

Why `addProp model` works: `model` is the rppoly_rf CDF parameter that selects which model subckt the netlister emits — the netlist names (`rppolys_rf` / `rppolyl_rf`) are model-file subckts, not cells.

## The verified import commands (CLI and parameter-file forms)

Two forms, both verified live on IC618. The standalone CLI carries the data flags only (`-netlistFile`, `-outputLib`, `-language`, `-reflibList`, `-devmapFile`); everything else (ASG tuning, overwrite control, conn2sch switches) lives in the `-param` parameter-file form. CLI form (battle-tested, two consecutive successful runs, 2026-11-23, a 7-instance attenuator cell — full place and route):

```sh
spiceIn -netlistFile <cell>.scs -outputLib <lib> -language Spectre \
        -reflibList "<pdk_lib> <user_lib> basic analogLib" \
        -devmapFile dev.map
```

Parameter-file form (verified 2026-11-24 with `+PLACE_ONLY`, SPICEIN-54, zero c2s warnings). Field spellings are camelCase — `refLibList` / `devMapFile` exactly as below; more optional fields (`topCell`, `overwriteCells`, `masterCellForGnd`, `outputViewName`, ...) in the official sample `<install>/tools/dfII/samples/spiceIn/param.il`:

```sh
cat > spiceIn.il <<'EOF'
spiceInParams = list(nil
  'netlistFile    "<cell>.scs"
  'outputLib      "<lib>"
  'language       "Spectre"
  'refLibList     "<pdk_lib> <user_lib> basic analogLib"
  'devMapFile     "dev.map"
  'conn2schArgs   "-asg +PLACE_ONLY -NOSQUARE -MIN_CROSSOVERS -FAST_LABELS +NOXTRSCH -VERBOSE"
)
EOF
spiceIn -param spiceIn.il
```

Products of the param form (verified): the `schematic` view plus `data.dm` — no `netlist_tmp` intermediate and no symbol (spiceIn auto-symbols only instantiated subckts; generate it per the next section).

## place_only: the placement ladder

| Mode | Behavior |
|---|---|
| default (Full Place and Route) | every net gets a drawn wire |
| `+PLACE_ONLY` | devices placed, no wires — connectivity carried by net-name labels (electrically complete: extracts, netlists, simulates, passes schCheck) |

`+place_only` is much faster (routing dominates the runtime) and is the recommended default for machine-driven imports; the `+` prefix is part of the switch name. **The standalone spiceIn CLI has no place-only flag** — its only CLI-level ASG switch is `-noasg` (disable ASG entirely). The reachable entry points:

| Entry point | place-only form |
|---|---|
| `conn2sch` command line | `+place_only` (the only direct switch form) |
| spiceIn parameter file | `'conn2schArgs "-asg +PLACE_ONLY ..."` (uppercase inside the string; from the official `tools/dfII/samples/spiceIn/param.il`) |
| spiceIn GUI form | Schematic Generation Tab, uncheck "Full Place and Route" |
| SKILL | `conn2Sch(lib cell "netlist" ?destViewName "schematic" ?block t)` (place-only keyword unverified) |

conn2sch switch reference (`conn2sch -h`): `-asg` (ASG placement, core switch), `+place_only`, `-min_crossovers` (minimize crossings — "takes plenty of time"), `-fast_labels` (fast label placement: midpoint, no overlap check), `-nosquare`, `+noxtrsch` (no extract), `-over_dense`, `-notechfile`, `-verbose`; IO group `-param/-lib/-cell/-view/-destlib/ -destview/-cdslib/-log/-f`.

## Import acceptance criteria (log level)

`spiceIn.log` records every parameter and the per-instance mapping; `c2s_<cell>.log` is the graphics-generation log. Success:

- `SPICEIN-54` (import) and `SPICEIN-33` (schematic) in spiceIn.log;
- per instance: `Master Cell '<netlist name>' mapped to '<cell>'` and `Master Cellview: '<lib>.<cell>:symbol' found`;
- property conversion sane (e.g. `model=rppolys_rf` carried as an addProp; geometry `1.0u` stored as `1e-06`);
- `c2s_<cell>.log` free of warnings and errors.

## Symbol generation and the `<cell>_tb` skeleton

spiceIn auto-generates symbols only for INSTANTIATED subckts — a single-subckt netlist import leaves the top cell with schematic + netlist_tmp views and NO symbol, which blocks instantiating it in a testbench. Fix with the Cadence-native one-flow pair before building the tb: `schSchemToPinList` + `schPinListToSymbol`, then `schGetPinOrder` readback to verify the pin order against the schematic (delete the generated view on mismatch and regenerate). The `plain_skill` scheme of `gen_vsch_import` emits this flow as one self-contained CIW-pasteable block, ending with a `<cell>_tb` schematic holding one `DUT` instance of the fresh symbol (`dbOpenCellViewByType` + `dbCreateInst`, then `schCheck`/`dbSave`).

Verified call shapes (IC618, live 2026-11-24):

```skill
pinl  = schSchemToPinList(lib cell "schematic")    ; three STRING args — not a cellview
symcv = schPinListToSymbol(lib cell "symbol" pinl) ; creates AND saves the symbol view
schGetPinOrder(dbOpenCellViewByType(lib cell "symbol" "" "r"))  ; readback check
inst  = dbCreateInst(tbcv master "DUT" list(0 0) "R0") ; 5-arg form — see below
```

Pitfalls verified with them: wrap multi-step bodies as `errset(progn(...))` — `errset(begin ...))` is illegal (begin parses as a stray argument and the call fails); `dbCreateInst` must be the 5-arg form above (the `nil`-master `x:y`-point variant errors out); a failed run can leave an unsaved view that makes the next `dbOpenCellViewByType(... "a")` return nil — delete the cell and retry instead of fighting the leftover.

## Running SKILL text headless (stdin REPL carrier)

`virtuoso -nograph` has NO `-execute` flag (only `-replay=<log>` / `-restore=<session>`) — the headless carrier is its stdin REPL (verified, IC618):

```sh
timeout -s KILL 300s virtuoso -nograph -log <tag>.log < script.il > <tag>.out 2>&1
```

- the script's last line must be `exit(0)` — and CIW-paste blocks must NOT carry it (it would close the user's session); append it only for headless use;
- startup takes 1-2.5 min; a plain `timeout` (SIGTERM) is ignored by virtuoso — `-s KILL` is mandatory;
- judge success by the printed markers, NOT the exit code: a broken site .cdsinit (e.g. a failed calibre.skl load) drops the process into an interactive `>` prompt, `exit(0)` never fires and the KILL lands after the work completed and was saved — rc=137 with markers present means success (verified: a tb-creation run that printed its DONE trail and saved OA still left rc=137).

## Parameter writeback — the CDF five-step (compose the SKILL text yourself)

The only step of the whole round trip with no lightweight CLI (Cadence has no SKILL interpreter independent of a Virtuoso process): writing tuned parameters back into a schematic copy must run inside a Virtuoso process — the headless stdin-REPL carrier (previous section), the user's front CIW (F1), or a wrapped `ocean -restore`. The textbook sequence:

```skill
;; save-as first (never edit the hand-drawn original in place)
dbCopyCellView("Lib" "Cell" "schematic" "Lib" "Cell_tuned" "schematic")
cv = dbOpenCellViewByType("Lib" "Cell_tuned" "schematic" "schematic" "a")
inst = car(setof(x cv~>instances x~>name == "Rser"))
iCDF = cdfGetInstCDF(inst)          ; (1) validate the param name through CDF — never bare dbReplaceProp
;; (2) write the value (p~>value, typed per paramType; atoi-normalize ints)
;; (3) errset(evalstring(cb)) per parameter to run callbacks — the
;;     rppoly_rf entryMode -> totalR derived chain requires it
cdfUpdateInstParam(inst)            ; (4) push the cell CDF state back onto the instance
schCheck(cv) dbSave(cv) dbClose(cv) ; (5) closing triple — miss one and nothing lands
```

Pitfalls:

- Bare `dbReplaceProp` has no validation: a wrong param name creates a same-named garbage property, the netlister ignores it, and the caller still reports ok (a live incident wrote an `ampl` onto a vsin and got a flat line).
- Derived quantities need callbacks: writing `l`/`w` without refreshing the callbacks leaves `totalR` stale. The robust technique saves each cell-CDF parameter value before the run and restores it afterwards.
- TSMC vocabulary: `nf` is read-only (change `fingers`), `m` may be called `simM`, `wf` semantics vary per kit.

Any SKILL text composed from this recipe should follow the F1 requirements (self-contained `let(...)` block, errset around every step, printf trail per step) so the same text works headless and as a CIW paste.

## F2 — re-import the tuned netlist

When parameter correctness is all that matters (or neither the background carrier nor a front CIW is convenient), re-import the tuned netlist through spiceIn as a NEW cell `<cell>_tuned`: fully lightweight CLI, proven to work alongside live DFII sessions; the graphics are ASG-placed ("ugly but correct") and connectivity/parameters are guaranteed by the netlist itself. Put a provenance comment on the first netlist line; choose the placement mode per the ladder above.
