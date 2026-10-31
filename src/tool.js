// Tool definition helper: compile the author-facing parameter DSL into the
// object-rooted JSON Schema the tool registry projects to the model.
//
// Why a local compiler: in a dev-mount the plugin file is loaded by absolute
// path, so `@deepseek-ai/dsh-tools` may not resolve from this package; the old
// dynamic-import fallback then registered the raw DSL (which the provider
// serialized as `type: null`). The compiler below is the single contract in
// every install shape. `required` may only appear as `true` (omit it for an
// optional parameter) — that is also the first-party defineTool rule.
const SCALAR_TYPES = new Set(['string', 'number', 'integer', 'boolean'])

export function compileParameters(spec) {
  const properties = {}
  const required = []
  for (const [key, raw] of Object.entries(spec || {})) {
    if (!raw || typeof raw !== 'object' || Array.isArray(raw)) {
      throw new Error(`parameters.${key} must be a value schema object`)
    }
    if (Object.hasOwn(raw, 'required') && raw.required !== true) {
      throw new Error(`parameters.${key}.required must be true when present (omit it for optional)`)
    }
    if (raw.required === true) required.push(key)
    const node = {}
    for (const [k, v] of Object.entries(raw)) {
      if (k === 'required') continue
      node[k] = v
    }
    if (Object.hasOwn(node, 'items') && node.items && typeof node.items === 'object') {
      node.items = compileValue(node.items, `parameters.${key}.items`)
    }
    if (Object.hasOwn(node, 'properties')) {
      const nested = compileParameters(node.properties)
      node.properties = nested.properties
      if (nested.required.length) node.required = nested.required
    }
    if (Object.hasOwn(node, 'enum') && (!Array.isArray(node.enum) || !node.enum.length)) {
      throw new Error(`parameters.${key}.enum must be a non-empty array`)
    }
    if (!SCALAR_TYPES.has(node.type) && node.type !== 'array' && node.type !== 'object'
        && node.type !== 'json' && !node.oneOf) {
      throw new Error(`parameters.${key}.type must be string/number/integer/boolean/array/object/json or oneOf`)
    }
    properties[key] = node
  }
  const schema = { type: 'object', properties }
  if (required.length) schema.required = required
  return schema
}

function compileValue(raw, path) {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) {
    throw new Error(`${path} must be a value schema object`)
  }
  const node = {}
  for (const [k, v] of Object.entries(raw)) node[k] = v
  if (Object.hasOwn(node, 'properties')) {
    const nested = compileParameters(node.properties)
    node.properties = nested.properties
    if (nested.required.length) node.required = nested.required
  }
  return node
}

export function validateArgs(schema, args) {
  const violations = []
  const value = args && typeof args === 'object' && !Array.isArray(args) ? args : {}
  for (const key of schema.required || []) {
    if (value[key] === undefined || value[key] === null) {
      violations.push(`missing required property "${key}"`)
    }
  }
  for (const [key, node] of Object.entries(schema.properties || {})) {
    const v = value[key]
    if (v === undefined || v === null) continue
    if (node.type === 'string' && typeof v !== 'string') violations.push(`"${key}" must be a string`)
    else if (node.type === 'number'
             && (typeof v !== 'number' || !Number.isFinite(v))) violations.push(`"${key}" must be a number`)
    else if (node.type === 'integer' && !Number.isInteger(v)) violations.push(`"${key}" must be an integer`)
    else if (node.type === 'boolean' && typeof v !== 'boolean') violations.push(`"${key}" must be a boolean`)
    else if (node.type === 'array' && !Array.isArray(v)) violations.push(`"${key}" must be an array`)
    else if (node.type === 'object'
             && (typeof v !== 'object' || Array.isArray(v))) violations.push(`"${key}" must be an object`)
    if (node.enum && !node.enum.includes(v)) {
      violations.push(`"${key}" must be one of ${node.enum.join(', ')}`)
    }
    if (node.type === 'array' && node.items?.type === 'string'
        && v.some((item) => typeof item !== 'string')) {
      violations.push(`"${key}" must be an array of strings`)
    }
  }
  return violations
}

export function makeTool(def) {
  const parameters = compileParameters(def.parameters)
  return {
    name: def.name,
    description: def.description,
    parameters,
    output: {
      // Object-rooted and explicitly open: the registry validates this schema.
      schema: { type: 'object', properties: { ok: { type: 'boolean' } }, additionalProperties: true },
      render: (_args, value) => [{
        type: 'text',
        text: typeof value === 'string' ? value : JSON.stringify(value, null, 2),
      }],
    },
    async execute(args, exec) {
      const violations = validateArgs(parameters, args)
      if (violations.length) throw new Error('invalid arguments: ' + violations.join('; '))
      return def.execute(args ?? {}, exec)
    },
  }
}

export function registerTools(ctx, defs) {
  const names = new Set()
  for (const def of defs) {
    if (!def?.name) throw new Error('tool definition without a name')
    if (names.has(def.name)) throw new Error(`duplicate tool name: ${def.name}`)
    names.add(def.name)
    if (def.name === 'run_code') throw new Error('tool name "run_code" is reserved')
    ctx.tools.register(makeTool(def))
  }
  return names
}
