/**
 * The rendering that WebGL could never have: a 2D canvas context is a plain object, so
 * what the view draws can be asserted instead of squinted at.
 */
import { describe, expect, it } from 'vitest'
import {
  MIN_HIT_RADIUS, POLYGON, drawNode, labelFor, paintPointerArea, tracePolygon,
} from './graph-draw.js'
import { EDGE_STYLE, STATUS, TIER_SIZE, sizeFor } from './graph-encoding.js'

/** Records every call and property set, so a drawing can be inspected after the fact. */
function recordingContext() {
  const calls = []
  const state = {}
  const record = (name) => (...args) => calls.push({ name, args })
  return new Proxy(
    { calls, state },
    {
      get(target, prop) {
        if (prop === 'calls' || prop === 'state') return target[prop]
        if (prop in state) return state[prop]
        return record(prop)
      },
      set(target, prop, value) {
        state[prop] = value
        calls.push({ name: `set:${prop}`, args: [value] })
        return true
      },
    },
  )
}

const node = (over = {}) => ({
  id: 'n', type: 'decision', tier: 'short-term', path: '/artifact/a-decision',
  x: 10, y: 20, needs_review: false, archived: false, degree: 0, ...over,
})

const called = (ctx, name) => ctx.calls.filter((c) => c.name === name)
const setTo = (ctx, prop) => ctx.calls.filter((c) => c.name === `set:${prop}`).map((c) => c.args[0])

const TYPES = Object.keys(POLYGON)

describe('silhouettes', () => {
  it('gives every node type a polygon spec', () => {
    for (const type of TYPES) expect(POLYGON[type]).toBeDefined()
  })

  it('draws distinguishable outlines, not all circles', () => {
    // Shape is the channel carrying type. If they all traced a circle the encoding would
    // be gone and only the legend would say otherwise.
    const traced = new Set()
    for (const type of TYPES) {
      const ctx = recordingContext()
      drawNode(ctx, node({ type }))
      const arcs = called(ctx, 'arc').length
      const lines = called(ctx, 'lineTo').length
      traced.add(`${arcs > 0 ? 'round' : ''}${lines}`)
    }
    expect(traced.size).toBeGreaterThan(4)
  })

  it('traces a circle when the spec has no sides', () => {
    const ctx = recordingContext()
    tracePolygon(ctx, 0, 0, 5, { sides: 0 })
    expect(called(ctx, 'arc')).toHaveLength(1)
  })

  it('traces n corners for an n-gon and closes the path', () => {
    const ctx = recordingContext()
    tracePolygon(ctx, 0, 0, 5, { sides: 6, rotation: 0 })
    expect(called(ctx, 'moveTo')).toHaveLength(1)
    expect(called(ctx, 'lineTo')).toHaveLength(5)
    expect(called(ctx, 'closePath')).toHaveLength(1)
  })

  it('outlines a change scope instead of filling it', () => {
    const ctx = recordingContext()
    const drawn = drawNode(ctx, node({ type: 'slice' }))
    expect(called(ctx, 'stroke').length).toBeGreaterThan(0)
    expect(drawn.shape).toBe('wireframe-box')
  })
})

describe('status is never colour alone', () => {
  it('rings a flagged node in the reserved critical colour', () => {
    const ctx = recordingContext()
    const drawn = drawNode(ctx, node({ needs_review: true }))
    expect(drawn.ring).toBe(true)
    expect(setTo(ctx, 'strokeStyle')).toContain(STATUS.critical)
  })

  it('leaves an unflagged node unringed', () => {
    const ctx = recordingContext()
    expect(drawNode(ctx, node()).ring).toBe(false)
    expect(setTo(ctx, 'strokeStyle')).not.toContain(STATUS.critical)
  })
})

describe('archived nodes recede rather than vanish', () => {
  it('draws them faded', () => {
    const ctx = recordingContext()
    drawNode(ctx, node({ archived: true }))
    expect(setTo(ctx, 'globalAlpha')).toContain(0.3)
  })

  it('draws a live node at full opacity', () => {
    const ctx = recordingContext()
    drawNode(ctx, node())
    expect(setTo(ctx, 'globalAlpha')).toContain(1)
  })
})

describe('labels', () => {
  it('always labels a promoted node, at any zoom', () => {
    // The durable ones are the ones worth reading at a glance.
    expect(labelFor(node({ tier: 'long-term' }), 0.2)).toBe('a-decision')
    expect(labelFor(node({ tier: 'lifetime' }), 0.2)).toBe('a-decision')
  })

  it('labels an ordinary node only once it is zoomed well in', () => {
    expect(labelFor(node({ tier: 'short-term' }), 0.5)).toBeNull()
    // A fitted whole-graph view already sits above 1.6, so a threshold there labels
    // everything at once and the centre becomes unreadable. It has to mean "zoomed into
    // a neighbourhood", not merely "zoomed in".
    expect(labelFor(node({ tier: 'short-term' }), 2)).toBeNull()
    expect(labelFor(node({ tier: 'short-term' }), 5)).toBe('a-decision')
  })

  it('shows the distinguishing end of the path, not the repeated prefix', () => {
    expect(labelFor(node({ tier: 'lifetime', path: '/change/store-doctor' }), 1)).toBe('store-doctor')
  })

  it('survives a node with no path', () => {
    expect(labelFor(node({ tier: 'lifetime', path: '' }), 3)).toBeNull()
  })

  it('draws label text in text ink, never the mark colour', () => {
    const ctx = recordingContext()
    drawNode(ctx, node({ tier: 'lifetime' }), { mode: 'dark', globalScale: 1 })
    const fills = setTo(ctx, 'fillStyle')
    expect(fills).toContain('#c3c2b7')
    expect(called(ctx, 'fillText')).toHaveLength(1)
  })
})

describe('size still means tier — which is the whole reason for 2D', () => {
  it('draws a bigger mark for a higher tier', () => {
    const at = (tier) => drawNode(recordingContext(), node({ tier })).radius
    expect(at('short-term')).toBeLessThan(at('lifetime'))
    expect(at('lifetime')).toBe(sizeFor(node({ tier: 'lifetime' })))
  })

  it('is unaffected by anything but the node itself', () => {
    // In 3D apparent size was size x distance, so this encoding could not survive the
    // projection. On a plane the radius drawn is the radius meant.
    const a = drawNode(recordingContext(), node({ tier: 'mid-term' }), { globalScale: 0.2 })
    const b = drawNode(recordingContext(), node({ tier: 'mid-term' }), { globalScale: 5 })
    expect(a.radius).toBe(b.radius)
    expect(a.radius).toBeCloseTo(TIER_SIZE['mid-term'], 5)
  })
})

describe('selection and hit targets', () => {
  it('haloes the selected node', () => {
    expect(drawNode(recordingContext(), node(), { selected: true }).halo).toBe(true)
    expect(drawNode(recordingContext(), node(), { selected: false }).halo).toBe(false)
  })

  it('paints a hit target larger than the mark', () => {
    const ctx = recordingContext()
    // Argument order is the library's: (node, colour, ctx).
    paintPointerArea(node({ tier: 'short-term' }), '#abc', ctx)
    const [, , radius] = called(ctx, 'arc')[0].args
    expect(radius).toBeGreaterThan(sizeFor(node({ tier: 'short-term' })))
  })

  it('keeps a clickable target even for the smallest mark', () => {
    const ctx = recordingContext()
    paintPointerArea({ ...node(), tier: 'short-term', degree: 0 }, '#abc', ctx)
    expect(called(ctx, 'arc')[0].args[2]).toBeGreaterThanOrEqual(6)
  })
})

describe('the context is left as it was found', () => {
  it('balances save and restore', () => {
    const ctx = recordingContext()
    drawNode(ctx, node({ needs_review: true, tier: 'lifetime', archived: true }))
    expect(called(ctx, 'save')).toHaveLength(1)
    expect(called(ctx, 'restore')).toHaveLength(1)
  })
})

describe('the hit target — why nodes felt unclickable', () => {
  it('is at least a comfortable size on screen however far out you are zoomed', () => {
    // The old radius was ~3 screen pixels at a fitted zoom: you had to hit a three-pixel
    // disc. Dividing the floor by globalScale turns a screen distance into graph units.
    const ctx = recordingContext()
    paintPointerArea(node({ tier: 'short-term' }), '#abc', ctx, 0.2)
    const radius = called(ctx, 'arc')[0].args[2]
    expect(radius * 0.2).toBeGreaterThanOrEqual(MIN_HIT_RADIUS - 0.001)
  })

  it('grows with the mark once the mark is the bigger of the two', () => {
    const ctx = recordingContext()
    paintPointerArea(node({ tier: 'lifetime' }), '#abc', ctx, 4)
    expect(called(ctx, 'arc')[0].args[2]).toBeGreaterThan(sizeFor(node({ tier: 'lifetime' })))
  })

  it('never collapses when the scale is missing or zero', () => {
    for (const scale of [undefined, 0]) {
      const ctx = recordingContext()
      paintPointerArea(node(), '#abc', ctx, scale)
      expect(called(ctx, 'arc')[0].args[2]).toBeGreaterThanOrEqual(MIN_HIT_RADIUS)
    }
  })
})

describe('edge styles are told apart without spending hues', () => {
  it('gives the non-status types distinct dash patterns', () => {
    const neutral = Object.entries(EDGE_STYLE).filter(([type]) => type !== 'CONTRADICTS')
    const patterns = neutral.map(([, s]) => JSON.stringify(s.dash))
    expect(new Set(patterns).size).toBe(neutral.length)
  })

  it('names every type for the legend', () => {
    for (const [type, style] of Object.entries(EDGE_STYLE)) {
      expect(style.label, type).toBeTruthy()
    }
  })

  it('draws membership and findability more faintly than content', () => {
    expect(EDGE_STYLE.SCOPED_TO.width).toBeLessThan(EDGE_STYLE.DEPENDS_ON.width)
    expect(EDGE_STYLE.HAS_FACET.width).toBeLessThan(EDGE_STYLE.DEPENDS_ON.width)
    expect(EDGE_STYLE.CONTRADICTS.width).toBeGreaterThan(EDGE_STYLE.DEPENDS_ON.width)
  })
})
