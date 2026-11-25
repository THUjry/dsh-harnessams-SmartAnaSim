// HarnessAMS-SmartAnaSim plugin entry (plain-JS publish entry, no build chain;
// dev-mount loads this file directly).
//
// A pure tool set, usable on its own — no other plugin is required:
//   spectre_pre_check  Spectre netlist pre-check for hand-written run
//                      directories (template*.scs + sweep_config.json):
//                      language format, sweep-parameter presence and the value
//                      gates — pure JS, no python on the tool path (see
//                      src/precheck.js)
//   snp_*             S-parameter reading / device extraction — guide-gated:
//                     only snp_guide is always declared; snp_read/snp_extract
//                     register deferred (deferLoading + when) and enter the
//                     session via the guide (see src/tools/snp.js)
//   gen_vsch_*        Virtuoso schematic interaction, command-assembly face:
//                     gen_vsch_import / gen_vsch_export return ready-to-run
//                     text blocks (shell + Cadence SKILL) for the netlist <->
//                     OA round trip and never execute anything (see
//                     src/tools/vsch.js)
//
// Architecture: the netlist pre-check runs in-process (pure JS); every other
// tool face spawns plain Python scripts shipped under scripts/ (worker and
// analysis logic lives in the script layer; intermediate steps are not
// codified). The sweep worker and the analysis scripts run directly with
// python anywhere; standing up a managed scan flow around them is outside
// this package.
//
// The bundled smart-spectre skill (skills/smart-spectre/) is the configuration
// reference for this package's own surface: netlist/template format, the
// sweep_config contract, the assembly checks, and companion pages for the
// worker, the analysis scripts, S-parameter flows, cascade stitching and the
// PSS variant. Registered fail-open below, like the tool face.
import z from '@deepseek-ai/schemastery'
import { registerTools } from './tool.js'
import { spectreTools } from './tools/spectre.js'
import { snpTools } from './tools/snp.js'
import { vschTools } from './tools/vsch.js'
import { registerSkills } from './skills.js'

export const name = 'dsh-harnessams-smart-ana-sim'
// skills must be declared alongside tools (cordis blocks ctx access to
// undeclared services — optional chaining cannot save that).
export const inject = ['tools', 'skills']

// DSH-native per-family toggles (official tool-fs precedent): each family
// registers only when its switch is not explicitly false (defaults keep
// the full 6-tool surface). Changing the config hot-swaps the plugin: cordis
// restarts the fiber and apply() re-registers with the new selection.
export const Config = z.object({
  spectre: z.boolean().default(true).description('Register the spectre_* tool family (Spectre netlist pre-check).'),
  snp: z.boolean().default(true).description('Register the snp_* tool family (Touchstone reading / device extraction).'),
  vsch: z.boolean().default(true).description('Register the gen_vsch_* tool family (Virtuoso schematic import/export command assembly).'),
})

export function toolDefinitions(ctx = {}, config = {}) {
  return [
    ...(config.spectre !== false ? spectreTools() : []),
    ...(config.snp !== false ? snpTools() : []),
    ...(config.vsch !== false ? vschTools() : []),
  ]
}

export function apply(ctx, config) {
  ctx.effect(() => {
    const names = registerTools(ctx, toolDefinitions(ctx, config))
    if (ctx.logger?.info) ctx.logger.info('[smart-ana-sim] registered %d tools', names.size)
    else console.log('[smart-ana-sim] registered', names.size, 'tools')
  }, 'smart-ana-sim tools')
  ctx.effect(() => {
    const names = registerSkills(ctx)
    if (names.length) {
      if (ctx.logger?.info) ctx.logger.info('[smart-ana-sim] registered %d skills: %s', names.length, names.join(', '))
      else console.log('[smart-ana-sim] registered', names.length, 'skills:', names.join(', '))
    }
  }, 'smart-ana-sim skills')
}
