<script>
  import { onMount, onDestroy } from 'svelte'
  import { get, post } from '../api.js'
  import {
    CLASS_COLORS, CLASS_LABEL, CLASS_OF, EDGE_STYLE, LAYOUT, LINK_LAYOUT, SHAPE_OF,
    STATUS, TIER_SIZE, assignCurvature, assignGroups, clusterForce, colorFor, endpointId,
    sizeFor,
  } from '../graph-encoding.js'
  import { drawNode, paintPointerArea } from '../graph-draw.js'

  let { onSelect = () => {} } = $props()

  let container
  let graph = null
  let hoveredLink = null
  let resize = null
  let THREE = null
  let bloomPass = null
  let data = $state({ nodes: [], links: [] })
  let selected = $state(null)
  let loading = $state(true)
  let error = $state(null)
  let saving = $state(null)

  // View settings. Defaults are the argued ones — see the block comments below.
  //
  // 2D is the default because 3D corrupts an encoding this view depends on: under a
  // perspective projection apparent size is size x distance, so a `lifetime` node at the
  // back is indistinguishable from a `short-term` node at the front — and size is how
  // tier is carried. Add occlusion and labels that cannot be shown without z-fighting,
  // and the third dimension costs more than it pays at this graph's size. 3D stays one
  // click away, and its 2 MB of runtime is only fetched if you ask for it.
  let dimensions = $state('2d')
  let controlType = $state('orbit')
  let autoOrbit = $state(false)
  let showArchived = $state(false)
  let highlightReview = $state(false)
  let typeFilter = $state(new Set())

  // Edit-panel drafts, kept separate from the node so an unsaved edit is visibly a draft.
  let bodyDraft = $state('')
  let newEdge = $state({ target: '', type: 'DEPENDS_ON' })

  const LINKABLE = ['DEPENDS_ON', 'CONTRADICTS', 'ABOUT', 'CONSOLIDATES']
  const TIERS = ['short-term', 'mid-term', 'long-term', 'lifetime']

  const mode = () =>
    document.documentElement.dataset.theme === 'light' ? 'light' : 'dark'

  const visibleNodes = $derived(
    typeFilter.size === 0 ? data.nodes : data.nodes.filter((n) => typeFilter.has(n.type)),
  )

  const selectedEdges = $derived(
    selected
      ? data.links
          .map((l) => ({
            source: endpointId(l.source),
            target: endpointId(l.target),
            type: l.type,
          }))
          .filter((l) => l.source === selected.id || l.target === selected.id)
      : [],
  )

  async function load() {
    loading = true
    error = null
    try {
      const payload = await get(`/api/graph${showArchived ? '?archived=1' : ''}`)
      assignCurvature(payload.links)
      assignGroups(payload.nodes, payload.links)
      data = payload
      if (graph) applyData()
    } catch (err) {
      error = err.message
    } finally {
      loading = false
    }
  }

  function applyData() {
    scheduleFrame()
    const keep = new Set(visibleNodes.map((n) => n.id))
    graph.graphData({
      nodes: visibleNodes,
      links: data.links.filter(
        (l) => keep.has(endpointId(l.source)) && keep.has(endpointId(l.target)),
      ),
    })
  }

  function geometryFor(node) {
    const r = sizeFor(node)
    switch (SHAPE_OF[node.type]) {
      case 'octahedron': return new THREE.OctahedronGeometry(r)
      case 'box': return new THREE.BoxGeometry(r * 1.5, r * 1.5, r * 1.5)
      case 'cone': return new THREE.ConeGeometry(r, r * 2, 16)
      case 'torus': return new THREE.TorusGeometry(r * 0.8, r * 0.32, 10, 20)
      case 'dodecahedron': return new THREE.DodecahedronGeometry(r)
      case 'icosahedron': return new THREE.IcosahedronGeometry(r)
      case 'wireframe-box': return new THREE.BoxGeometry(r * 1.8, r * 1.8, r * 1.8)
      case 'tetrahedron': return new THREE.TetrahedronGeometry(r)
      default: return new THREE.SphereGeometry(r, 18, 14)
    }
  }

  function meshFor(node) {
    const group = new THREE.Group()
    const flagged = node.needs_review
    const material = new THREE.MeshLambertMaterial({
      color: colorFor(node, mode()),
      transparent: node.archived,
      opacity: node.archived ? 0.28 : 1,
      wireframe: SHAPE_OF[node.type] === 'wireframe-box',
      // Bloom is opt-in and SELECTIVE: only flagged nodes emit, so the effect marks the
      // review queue instead of washing every hue toward white.
      emissive: new THREE.Color(highlightReview && flagged ? STATUS.critical : '#000000'),
      emissiveIntensity: highlightReview && flagged ? 1.4 : 0,
    })
    group.add(new THREE.Mesh(geometryFor(node), material))
    if (flagged) {
      // A ring, not just a hue: status must never be carried by colour alone.
      const r = sizeFor(node)
      const ring = new THREE.Mesh(
        new THREE.TorusGeometry(r * 1.55, r * 0.11, 8, 28),
        new THREE.MeshBasicMaterial({ color: STATUS.critical }),
      )
      ring.rotation.x = Math.PI / 2
      group.add(ring)
    }
    return group
  }

  function refreshVisuals() {
    if (!graph) return
    if (dimensions === '3d') graph.nodeThreeObject(meshFor).nodeColor((n) => colorFor(n, mode()))
    if (bloomPass) bloomPass.strength = highlightReview ? 1.7 : 0
  }

  async function select(node) {
    selected = node ? data.nodes.find((n) => n.id === node.id) || node : null
    bodyDraft = selected ? selected.body : ''
    newEdge = { target: '', type: 'DEPENDS_ON' }
    onSelect(selected?.id ?? null)
    if (node && graph) {
      if (dimensions === '3d') {
        // Fly to a point offset along the vector from origin, so the node lands centred
        // at a readable distance instead of filling the frame.
        const distance = 120
        const ratio = 1 + distance / Math.hypot(node.x || 1, node.y || 1, node.z || 1)
        graph.cameraPosition(
          { x: (node.x || 0) * ratio, y: (node.y || 0) * ratio, z: (node.z || 0) * ratio },
          node,
          900,
        )
      } else {
        graph.centerAt(node.x, node.y, 700)
        graph.zoom(2.4, 700)
      }
    }
  }

  async function act(label, fn) {
    saving = label
    error = null
    try {
      await fn()
      await load()
      if (selected) selected = data.nodes.find((n) => n.id === selected.id) || null
    } catch (err) {
      error = err.message
    } finally {
      saving = null
    }
  }

  const saveBody = () =>
    act('body', () => post(`/api/nodes/${selected.id}/body`, { body: bodyDraft }))

  const setTier = (tier) =>
    act('tier', () =>
      post(`/api/nodes/${selected.id}/tier`, {
        tier,
        // The MT3-18 checkpoint: the client asks, and says that it asked.
        confirmed:
          tier !== 'lifetime' ||
          confirm(
            'Promote to lifetime? Lifetime nodes are roots of the live set and survive ' +
              'every sweep. This is the one promotion with no automatic way back.',
          ),
        reason: 'promoted in the graph view',
      }),
    )

  const setArchived = (archived) =>
    act('archived', () => post(`/api/nodes/${selected.id}/archived`, { archived }))

  const clearFlag = () =>
    act('flag', () =>
      post(`/api/nodes/${selected.id}/clear-flag`, { reason: 'reviewed in the graph view' }),
    )

  const addEdge = () =>
    act('edge', () =>
      post('/api/edges', {
        source: selected.id,
        target: newEdge.target,
        type: newEdge.type,
        reason: 'linked in the graph view',
      }),
    )

  const removeEdge = (edge) =>
    act('edge', () =>
      post('/api/edges/delete', {
        source: edge.source,
        target: edge.target,
        type: edge.type,
        reason: 'removed in the graph view',
      }),
    )

  const pathOf = (id) => data.nodes.find((n) => n.id === id)?.path ?? id

  async function loadRenderer() {
    if (dimensions === '3d') {
      // three.js plus the 3D runtime is ~2 MB; fetched only when someone asks for 3D.
      const [{ default: ForceGraph3D }, three, { UnrealBloomPass }] = await Promise.all([
        import('3d-force-graph'),
        import('three'),
        import('three/examples/jsm/postprocessing/UnrealBloomPass.js'),
      ])
      THREE = three
      Bloom = UnrealBloomPass
      ForceGraph = ForceGraph3D
    } else {
      const { default: ForceGraph2D } = await import('force-graph')
      ForceGraph = ForceGraph2D
    }
  }

  onMount(async () => {
    await loadRenderer()
    await load()
    build()
    // Sizing is re-measured rather than trusted once. A single measurement taken while
    // the flex layout was still settling left the renderer at 1574x721 inside an 808px
    // box — the canvas visibly short of its own frame, with the filter row stranded on
    // the page background below it. The observer catches later changes; the frame and
    // timeout catch the settling that happens before the observer is even attached.
    resize = new ResizeObserver(sizeToBox)
    resize.observe(container)
    requestAnimationFrame(sizeToBox)
    setTimeout(sizeToBox, 400)
  })

  function sizeToBox() {
    if (!container || !graph) return
    const box = container.getBoundingClientRect()
    if (box.width < 2 || box.height < 2) return
    // Resizing must not yank the camera — only set the viewport.
    graph.width(Math.round(box.width)).height(Math.round(box.height))
  }

  let ForceGraph = null
  let Bloom = null
  // Set once the viewer has aimed the camera themselves; auto-framing defers after that.
  let userMoved = false

  async function swapRenderer() {
    await loadRenderer()
    userMoved = false
    rebuild()
  }

  function rebuild() {
    const camera = graph?.cameraPosition?.()
    graph?._destructor?.()
    container.replaceChildren()
    build()
    if (camera) graph.cameraPosition(camera, undefined, 0)
  }

  function build() {
    // Everything both renderers understand. The 2D and 3D libraries are the same author's
    // and share this vocabulary, which is what makes offering both cheap.
    graph = new ForceGraph(container, dimensions === '3d' ? { controlType } : undefined)
      .backgroundColor(mode() === 'light' ? '#fcfcfb' : '#111110')
      .nodeLabel((n) => `${n.path}\n${n.type} · ${n.tier}${n.needs_review ? ' · disputed' : ''}`)
      .linkCurvature('curvature')
      .linkColor((l) => (EDGE_STYLE[l.type] || EDGE_STYLE.DEPENDS_ON).color)
      .linkWidth((l) => (EDGE_STYLE[l.type] || EDGE_STYLE.DEPENDS_ON).width)
      // Every edge type is DIRECTED and the direction carries meaning — A CONTRADICTS B
      // flags B, not A. An undirected picture would misstate the data.
      .linkDirectionalArrowLength(dimensions === '3d' ? 3.5 : 4)
      .linkDirectionalArrowRelPos(1)
      .linkDirectionalArrowColor((l) => (EDGE_STYLE[l.type] || EDGE_STYLE.DEPENDS_ON).color)
      .linkDirectionalParticles((l) => (l === hoveredLink ? 3 : 0))
      .onNodeClick((n) => {
        userMoved = true
        select(n)
      })
      .onBackgroundClick(() => select(null))
      .onLinkHover((l) => {
        hoveredLink = l
        graph.linkDirectionalParticles((x) => (x === l ? 3 : 0))
      })
      .onEngineStop(fitNow)

    if (dimensions === '3d') {
      graph
        .showNavInfo(false)
        .nodeThreeObject(meshFor)
        .linkOpacity(0.5)
    } else {
      graph
        .nodeCanvasObject((node, ctx, globalScale) =>
          drawNode(ctx, node, {
            mode: mode(),
            globalScale,
            selected: selected?.id === node.id,
          }),
        )
        // The hit target is painted separately and made generous, so a short-term node
        // drawn small is still comfortably clickable.
        .nodePointerAreaPaint(paintPointerArea)
    }

    tuneLayout()
    applyData()

    if (dimensions === '3d') {
      // Bloom is added once and left at strength 0 until asked for. An always-on glow is
      // the single most common way a 3D graph becomes unreadable: it pushes every hue
      // toward white, which is exactly the channel the colour encoding depends on.
      bloomPass = new Bloom(undefined, 0, 0.7, 0.2)
      bloomPass.strength = highlightReview ? 1.7 : 0
      graph.postProcessingComposer().addPass(bloomPass)
    } else {
      bloomPass = null
    }
    graph.width(container.clientWidth).height(container.clientHeight)
  }

  /**
   * Spacing and grouping. Both were asked for, and both come from here rather than from
   * anything drawn: the default forces produce an evenly-spread hairball because they
   * treat every edge as the same kind of relationship, when SCOPED_TO means "belongs to"
   * and HAS_FACET means "is findable under" — and one facet here reaches ten scopes.
   */
  function tuneLayout() {
    const charge = graph.d3Force('charge')
    if (charge) charge.strength(LAYOUT.charge).distanceMax(LAYOUT.chargeDistanceMax)
    const link = graph.d3Force('link')
    if (link) {
      link
        .distance((l) => (LINK_LAYOUT[l.type] || LINK_LAYOUT.DEPENDS_ON).distance)
        .strength((l) => (LINK_LAYOUT[l.type] || LINK_LAYOUT.DEPENDS_ON).strength)
    }
    graph.d3Force('cluster', clusterForce())
  }

  let frameFallback = []
  /**
   * `onEngineStop` is the right signal — it means the layout has settled — but it is not
   * a guaranteed one: a simulation that never fully cools, or a tab that is throttled or
   * driven by an automation harness, may not deliver it at all, and the view is then left
   * on the renderer's default camera with the graph a speck in the middle. So the settle
   * signal frames the graph when it arrives, and a fallback timer frames it if it does
   * not. Whichever runs first wins; the other is a no-op once the viewer has taken over.
   */
  function fitNow() {
    clearFallbacks()
    if (userMoved) return
    frame(600)
  }

  function clearFallbacks() {
    for (const timer of frameFallback) clearTimeout(timer)
    frameFallback = []
  }

  /**
   * Several fits, not one, and this is why: the force layout expands and then contracts
   * over a second or more, so a single fit at a fixed delay frames it mid-flight — too
   * early and the camera ends up inside the cluster, a moment later and the graph is a
   * speck once it pulls together. Both were observed. Staggering means one of them lands
   * after the layout has settled, and re-fitting an already-framed graph is a no-op.
   *
   * `onEngineStop` is still the signal that matters when it arrives — it clears these.
   * It just cannot be relied upon: a throttled tab or an automation harness may never
   * deliver it, and then the view sits on the renderer's default zoom.
   */
  function scheduleFrame() {
    clearFallbacks()
    // The last one is deliberately late: anchoring the groups makes the layout keep
    // expanding for several seconds, and a fit that stops at six leaves the graph
    // overflowing its own frame.
    frameFallback = [1200, 3000, 6000, 11000, 18000].map((delay) =>
      setTimeout(() => !userMoved && frame(500), delay),
    )
  }

  /**
   * Frame the graph from its own bounding sphere rather than with `zoomToFit`.
   *
   * The library's fit repeatedly mis-framed this graph — the same code produced a camera
   * inside the cluster in one render and a speck in the middle of an empty canvas in the
   * next, because the force layout expands and then contracts and the heuristic is
   * sensitive to when it runs. Computing the centroid and radius directly, and placing
   * the camera at the distance the vertical field of view actually requires, is
   * deterministic: same positions in, same frame out.
   */
  function frame(ms = 600) {
    if (!graph) return
    if (dimensions !== '3d') {
      // The 2D fit is a plain bounding-box calculation with no camera to get wrong, and
      // it behaves. The hand-rolled version below exists because the 3D one did not.
      graph.zoomToFit(ms, 60)
      return
    }
    const nodes = graph.graphData().nodes
    if (!nodes.length) return
    let cx = 0, cy = 0, cz = 0
    for (const n of nodes) { cx += n.x || 0; cy += n.y || 0; cz += n.z || 0 }
    cx /= nodes.length; cy /= nodes.length; cz /= nodes.length
    let radius = 0
    for (const n of nodes) {
      radius = Math.max(radius, Math.hypot((n.x || 0) - cx, (n.y || 0) - cy, (n.z || 0) - cz))
    }
    radius = Math.max(radius, 40)
    const fov = ((graph.camera()?.fov ?? 50) * Math.PI) / 180
    // 1.12 leaves a little air around the outermost node instead of touching the frame.
    const distance = (radius / Math.sin(fov / 2)) * 1.12
    graph.cameraPosition({ x: cx, y: cy, z: cz + distance }, { x: cx, y: cy, z: cz }, ms)
  }

  $effect(() => {
    // `controlType` is a CONSTRUCTOR option in 3d-force-graph, not a runtime setter — it
    // is read once out of the options object and never again. Changing the toggle
    // therefore has to rebuild the instance; treating it as a live setter looks like it
    // works and silently does nothing.
    void controlType
    if (graph) rebuild()
  })

  $effect(() => {
    // Switching dimension swaps the whole renderer, and may have to fetch it first.
    void dimensions
    if (graph) swapRenderer()
  })

  $effect(() => {
    // Auto-orbit is a presentation mode, not a working one: a moving target is harder to
    // click and the motion tires you out. Off by default. Only OrbitControls implements
    // autoRotate — trackball and fly have no such property — so the toggle is disabled
    // for those rather than pretending.
    const controls = graph?.controls?.()
    if (controls && 'autoRotate' in controls) {
      controls.autoRotate = autoOrbit && controlType === 'orbit'
      controls.autoRotateSpeed = 0.6
    }
  })

  $effect(() => {
    void highlightReview
    refreshVisuals()
  })

  $effect(() => {
    // The 2D canvas paints the selection halo, so it has to be told to repaint.
    void selected
    if (graph && dimensions !== '3d') graph.nodeCanvasObject((node, ctx, globalScale) =>
      drawNode(ctx, node, { mode: mode(), globalScale, selected: selected?.id === node.id }),
    )
  })

  $effect(() => {
    void showArchived
    if (graph) load()
  })

  $effect(() => {
    void typeFilter
    if (graph) applyData()
  })

  function toggleType(type) {
    const next = new Set(typeFilter)
    next.has(type) ? next.delete(type) : next.add(type)
    typeFilter = next
  }

  onDestroy(() => {
    clearFallbacks()
    resize?.disconnect()
    graph?._destructor?.()
  })
</script>

<div class="d-flex" style="height: calc(100vh - 190px); min-height: 460px;">
  <div class="flex-grow-1 position-relative border rounded overflow-hidden">
    <div bind:this={container} class="position-absolute top-0 start-0 bottom-0 end-0"
         onpointerdowncapture={() => (userMoved = true)}
         onwheelcapture={() => (userMoved = true)}></div>

    {#if loading}
      <div class="position-absolute top-50 start-50 translate-middle text-secondary small">
        loading graph…
      </div>
    {/if}

    <!-- Legend: always present. Identity is never colour-alone, and three of the light
         steps sit below 3:1 on the light surface, so the labels are the relief. -->
    <div
      class="position-absolute top-0 start-0 m-2 p-2 rounded small"
      style="background: rgba(20,20,19,.72); color: #e8e8e4; backdrop-filter: blur(3px);"
    >
      {#each Object.entries(CLASS_LABEL) as [key, label]}
        <div class="d-flex align-items-center gap-2">
          <span
            style="width:.7rem;height:.7rem;border-radius:50%;display:inline-block;background:{CLASS_COLORS[
              mode()
            ][key]}"
          ></span>
          <span>{label}</span>
        </div>
      {/each}
      <div class="d-flex align-items-center gap-2 mt-1 pt-1 border-top border-secondary">
        <span
          style="width:.7rem;height:.7rem;border-radius:50%;display:inline-block;border:2px solid {STATUS.critical}"
        ></span>
        <span>disputed (ring + red)</span>
      </div>
      <div class="text-secondary mt-1">shape = type · size = tier</div>
    </div>

    <div class="position-absolute top-0 end-0 m-2 p-2 rounded d-flex flex-column gap-1 align-items-end"
         style="background: rgba(20,20,19,.72); color: #e8e8e4; backdrop-filter: blur(3px);">
      <div class="btn-group btn-group-sm">
        <button class="btn btn-sm {dimensions === '2d' ? 'btn-secondary' : 'btn-outline-secondary'}"
                title="Flat. Size means tier, labels are readable, nothing hides behind anything."
                onclick={() => (dimensions = '2d')}>2D</button>
        <button class="btn btn-sm {dimensions === '3d' ? 'btn-secondary' : 'btn-outline-secondary'}"
                title="Depth, at a cost: perspective makes a distant large node look like a near small one, so size stops meaning tier. Loads ~2 MB of renderer."
                onclick={() => (dimensions = '3d')}>3D</button>
      </div>
      {#if dimensions === '3d'}
        <div class="btn-group btn-group-sm">
          {#each ['orbit', 'trackball', 'fly'] as kind}
            <button
              class="btn btn-sm {controlType === kind ? 'btn-secondary' : 'btn-outline-secondary'}"
              onclick={() => (controlType = kind)}
              title={kind === 'fly'
                ? 'WASD flight. Good for getting inside a dense cluster; awkward for clicking things.'
                : kind === 'orbit'
                  ? 'Drag to orbit, scroll to zoom. The predictable one for editing.'
                  : 'Trackball: free rotation with no fixed up-vector.'}
            >
              {kind}
            </button>
          {/each}
        </div>
        <div class="form-check form-switch form-check-reverse small">
          <input class="form-check-input" type="checkbox" id="orbit" bind:checked={autoOrbit}
                 disabled={controlType !== 'orbit'} />
          <label class="form-check-label" for="orbit"
                 title={controlType === 'orbit'
                   ? 'Slow rotation for presenting. Off while editing: a moving target is harder to click.'
                   : 'Only orbit controls implement auto-rotation.'}>auto-orbit</label>
        </div>
        <div class="form-check form-switch form-check-reverse small">
          <input class="form-check-input" type="checkbox" id="glow" bind:checked={highlightReview} />
          <label class="form-check-label" for="glow"
                 title="Bloom, applied only to flagged nodes. A global glow would wash every hue toward white and destroy the colour encoding.">glow disputed</label>
        </div>
      {/if}
      <div class="form-check form-switch form-check-reverse small">
        <input class="form-check-input" type="checkbox" id="arch" bind:checked={showArchived} />
        <label class="form-check-label" for="arch">show archived</label>
      </div>
      <button class="btn btn-sm btn-outline-light"
              title="Re-frame the whole graph"
              onclick={() => frame(500)}>fit</button>
    </div>

    <div class="position-absolute bottom-0 start-0 m-2 p-1 rounded d-flex flex-wrap gap-1"
         style="background: rgba(20,20,19,.72); backdrop-filter: blur(3px); max-width: calc(100% - 1rem);">
      {#each Object.keys(SHAPE_OF) as type}
        <button
          class="btn btn-sm py-0 {typeFilter.size && !typeFilter.has(type)
            ? 'btn-outline-secondary opacity-50'
            : 'btn-outline-light'}"
          style="--bs-btn-color:#e8e8e4;--bs-btn-border-color:#6b6b66;--bs-btn-hover-color:#111;font-size:.72rem"
          onclick={() => toggleType(type)}
        >
          {type}
        </button>
      {/each}
    </div>
  </div>

  {#if selected}
    <div class="ps-3" style="width: 27rem; overflow-y: auto;">
      {#if error}
        <div class="alert alert-danger py-1 px-2 small">{error}</div>
      {/if}

      <div class="d-flex justify-content-between align-items-start">
        <div>
          <div class="fw-semibold">{selected.path}</div>
          <div class="text-secondary small">
            {selected.type} · {selected.tier}
            {#if selected.needs_review}· <span class="text-danger">disputed</span>{/if}
            {#if selected.archived}· dormant{/if}
          </div>
        </div>
        <button class="btn-close" aria-label="Close" onclick={() => select(null)}></button>
      </div>

      <div class="text-secondary small mt-2">
        trust {selected.trust_weight?.toFixed(2)} · retrieval {selected.retrieval_weight?.toFixed(2)}
        · degree {selected.degree}
      </div>

      <label class="form-label small mt-3 mb-1" for="body">Body</label>
      <textarea id="body" class="form-control form-control-sm font-monospace" rows="7"
                bind:value={bodyDraft}></textarea>
      <button class="btn btn-sm btn-primary mt-2" disabled={saving === 'body' || bodyDraft === selected.body}
              onclick={saveBody}>
        {saving === 'body' ? 'Saving…' : 'Save body'}
      </button>

      <hr />
      <div class="small fw-semibold mb-1">Tier</div>
      <div class="btn-group btn-group-sm w-100">
        {#each TIERS as tier}
          <button class="btn {selected.tier === tier ? 'btn-secondary' : 'btn-outline-secondary'}"
                  disabled={saving === 'tier'} onclick={() => setTier(tier)}>{tier}</button>
        {/each}
      </div>

      <div class="d-flex gap-2 mt-2">
        {#if selected.needs_review}
          <button class="btn btn-sm btn-outline-success" disabled={saving === 'flag'} onclick={clearFlag}>
            Clear flag
          </button>
        {/if}
        <button class="btn btn-sm btn-outline-secondary" disabled={saving === 'archived'}
                onclick={() => setArchived(!selected.archived)}>
          {selected.archived ? 'Reactivate' : 'Archive'}
        </button>
      </div>

      <hr />
      <div class="small fw-semibold mb-1">Edges ({selectedEdges.length})</div>
      <ul class="list-unstyled small mb-2">
        {#each selectedEdges as edge}
          <li class="d-flex align-items-center gap-2 py-1 border-bottom">
            <span class="badge text-bg-light">{edge.type}</span>
            <span class="text-truncate flex-grow-1" title={pathOf(edge.source === selected.id ? edge.target : edge.source)}>
              {edge.source === selected.id ? '→' : '←'}
              {pathOf(edge.source === selected.id ? edge.target : edge.source)}
            </span>
            <button class="btn btn-sm btn-outline-danger py-0 px-1" disabled={saving === 'edge'}
                    title="Remove this edge (journaled)" onclick={() => removeEdge(edge)}>×</button>
          </li>
        {/each}
      </ul>

      <div class="input-group input-group-sm">
        <select class="form-select" style="max-width:9rem" bind:value={newEdge.type}>
          {#each LINKABLE as type}<option>{type}</option>{/each}
        </select>
        <input class="form-control" list="graph-nodes" placeholder="target node id"
               bind:value={newEdge.target} />
        <button class="btn btn-outline-primary" disabled={!newEdge.target || saving === 'edge'}
                onclick={addEdge}>Link</button>
      </div>
      <datalist id="graph-nodes">
        {#each data.nodes as node}<option value={node.id}>{node.path}</option>{/each}
      </datalist>
    </div>
  {/if}
</div>
