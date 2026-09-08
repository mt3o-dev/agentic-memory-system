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

/**
 * Ordinal: a promoted node is a bigger mark. Tier is *lifespan* — how long this knowledge
 * is expected to outlive the change that made it — and it is the human curation axis, so
 * it gets the size channel to itself.
 *
 * These are graph units, and the previous values (3.2 to 7.4) were far too small against
 * link distances of 45 to 260: nodes drew as two-pixel dots at a fitted zoom, the four
 * tiers were indistinguishable, and the click target was about three screen pixels — which
 * is why nodes felt unclickable. Roughly tripled, with wider gaps between steps.
 */
export const TIER_SIZE = {
  'short-term': 7,
  'mid-term': 11.5,
  'long-term': 17,
  lifetime: 24,
}

/**
 * Edge types, told apart by **dash pattern and weight** rather than by colour.
 *
 * Six edge hues would be a second categorical scale competing with the node one, and the
 * all-pairs limit that caps node colour at three applies to lines just as much. Dash is a
 * texture channel: it is free of that constraint, survives colour-vision deficiency
 * completely, and still prints. Only CONTRADICTS takes a hue, because it is the one edge
 * that means *something is wrong here* — a status, not a category.
 *
 * `dash` is in graph units, so patterns hold their proportions as you zoom.
 */
export const EDGE_STYLE = {
  CONTRADICTS: { color: STATUS.critical, width: 2.2, dash: null, label: 'contradicts' },
  DEPENDS_ON: { color: '#9a9a95', width: 1.4, dash: null, label: 'depends on' },
  ABOUT: { color: '#9a9a95', width: 1.2, dash: [7, 4], label: 'about' },
  CONSOLIDATES: { color: '#9a9a95', width: 1.2, dash: [10, 3, 2, 3], label: 'consolidates' },
  SCOPED_TO: { color: '#6c6c68', width: 0.9, dash: [2, 4], label: 'scoped to (membership)' },
  HAS_FACET: { color: '#5f5f5c', width: 0.7, dash: [1, 6], label: 'has facet (findability)' },
}

export function colorFor(node, mode) {
  const palette = CLASS_COLORS[mode] || CLASS_COLORS.dark
  return palette[CLASS_OF[node.type] || 'structure']
}

export function sizeFor(node) {
  // Size is tier and nothing else. It previously carried a small degree component so hubs
  // read as hubs, which muddied the one thing size is supposed to say: a busy short-term
  // note and a quiet mid-term one came out nearly the same size. Connectivity is now
  // expressed by the layout — well-connected nodes cluster — which is where it belongs.
  return TIER_SIZE[node.tier] ?? TIER_SIZE['short-term']
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


/**
 * Per-edge-type layout weights — the thing that decides whether the picture has groups.
 *
 * The edge types do not describe one kind of relationship, and treating them alike is why
 * the first layout was a single hairball. `SCOPED_TO` is *membership*: it ties a change's
 * artifacts to their change, and it is what a group IS. `HAS_FACET` is the opposite —
 * findability only, never walked by the retrieval walker — and one facet in this project's
 * own graph touches **ten different change scopes**, so at full strength it drags ten
 * clusters into one point. It gets distance and almost no pull: still visible, no longer
 * steering.
 */
export const LINK_LAYOUT = {
  SCOPED_TO: { distance: 45, strength: 0.9 },
  DEPENDS_ON: { distance: 95, strength: 0.35 },
  ABOUT: { distance: 120, strength: 0.18 },
  CONTRADICTS: { distance: 130, strength: 0.14 },
  CONSOLIDATES: { distance: 150, strength: 0.06 },
  HAS_FACET: { distance: 260, strength: 0.02 },
}

export const LAYOUT = {
  // Repulsion. The default is far too gentle for a graph this connected — nodes ended up
  // overlapping in a ball where nothing could be told apart or clicked.
  charge: -340,
  chargeDistanceMax: 900,
  // How hard a node that has wandered out of its group's region is pulled back.
  cluster: 0.9,
  // Room each group is given, per sqrt(member): the area a group needs grows with its
  // membership, and inside that room repulsion is left to do the spacing.
  groupRoom: 26,
}

/**
 * Find communities — sets of nodes that are densely connected to each other — by weighted
 * label propagation.
 *
 * Grouping used to be "what type is this node", which is not a grouping at all: it puts a
 * decision from one change beside an unrelated decision from another purely because both
 * are decisions. What actually belongs together is what is *linked* together, so that is
 * what this computes.
 *
 * Edges are weighted by what they mean, reusing the layout weights. `SCOPED_TO` dominates,
 * because membership of a change is the strongest statement of belonging the data makes;
 * `HAS_FACET` counts for almost nothing, because a facet is findability and one of them
 * here touches ten different change scopes — let it vote and every community merges into
 * one.
 *
 * Label propagation, not something cleverer, because it is O(edges) per round, needs no
 * tuning, and — with nodes visited in sorted order and ties broken by the smallest label —
 * is **deterministic**, so the same graph groups the same way on every reload.
 */
export function detectCommunities(nodes, links, { rounds = 12 } = {}) {
  const neighbours = new Map(nodes.map((n) => [n.id, []]))
  for (const link of links) {
    const a = endpointId(link.source)
    const b = endpointId(link.target)
    if (!neighbours.has(a) || !neighbours.has(b)) continue
    const weight = (LINK_LAYOUT[link.type] || LINK_LAYOUT.DEPENDS_ON).strength
    neighbours.get(a).push([b, weight])
    neighbours.get(b).push([a, weight])
  }

  const label = new Map(nodes.map((n) => [n.id, n.id]))
  const order = [...nodes].map((n) => n.id).sort()

  for (let round = 0; round < rounds; round += 1) {
    let moved = false
    for (const id of order) {
      const scores = new Map()
      for (const [other, weight] of neighbours.get(id) || []) {
        const key = label.get(other)
        scores.set(key, (scores.get(key) || 0) + weight)
      }
      if (!scores.size) continue
      let best = null
      let bestScore = -Infinity
      // Sorted keys plus a strict `>` makes the tie-break the smallest label, which is
      // what keeps this stable across runs rather than dependent on Map order.
      for (const key of [...scores.keys()].sort()) {
        const score = scores.get(key)
        if (score > bestScore) {
          bestScore = score
          best = key
        }
      }
      if (best !== label.get(id)) {
        label.set(id, best)
        moved = true
      }
    }
    if (!moved) break
  }
  return label
}

/**
 * Assign each node a layout group, mutating them in place (which is what the force needs).
 *
 * The group is its community. A node with no edges at all keeps a group of its own rather
 * than being herded in with other unconnected nodes, which would say something false.
 */
export function assignGroups(nodes, links) {
  const communities = detectCommunities(nodes, links)
  for (const node of nodes) node.group = communities.get(node.id) ?? node.id
  return nodes
}

/**
 * Deterministic anchor per group, placed evenly on a circle.
 *
 * The first attempt pulled each node toward its group's *centroid*, which holds a group
 * together and does nothing whatever to keep groups apart — they simply overlap, and the
 * picture stays one even mesh. Anchors fix the groups in place relative to each other, so
 * separation is guaranteed rather than hoped for.
 *
 * Groups are sorted by id before being placed, so the same graph produces the same
 * arrangement every time: a layout that reshuffles on reload cannot be learned, and this
 * project treats determinism as a property worth having everywhere else too.
 */
export function groupAnchors(nodes, radius) {
  const groups = [...new Set(nodes.map((n) => n.group).filter(Boolean))].sort()
  const anchors = new Map()
  if (!groups.length) return anchors
  // Grows with the group count so a busy graph does not crowd its own rings together.
  const r = radius ?? Math.max(200, 72 * Math.sqrt(groups.length))
  groups.forEach((group, index) => {
    const angle = (index / groups.length) * 2 * Math.PI
    anchors.set(group, { x: Math.cos(angle) * r, y: Math.sin(angle) * r })
  })
  return anchors
}

/**
 * Keep each group in its own region — without collapsing it to a point.
 *
 * A plain attractor pulls every member onto the anchor, and for a group with many members
 * that beats the repulsion holding them apart: the nine facet values landed exactly on top
 * of one another, nine labels stacked in one place. So this is a *containment* force. A
 * node inside its group's allowance is left entirely alone, and charge spreads it out as
 * usual; only a node that has wandered outside is pulled back, in proportion to how far.
 *
 * The allowance grows with the square root of the group's size, which is how the area a
 * group needs grows with its membership.
 *
 * Only x and y are constrained. In 3D the third axis stays free, so a group reads as a
 * cloud in a fixed place rather than a flat disc.
 */
export function clusterForce(strength = LAYOUT.cluster) {
  let nodes = []
  let anchors = new Map()
  let allowance = new Map()
  function force(alpha) {
    const k = alpha * strength
    for (const node of nodes) {
      const anchor = anchors.get(node.group)
      if (!anchor) continue
      const dx = anchor.x - (node.x || 0)
      const dy = anchor.y - (node.y || 0)
      const distance = Math.hypot(dx, dy)
      const allowed = allowance.get(node.group) || 0
      if (distance <= allowed || distance === 0) continue
      const pull = (k * (distance - allowed)) / distance
      node.vx += dx * pull
      node.vy += dy * pull
    }
  }
  force.initialize = (initial) => {
    nodes = initial
    anchors = groupAnchors(initial)
    const counts = new Map()
    for (const node of initial) {
      if (!node.group) continue
      counts.set(node.group, (counts.get(node.group) || 0) + 1)
    }
    allowance = new Map(
      [...counts].map(([group, count]) => [group, LAYOUT.groupRoom * Math.sqrt(count)]),
    )
  }
  return force
}


/**
 * The node nearest a point, within a radius. Graph coordinates throughout.
 *
 * The 2D view does its own hit-testing rather than trusting the library's pointer-area
 * canvas. Measured, that canvas gave a click target about **4 pixels wide** no matter what
 * `nodePointerAreaPaint` painted — a 12-screen-pixel disc went in and a near-point came
 * out — so nodes were only clickable if you hit their exact centre, which is what "it
 * doesn't register me clicking" was.
 *
 * Ties are broken by id so a click between two coincident nodes always picks the same one,
 * rather than depending on iteration order.
 */
export function nearestNode(nodes, x, y, maxDistance) {
  let best = null
  let bestDistance = Infinity
  for (const node of nodes) {
    const distance = Math.hypot((node.x ?? Infinity) - x, (node.y ?? Infinity) - y)
    // A node's own mark should always be clickable even where it exceeds the radius.
    if (distance > Math.max(maxDistance, sizeFor(node))) continue
    if (distance < bestDistance || (distance === bestDistance && best && node.id < best.id)) {
      best = node
      bestDistance = distance
    }
  }
  return best
}
