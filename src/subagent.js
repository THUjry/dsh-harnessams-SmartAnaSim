// Subagent identity and per-session state policy, shared VERBATIM between the
// two plugin repos: this file is the source of truth (SmartAnaSim stays
// standalone and never imports HarnessAMS; HarnessAMS already consumes SAS
// assets, so its copy at the same path is a mirror). Edits land here first
// and are mirrored byte-identical; on divergence this copy wins.
//
// Why this exists: DSH never accounts a delegated subagent session into any
// workspace (the workspaceRegistry sessionIds account only receives
// session-controller creates, chat imports and schedules; ctx.agents.create
// attaches nothing), so a subagent's own id resolves no project and no
// per-session entry. Policy ruling (2026-11-26): a delegated subagent is an
// extension of the TOPMOST user-visible session, not a work unit of its own.
//   - effectiveSessionId: session-keyed operations (project anchoring,
//     per-session config, registrations) use the first NON-subagent ancestor
//     — a top-level session or a fork. Ordinary forks carry no header origin,
//     so the walk stops there: a branch breaks the lineage. Session stores
//     (sessions.json) stay keyed by user-visible sessions only.
//   - inheritsSessionState: per-session activation state (guide gates) is a
//     declaration-level fact. A SEEDED subagent inherits its parent's log
//     prefix, so a state an ancestor already earned (a guide doc read) is
//     reflected in the child's declarations too — the walk continues only
//     while every child link is seeded. A non-seeded subagent must earn the
//     state itself (call the guide). The state stays registered on the agent
//     that actually read it; it never propagates upward or sideways.
// Both walks read headers synchronously from LIVE agents
// (ctx.agents.get -> .session.header; agentless tests pass a plain object
// with the same shape). A missing (non-live) ancestor breaks the walk:
// effectiveSessionId falls back to the raw id (anchoring then fails with the
// usual not-accounted error and lookups fail open) while state inheritance
// returns false. Cycle-safe via a visited set; depth capped at MAX_WALK.

const MAX_WALK = 32

function agentHeader(agent) {
  const h = agent?.session?.header
  return h && typeof h === 'object' ? h : null
}

// parentSession of `agent` when it is a delegated subagent, else null.
function subagentParent(agent) {
  const h = agentHeader(agent)
  const pid = h?.origin === 'subagent' ? h.parentSession : undefined
  return typeof pid === 'string' && pid ? pid : null
}

// The session id a session-keyed operation should use for `agent`: its own
// id when it is not a delegated subagent, else the topmost user-visible
// ancestor's id (forks and top-level sessions break the chain). Returns null
// for an agent-less call; a broken chain falls back to the raw id.
export function effectiveSessionId(ctx, agent) {
  const id = typeof agent?.id === 'string' && agent.id ? agent.id : null
  if (id === null) return null
  if (subagentParent(agent) === null) return id
  const seen = new Set([id])
  let cur = agent
  for (let i = 0; i < MAX_WALK; i++) {
    const pid = subagentParent(cur)
    if (pid === null || seen.has(pid)) break
    seen.add(pid)
    const parent = ctx?.agents?.get?.(pid)
    const ph = agentHeader(parent)
    if (ph === null) break
    if (ph.origin !== 'subagent') return pid
    cur = parent
  }
  return id
}

// Whether `agent` inherits a per-session state an ancestor already earned:
// true when the chain of SEEDED subagent links reaches an ancestor the probe
// flags. The probe is a has(id) -> boolean predicate or a Set of session ids.
export function inheritsSessionState(ctx, agent, probe) {
  const has = typeof probe === 'function' ? probe : (pid) => probe?.has?.(pid) === true
  const first = subagentParent(agent)
  if (first === null) return false
  const seen = new Set([typeof agent?.id === 'string' && agent.id ? agent.id : first])
  let cur = agent
  for (let i = 0; i < MAX_WALK; i++) {
    const h = agentHeader(cur)
    if (!h || h.origin !== 'subagent' || h.isSeeded !== true) return false
    const pid = subagentParent(cur)
    if (pid === null || seen.has(pid)) return false
    if (has(pid)) return true
    const parent = ctx?.agents?.get?.(pid)
    if (agentHeader(parent) === null) return false
    seen.add(pid)
    cur = parent
  }
  return false
}
