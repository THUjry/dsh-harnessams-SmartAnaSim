// HarnessAMS-SmartAnaSim plugin entry (plain-JS publish entry, no build chain;
// dev-mount loads this file directly).
//
// Four capability families + one shared family:
//   ngspice_*  local ngspice assembly + batch sweep
//   spectre_*  local Spectre assembly + batch sweep + psfascii parsing
//   bsub_*     generic remote LSF batch channel through dsh-remote (no simulator names)
//   snp_*      S-parameter reading / device extraction
//   sim_*      simulator-agnostic collect / progress / report / trash
//
// Architecture: the plugin is only a channel — tool executes spawn plain Python
// scripts shipped under scripts/ (assembly/check/adjudication logic lives in the
// script layer; intermediate steps are not codified).
import { registerTools } from './tool.js'
import { ngspiceTools } from './tools/ngspice.js'
import { spectreTools } from './tools/spectre.js'
import { bsubTools } from './tools/bsub.js'
import { sharedTools } from './tools/shared.js'
import { snpTools } from './tools/snp.js'

export const name = 'dsh-harnessams-smart-ana-sim'
export const inject = ['tools']

export function toolDefinitions(ctx = {}) {
  return [
    ...ngspiceTools(),
    ...spectreTools(),
    ...bsubTools(ctx),
    ...sharedTools(),
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
