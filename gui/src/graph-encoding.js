/**
 * How a memory node becomes a mark in the 3D view.
 *
 * The colour decision here is computed, not chosen. A force-directed graph is an
 * ALL-PAIRS form — any two nodes can end up adjacent on screen, exactly like a scatter
 * plot — and the validated palette caps all-pairs categorical use at THREE slots. Running
 * the validator on one-hue-per-node-type (seven slots) fails hard:
 *
 *     CVD separation   #008300 <-> #eb6834  dE 3.2 (protan)   — needs >= 8
 *     Normal vision    #e87ba4 <-> #eb6834  dE 12.9           — needs >= 15
 *
 * So type is NOT carried by hue. Colour carries the three-way split this project's own
 * design already makes (docs/06_DOMAIN_ENTITIES.md): a node either ASSERTS a claim, NAMES
 * a referent, or is STRUCTURE. Those three pass all-pairs in both modes (worst CVD
 * dE 9.2 light / 9.4 dark). Type is carried by SHAPE, which has far more discriminable
 * levels than hue and is the conventional channel for node-link diagrams anyway.
 *
 *     colour  ->  class      assert / name / structure     (3 validated hues)
 *     shape   ->  type       9 geometries
 *     size    ->  tier       ordinal: short -> lifetime
 *     ring    ->  status     needs_review, reserved critical red, never colour alone
 *     opacity ->  archived   dormant nodes recede
 */

// Categorical slots 1-3 of the reference palette, light and dark steps.
export const CLASS_COLORS = {
  light: { assert: '#2a78d6', name: '#eb6834', structure: '#1baf7a' },
  dark: { assert: '#3987e5', name: '#d95926', structure: '#199e70' },
}

// Reserved status colour — never reused as a fourth category.
export const STATUS = { critical: '#d03b3b' }

export const CLASS_OF = {
  decision: 'assert', concept: 'assert', constraint: 'assert',
  issue: 'assert', invariant: 'assert',
  entity: 'name',
  goal: 'structure', slice: 'structure', facet_value: 'structure',
}

export const CLASS_LABEL = {
  assert: 'asserts a claim',
  name: 'names a referent',
  structure: 'structure (goal, change, facet)',
}

/** Shape per type. Distinct silhouettes, not a ramp — type is nominal. */
export const SHAPE_OF = {
  decision: 'sphere',
  concept: 'octahedron',
  constraint: 'box',
  issue: 'cone',
  invariant: 'torus',
  entity: 'dodecahedron',
  goal: 'icosahedron',
  slice: 'wireframe-box',
  facet_value: 'tetrahedron',
}

/** Ordinal: a promoted node is a bigger mark, which is the human curation axis. */
export const TIER_SIZE = {
  'short-term': 3.2,
  'mid-term': 4.4,
  'long-term': 5.8,
  lifetime: 7.4,
}

export const EDGE_STYLE = {
  // CONTRADICTS is the one edge that means "something is wrong here", so it takes the
  // reserved status colour and a thicker line. The rest are neutral: six edge hues would
  // be a second categorical scale competing with the node one, which the method forbids.
  CONTRADICTS: { color: STATUS.critical, width: 1.6 },
  DEPENDS_ON: { color: '#8a8a86', width: 1.1 },
  ABOUT: { color: '#8a8a86', width: 0.8 },
  CONSOLIDATES: { color: '#8a8a86', width: 0.8 },
  SCOPED_TO: { color: '#5b5b58', width: 0.5 },
  HAS_FACET: { color: '#5b5b58', width: 0.5 },
}

export function colorFor(node, mode) {
  const palette = CLASS_COLORS[mode] || CLASS_COLORS.dark
  return palette[CLASS_OF[node.type] || 'structure']
}

export function sizeFor(node) {
  const base = TIER_SIZE[node.tier] ?? TIER_SIZE['short-term']
  // Degree nudges size a little so hubs read as hubs, but tier stays dominant: a
  // well-connected short-term note must not outrank a promoted foundation.
  return base * (1 + Math.min(node.degree || 0, 20) / 60)
}

export function endpointId(end) {
  return end && typeof end === 'object' ? end.id : end
}

/**
 * Parallel edges must not overlap. Two nodes can carry both a DEPENDS_ON and a
 * CONTRADICTS — that pair is exactly what the review queue is about — and drawn straight
 * they land on the same line and one of them silently disappears. Curvature is assigned
 * per parallel group so every edge in it stays visible and separately clickable.
 */
export function assignCurvature(links) {
  const groups = new Map()
  for (const link of links) {
    const key = [endpointId(link.source), endpointId(link.target)].sort().join(' ')
    if (!groups.has(key)) groups.set(key, [])
    groups.get(key).push(link)
  }
  for (const group of groups.values()) {
    if (group.length === 1) {
      group[0].curvature = 0
      continue
    }
    const step = 0.7 / group.length
    group.forEach((link, i) => {
      link.curvature = step * (i + 1) * (i % 2 === 0 ? 1 : -1)
    })
  }
  return links
}
