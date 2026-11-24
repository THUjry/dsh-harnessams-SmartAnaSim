// Plugin-bundled skills: every <pkg>/skills/<name>/SKILL.md registers into
// the host skill registry (ctx.skills) so the model-facing catalog lists it
// and the `skill` tool loads the body on demand. Only name + description
// enter the catalog (two lines); bodies stay on disk until loaded.
// resourceBase points at the skill directory so a loaded body's relative
// references (companion pages like spectre-cli.md, scripts/ via ../scripts)
// resolve — the official dsh-skill-badge precedent for packaged skills.
// Companion .md pages are NOT enumerated at load: the root SKILL.md must
// name every one of them explicitly. Fail-open by design: a host without
// ctx.skills, or one broken SKILL.md, logs and skips — tool registration is
// never blocked by the skill surface.
import fs from 'node:fs'
import path from 'node:path'

const SKILLS_DIR = path.join(import.meta.dirname ?? '.', '..', 'skills')

// Minimal frontmatter reader for the fields the registry consumes: `name`
// (plain) and `description` (plain, or a `>`/`|` folded block of indented
// continuation lines). Anything else in the header is ignored.
// Returns { name, description, content } or { error }.
export function readSkillManifest(file) {
  let text
  try {
    text = fs.readFileSync(file, 'utf8')
  } catch (e) {
    return { error: `unreadable: ${e}` }
  }
  const m = text.match(/^---\r?\n([\s\S]*?)\r?\n---/)
  if (!m) return { error: 'no frontmatter block' }
  const lines = m[1].split(/\r?\n/)
  const name = lines.find((l) => /^name:\s*\S/.test(l))?.replace(/^name:\s*/, '').trim()
  let description = ''
  // plain single-line value (starts with a non-space that is not a folded-
  // scalar marker); else a `>`/`|` block of indented continuation lines
  const di = lines.findIndex((l) => /^description:\s*[^\s>|]/.test(l))
  if (di >= 0) {
    description = lines[di].replace(/^description:\s*/, '').trim()
  } else {
    const fi = lines.findIndex((l) => /^description:\s*[>|]/.test(l))
    if (fi >= 0) {
      description = lines.slice(fi + 1)
        .filter((l) => /^\s+\S/.test(l))
        .map((l) => l.trim())
        .join(' ')
    }
  }
  if (!name || !description) return { error: 'frontmatter needs name and description' }
  const content = text.slice(m[0].length).replace(/^\r?\n/, '')
  return { name, description, content }
}

// Every <dir>/<name>/SKILL.md as { name, dir, file, manifest } — broken or
// missing manifests are returned with { error } so the caller can log them
// individually (a half-broken skills dir never hides the healthy skills).
export function bundledSkills(dir = SKILLS_DIR) {
  let entries
  try {
    entries = fs.readdirSync(dir, { withFileTypes: true })
  } catch {
    return []
  }
  const out = []
  for (const ent of entries.sort((a, b) => a.name.localeCompare(b.name))) {
    if (!ent.isDirectory()) continue
    const skillDir = path.join(dir, ent.name)
    const file = path.join(skillDir, 'SKILL.md')
    if (!fs.existsSync(file)) continue
    const manifest = readSkillManifest(file)
    out.push(manifest.error
      ? { name: ent.name, dir: skillDir, file, error: manifest.error }
      : { name: manifest.name, dir: skillDir, file, manifest })
  }
  return out
}

// Register every bundled skill into ctx.skills (runtime provider, both
// invocation surfaces). Returns the registered names; failures are logged
// and skipped. Idempotent per process — the catalog de-duplicates by name.
export function registerSkills(ctx, dir = SKILLS_DIR) {
  if (typeof ctx?.skills?.register !== 'function') {
    (ctx?.logger?.warn ?? console.warn)('[smart-ana-sim] ctx.skills unavailable — bundled skills not registered')
    return []
  }
  const registered = []
  for (const skill of bundledSkills(dir)) {
    if (skill.error) {
      (ctx?.logger?.warn ?? console.warn)(`[smart-ana-sim] skill ${skill.name} skipped: ${skill.error}`)
      continue
    }
    try {
      ctx.skills.register({
        name: skill.manifest.name,
        description: skill.manifest.description,
        source: 'bundled',
        resourceBase: { kind: 'directory', path: skill.dir },
        content: skill.manifest.content,
      })
      registered.push(skill.manifest.name)
    } catch (e) {
      (ctx?.logger?.warn ?? console.warn)(`[smart-ana-sim] skill ${skill.name} rejected: ${e}`)
    }
  }
  return registered
}
