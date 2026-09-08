import { describe, expect, it } from 'vitest'
import {
  CLASS_COLORS,
  CLASS_OF,
  EDGE_STYLE,
  SHAPE_OF,
  STATUS,
  TIER_SIZE,
  assignCurvature,
  colorFor,
  endpointId,
  sizeFor,
} from './graph-encoding.js'

// Every node type the schema allows. If the store gains a tenth, these tests fail rather
// than the view silently rendering it as an unlabelled default sphere in the wrong colour.
const NODE_TYPES = [
  'decision', 'concept', 'constraint', 'issue', 'invariant',
  'entity', 'goal', 'slice', 'facet_value',
]
const EDGE_TYPES = ['DEPENDS_ON', 'CONTRADICTS', 'SCOPED_TO', 'HAS_FACET', 'ABOUT', 'CONSOLIDATES']
const TIERS = ['short-term', 'mid-term', 'long-term', 'lifetime']

describe('the encoding covers the whole schema', () => {
  it('gives every node type a class', () => {
    for (const type of NODE_TYPES) expect(CLASS_OF[type], type).toBeDefined()
  })

  it('gives every node type its own shape', () => {
    for (const type of NODE_TYPES) expect(SHAPE_OF[type], type).toBeDefined()
    // Shape is the channel carrying type, so two types sharing one is a collision that
    // makes them indistinguishable — colour cannot rescue it, since colour is class.
    expect(new Set(Object.values(SHAPE_OF)).size).toBe(NODE_TYPES.length)
  })

  it('gives every edge type a style', () => {
    for (const type of EDGE_TYPES) expect(EDGE_STYLE[type], type).toBeDefined()
  })

  it('gives every tier a size', () => {
    for (const tier of TIERS) expect(TIER_SIZE[tier], tier).toBeGreaterThan(0)
  })
})

describe('colour', () => {
  // These hexes are not decoration: they are slots 1-3 of the validated palette, and the
  // set passes the all-pairs CVD and normal-vision gates in both modes. Anyone "tidying"
  // them is re-opening a check that was run — so the values are pinned here.
  it('uses the validated palette slots, unchanged', () => {
    expect(CLASS_COLORS.light).toEqual({
      assert: '#2a78d6', name: '#eb6834', structure: '#1baf7a',
    })
    expect(CLASS_COLORS.dark).toEqual({
      assert: '#3987e5', name: '#d95926', structure: '#199e70',
    })
  })

  it('uses exactly three categorical hues per mode', () => {
    // The all-pairs cap. A fourth would need the validator run again, and on the
    // documented palette a fourth slot fails the all-pairs floors.
    for (const mode of ['light', 'dark']) {
      expect(new Set(Object.values(CLASS_COLORS[mode])).size).toBe(3)
    }
  })

  it('keeps the status colour out of the categorical set', () => {
    for (const mode of ['light', 'dark']) {
      expect(Object.values(CLASS_COLORS[mode])).not.toContain(STATUS.critical)
    }
  })

  it('colours by class, not by type', () => {
    const decision = { type: 'decision', tier: 'short-term' }
    const constraint = { type: 'constraint', tier: 'short-term' }
    const entity = { type: 'entity', tier: 'short-term' }
    // Five artifact types share one hue on purpose — that is what keeps the palette to
    // three all-pairs-safe slots.
    expect(colorFor(decision, 'dark')).toBe(colorFor(constraint, 'dark'))
    expect(colorFor(decision, 'dark')).not.toBe(colorFor(entity, 'dark'))
  })

  it('falls back to a real colour for an unknown type rather than undefined', () => {
    expect(colorFor({ type: 'something-new' }, 'dark')).toMatch(/^#[0-9a-f]{6}$/)
  })

  it('reserves red for CONTRADICTS alone among edges', () => {
    const red = Object.entries(EDGE_STYLE).filter(([, s]) => s.color === STATUS.critical)
    expect(red.map(([type]) => type)).toEqual(['CONTRADICTS'])
  })
})

describe('size', () => {
  it('increases with tier', () => {
    const at = (tier) => sizeFor({ tier, degree: 0 })
    expect(at('short-term')).toBeLessThan(at('mid-term'))
    expect(at('mid-term')).toBeLessThan(at('long-term'))
    expect(at('long-term')).toBeLessThan(at('lifetime'))
  })

  it('lets degree nudge size without letting it outrank tier', () => {
    // A hub should read as a hub; it must not read as a promoted foundation. This is the
    // property that keeps two channels from fighting over the same visual weight.
    const busyShortTerm = sizeFor({ tier: 'short-term', degree: 200 })
    const quietMidTerm = sizeFor({ tier: 'mid-term', degree: 0 })
    expect(busyShortTerm).toBeGreaterThan(sizeFor({ tier: 'short-term', degree: 0 }))
    expect(busyShortTerm).toBeLessThan(quietMidTerm)
  })

  it('treats an unknown tier as the smallest rather than crashing', () => {
    expect(sizeFor({ tier: 'invented', degree: 0 })).toBe(TIER_SIZE['short-term'])
  })
})

describe('parallel edges', () => {
  const link = (source, target, type) => ({ source, target, type })

  it('leaves a lone edge straight', () => {
    const [only] = assignCurvature([link('a', 'b', 'DEPENDS_ON')])
    expect(only.curvature).toBe(0)
  })

  it('separates two edges between the same pair', () => {
    // The case this exists for: a DEPENDS_ON and a CONTRADICTS between one pair is
    // exactly what the review queue is about, and drawn straight one hides the other.
    const links = assignCurvature([
      link('a', 'b', 'DEPENDS_ON'),
      link('a', 'b', 'CONTRADICTS'),
    ])
    expect(links[0].curvature).not.toBe(links[1].curvature)
    expect(links.every((l) => l.curvature !== 0)).toBe(true)
  })

  it('treats a->b and b->a as the same pair', () => {
    // They occupy the same line on screen regardless of direction, so they must curve
    // apart — grouping by an ordered key would leave them overlapping.
    const links = assignCurvature([link('a', 'b', 'DEPENDS_ON'), link('b', 'a', 'DEPENDS_ON')])
    expect(links[0].curvature).not.toBe(links[1].curvature)
  })

  it('gives every edge in a larger bundle a distinct curvature', () => {
    const links = assignCurvature([
      link('a', 'b', 'DEPENDS_ON'),
      link('a', 'b', 'CONTRADICTS'),
      link('a', 'b', 'ABOUT'),
      link('b', 'a', 'CONSOLIDATES'),
    ])
    expect(new Set(links.map((l) => l.curvature)).size).toBe(4)
  })

  it('does not confuse different pairs', () => {
    const links = assignCurvature([link('a', 'b', 'DEPENDS_ON'), link('c', 'd', 'DEPENDS_ON')])
    expect(links.every((l) => l.curvature === 0)).toBe(true)
  })

  it('works after the layout has replaced endpoints with node objects', () => {
    // force-graph mutates links in place, swapping the id for the node object. Curvature
    // is recomputed on reload, by which time the previous run has already done that.
    const links = assignCurvature([
      { source: { id: 'a' }, target: { id: 'b' }, type: 'DEPENDS_ON' },
      { source: 'a', target: 'b', type: 'CONTRADICTS' },
    ])
    expect(links[0].curvature).not.toBe(links[1].curvature)
  })
})

describe('endpointId', () => {
  it('accepts both the id and the hydrated node', () => {
    expect(endpointId('abc')).toBe('abc')
    expect(endpointId({ id: 'abc' })).toBe('abc')
  })
})
