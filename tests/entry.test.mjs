// Plugin metadata, tool surface, parameter-schema, and family-toggle tests.
import test from 'node:test'
import assert from 'node:assert/strict'
import { readdirSync, readFileSync } from 'node:fs'
import { readFile } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { name, inject, apply, toolDefinitions, Config } from '../src/index.js'
import { compileParameters, makeTool } from '../src/tool.js'
import { registerSkills } from '../src/skills.js'

const REPO = path.join(path.dirname(fileURLToPath(import.meta.url)), '..')

const EXPECTED = [
  'gen_vsch_export', 'gen_vsch_import',
  'snp_extract', 'snp_guide', 'snp_read',
  'spectre_pre_check',
]

test('plugin metadata', () => {
  assert.equal(name, 'dsh-harnessams-smart-ana-sim')
  assert.deepEqual(inject, ['tools', 'skills'])
  assert.equal(typeof apply, 'function')
})

test('bundled smart-spectre skill registers with resourceBase and names every companion page', () => {
  const registered = []
  const ctx = {
    effect: (fn) => { fn() },
    tools: { register: () => {} },
    skills: { register: (s) => registered.push(s) },
  }
  apply(ctx)
  assert.deepEqual(registered.map((s) => s.name), ['smart-spectre'])
  const s = registered[0]
  assert.equal(s.source, 'bundled')
  assert.equal(s.resourceBase.kind, 'directory')
  assert.ok(s.description.length > 20 && s.description.length <= 500)
  assert.ok(s.content.length > 1000, 'root page is the format reference, not a bare stub')
  // the skill loader does NOT enumerate the directory: every companion .md
  // must be named in the root SKILL.md or the model can never find it
  const dir = s.resourceBase.path
  const root = readFileSync(path.join(dir, 'SKILL.md'), 'utf8')
  for (const f of readdirSync(dir).filter((f) => f.endsWith('.md') && f !== 'SKILL.md')) {
    assert.ok(root.includes(`\`${f}\``), `${f} must be named in the root SKILL.md`)
  }
  assert.equal(registerSkills({ skills: null }).length, 0, 'fail-open without ctx.skills')
})

test('apply registers the 6 tools', () => {
  const registered = []
  const ctx = {
    effect: (fn) => { fn() },
    tools: { register: (t) => registered.push(t.name) },
  }
  apply(ctx)
  assert.deepEqual(registered.sort(), EXPECTED)
})

test('Config is a real schemastery schema (cordis resolveConfig requires ~standard)', () => {
  assert.equal(typeof Config?.['~standard']?.validate, 'function')
  const resolved = Config['~standard'].validate({})
  assert.equal(resolved.issues, undefined)
  assert.deepEqual(resolved.value, { spectre: true, snp: true, vsch: true })
})

test('schemastery ships as a runtime dependency (installed bundles do not get devDependencies)', async () => {
  const pkg = JSON.parse(await readFile(new URL('../package.json', import.meta.url), 'utf8'))
  assert.equal(pkg.dependencies?.['@deepseek-ai/schemastery'], '^3.18.1')
  assert.equal(pkg.peerDependencies?.['@deepseek-ai/schemastery'], undefined)
  assert.equal(pkg.devDependencies?.['@deepseek-ai/schemastery'], undefined)
})

test('family toggles select the registered tool families (only explicit false trims)', () => {
  const names = (config) => toolDefinitions({}, config).map((d) => d.name).sort()
  assert.deepEqual(names({}), EXPECTED)
  assert.deepEqual(names(undefined), EXPECTED)
  assert.deepEqual(names({ spectre: false }), ['gen_vsch_export', 'gen_vsch_import', 'snp_extract', 'snp_guide', 'snp_read'])
  assert.deepEqual(names({ snp: false }), ['gen_vsch_export', 'gen_vsch_import', 'spectre_pre_check'])
  assert.deepEqual(names({ vsch: false }), ['snp_extract', 'snp_guide', 'snp_read', 'spectre_pre_check'])
  assert.deepEqual(names({ spectre: false, snp: false }), ['gen_vsch_export', 'gen_vsch_import'])
  assert.deepEqual(names({ spectre: false, snp: false, vsch: false }), [])

  const registered = []
  const ctx = {
    effect: (fn) => { fn() },
    tools: { register: (t) => registered.push(t.name) },
  }
  apply(ctx, { snp: false })
  assert.deepEqual(registered.sort(), ['gen_vsch_export', 'gen_vsch_import', 'spectre_pre_check'])
})

test('every tool compiles to an object-rooted JSON Schema with typed properties', () => {
  const defs = toolDefinitions()
  assert.equal(defs.length, 6)
  for (const def of defs) {
    const tool = makeTool(def)
    assert.equal(tool.parameters.type, 'object', `${def.name} parameters root`)
    for (const [key, prop] of Object.entries(tool.parameters.properties)) {
      assert.equal(typeof prop.type, 'string', `${def.name}.${key} type`)
      assert.notEqual(prop.required, false, `${def.name}.${key} must not carry required:false`)
    }
    for (const key of tool.parameters.required || []) {
      assert.ok(Object.hasOwn(tool.parameters.properties, key), `${def.name} required ${key}`)
    }
    // The registry validates execute() results against output.schema: every
    // tool uses makeTool's object-rooted default except snp_guide, whose
    // string flow doc carries a string-schema output override.
    assert.equal(tool.output.schema.type, def.name === 'snp_guide' ? 'string' : 'object')
    if (def.name !== 'snp_guide') {
      assert.equal(tool.output.schema.additionalProperties, true)
    }
    assert.equal(typeof tool.output.render, 'function')
  }
})

test('the schema compiler rejects required:false and accepts required:true', () => {
  assert.throws(() => compileParameters({ a: { type: 'string', required: false } }),
    /required must be true/)
  const schema = compileParameters({
    a: { type: 'string', required: true },
    b: { type: 'number' },
    c: { type: 'array', items: { type: 'string' } },
  })
  assert.deepEqual(schema.required, ['a'])
  assert.equal(schema.properties.b.type, 'number')
  assert.equal(schema.properties.c.items.type, 'string')
})

test('argument validation runs before execution', async () => {
  const defs = toolDefinitions()
  const precheck = makeTool(defs.find((d) => d.name === 'spectre_pre_check'))
  await assert.rejects(() => precheck.execute({}), /missing required property "run_dir"/)

  const read = makeTool(defs.find((d) => d.name === 'snp_read'))
  await assert.rejects(() => read.execute({ file: 7 }), /"file" must be a string/)

  const vschImport = makeTool(defs.find((d) => d.name === 'gen_vsch_import'))
  await assert.rejects(() => vschImport.execute({}), /missing required property "netlist_file"/)
  const vschExport = makeTool(defs.find((d) => d.name === 'gen_vsch_export'))
  await assert.rejects(() => vschExport.execute({ lib: 'x' }), /missing required property "cell"/)
})

test('parameter enum declarations survive compilation', () => {
  const defs = toolDefinitions()
  const extract = makeTool(defs.find((d) => d.name === 'snp_extract'))
  assert.deepEqual(extract.parameters.properties.type.enum,
    ['tl', 'coupled_tl', 'transformer', 'inductor'])
  assert.deepEqual(extract.parameters.required.sort(), ['file', 'type'])
})

test('deferred snp tools register with when + deferLoading', () => {
  const registered = new Map() // name -> { tool, opts }
  const ctx = {
    effect: (fn) => { fn() },
    tools: { register: (t, opts) => registered.set(t.name, { tool: t, opts }) },
  }
  apply(ctx)
  for (const name of ['snp_read', 'snp_extract']) {
    assert.equal(registered.get(name).tool.deferLoading, true, `${name} deferLoading`)
    assert.equal(typeof registered.get(name).opts?.when, 'function', `${name} when`)
  }
})

test('snp_guide activates the deferred family for the calling agent only', async () => {
  const defs = toolDefinitions()
  const guide = makeTool(defs.find((d) => d.name === 'snp_guide'))
  const deferred = defs.filter((d) => d.deferLoading === true)
  assert.deepEqual(deferred.map((d) => d.name).sort(), ['snp_extract', 'snp_read'])
  // Unique id: the activation set is module-level and shared across tests.
  const agent = { id: `session-guide-test-${Date.now()}` }
  assert.equal(deferred.every((d) => d.when({ agent }) === false), true)
  const doc = await guide.execute({}, { agent })
  assert.equal(typeof doc, 'string')
  assert.match(doc, /snp_read/)
  assert.equal(deferred.every((d) => d.when({ agent }) === true), true)
  assert.equal(deferred.every((d) => d.when({ agent: { id: 'some-other-agent' } }) === false), true)
  // Degenerate contexts (service-level call without an agent, activation
  // context without an id) must not admit or crash.
  await guide.execute({}, {})
  assert.equal(deferred.every((d) => d.when({}) === false), true)
})
