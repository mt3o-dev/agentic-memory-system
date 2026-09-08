import { describe, expect, it } from 'vitest'
import {
  CLASS_COLORS,
  CLASS_OF,
  EDGE_STYLE,
  GROUP,
  LAYOUT,
  LINK_LAYOUT,
  assignGroups,
  clusterForce,
  groupAnchors,
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

describe('layout groups', () => {
  const g = (id, type, over = {}) => ({ id, type, tier: 'short-term', ...over })
  const scoped = (slice, member) => ({ source: slice, target: member, type: 'SCOPED_TO' })

  it('groups an artifact with the change it belongs to', () => {
    const nodes = [g('s1', 'slice'), g('a', 'decision'), g('b', 'constraint')]
    assignGroups(nodes, [scoped('s1', 'a'), scoped('s1', 'b')])
    expect(nodes[1].group).toBe('s1')
    expect(nodes[2].group).toBe('s1')
  })

  it('keeps two changes apart', () => {
    const nodes = [g('s1', 'slice'), g('s2', 'slice'), g('a', 'decision'), g('b', 'decision')]
    assignGroups(nodes, [scoped('s1', 'a'), scoped('s2', 'b')])
    expect(nodes[2].group).not.toBe(nodes[3].group)
  })

  it('gives a change scope its own group so it sits with its members', () => {
    const nodes = [g('s1', 'slice')]
    assignGroups(nodes, [])
    expect(nodes[0].group).toBe('s1')
  })

  it('gives the unscoped classes a home of their own', () => {
    // Entities and facet values are deliberately unscoped in the data model — they
    // outlive the change that named them — so without this they drift through the middle.
    const nodes = [g('e', 'entity'), g('f', 'facet_value')]
    assignGroups(nodes, [])
    expect(nodes[0].group).toBe(GROUP.DOMAIN)
    expect(nodes[1].group).toBe(GROUP.FACETS)
    expect(nodes[0].group).not.toBe(nodes[1].group)
  })

  it('ignores a SCOPED_TO edge that does not come from a slice', () => {
    const nodes = [g('x', 'decision'), g('a', 'decision')]
    assignGroups(nodes, [{ source: 'x', target: 'a', type: 'SCOPED_TO' }])
    expect(nodes[1].group).toBeNull()
  })

  it('works after the layout has hydrated link endpoints into objects', () => {
    const nodes = [g('s1', 'slice'), g('a', 'decision')]
    assignGroups(nodes, [{ source: { id: 's1' }, target: { id: 'a' }, type: 'SCOPED_TO' }])
    expect(nodes[1].group).toBe('s1')
  })
})

describe('link layout weights', () => {
  it('covers every edge type', () => {
    for (const type of EDGE_TYPES) expect(LINK_LAYOUT[type], type).toBeDefined()
  })

  it('holds a change together and lets findability edges go slack', () => {
    // SCOPED_TO is membership — it is what a group is. HAS_FACET is findability only and
    // one facet here reaches ten scopes, so at full strength it drags them into one blob.
    expect(LINK_LAYOUT.SCOPED_TO.strength).toBeGreaterThan(LINK_LAYOUT.DEPENDS_ON.strength)
    expect(LINK_LAYOUT.HAS_FACET.strength).toBeLessThan(LINK_LAYOUT.DEPENDS_ON.strength / 5)
    expect(LINK_LAYOUT.HAS_FACET.distance).toBeGreaterThan(LINK_LAYOUT.SCOPED_TO.distance * 3)
  })

  it('pushes nodes apart rather than pulling them together', () => {
    expect(LAYOUT.charge).toBeLessThan(0)
  })
})

describe('group anchors', () => {
  const n = (id, group) => ({ id, group })

  it('places every group somewhere distinct', () => {
    const anchors = groupAnchors([n('a', 'g1'), n('b', 'g2'), n('c', 'g3')])
    const places = [...anchors.values()].map((p) => `${p.x.toFixed(2)},${p.y.toFixed(2)}`)
    expect(new Set(places).size).toBe(3)
  })

  it('is deterministic: the same graph arranges the same way every time', () => {
    // A layout that reshuffles on reload cannot be learned, and determinism is a property
    // this project holds everywhere else.
    const nodes = [n('a', 'g2'), n('b', 'g1'), n('c', 'g3')]
    const first = groupAnchors(nodes)
    const second = groupAnchors([...nodes].reverse())
    for (const key of first.keys()) expect(second.get(key)).toEqual(first.get(key))
  })

  it('ignores ungrouped nodes', () => {
    expect(groupAnchors([n('a', null), n('b', undefined)]).size).toBe(0)
  })

  it('spreads further as groups multiply, so rings do not crowd', () => {
    const few = groupAnchors(Array.from({ length: 3 }, (_, i) => n(`n${i}`, `g${i}`)))
    const many = groupAnchors(Array.from({ length: 30 }, (_, i) => n(`n${i}`, `g${i}`)))
    const spread = (a) => Math.max(...[...a.values()].map((p) => Math.hypot(p.x, p.y)))
    expect(spread(many)).toBeGreaterThan(spread(few))
  })
})

describe('the cluster force', () => {
  const withPos = (id, group, x, y) => ({ id, group, x, y, vx: 0, vy: 0 })
  const run = (nodes, alpha = 1) => {
    const force = clusterForce(1)
    force.initialize(nodes)
    force(alpha)
    return nodes
  }

  it('leaves a node that is inside its group’s room completely alone', () => {
    // The point of a containment force: a plain attractor collapses a group onto one
    // spot, which is exactly what stacked nine facet labels in one place.
    const anchor = groupAnchors([withPos('a', 'g1', 0, 0)]).get('g1')
    const nodes = run([withPos('a', 'g1', anchor.x, anchor.y)])
    expect(nodes[0].vx).toBe(0)
    expect(nodes[0].vy).toBe(0)
  })

  it('pulls back a node that has wandered out of its region', () => {
    const anchor = groupAnchors([withPos('a', 'g1', 0, 0)]).get('g1')
    const nodes = run([withPos('a', 'g1', anchor.x + 900, anchor.y)])
    expect(nodes[0].vx).toBeLessThan(0)
  })

  it('pulls harder the further out a node has drifted', () => {
    const anchor = groupAnchors([withPos('a', 'g1', 0, 0)]).get('g1')
    const near = run([withPos('a', 'g1', anchor.x + 200, anchor.y)])
    const far = run([withPos('a', 'g1', anchor.x + 900, anchor.y)])
    expect(Math.abs(far[0].vx)).toBeGreaterThan(Math.abs(near[0].vx))
  })

  it('gives a bigger group more room', () => {
    const small = [withPos('a', 'g1', 0, 0)]
    const big = Array.from({ length: 16 }, (_, i) => withPos(`n${i}`, 'g1', 0, 0))
    const roomOf = (nodes) => {
      const anchor = groupAnchors(nodes).get('g1')
      // Walk outward until the force starts acting: that distance is the allowance.
      for (let d = 5; d < 400; d += 5) {
        const probe = nodes.map((n) => ({ ...n, x: anchor.x + d, y: anchor.y, vx: 0, vy: 0 }))
        const force = clusterForce(1)
        force.initialize(probe)
        force(1)
        if (probe[0].vx !== 0) return d
      }
      return Infinity
    }
    expect(roomOf(big)).toBeGreaterThan(roomOf(small))
  })

  it('keeps two groups in different places, which is what keeps them apart', () => {
    const nodes = run([withPos('a', 'g1', 0, 0), withPos('b', 'g2', 0, 0)])
    expect([nodes[0].vx, nodes[0].vy]).not.toEqual([nodes[1].vx, nodes[1].vy])
  })

  it('leaves an ungrouped node alone', () => {
    const nodes = run([withPos('a', null, 900, 900), withPos('b', 'g1', 0, 0)])
    expect(nodes[0].vx).toBe(0)
    expect(nodes[0].vy).toBe(0)
  })

  it('does not touch the third dimension', () => {
    const nodes = [{ id: 'a', group: 'g1', x: 900, y: 900, z: 5, vx: 0, vy: 0, vz: 0 }]
    run(nodes)
    expect(nodes[0].vz).toBe(0)
  })

  it('scales with alpha, so it fades as the layout settles', () => {
    const anchor = groupAnchors([withPos('a', 'g1', 0, 0)]).get('g1')
    const hot = run([withPos('a', 'g1', anchor.x + 900, anchor.y)], 1)
    const cold = run([withPos('a', 'g1', anchor.x + 900, anchor.y)], 0.1)
    expect(Math.abs(hot[0].vx)).toBeGreaterThan(Math.abs(cold[0].vx))
  })
})
