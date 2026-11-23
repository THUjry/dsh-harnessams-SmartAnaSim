// gen_vsch_import / gen_vsch_export assembly contracts: pure text output
// (no file artifacts), scheme listing vs single-scheme selection, and the
// verbatim recipe fragments the schemes must embed.
import test from 'node:test'
import assert from 'node:assert/strict'
import { toolDefinitions } from '../src/index.js'
import { makeTool } from '../src/tool.js'

const defs = toolDefinitions()
const importTool = makeTool(defs.find((d) => d.name === 'gen_vsch_import'))
const exportTool = makeTool(defs.find((d) => d.name === 'gen_vsch_export'))
const run = (tool, args) => tool.execute(args, {})

function shellOf(scheme) {
  const b = scheme.blocks.find((x) => x.kind === 'shell')
  assert.ok(b, `scheme ${scheme.name} needs a shell block`)
  return b.text
}

function skillOf(scheme) {
  const b = scheme.blocks.find((x) => x.kind === 'skill')
  assert.ok(b, `scheme ${scheme.name} needs a skill block`)
  return b.text
}

test('every scheme keeps the text-first shape (blocks only, no file artifacts)', async () => {
  for (const result of [await run(exportTool, { lib: 'lib0', cell: 'amp' }),
    await run(importTool, { netlist_file: 'amp.scs' })]) {
    assert.equal(result.ok, true)
    for (const scheme of result.schemes) {
      assert.ok(Array.isArray(scheme.blocks) && scheme.blocks.length > 0)
      assert.ok(!('files' in scheme), 'no files field — output is text blocks only')
      for (const block of scheme.blocks) {
        assert.ok(['shell', 'skill'].includes(block.kind))
        assert.equal(typeof block.text, 'string')
        assert.ok(block.text.length > 0)
      }
      assert.ok(Array.isArray(scheme.prerequisites) && scheme.prerequisites.length > 0)
      assert.ok(Array.isArray(scheme.caveats) && scheme.caveats.length > 0)
      assert.equal(typeof scheme.fallback, 'string')
    }
  }
})

test('export without scheme lists all four routes in order', async () => {
  const result = await run(exportTool, { lib: 'lib0', cell: 'amp' })
  assert.equal(result.operation, 'export')
  assert.equal(result.scheme, 'listing')
  assert.deepEqual(result.schemes.map((s) => s.name), ['si', 'ocean', 'cdlout', 'plain_skill'])
})

test('si scheme: nl command, si.env mint block, mint pitfall', async () => {
  const result = await run(exportTool, { lib: 'lib0', cell: 'amp', scheme: 'si' })
  assert.deepEqual(result.schemes.map((s) => s.name), ['si'])
  assert.match(shellOf(result.schemes[0]), /si -batch -cdslib .* -command nl/)
  assert.match(shellOf(result.schemes[0]), /OSSHNL-510/)
  const mint = skillOf(result.schemes[0])
  assert.match(mint, /simInitEnvWithArgs\(/)
  assert.match(mint, /"spectre"/)
})

test('ocean scheme: heredoc .ocn + inlined log isolation in one shell block', async () => {
  const result = await run(exportTool, { lib: 'lib0', cell: 'amp', scheme: 'ocean' })
  const shell = shellOf(result.schemes[0])
  // one self-sufficient paste: the .ocn heredoc and the invocation share a block
  assert.match(shell, /cat > export\.ocn <<'EOF'/)
  assert.match(shell, /createNetlist\(\?recreateAll t\)/)
  assert.match(shell, /exit\(\)/)
  assert.match(shell, /CDS_LOG_VERSION=pid/)
  assert.match(shell, /CDS_LOG_PATH="\$PWD"/)
  assert.match(shell, /-log "\$PWD\/CDS\.log"/)
  assert.match(shell, /timeout -s KILL 600s/)
  assert.match(shell, /-restore/)
  assert.doesNotMatch(shell, /-replay/)
})

test('ocean and plain_skill render modelFile lines from model_includes', async () => {
  const ocean = (await run(exportTool, {
    lib: 'lib0', cell: 'amp', scheme: 'ocean',
    model_includes: ['models.scs tt_cap', 'models.scs tt_res'],
  })).schemes[0]
  assert.match(shellOf(ocean), /modelFile\(list\("models\.scs" "tt_cap"\)\)/)
  assert.match(shellOf(ocean), /modelFile\(list\("models\.scs" "tt_res"\)\)/)
  const plain = (await run(exportTool, {
    lib: 'lib0', cell: 'amp', scheme: 'plain_skill',
    model_includes: ['models.scs tt_cap'],
  })).schemes[0]
  assert.match(skillOf(plain), /modelFile\(list\("models\.scs" "tt_cap"\)\)/)
})

test('deck_mode without model_includes points at the sim-log-only source', async () => {
  for (const schemeName of ['ocean', 'plain_skill']) {
    const result = await run(exportTool, { lib: 'lib0', cell: 'amp', scheme: schemeName, deck_mode: true })
    assert.ok(result.schemes[0].caveats.some((c) => c.includes('input.scs') && c.includes('never browse')),
      `${schemeName} deck_mode caveat`)
  }
})

test('import default: place_only param-file scheme first, battle-tested cli second', async () => {
  const result = await run(importTool, { netlist_file: 'amp.scs' })
  assert.equal(result.scheme, 'listing')
  assert.deepEqual(result.schemes.map((s) => s.name), ['param', 'cli', 'plain_skill'])
  const shell = shellOf(result.schemes[0])
  // parameter file and invocation share one paste block
  assert.match(shell, /cat > spiceIn\.il <<'EOF'/)
  assert.match(shell, /spiceIn -param spiceIn\.il/)
  assert.match(shell, /conn2schArgs\s+"-asg \+PLACE_ONLY -NOSQUARE -MIN_CROSSOVERS -FAST_LABELS \+NOXTRSCH -VERBOSE"/)
  // the system() ban and the no-place-only-CLI-flag caveat ride along
  assert.ok(result.schemes[0].caveats.some((c) => c.includes('system()')))
  assert.ok(result.schemes[0].caveats.some((c) => c.includes('NO place-only flag')))
  const cli = shellOf(result.schemes[1])
  assert.match(cli, /spiceIn -netlistFile amp\.scs -outputLib <lib> -language Spectre/)
  assert.match(cli, /-reflibList/)
  assert.match(cli, /-devmapFile/)
})

test('import place_mode=full drops +PLACE_ONLY from conn2schArgs', async () => {
  const result = await run(importTool, { netlist_file: 'amp.scs', place_mode: 'full' })
  const shell = shellOf(result.schemes[0])
  assert.doesNotMatch(shell, /\+PLACE_ONLY/)
  assert.match(shell, /conn2schArgs\s+"-asg -NOSQUARE/)
})

test('plain_skill import block: conn2Sch + symbol pair + tb, and gen_symbol_tb=false trims it', async () => {
  const full = (await run(importTool, { netlist_file: 'amp.scs', output_lib: 'lib0' })).schemes[2]
  const text = skillOf(full)
  assert.match(text, /conn2Sch\(lib cell nlv/)
  assert.match(text, /schSchemToPinList/)
  assert.match(text, /schPinListToSymbol/)
  assert.match(text, /schGetPinOrder/)
  assert.match(text, /dbCreateInst\(tbcv nil master "DUT" 0:0\)/)
  assert.match(text, /amp_tb/)
  assert.match(text, /errset\(/)
  assert.match(text, /IMPORT_DONE/)
  const trimmed = (await run(importTool, { netlist_file: 'amp.scs', gen_symbol_tb: false })).schemes[2]
  assert.doesNotMatch(skillOf(trimmed), /schPinListToSymbol/)
  assert.match(skillOf(trimmed), /conn2Sch\(lib cell nlv/)
})

test('unknown schemes are rejected by the enum at validation time', async () => {
  await assert.rejects(() => run(importTool, { netlist_file: 'amp.scs', scheme: 'si' }),
    /"scheme" must be one of param, cli, plain_skill/)
  await assert.rejects(() => run(exportTool, { lib: 'lib0', cell: 'amp', scheme: 'param' }),
    /"scheme" must be one of si, ocean, cdlout, plain_skill/)
})
