// src/subagent.js — the subagent identity / state-inheritance policy shared
// verbatim with the HarnessAMS repo (this repo is the source of truth).
// Unit cases use plain objects shaped like DSH Agent records: { id, session:
// { header: { origin?, parentSession?, isSeeded? } } } with ctx.agents.get
// as a live-agent map. The last test is the soft sync guard: in the dev
// layout the sibling repo's copy must be byte-identical; an installed copy
// (no sibling) skips.
import test from 'node:test'
import assert from 'node:assert/strict'
import { existsSync, readFileSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { effectiveSessionId, inheritsSessionState } from '../src/subagent.js'

const REPO = path.join(path.dirname(fileURLToPath(import.meta.url)), '..')

const user = (id) => ({ id, session: { header: { origin: undefined, isSeeded: false } } })
const sub = (id, parentId, isSeeded) => ({
  id,
  session: { header: { origin: 'subagent', parentSession: parentId, isSeeded } },
})
const ctxOf = (...agents) => {
  const m = new Map(agents.map((a) => [a.id, a]))
  return { agents: { get: (id) => m.get(id) } }
}

// ---- effectiveSessionId ----------------------------------------------------

test('effectiveSessionId: agent-less call -> null', () => {
  assert.equal(effectiveSessionId(ctxOf(), undefined), null)
  assert.equal(effectiveSessionId(ctxOf(), {}), null)
  assert.equal(effectiveSessionId(ctxOf(), { session: { header: {} } }), null)
})

test('effectiveSessionId: plain session -> its own id (no header, fork, top-level)', () => {
  assert.equal(effectiveSessionId(ctxOf(), { id: 's1' }), 's1')
  // a fork carries no origin; a stale parentSession must not extend the walk
  const fork = { id: 's2', session: { header: { origin: undefined, parentSession: 's1', isSeeded: true } } }
  assert.equal(effectiveSessionId(ctxOf(fork), fork), 's2')
})

test('effectiveSessionId: subagent -> topmost user-visible ancestor', () => {
  const root = user('root')
  const mid = sub('mid', 'root', true)
  const leaf = sub('leaf', 'mid', false)
  assert.equal(effectiveSessionId(ctxOf(root, mid, leaf), leaf), 'root')
  assert.equal(effectiveSessionId(ctxOf(root, mid, leaf), mid), 'root')
  assert.equal(effectiveSessionId(ctxOf(root, mid, leaf), root), 'root')
})

test('effectiveSessionId: fork parent breaks the chain at the fork', () => {
  const fork = { id: 'fork', session: { header: { origin: undefined, isSeeded: true } } }
  const child = sub('child', 'fork', true)
  assert.equal(effectiveSessionId(ctxOf(fork, child), child), 'fork')
})

test('effectiveSessionId: broken chain (ancestor not live) -> raw id; no ctx.agents same', () => {
  const child = sub('child', 'gone', true)
  assert.equal(effectiveSessionId(ctxOf(), child), 'child')
  assert.equal(effectiveSessionId({}, child), 'child')
  assert.equal(effectiveSessionId(undefined, child), 'child')
})

test('effectiveSessionId: cycle-proof (mutual parents never hang)', () => {
  const a = sub('a', 'b', true)
  const b = sub('b', 'a', true)
  assert.equal(effectiveSessionId(ctxOf(a, b), a), 'a')
})

// ---- inheritsSessionState --------------------------------------------------

test('inheritsSessionState: seeded child inherits an activated parent (Set and predicate probes)', () => {
  const parent = user('parent')
  const child = sub('child', 'parent', true)
  const activated = new Set(['parent'])
  assert.equal(inheritsSessionState(ctxOf(parent, child), child, activated), true)
  assert.equal(inheritsSessionState(ctxOf(parent, child), child, (id) => id === 'parent'), true)
  assert.equal(inheritsSessionState(ctxOf(parent, child), child, new Set()), false)
})

test('inheritsSessionState: non-seeded child inherits nothing', () => {
  const parent = user('parent')
  const child = sub('child', 'parent', false)
  assert.equal(inheritsSessionState(ctxOf(parent, child), child, new Set(['parent'])), false)
})

test('inheritsSessionState: seeded chain reaches through a seeded intermediate, stops at a non-seeded one', () => {
  const root = user('root')
  const seededMid = sub('mid', 'root', true)
  const unseededMid = sub('umid', 'root', false)
  const leaf = sub('leaf', 'mid', true)
  const leaf2 = sub('leaf2', 'umid', true)
  const activated = new Set(['root'])
  assert.equal(inheritsSessionState(ctxOf(root, seededMid, leaf), leaf, activated), true)
  assert.equal(inheritsSessionState(ctxOf(root, unseededMid, leaf2), leaf2, activated), false)
})

test('inheritsSessionState: plain session or agent-less call -> false, never throws', () => {
  const plain = user('plain')
  assert.equal(inheritsSessionState(ctxOf(plain), plain, new Set(['plain'])), false)
  assert.equal(inheritsSessionState(ctxOf(), undefined, new Set(['x'])), false)
  assert.equal(inheritsSessionState(ctxOf(), {}, () => true), false)
})

test('inheritsSessionState: membership holds even for a non-live parent; miss + dead parent -> false; cycle never hangs', () => {
  // The parent id sits in the activated set (it called the guide in this
  // process): the child's inherited history carries that fact whether or not
  // the parent Agent object is still resident.
  const child = sub('child', 'gone', true)
  assert.equal(inheritsSessionState(ctxOf(), child, new Set(['gone'])), true)
  assert.equal(inheritsSessionState(ctxOf(), child, new Set(['other'])), false)
  const a = sub('a', 'b', true)
  const b = sub('b', 'a', true)
  assert.equal(inheritsSessionState(ctxOf(a, b), a, new Set(['x'])), false)
})

// ---- the verbatim-sync guard (dev layout only) ------------------------------

test('shared module stays byte-identical with the HarnessAMS mirror (dev layout)', () => {
  const sibling = path.join(REPO, '..', 'HarnessAMS', 'src', 'subagent.js')
  if (!existsSync(sibling)) return // installed copy: no sibling repo, skip
  const here = readFileSync(path.join(REPO, 'src', 'subagent.js'), 'utf8')
  const there = readFileSync(sibling, 'utf8')
  assert.equal(here, there, 'src/subagent.js drifted from the HarnessAMS mirror — edit the SAS copy first, then mirror')
})
