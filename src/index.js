// HarnessAMS-SmartAnaSim plugin entry (plain-JS publish entry, no build chain;
// dev-mount loads this file directly).
//
// Simulator families only (2026-11-01 redistribution: the generic remote batch
// channel batch_* — renamed from bsub_* on 2026-11-09 — and the simulator-
// agnostic analysis tools moved to the top-level dsh-harnessams plugin — this
// package stays simulator-specific):
//   spectre_*  local Spectre assembly + batch sweep + psfascii parsing
//   snp_*      S-parameter reading / device extraction
//
// Architecture: the plugin is only a channel — tool executes spawn plain Python
// scripts shipped under scripts/ (assembly/check/adjudication logic lives in the
// script layer; intermediate steps are not codified). Batch submission of these
// workers to a cluster and the sweep-result analysis/report pipeline are owned
// by the dsh-harnessams plugin (batch_submit takes its worker from the workspace
// sim_scripts_dir, which points at this package's scripts/ tree).
import z from '@deepseek-ai/schemastery'
import { registerTools } from './tool.js'
import { spectreTools } from './tools/spectre.js'
import { snpTools } from './tools/snp.js'

export const name = 'dsh-harnessams-smart-ana-sim'
export const inject = ['tools']

// DSH-native per-family toggles (official tool-fs precedent): each simulator
// family registers only when its switch is not explicitly false (defaults keep
// the full 5-tool surface). Changing the config hot-swaps the plugin: cordis
// restarts the fiber and apply() re-registers with the new selection.
export const Config = z.object({
  spectre: z.boolean().default(true).description('Register the spectre_* tool family (local Spectre assembly + batch sweep + psfascii parsing).'),
  snp: z.boolean().default(true).description('Register the snp_* tool family (Touchstone reading / device extraction).'),
})

export function toolDefinitions(ctx = {}, config = {}) {
  return [
    ...(config.spectre !== false ? spectreTools() : []),
    ...(config.snp !== false ? snpTools() : []),
  ]
}

export function apply(ctx, config) {
  ctx.effect(() => {
    const names = registerTools(ctx, toolDefinitions(ctx, config))
    if (ctx.logger?.info) ctx.logger.info('[smart-ana-sim] registered %d tools', names.size)
    else console.log('[smart-ana-sim] registered', names.size, 'tools')
  }, 'smart-ana-sim tools')
}
