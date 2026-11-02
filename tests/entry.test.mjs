// Plugin metadata, tool surface, parameter-schema, and family-toggle tests.
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { name, inject, apply, toolDefinitions, Config } from '../src/index.js'
import { compileParameters, makeTool } from '../src/tool.js'

const EXPECTED = [
  'snp_extract', 'snp_read',
  'spectre_assemble', 'spectre_parse', 'spectre_run_sweep',
]

test('plugin metadata', () => {
  assert.equal(name, 'dsh-harnessams-smart-ana-sim')
  assert.deepEqual(inject, ['tools'])
  assert.equal(typeof apply, 'function')
})

test('apply registers the 5 simulator tools', () => {
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
  assert.deepEqual(resolved.value, { spectre: true, snp: true })
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
  assert.deepEqual(names({ spectre: false }), ['snp_extract', 'snp_read'])
  assert.deepEqual(names({ snp: false }),
    ['spectre_assemble', 'spectre_parse', 'spectre_run_sweep'])
  assert.deepEqual(names({ spectre: false, snp: false }), [])

  const registered = []
  const ctx = {
    effect: (fn) => { fn() },
    tools: { register: (t) => registered.push(t.name) },
  }
  apply(ctx, { snp: false })
  assert.deepEqual(registered.sort(), ['spectre_assemble', 'spectre_parse', 'spectre_run_sweep'])
})

test('every tool compiles to an object-rooted JSON Schema with typed properties', () => {
  const defs = toolDefinitions()
  assert.equal(defs.length, 5)
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
    assert.equal(tool.output.schema.type, 'object')
    assert.equal(tool.output.schema.additionalProperties, true)
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
  const assembly = makeTool(defs.find((d) => d.name === 'spectre_assemble'))
  await assert.rejects(() => assembly.execute({}), /missing required property "project_dir"/)

  const sweep = makeTool(defs.find((d) => d.name === 'spectre_run_sweep'))
  await assert.rejects(() => sweep.execute({ run_dir: 5 }), /"run_dir" must be a string/)

  const parse = makeTool(defs.find((d) => d.name === 'spectre_parse'))
  await assert.rejects(() => parse.execute({ raw_dir: 5 }), /"raw_dir" must be a string/)

  const read = makeTool(defs.find((d) => d.name === 'snp_read'))
  await assert.rejects(() => read.execute({ file: 7 }), /"file" must be a string/)
})

test('parameter enum declarations survive compilation', () => {
  const defs = toolDefinitions()
  const extract = makeTool(defs.find((d) => d.name === 'snp_extract'))
  assert.deepEqual(extract.parameters.properties.type.enum,
    ['tl', 'coupled_tl', 'transformer', 'inductor'])
  assert.deepEqual(extract.parameters.required.sort(), ['file', 'type'])
})
