// Plugin metadata, tool surface, and parameter-schema tests.
import test from 'node:test'
import assert from 'node:assert/strict'
import { name, inject, apply, toolDefinitions } from '../src/index.js'
import { compileParameters, makeTool } from '../src/tool.js'

const EXPECTED = [
  'bsub_fetch', 'bsub_poll', 'bsub_submit',
  'ngspice_assemble', 'ngspice_run_sweep',
  'sim_collect', 'sim_progress', 'sim_report', 'sim_trash',
  'snp_extract', 'snp_read',
  'spectre_assemble', 'spectre_parse', 'spectre_run_sweep',
]

test('plugin metadata', () => {
  assert.equal(name, 'dsh-harnessams-smart-ana-sim')
  assert.deepEqual(inject, ['tools'])
  assert.equal(typeof apply, 'function')
})

test('apply registers the 14 tools', () => {
  const registered = []
  const ctx = {
    effect: (fn) => { fn() },
    tools: { register: (t) => registered.push(t.name) },
  }
  apply(ctx)
  assert.deepEqual(registered.sort(), EXPECTED)
})

test('every tool compiles to an object-rooted JSON Schema with typed properties', () => {
  const defs = toolDefinitions()
  assert.equal(defs.length, 14)
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
  const assembly = makeTool(defs.find((d) => d.name === 'ngspice_assemble'))
  await assert.rejects(() => assembly.execute({}), /missing required property "project_dir"/)

  const progress = makeTool(defs.find((d) => d.name === 'sim_progress'))
  await assert.rejects(() => progress.execute({ run_dir: 5 }), /"run_dir" must be a string/)

  const submit = makeTool(defs.find((d) => d.name === 'bsub_submit'))
  await assert.rejects(() => submit.execute({
    run_dir: 'r', remote_dir: 'd', worker: 'w', worker_args: [1],
  }), /"worker_args" must be an array of strings/)

  const trash = makeTool(defs.find((d) => d.name === 'sim_trash'))
  await assert.rejects(() => trash.execute({ action: 'nope', path: 'x' }), /must be one of/)
})

test('parameter enum declarations survive compilation', () => {
  const defs = toolDefinitions()
  const extract = makeTool(defs.find((d) => d.name === 'snp_extract'))
  assert.deepEqual(extract.parameters.properties.type.enum,
    ['tl', 'coupled_tl', 'transformer', 'inductor'])
  assert.deepEqual(extract.parameters.required.sort(), ['file', 'type'])
})
