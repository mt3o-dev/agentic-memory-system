/**
 * What can and cannot be tested here.
 *
 * jsdom has no WebGL, so nothing below renders a single pixel — the geometry, the camera
 * and the bloom pass are out of reach and stay verified by rendering the page in a real
 * browser and looking at it. What IS reachable is the part that changes the graph: which
 * endpoint each control calls, with what payload, and whether the safety gates hold. That
 * is the half worth automating, because it is the half that can corrupt data rather than
 * just look wrong.
 *
 * `3d-force-graph` and `three` are mocked with a chainable recorder, which also captures
 * the event handlers the component registers — so a node click can be simulated by
 * invoking the handler the component actually gave the library.
 */
import { cleanup, render, screen, waitFor } from '@testing-library/svelte'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const handlers = {}
let graphData = null

/**
 * Chainable stand-in: every config call returns the graph, the way the real builder does.
 *
 * The recorder must return the PROXY, not the underlying object — returning the target
 * breaks the chain at the second call, because the plain object has none of the methods.
 */
function makeGraph() {
  let proxy
  const target = {
    graphData: (data) => {
      if (data === undefined) return graphData || { nodes: [], links: [] }
      graphData = data
      return proxy
    },
    camera: () => ({ fov: 50 }),
    controls: () => ({}),
    postProcessingComposer: () => ({ addPass: () => {} }),
    cameraPosition: () => proxy,
    _destructor: () => {},
  }
  proxy = new Proxy(target, {
    get(obj, prop) {
      if (prop in obj) return obj[prop]
      return (arg) => {
        // Record the handlers so tests can fire them.
        if (typeof prop === 'string' && prop.startsWith('on') && typeof arg === 'function') {
          handlers[prop] = arg
        }
        return proxy
      }
    },
  })
  return proxy
}

// A regular function, not an arrow: the component calls it with `new`, and an arrow
// function is not constructible. Returning an object from a constructor overrides `this`.
vi.mock('3d-force-graph', () => ({ default: function ForceGraph3D() { return makeGraph() } }))
vi.mock('three/examples/jsm/postprocessing/UnrealBloomPass.js', () => ({
  UnrealBloomPass: class { constructor() { this.strength = 0 } },
}))
vi.mock('three', () => {
  const Geometry = class {}
  return {
    Group: class { add() {} },
    Mesh: class {},
    MeshLambertMaterial: class {},
    MeshBasicMaterial: class {},
    Color: class {},
    SphereGeometry: Geometry, BoxGeometry: Geometry, ConeGeometry: Geometry,
    TorusGeometry: Geometry, OctahedronGeometry: Geometry, DodecahedronGeometry: Geometry,
    IcosahedronGeometry: Geometry, TetrahedronGeometry: Geometry,
  }
})

import Graph3D from './Graph3D.svelte'

const NODES = [
  {
    id: 'n1', type: 'decision', tier: 'short-term', path: '/artifact/one',
    body: 'The first decision.', needs_review: false, archived: false,
    degree: 1, trust_weight: 1, retrieval_weight: 1,
  },
  {
    id: 'n2', type: 'constraint', tier: 'mid-term', path: '/artifact/two',
    body: 'The second.', needs_review: true, archived: false,
    degree: 1, trust_weight: 0.4, retrieval_weight: 1,
  },
]
const LINKS = [{ source: 'n1', target: 'n2', type: 'DEPENDS_ON' }]

let calls = []

beforeEach(() => {
  calls = []
  graphData = null
  for (const key of Object.keys(handlers)) delete handlers[key]
  globalThis.ResizeObserver = class { observe() {} disconnect() {} }
  globalThis.fetch = vi.fn(async (url, init) => {
    calls.push({ url, method: init?.method || 'GET', body: init?.body ? JSON.parse(init.body) : null })
    if (String(url).startsWith('/api/graph')) {
      return { ok: true, json: async () => ({ nodes: NODES, links: LINKS }) }
    }
    return { ok: true, json: async () => ({ ok: true }) }
  })
})

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

const clickNode = async (node) => {
  await waitFor(() => expect(handlers.onNodeClick).toBeTypeOf('function'))
  handlers.onNodeClick(node)
}

describe('loading', () => {
  it('asks for the graph and hands it to the layout', async () => {
    render(Graph3D)
    await waitFor(() => expect(graphData).not.toBeNull())
    expect(calls[0].url).toBe('/api/graph')
    expect(graphData.nodes).toHaveLength(2)
    expect(graphData.links).toHaveLength(1)
  })

  it('excludes archived nodes unless asked', async () => {
    render(Graph3D)
    await waitFor(() => expect(calls.length).toBeGreaterThan(0))
    // Archived nodes are the dormant remains of merged changes; the first thing a person
    // sees should not be mostly history.
    expect(calls[0].url).not.toContain('archived=1')

    await screen.getByLabelText('show archived').click()
    await waitFor(() => expect(calls.some((c) => c.url.includes('archived=1'))).toBe(true))
  })
})

describe('the edit panel', () => {
  it('opens on a node click and shows that node', async () => {
    render(Graph3D)
    await clickNode(NODES[0])
    // Scoped: the path also appears in the add-edge datalist, so a bare text query
    // matches twice and says nothing about the panel.
    expect(await screen.findByText('/artifact/one', { selector: '.fw-semibold' })).toBeTruthy()
    expect(screen.getByLabelText('Body').value).toBe('The first decision.')
  })

  it('saves the body to the node it is showing', async () => {
    const user = render(Graph3D)
    await clickNode(NODES[0])
    const box = await screen.findByLabelText('Body')
    box.value = 'Rewritten.'
    box.dispatchEvent(new Event('input', { bubbles: true }))
    await waitFor(() => expect(screen.getByText('Save body').disabled).toBe(false))
    screen.getByText('Save body').click()

    await waitFor(() => expect(calls.some((c) => c.url === '/api/nodes/n1/body')).toBe(true))
    const save = calls.find((c) => c.url === '/api/nodes/n1/body')
    expect(save.method).toBe('POST')
    expect(save.body.body).toBe('Rewritten.')
  })

  it('will not offer to save an unedited body', async () => {
    render(Graph3D)
    await clickNode(NODES[0])
    expect((await screen.findByText('Save body')).disabled).toBe(true)
  })
})

describe('the safety gates hold in this view too', () => {
  it('asks for confirmation before a lifetime promotion, and says so to the server', async () => {
    const confirmed = vi.spyOn(globalThis, 'confirm').mockReturnValue(true)
    render(Graph3D)
    await clickNode(NODES[0])
    ;(await screen.findByText('lifetime')).click()

    await waitFor(() => expect(calls.some((c) => c.url === '/api/nodes/n1/tier')).toBe(true))
    expect(confirmed).toHaveBeenCalled()
    expect(calls.find((c) => c.url === '/api/nodes/n1/tier').body).toMatchObject({
      tier: 'lifetime',
      confirmed: true,
    })
  })

  it('reports a refused confirmation honestly rather than promoting anyway', async () => {
    vi.spyOn(globalThis, 'confirm').mockReturnValue(false)
    render(Graph3D)
    await clickNode(NODES[0])
    ;(await screen.findByText('lifetime')).click()

    await waitFor(() => expect(calls.some((c) => c.url === '/api/nodes/n1/tier')).toBe(true))
    // The server is the gate; the client must not claim a confirmation it did not get.
    expect(calls.find((c) => c.url === '/api/nodes/n1/tier').body.confirmed).toBe(false)
  })

  it('does not ask for confirmation for the other tiers', async () => {
    const confirmed = vi.spyOn(globalThis, 'confirm').mockReturnValue(true)
    render(Graph3D)
    await clickNode(NODES[0])
    ;(await screen.findByText('mid-term')).click()
    await waitFor(() => expect(calls.some((c) => c.url === '/api/nodes/n1/tier')).toBe(true))
    expect(confirmed).not.toHaveBeenCalled()
  })
})

describe('edges', () => {
  it('lists the selected node’s edges in both directions', async () => {
    render(Graph3D)
    await clickNode(NODES[1])
    // n1 -> n2, so from n2's side it is an incoming edge and must still be listed.
    expect(await screen.findByText('Edges (1)')).toBeTruthy()
    // The badge, not the <option> of the same name in the add-edge type select.
    expect(screen.getByText('DEPENDS_ON', { selector: 'span.badge' })).toBeTruthy()
  })

  it('removes an edge through the journaled endpoint', async () => {
    render(Graph3D)
    await clickNode(NODES[0])
    ;(await screen.findByTitle('Remove this edge (journaled)')).click()

    await waitFor(() => expect(calls.some((c) => c.url === '/api/edges/delete')).toBe(true))
    expect(calls.find((c) => c.url === '/api/edges/delete').body).toMatchObject({
      source: 'n1', target: 'n2', type: 'DEPENDS_ON',
    })
  })

  it('creates an edge from the selected node', async () => {
    render(Graph3D)
    await clickNode(NODES[0])
    const target = await screen.findByPlaceholderText('target node id')
    target.value = 'n2'
    target.dispatchEvent(new Event('input', { bubbles: true }))
    await waitFor(() => expect(screen.getByText('Link').disabled).toBe(false))
    screen.getByText('Link').click()

    await waitFor(() => expect(calls.some((c) => c.url === '/api/edges')).toBe(true))
    expect(calls.find((c) => c.url === '/api/edges').body).toMatchObject({
      source: 'n1', target: 'n2', type: 'DEPENDS_ON',
    })
  })

  it('will not link to nothing', async () => {
    render(Graph3D)
    await clickNode(NODES[0])
    expect((await screen.findByText('Link')).disabled).toBe(true)
  })
})

describe('failures are surfaced, not swallowed', () => {
  it('shows the server’s message when an edit is refused', async () => {
    globalThis.fetch = vi.fn(async (url) => {
      if (String(url).startsWith('/api/graph')) {
        return { ok: true, json: async () => ({ nodes: NODES, links: LINKS }) }
      }
      return { ok: false, status: 400, statusText: 'Bad Request',
               json: async () => ({ error: 'no such edge' }) }
    })
    render(Graph3D)
    await clickNode(NODES[0])
    ;(await screen.findByTitle('Remove this edge (journaled)')).click()
    expect(await screen.findByText('no such edge')).toBeTruthy()
  })
})
