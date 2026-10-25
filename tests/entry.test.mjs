// Starter unit tests for the plugin entry (node --test tests/).
// Covers the metadata surface and the tool-registration path with a mock ctx; the
// pipeline scripts themselves are exercised by the workspace smoke runs.
import test from 'node:test'
import assert from 'node:assert/strict'
import { name, inject, apply } from '../src/index.js'

test('plugin metadata', () => {
  assert.equal(name, 'dsh-harnessams-smart-ana-sim')
  assert.deepEqual(inject, ['tools'])
  assert.equal(typeof apply, 'function')
})

test('apply registers the six sim_* tools', async () => {
  const registered = []
  const ctx = {
    effect: (fn) => { fn() },
    tools: { register: (t) => registered.push(t.name) },
  }
  await apply(ctx)
  await new Promise((r) => setTimeout(r, 500))
  assert.deepEqual(registered.sort(), [
    'sim_assemble', 'sim_collect', 'sim_progress', 'sim_report', 'sim_run_sweep', 'sim_trash',
  ])
})
