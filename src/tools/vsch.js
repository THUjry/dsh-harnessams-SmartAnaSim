// vsch family: Virtuoso schematic interaction, command-assembly face.
//
// gen_vsch_import / gen_vsch_export ASSEMBLE ready-to-run text for the
// netlist <-> OA round trip but never execute anything and never write
// files: the cds.lib layout, install roots and license hosts are
// environment-specific, so execution belongs to the caller (the model via
// its own shell, or the user through a pasted block). Every scheme is a
// list of text blocks — shell blocks paste into a terminal as-is
// (environment variables are inlined as command prefixes; the only files
// created are the mechanical carriers a tool strictly requires, written by
// a heredoc inside the same block), skill blocks are self-contained
// Cadence SKILL text (paste into an open CIW, or run headless through the
// stdin-REPL carrier — see HEADLESS_SKILL_NOTE; `virtuoso -nograph` has no
// -execute flag).
//
// Recipes and pitfalls migrated from the project's Virtuoso-interaction
// research (2026-11-23) live in the bundled smart-spectre skill pages
// `virtuoso-import.md` / `virtuoso-export.md`; the shapes embedded below
// were verified live on IC618 (2026-11-24, a 7-instance place_only import
// through symbol/tb generation and an ocean re-export round trip).
const SHELL_BLOCK = 'shell'
const SKILL_BLOCK = 'skill'

// ---- shared caveat / note constants ------------------------------------

const SPICEIN_BAN = 'Never run spiceIn through SKILL system(): it starts an internal '
  + 'Virtuoso process that deadlocks the CIW. Run it from a shell/SSH only.'

const NO_OVERWRITE = 'spiceIn does not overwrite an existing same-name cell: re-import '
  + 'under a new name (e.g. <cell>_tuned) or delete the old cell first.'

const DEVMAP_NOTE = 'Matching requires all four: cell name match (devSelect), a symbol '
  + 'view present, equal terminal count, and a termOrder source (CDF simInfo or a devmap '
  + 'termOrder — there is no default termOrder). devmap constructs: devSelect / propMatch '
  + '/ propMap / addProp / termOrder / inhTerms (official sample: '
  + '<install>/tools/dfII/samples/spiceIn/dev.map). PDK model-subckt names often map onto '
  + 'one cell plus an addProp model property; self-made cells without CDF simInfo need an '
  + 'explicit termOrder (pin order per the dspf .subckt header).'

const PLACE_MODE_NOTE = 'place_only = devices placed, connectivity carried by net-name '
  + 'labels (electrically complete, netlistable, passes schCheck — just no drawn wires; '
  + 'much faster than routing). full = full place and route, every net gets a wire.'

const F1_FALLBACK = 'If the headless carrier fails (startup hang / license wait / lost '
  + 'SSH) and the user is present: paste the skill block verbatim into the user\'s '
  + 'already-open CIW and ask them to copy the printed echo back — zero new startup cost.'

const HEADLESS_SKILL_NOTE = 'Headless SKILL carrier (IC618-verified): `virtuoso -nograph` '
  + 'has NO -execute flag (only -replay=<log> / -restore=<session>) — pipe the SKILL text '
  + 'into its stdin REPL instead: `timeout -s KILL 300s virtuoso -nograph -log <tag>.log '
  + '< script.il > <tag>.out 2>&1` with exit(0) as the last script line (append it to a '
  + 'CIW-shaped block before headless use). Startup takes 1-2.5 min; plain timeout '
  + '(SIGTERM) is ignored — -s KILL is mandatory. Judge success by the printed markers, '
  + 'NOT the exit code: a broken site .cdsinit (e.g. a failed calibre.skl load) drops the '
  + 'process into an interactive ">" prompt, exit(0) never fires and the KILL lands after '
  + 'the work completed and saved — rc=137 with markers present means success.'

// ---- small helpers ------------------------------------------------------

function ph(value, placeholder) {
  // Fill a command placeholder from the argument, or keep a visible <...>.
  return value && String(value).trim() ? String(value).trim() : placeholder
}

function cellFromNetlist(netlistFile) {
  const base = String(netlistFile || '').split('/').pop() || ''
  const stem = base.replace(/\.[^.]*$/, '')
  return stem || '<cell>'
}

function scheme(name, summary, blocks, prerequisites, caveats, fallback) {
  return { name, summary, blocks, prerequisites, caveats, fallback }
}

// ---- import schemes ------------------------------------------------------

function importParamScheme(args) {
  const placeOnly = (args.place_mode ?? 'place_only') === 'place_only'
  const conn2schArgs = placeOnly
    ? '-asg +PLACE_ONLY -NOSQUARE -MIN_CROSSOVERS -FAST_LABELS +NOXTRSCH -VERBOSE'
    : '-asg -NOSQUARE -MIN_CROSSOVERS -FAST_LABELS +NOXTRSCH -VERBOSE'
  const netlistFile = ph(args.netlist_file, '<netlist.scs>')
  const outputLib = ph(args.output_lib, '<lib>')
  const language = ph(args.language, 'Spectre')
  const reflibList = ph(args.reflib_list, '<pdk_lib> <user_lib> basic analogLib')
  const devmapFile = ph(args.devmap_file, 'dev.map')
  const text = [
    '# Run from a directory whose cds.lib defines the output library and every',
    '# referenced library. Field spellings verified live on IC618 (2026-11-24):',
    '# refLibList / devMapFile are camelCase; more optional fields (topCell,',
    '# overwriteCells, masterCellForGnd, outputViewName...) in the official sample',
    '# <install>/tools/dfII/samples/spiceIn/param.il.',
    'cat > spiceIn.il <<\'EOF\'',
    'spiceInParams = list(nil',
    `  'netlistFile    "${netlistFile}"`,
    `  'outputLib      "${outputLib}"`,
    `  'language       "${language}"`,
    `  'refLibList     "${reflibList}"`,
    `  'devMapFile     "${devmapFile}"`,
    `  'conn2schArgs   "${conn2schArgs}"`,
    ')',
    'EOF',
    'spiceIn -param spiceIn.il',
  ].join('\n')
  return scheme(
    'param',
    placeOnly
      ? 'spiceIn driven by a parameter file with +PLACE_ONLY label connectivity (the default).'
      : 'spiceIn driven by a parameter file with full place and route.',
    [{ title: 'spiceIn parameter-file run (paste into a shell on the Virtuoso host)', kind: SHELL_BLOCK, text }],
    [
      'a run directory holding a cds.lib that defines the output lib and all reflibs',
      'a devmap file when devices need mapping (see caveats)',
    ],
    [
      'The standalone spiceIn CLI has NO place-only flag (its only CLI-level ASG switch '
        + 'is -noasg); +PLACE_ONLY is reachable only through the conn2schArgs parameter '
        + 'file (or the standalone conn2sch tool).',
      'Products of this param form (verified live): the schematic + data.dm — no '
        + 'netlist_tmp intermediate, no symbol (spiceIn auto-symbols only instantiated '
        + 'subckts). Generate the symbol/tb separately (plain_skill scheme).',
      PLACE_MODE_NOTE,
      SPICEIN_BAN,
      NO_OVERWRITE,
      DEVMAP_NOTE,
    ],
    F1_FALLBACK,
  )
}

function importCliScheme(args) {
  const netlistFile = ph(args.netlist_file, '<netlist.scs>')
  const outputLib = ph(args.output_lib, '<lib>')
  const language = ph(args.language, 'Spectre')
  const reflibList = ph(args.reflib_list, '<pdk_lib> <user_lib> basic analogLib')
  const devmapFile = ph(args.devmap_file, 'dev.map')
  const text = [
    '# Battle-tested standalone form (full place and route). Run from a directory',
    '# whose cds.lib defines the output library and every referenced library.',
    `spiceIn -netlistFile ${netlistFile} -outputLib ${outputLib} -language ${language} \\`,
    `        -reflibList "${reflibList}" -devmapFile ${devmapFile}`,
  ].join('\n')
  return scheme(
    'cli',
    'Standalone spiceIn CLI flags — the battle-tested form (full place and route, every net gets a wire).',
    [{ title: 'spiceIn CLI run (paste into a shell on the Virtuoso host)', kind: SHELL_BLOCK, text }],
    [
      'a run directory holding a cds.lib that defines the output lib and all reflibs',
      'a devmap file when devices need mapping (see caveats)',
    ],
    [
      'No place-only mode exists at the CLI flag level — only the param scheme (or the '
        + 'standalone conn2sch tool) can switch to label connectivity.',
      SPICEIN_BAN,
      NO_OVERWRITE,
      DEVMAP_NOTE,
    ],
    F1_FALLBACK,
  )
}

function importPlainSkillScheme(args) {
  const lib = ph(args.output_lib, '<lib>')
  const cell = cellFromNetlist(args.netlist_file)
  const tb = ph(args.tb_name, `${cell}_tb`)
  const genTb = args.gen_symbol_tb !== false
  const n = genTb ? 4 : 2
  const goal = genTb
    ? `, generate the symbol, and build a ${tb} schematic with one DUT instance`
    : ' and check it'
  const text = [
    ';; For a cell that already holds a netlist-form view (spiceIn names it',
    `;; netlist_tmp; si names it netlist): turn it into a schematic${goal} — one block.`,
    ';; Self-contained CIW paste — do NOT add exit() here (it would close the user\'s',
    ';; session; append exit(0) as the last line only when running headless through',
    ';; the stdin-REPL carrier). Every step is errset(progn(...))-guarded and prints',
    ';; a trail; copy the whole echo back for diagnosis. Signature shapes verified',
    ';; live on IC618; only the conn2Sch place-only keyword is still unverified',
    ';; (see `conn2sch -h` before relying on it).',
    `let( (lib cell tb nlv schem pinl symcv tbcv master inst rc)`,
    `  lib  = "${lib}"`,
    `  cell = "${cell}"`,
    `  tb   = "${tb}"`,
    '  nlv  = "netlist_tmp"',
    `  ;; 1/${n} netlist view -> schematic`,
    '  rc = errset(progn(',
    '    conn2Sch(lib cell nlv ?destViewName "schematic" ?block t)',
    '  ))',
    `  printf("[1/${n}] conn2Sch %s/%s %s -> schematic: %s\\n" lib cell nlv if(rc "done" "FAILED"))`,
    '  unless(rc error("conn2Sch failed — see messages above"))',
    ...(genTb ? [
      '  ;; 2/4 schematic -> symbol (schPinListToSymbol saves the view itself)',
      '  rc = errset(progn(',
      '    pinl  = schSchemToPinList(lib cell "schematic")   ; three string args, not a cellview',
      '    unless(pinl error("schSchemToPinList returned nil"))',
      '    symcv = schPinListToSymbol(lib cell "symbol" pinl)',
      '    unless(symcv error("schPinListToSymbol returned nil"))',
      '    printf("    symbol pin order: %L\\n" schGetPinOrder(dbOpenCellViewByType(lib cell "symbol" "" "r")))',
      '  ))',
      '  printf("[2/4] symbol generation: %s\\n" if(rc "done" "FAILED"))',
      '  unless(rc error("symbol generation failed — delete partial views before retrying"))',
      `  ;; 3/4 ${tb} schematic with one instance of the fresh symbol`,
      '  rc = errset(progn(',
      '    tbcv   = dbOpenCellViewByType(lib tb "schematic" "schematic" "a")',
      '    master = dbOpenCellViewByType(lib cell "symbol" "" "r")',
      '    inst   = dbCreateInst(tbcv master "DUT" list(0 0) "R0")   ; 5-arg form — the nil-master x:y variant errors',
      '    schCheck(tbcv)',
      '    dbSave(tbcv)',
      '    dbClose(tbcv)',
      '  ))',
      `  printf("[3/4] %s schematic with DUT instance: %s\\n" tb if(rc "done" "FAILED"))`,
    ] : []),
    `  ;; ${n}/${n} re-check and save the imported schematic`,
    '  rc = errset(progn(',
    '    schem = dbOpenCellViewByType(lib cell "schematic" "schematic" "a")',
    '    schCheck(schem)',
    '    dbSave(schem)',
    '    dbClose(schem)',
    '  ))',
    `  printf("[${n}/${n}] schCheck + save imported schematic: %s\\n" if(rc "done" "FAILED"))`,
    '  printf("IMPORT_DONE\\n")',
    ')',
  ].join('\n')
  return scheme(
    'plain_skill',
    genTb
      ? `Cadence SKILL text for an open CIW: conn2Sch the netlist view into a schematic, generate the symbol, build the ${tb} — one self-contained block.`
      : 'Cadence SKILL text for an open CIW: conn2Sch the netlist view into a schematic, check and save (gen_symbol_tb=false) — one self-contained block.',
    [{ title: genTb ? 'import + symbol + tb (paste into an open CIW)' : 'import (paste into an open CIW)', kind: SKILL_BLOCK, text }],
    [
      'the cell already holds a netlist-form view (netlist_tmp from spiceIn, netlist from si)',
      'a cds.lib defining the library',
    ],
    [
      'spiceIn auto-generates symbols only for INSTANTIATED subckts — a single-subckt '
        + 'netlist import leaves the top cell without a symbol, which step 2/4 fixes.',
      'Verified live (IC618): schSchemToPinList takes three string args (lib cell view); '
        + 'schPinListToSymbol saves the generated symbol view itself; dbCreateInst must be '
        + 'the 5-arg form dbCreateInst(cv master "name" list(x y) "R0") — the nil-master '
        + 'x:y variant errors out; errset wraps progn (a bare begin is parsed as a stray '
        + 'argument and fails).',
      'A failed run can leave a broken/unsaved view behind that makes the next '
        + 'dbOpenCellViewByType(... "a") return nil — delete the cell (or its partial '
        + 'views) and retry instead of fighting the leftover.',
      HEADLESS_SKILL_NOTE,
    ],
    F1_FALLBACK,
  )
}

// ---- export schemes ------------------------------------------------------

function exportSiScheme(args) {
  const lib = ph(args.lib, '<lib>')
  const cell = ph(args.cell, '<cell>')
  const view = ph(args.view, 'schematic')
  const runDir = ph(args.run_dir, '<run_dir>')
  const siEnv = ph(args.si_env, '<minted>/si.env')
  const shell = [
    `mkdir -p ${runDir} && cp ${siEnv} ${runDir}/si.env && cd ${runDir}`,
    'si -batch -cdslib <cds.lib> -command nl',
    '# Spectre must use `nl` (-command netlist gives OSSHNL-510); HSPICE/auCdl',
    '# use -command netlist. Product: <run_dir>/netlist — a pure circuit netlist',
    '# (no parameters line, no model includes, no analysis statements).',
  ].join('\n')
  const mint = [
    ';; one-time si.env mint (needs a live CIW; the file is static text afterwards,',
    ';; reusable for the same lib/cell/view/simulator combo. Hand-writing the shared',
    ';; field family is now verified live — via the auCdl route, see the cdlout',
    ';; scheme; a mint or a known-good template remains the safest source):',
    `simInitEnvWithArgs("${runDir}" "${lib}" "${cell}" "${view}" "spectre" nil)`,
  ].join('\n')
  return scheme(
    'si',
    'si -batch batch translator — the first-choice export (lightweight CLI, no GUI, no Maestro).',
    [
      { title: 'si batch netlist (paste into a shell on the Virtuoso host)', kind: SHELL_BLOCK, text: shell },
      { title: 'si.env one-time mint (paste into a CIW)', kind: SKILL_BLOCK, text: mint },
    ],
    [
      'si.env minted once via a CIW simInitEnvWithArgs call (block two) — the only '
        + 'point where this scheme is forced to touch a live Virtuoso',
      'a cds.lib reachable at the -cdslib path',
    ],
    [
      'Product is a PURE circuit netlist — no parameters line, no includes, no '
        + 'analyses (Maestro decks carry those; add them at the netlist level).',
    ],
    'No CIW available at all: switch to the ocean scheme (log isolation inlined, no '
      + 'interlock) or the plain_skill scheme.',
  )
}

function exportOceanScheme(args) {
  const lib = ph(args.lib, '<lib>')
  const cell = ph(args.cell, '<cell>')
  const view = ph(args.view, 'schematic')
  const runDir = ph(args.run_dir, '<run_dir>')
  const includes = Array.isArray(args.model_includes) ? args.model_includes : []
  const modelLines = includes
    .map((entry) => String(entry).trim())
    .filter(Boolean)
    .map((entry) => {
      const parts = entry.split(/\s+/)
      const p = parts[0] ?? '<models.scs>'
      const s = parts[1] ?? '<section>'
      return `modelFile(list("${p}" "${s}"))`
    })
  const ocn = [
    ';; pre-check pass (verified self-heal): a freshly imported cell that never went',
    ';; through schCheck + dbSave fails createNetlist with OSSHNL-109 then 514 — open,',
    ';; check, save, close first. Harmless on already-clean cells.',
    `cv = dbOpenCellViewByType("${lib}" "${cell}" "${view}" "schematic" "a")`,
    'schCheck(cv)',
    'dbSave(cv)',
    'dbClose(cv)',
    'printf("PRECHECK_DONE\\n")',
    'simulator(\'spectre)',
    `design("${lib}" "${cell}" "${view}")`,
    ...(modelLines.length ? modelLines : []),
    'createNetlist(?recreateAll t)',
    'printf("EXPORT_DONE\\n")',
    'exit()   ; mandatory: an error under -restore drops into a prompt and holds the license',
  ].join('\n')
  const deckNote = modelLines.length
    ? ['modelFile lines rendered from model_includes — the deck still needs desVar / analysis',
      'statements to be runnable; without them the product stays circuit-only.']
    : ['Only simulator + design declared: the product is ~= a pure circuit netlist (same',
      'grade as si) — right for diff/tuning round-trips. A runnable deck additionally',
      'needs modelFile(list("<models.scs>" "<section>")) per include, desVar per',
      'parameter and analysis statements.']
  if (args.deck_mode === true && !modelLines.length) {
    deckNote.push('deck_mode=true with no model_includes: source the modelFile entries ONLY '
      + 'from the include lines of a user-provided simulation log (input.scs / run_dir '
      + 'artifacts) — never browse PDK or model-library directories.')
  }
  const shell = [
    `mkdir -p ${runDir} && cd ${runDir}`,
    'cat > export.ocn <<\'EOF\'',
    ocn,
    'EOF',
    '# per-run CDS.log isolation (any one of the three alone already works):',
    'CDS_LOG_VERSION=pid CDS_LOG_PATH="$PWD" timeout -s KILL 600s \\',
    '  ocean -nograph -restore export.ocn -log "$PWD/CDS.log" > out.txt 2>&1 < /dev/null',
    'rc=$?',
    'if [ $rc -ne 0 ] || grep -q -a \'\\*Error\\*\' CDS.log; then echo "ocean FAILED rc=$rc"; else echo "ocean OK"; fi',
    '# success = rc 0 AND no *Error* in CDS.log AND the EXPORT_DONE marker present;',
    '# rc alone is unreliable (replay-mode errors still exit 0 — keep -restore).',
  ].join('\n')
  return scheme(
    'ocean',
    'Headless ocean createNetlist with per-run CDS.log isolation — no si.env, coexists with live GUI sessions.',
    [{ title: 'ocean headless export (paste into a shell on the Virtuoso host)', kind: SHELL_BLOCK, text: shell }],
    [
      'a run directory (one per run — the log isolation is per-directory)',
      'ocean on PATH; cds.lib reachable from the run directory',
    ],
    [
      ...deckNote,
      'Fresh-import self-heal: the PRECHECK block inside export.ocn is load-bearing — '
        + 'a cell fresh out of spiceIn that never went through schCheck + dbSave fails '
        + 'createNetlist with OSSHNL-109 then 514 (verified live; the precheck fixes it).',
      'The netlist does NOT land in the cwd: ocean writes under its own simulation root '
        + 'as <sim_root>/<cell>/spectre/schematic/netlist/input.scs — read the path '
        + 'createNetlist echoes (recorded in the run dir CDS.log), not the run directory.',
      'With no modelFile declared, createNetlist may still emit the environment default '
        + 'model setup (observed live: a full corner include list injected by the site '
        + 'environment) — the product is circuit + env defaults, not include-free.',
      'Keep -restore (not -replay): -replay continues past errors and exits 0 with '
        + 'empty stdout — easy to misjudge.',
      'timeout -s KILL is mandatory: an erroring -restore script parks ocean at a '
        + 'prompt holding the license.',
      'Never touch ~/CDS.log* or their .cdslck sidecars — those belong to GUI sessions.',
    ],
    F1_FALLBACK,
  )
}

function exportCdloutScheme(args) {
  const lib = ph(args.lib, '<lib>')
  const cell = ph(args.cell, '<cell>')
  const view = ph(args.view, 'schematic')
  const runDir = ph(args.run_dir, '<run_dir>')
  const shell = [
    '# CDL/LVS-vocabulary export as a lightweight CLI (verified live, IC618): the',
    '# si batch translator with a hand-built si.env whose simulator is auCdl — full',
    '# device coverage, no Virtuoso process, seconds to run.',
    `mkdir -p ${runDir} && cd ${runDir}`,
    'cat > si.env <<\'EOF\'',
    `simLibName = "${lib}"`,
    `simCellName = "${cell}"`,
    `simViewName = "${view}"`,
    `simRunDir = "${runDir}"`,
    'simSimulator = "auCdl"',
    `hnlNetlistFileName = "${cell}.cdl"`,
    'simViewList = \'("auCdl" "schematic" "symbol")',
    'simStopList = \'("auCdl")',
    'simNetlistHier = t',
    'EOF',
    'si -batch -cdslib <cds.lib> -command netlist',
    `# product: ${runDir}/${cell}.cdl`,
  ].join('\n')
  return scheme(
    'cdlout',
    'CDL/LVS vocabulary through the si batch translator with a hand-built auCdl si.env — verified live, full devices, no Virtuoso process.',
    [{ title: 'si auCdl batch export (paste into a shell on the Virtuoso host)', kind: SHELL_BLOCK, text: shell }],
    [
      'si on PATH (ships with the Virtuoso install); a cds.lib defining the library at the -cdslib path',
      'a run/output directory',
    ],
    [
      'The simulator MUST be "auCdl" (the analog netlister): the "cdl" digital flavor '
        + 'SILENTLY DROPS PDK devices without cdl views (OSSHNL-117 — they stop at an empty '
        + 'symbol view and the netlist comes out hollow, only subckt stubs left).',
      'The hand-built si.env above is verified live; tunables (shortRES, resistorCheck, '
        + 'capacitorCheck, diodeCheck, ...) follow the official sample '
        + '<install>/tools.lnx86/dfII/samples/transUI/cdlOut.il.',
      'auCdl wording differs from the spectre netlist (LVS grade): geometry is simplified '
        + '(nf folds into m with w*nf, macro device names shorten), model subckt names become '
        + 'the master token, instance-only properties may drop — normalize before diffing '
        + 'against a spectre export.',
      'The SKILL cdlOut function is NOT defined in a bare headless session (verified: getd '
        + 'returns nil) — it lives in the GUI CDL Out form context.',
    ],
    'The SKILL cdlOut function only exists inside the GUI CDL Out form context: F1 = the '
    + 'user runs File -> Export -> CDL (Netlisting Mode: Analog) in their open session.',
  )
}

function exportPlainSkillScheme(args) {
  const lib = ph(args.lib, '<lib>')
  const cell = ph(args.cell, '<cell>')
  const view = ph(args.view, 'schematic')
  const includes = Array.isArray(args.model_includes) ? args.model_includes : []
  const modelLines = includes
    .map((entry) => String(entry).trim())
    .filter(Boolean)
    .map((entry) => {
      const parts = entry.split(/\s+/)
      const p = parts[0] ?? '<models.scs>'
      const s = parts[1] ?? '<section>'
      return `modelFile(list("${p}" "${s}"))`
    })
  const text = [
    ';; Spectre netlist export straight in an open CIW. OCEAN commands are SKILL',
    ';; evaluated in the live CIW (ADE/OCEAN loaded in that session) — no si.env',
    ';; mint, no headless startup, no log interlock.',
    'simulator(\'spectre)',
    `design("${lib}" "${cell}" "${view}")`,
    ...(modelLines.length ? modelLines : []),
    'createNetlist(?recreateAll t)',
    'printf("EXPORT_DONE — netlist path reported above\\n")',
  ].join('\n')
  return scheme(
    'plain_skill',
    'OCEAN/SKILL text to paste into the user\'s open CIW — the zero-startup export path.',
    [{ title: 'netlist export (paste into an open CIW)', kind: SKILL_BLOCK, text }],
    [
      'an open CIW with ADE/OCEAN loaded (the user\'s drawing session qualifies)',
      'cds.lib defining the library',
    ],
    [
      'Exported content = the circuit plus whatever setup the session declares: '
        + 'includes need modelFile lines (rendered above when given), parameters need '
        + 'desVar, analyses need analysis statements; simulator + design alone give a '
        + 'circuit-only netlist (diff-grade).',
      ...(args.deck_mode === true && !modelLines.length
        ? ['deck_mode=true with no model_includes: source the modelFile entries ONLY from '
            + 'the include lines of a user-provided simulation log (input.scs / run_dir '
            + 'artifacts) — never browse PDK or model-library directories.']
        : []),
    ],
    'The createNetlist path is printed in the CIW — ask the user to copy it back '
      + 'along with the EXPORT_DONE marker.',
  )
}

// ---- assembly ------------------------------------------------------------

const IMPORT_SCHEMES = { param: importParamScheme, cli: importCliScheme, plain_skill: importPlainSkillScheme }
const EXPORT_SCHEMES = { si: exportSiScheme, ocean: exportOceanScheme, cdlout: exportCdloutScheme, plain_skill: exportPlainSkillScheme }

function assemble(operation, requested, builders, order, args) {
  const valid = Object.keys(builders)
  if (requested && !valid.includes(requested)) {
    throw new Error(`invalid arguments: scheme must be one of ${valid.join(', ')} for ${operation} (got "${requested}")`)
  }
  const names = requested ? [requested] : order
  return {
    ok: true,
    operation,
    scheme: requested ?? 'listing',
    schemes: names.map((name) => builders[name](args)),
  }
}

export function vschTools() {
  return [
    {
      name: 'gen_vsch_import',
      description: 'Assemble Virtuoso netlist-import text (netlist -> OA schematic). '
        + 'Returns ready-to-run text blocks only — nothing is executed and no files are '
        + 'written (cds.lib layout and install roots are environment-specific, so '
        + 'execution is left to the caller). Schemes: param (spiceIn -param parameter '
        + 'file; +PLACE_ONLY label connectivity by default), cli (standalone spiceIn '
        + 'flags, full place and route, battle-tested), plain_skill (self-contained '
        + 'Cadence SKILL text: conn2Sch + symbol + <cell>_tb, CIW-pasteable). Without '
        + 'scheme, every scheme comes back structured — try them in order. Full recipes '
        + 'and pitfalls: the bundled smart-spectre skill, virtuoso-import.md / '
        + 'virtuoso-export.md.',
      parameters: {
        netlist_file: { type: 'string', required: true, description: 'Netlist file to import (.scs / .spf / .dspf)' },
        scheme: { type: 'string', description: 'Assemble one scheme only: param | cli | plain_skill (default: all three, ordered)', enum: ['param', 'cli', 'plain_skill'] },
        output_lib: { type: 'string', description: 'Target OA library name' },
        language: { type: 'string', description: 'Netlist language flavor (default Spectre)' },
        reflib_list: { type: 'string', description: 'Space-separated reference library list for cds.lib resolution' },
        devmap_file: { type: 'string', description: 'Device-map file (devSelect/addProp/termOrder...) when devices need mapping' },
        place_mode: { type: 'string', description: 'place_only (default: label connectivity) | full (place and route, wires drawn)' },
        gen_symbol_tb: { type: 'boolean', description: 'Include symbol + <cell>_tb generation in the plain_skill scheme (default true)' },
        tb_name: { type: 'string', description: 'Testbench cell name (default <cell>_tb)' },
      },
      async execute(args) {
        return assemble('import', args.scheme, IMPORT_SCHEMES, ['param', 'cli', 'plain_skill'], args)
      },
    },
    {
      name: 'gen_vsch_export',
      description: 'Assemble Virtuoso schematic-export text (.oa -> Spectre netlist). '
        + 'Returns ready-to-run text blocks only — nothing is executed and no files are '
        + 'written. Schemes: si (si -batch -command nl, needs a one-time CIW-minted '
        + 'si.env), ocean (headless ocean with per-run CDS.log isolation, no si.env), '
        + 'cdlout (CDL/LVS vocabulary via si -command netlist with a hand-built auCdl '
        + 'si.env), plain_skill (OCEAN/SKILL text to paste into an open CIW — zero '
        + 'startup). Without scheme, every scheme comes back structured — try them in '
        + 'order. Full recipes and pitfalls: the bundled smart-spectre skill, '
        + 'virtuoso-export.md.',
      parameters: {
        lib: { type: 'string', required: true, description: 'OA library of the cell to export' },
        cell: { type: 'string', required: true, description: 'Cell to export' },
        scheme: { type: 'string', description: 'Assemble one scheme only: si | ocean | cdlout | plain_skill (default: all four, ordered)', enum: ['si', 'ocean', 'cdlout', 'plain_skill'] },
        view: { type: 'string', description: 'Source view (default schematic)' },
        run_dir: { type: 'string', description: 'Run/output directory for the export artifacts' },
        si_env: { type: 'string', description: 'Path to a minted si.env (si scheme; mint block included when absent)' },
        model_includes: {
          type: 'array',
          items: { type: 'string' },
          description: 'Include entries as "<models.scs> <section>" strings — rendered as modelFile lines (ocean / plain_skill schemes)',
        },
        deck_mode: { type: 'boolean', description: 'Intent flag: true = building toward a runnable deck (modelFile lines expected), false/absent = circuit-only, diff-grade' },
      },
      async execute(args) {
        return assemble('export', args.scheme, EXPORT_SCHEMES, ['si', 'ocean', 'cdlout', 'plain_skill'], args)
      },
    },
  ]
}
