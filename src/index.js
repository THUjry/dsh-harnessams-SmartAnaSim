// HarnessAMS-SmartAnaSim plugin entry (plain-JS publish entry, no build chain;
// dev-mount loads this file directly).
//
// Simulator families only (2026-11-01 redistribution: the generic remote batch
// channel bsub_* and the simulator-agnostic analysis tools sim_* moved to the
// top-level dsh-harnessams plugin — this package stays simulator-specific):
//   ngspice_*  local ngspice assembly + batch sweep
//   spectre_*  local Spectre assembly + batch sweep + psfascii parsing
//   snp_*      S-parameter reading / device extraction
//
// Architecture: the plugin is only a channel — tool executes spawn plain Python
// scripts shipped under scripts/ (assembly/check/adjudication logic lives in the
// script layer; intermediate steps are not codified). Batch submission of these
// workers to a cluster and the sweep-result analysis/report pipeline are owned
// by the dsh-harnessams plugin (bsub_submit takes its worker from the workspace
// sim_scripts_dir, which points at this package's scripts/ tree).
import { registerTools } from './tool.js'
import { ngspiceTools } from './tools/ngspice.js'
import { spectreTools } from './tools/spectre.js'
import { snpTools } from './tools/snp.js'

export const name = 'dsh-harnessams-smart-ana-sim'
export const inject = ['tools']

export function toolDefinitions(ctx = {}) {
  return [
    ...ngspiceTools(),
    ...spectreTools(),
    ...snpTools(),
  ]
}

export function apply(ctx) {
  ctx.effect(() => {
    const names = registerTools(ctx, toolDefinitions(ctx))
    if (ctx.logger?.info) ctx.logger.info('[smart-ana-sim] registered %d tools', names.size)
    else console.log('[smart-ana-sim] registered', names.size, 'tools')
  }, 'smart-ana-sim tools')
}
