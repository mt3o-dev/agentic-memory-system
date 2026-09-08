/**
 * Drawing a memory node on a 2D canvas.
 *
 * Separated from the view for two reasons. It is the part with real logic — silhouettes,
 * the status ring, when a label is worth the ink — and, unlike the WebGL path it replaces,
 * it is **testable**: a recording 2D context is a plain object, so what gets drawn can be
 * asserted rather than eyeballed.
 *
 * Why 2D at all, having built 3D first: **perspective destroys the size channel.** Size
 * encodes tier, and under a perspective projection apparent size is size x distance, so a
 * `lifetime` node at the back of the scene is indistinguishable from a `short-term` node
 * at the front. Add occlusion — nodes simply hidden behind other nodes — and labels that
 * cannot be shown without z-fighting, and the third dimension costs more than it pays on a
 * graph this size.
 */
import { CLASS_OF, SHAPE_OF, STATUS, colorFor, sizeFor } from './graph-encoding.js'

/**
 * Nine distinct silhouettes. Shape carries type because colour cannot: a force graph is an
 * all-pairs colour form, capped at three hues (see graph-encoding.js), so the nine types
 * have to be told apart some other way.
 */
export const POLYGON = {
  decision: { sides: 0 },                       // circle
  concept: { sides: 4, rotation: 0 },           // diamond (vertex up)
  constraint: { sides: 4, rotation: Math.PI / 4 }, // square (flat top)
  issue: { sides: 3, rotation: -Math.PI / 2 },  // triangle up
  invariant: { sides: 6, rotation: 0 },         // hexagon
  entity: { sides: 5, rotation: -Math.PI / 2 }, // pentagon
  goal: { sides: 0, halo: true },               // circle with a second ring
  slice: { sides: 4, rotation: Math.PI / 4, hollow: true }, // outlined square
  facet_value: { sides: 3, rotation: Math.PI / 2 },         // triangle down
}

/** Tiers at or above this always carry their label: they are the promoted, durable ones. */
const ALWAYS_LABELLED = new Set(['long-term', 'lifetime'])
/**
 * Below this zoom, labelling everything is unreadable overlapping mush — which is exactly
 * what the first render produced at 1.6, because a fitted graph already sits above that.
 * The threshold is not "can you read one label" but "are the nodes far enough apart on
 * screen that a hundred of them will not collide", which is a much higher bar.
 */
const LABEL_ZOOM = 4

export function tracePolygon(ctx, x, y, radius, spec) {
  ctx.beginPath()
  if (!spec || !spec.sides) {
    ctx.arc(x, y, radius, 0, 2 * Math.PI)
    return
  }
  const { sides, rotation = 0 } = spec
  for (let i = 0; i < sides; i += 1) {
    const angle = rotation + (i * 2 * Math.PI) / sides
    const px = x + radius * Math.cos(angle)
    const py = y + radius * Math.sin(angle)
    if (i === 0) ctx.moveTo(px, py)
    else ctx.lineTo(px, py)
  }
  ctx.closePath()
}

export function labelFor(node, globalScale) {
  // The path's last segment: the whole path is long and mostly repeated prefix, and the
  // distinguishing part is at the end.
  const short = String(node.path || '').split('/').filter(Boolean).pop() || ''
  if (!short) return null
  if (ALWAYS_LABELLED.has(node.tier)) return short
  return globalScale >= LABEL_ZOOM ? short : null
}

/**
 * Draw one node. Returns what it drew, which is what the tests assert on — a drawing
 * function that reports nothing can only be checked by looking at it.
 */
export function drawNode(ctx, node, { mode = 'dark', globalScale = 1, selected = false } = {}) {
  const radius = sizeFor(node)
  const spec = POLYGON[node.type] || POLYGON.decision
  const drawn = { shape: SHAPE_OF[node.type], radius, ring: false, halo: false, label: null }

  ctx.save()
  // Archived nodes recede rather than vanish: dormant is not deleted, and a view that
  // hides them cannot be used to audit what went dormant.
  ctx.globalAlpha = node.archived ? 0.3 : 1

  if (selected) {
    ctx.beginPath()
    ctx.arc(node.x, node.y, radius * 2.1, 0, 2 * Math.PI)
    ctx.fillStyle = 'rgba(255,255,255,0.16)'
    ctx.fill()
    drawn.halo = true
  }

  tracePolygon(ctx, node.x, node.y, radius, spec)
  if (spec.hollow) {
    // A change scope is an anchor, not content — hollow says "container" without
    // spending a hue on it.
    ctx.strokeStyle = colorFor(node, mode)
    ctx.lineWidth = Math.max(1, radius / 4)
    ctx.stroke()
  } else {
    ctx.fillStyle = colorFor(node, mode)
    ctx.fill()
  }

  if (spec.halo) {
    ctx.beginPath()
    ctx.arc(node.x, node.y, radius * 1.45, 0, 2 * Math.PI)
    ctx.strokeStyle = colorFor(node, mode)
    ctx.lineWidth = Math.max(1, radius / 5)
    ctx.stroke()
  }

  if (node.needs_review) {
    // A ring, in the reserved status colour. Status is never carried by colour alone —
    // the ring is the shape half of that, and the legend names it.
    ctx.beginPath()
    ctx.arc(node.x, node.y, radius * 1.7, 0, 2 * Math.PI)
    ctx.strokeStyle = STATUS.critical
    ctx.lineWidth = Math.max(1.2, radius / 3.5)
    ctx.stroke()
    drawn.ring = true
  }

  const label = labelFor(node, globalScale)
  if (label) {
    const size = Math.max(9, 11 / globalScale)
    ctx.font = `${size}px ui-sans-serif, system-ui, sans-serif`
    ctx.textAlign = 'center'
    ctx.textBaseline = 'top'
    // Text wears text ink, never the series colour — the mark beside it carries identity.
    ctx.fillStyle = mode === 'light' ? '#52514e' : '#c3c2b7'
    ctx.fillText(label, node.x, node.y + radius * 1.9)
    drawn.label = label
  }

  ctx.restore()
  return drawn
}

/** Below this many screen pixels a target is a coin toss, whatever the zoom. */
export const MIN_HIT_RADIUS = 12

/**
 * The click target, in graph units — and never smaller than {@link MIN_HIT_RADIUS} on
 * screen, whatever the zoom.
 *
 * This is why nodes felt unclickable. The radius used to be `max(size * 1.8, 6)` in graph
 * units, and with the old sizes that was about three screen pixels at a fitted zoom: you
 * had to hit a three-pixel disc. Dividing the floor by `globalScale` converts a screen
 * distance into graph units, so the target stays the same comfortable size on screen as
 * you zoom out.
 *
 * The argument order is the library's, not a choice — `nodePointerAreaPaint` calls back
 * with `(node, colour, ctx, globalScale)`. Getting it wrong hands a node where a context
 * belongs and throws `beginPath is not a function` on the first frame, which inside an
 * async mount is swallowed and leaves an empty canvas with no error on screen.
 */
export function paintPointerArea(node, color, ctx, globalScale = 1) {
  const radius = Math.max(sizeFor(node) * 1.35, MIN_HIT_RADIUS / (globalScale || 1))
  ctx.fillStyle = color
  ctx.beginPath()
  ctx.arc(node.x, node.y, radius, 0, 2 * Math.PI)
  ctx.fill()
}

export const CLASS_OF_TYPE = CLASS_OF
