# The graph view — what it encodes, and which features earn their place

*The v2 the original GUI design deferred: "Graph viz: none in v1 — click-through edge
walking. Nothing fancy; force-directed view is v2."*

A force-directed view of the whole graph, lazy-loaded into the existing Svelte GUI as a
**Graph** tab, with node and edge editing in a side panel. **2D by default**
([`force-graph`](https://github.com/vasturiano/force-graph)), with 3D
([`3d-force-graph`](https://github.com/vasturiano/3d-force-graph) + three.js) one click
away. `graph-encoding.js` holds the encoding, `graph-draw.js` the 2D rendering,
`GraphView.svelte` the view.

## 0. Why 2D is the default

3D was built first, looked at, and demoted — because **perspective destroys one of the
encodings this view depends on**. Size carries tier, and under a perspective projection
apparent size is size x distance, so a `lifetime` node at the back of the scene is
indistinguishable from a `short-term` node at the front. The channel is not weakened, it
is *gone*.

Three more things follow from the same projection:

- **occlusion** — nodes are simply hidden behind other nodes, and a curation surface that
  hides some of its content is not doing its job;
- **labels** cannot be shown at rest without z-fighting and constant re-layout, so the 3D
  view is unlabelled and every node has to be hovered to be identified;
- **clicking** a specific node is harder, because depth makes two nodes that look adjacent
  be nowhere near each other.

Against that, 3D buys room to untangle a dense graph — which matters at a scale this one
is nowhere near. It stays available, because "let me look at it from another angle" is a
real thing to want, and its ~2 MB of renderer is fetched only if asked for. The 2D
renderer is 90 kB.

The 2D path is also the only one that can be **tested**: a canvas context is a plain
object, so `graph-draw.test.js` asserts what was drawn. WebGL offers nothing to assert.

---

## 1. The encoding is computed, not chosen

A force-directed graph is an **all-pairs** form: any two nodes can end up adjacent on
screen, exactly like a scatter plot. The validated palette caps all-pairs categorical use
at **three slots**, and the obvious design — one hue per node type, nine of them — fails
the gate outright:

```
7 hues, --pairs all, light
  [FAIL] CVD separation      #008300 <-> #eb6834  dE 3.2 (protan)   — needs >= 8
  [FAIL] Normal-vision floor #e87ba4 <-> #eb6834  dE 12.9           — needs >= 15
```

Green and orange are 3.2 apart for a protanope; magenta and orange are 12.9 apart for
*everyone*. That is not a suboptimal palette, it is an unreadable one — and it is what
picking colours by eye would have produced.

So **type is not carried by hue.** Colour carries the three-way split this project's own
design already makes (`06_DOMAIN_ENTITIES.md`): a node either **asserts a claim**, **names
a referent**, or is **structure**. Three slots, and they pass in both modes:

```
3 hues, --pairs all
  light  worst CVD dE 9.2 (deutan) · normal-vision 24.0   ALL CHECKS PASS
  dark   worst CVD dE 9.4 (deutan) · normal-vision 20.9   ALL CHECKS PASS
```

| channel | carries | why that channel |
|---|---|---|
| **colour** | class: asserts / names / structure | 3 validated hues, the all-pairs limit |
| **shape** | node type (9 geometries) | nominal data with more levels than hue can hold; the conventional channel for node-link diagrams |
| **size** | tier, short → lifetime | ordinal magnitude, and tier *is* the human curation axis. Degree nudges it slightly so hubs read as hubs, but never enough for a well-connected note to outrank a promoted foundation. **Only meaningful in 2D** — see §0 |
| **label** | path's last segment | 2D only. Always on for long-term and lifetime nodes (the promoted few); on for the rest only above a high zoom, because a fitted whole-graph view is already zoomed enough to label everything at once and turn the centre into mush |
| **ring + red** | `needs_review` | reserved status colour, and a **ring** so status is never carried by colour alone |
| **opacity** | archived | dormant nodes recede rather than vanish |

Edges are neutral grey except **CONTRADICTS**, which takes the reserved critical red and a
heavier line. Six edge hues would be a second categorical scale competing with the node
one; a contradiction is the one edge that means *something is wrong here*, which is a
status, not a category.

## 2. Which of the requested features earn their place

The brief asked for curved lines, fly controls, auto-orbit, zoom, click-to-focus and
bloom. Not all of them survive contact with what this view is *for* — a curation surface
where you find a node and edit it, not a demo.

| feature | verdict | reasoning |
|---|---|---|
| **Curved links** | **Needed** | Two nodes can carry both a `DEPENDS_ON` and a `CONTRADICTS`, and that pair is precisely what the review queue is about. Drawn straight they land on the same line and one silently disappears. Curvature is assigned per parallel group so every edge stays visible *and separately clickable* |
| **Directional arrows** | **Needed — and not requested** | Every edge type is directed and the direction carries meaning: `A CONTRADICTS B` flags **B**, not A. An undirected picture misstates the data |
| **Zoom** | **Needed** | Free with the controls; plus explicit framing on load and a `fit` button |
| **Click to focus** | **Needed** | The core interaction: click selects, flies the camera to the node, and opens the edit panel |
| **Orbit controls** | **Needed, as the default** | Predictable, mouse-only, no mode switching |
| **Fly controls** | **Available, not default** | Genuinely useful for getting *inside* a dense cluster, and genuinely awkward for clicking a specific node — which is what you are here to do. Offered as a toggle alongside trackball |
| **Auto-orbit** | **Off by default** | A moving target is harder to click and constant motion is tiring. It is a presentation feature, so it ships as one: a toggle, and only orbit controls implement it (trackball and fly have no such property, so the switch disables itself rather than pretending) |
| **Bloom** | **3D only, off by default, selective when on** | As a global glow it is actively harmful: bloom pushes every hue toward white, destroying the one channel the colour encoding depends on. It earns its place only as a *highlighter* — "glow disputed" makes flagged nodes emit, so the effect marks the review queue instead of washing out the graph. In 2D the ring already does that job, sharply, so there is nothing for bloom to add |

Added because the data needed them: **filter by type**, **show/hide archived** (dormant
nodes are excluded by default — the first thing you see should not be mostly history), and
an always-present **legend**, which is also the relief for the three light-mode steps that
sit below 3:1 contrast.

## 2b. Spacing and grouping — the layout is an encoding too

The first layouts were an evenly-spread hairball, and the cause was treating every edge as
the same kind of relationship. They are not:

| edge | what it means | layout weight |
|---|---|---|
| `SCOPED_TO` | *membership* — this artifact belongs to this change | short and strong: it is what a group **is** |
| `DEPENDS_ON` | content structure | medium |
| `ABOUT` / `CONTRADICTS` | content, cross-cutting | medium, slack |
| `HAS_FACET` | *findability only*, never walked by the retrieval walker | long and almost no pull |

`HAS_FACET` is the one that mattered. It is 114 of 347 edges here, and a single facet —
`/facet/retrieval` — touches **ten different change scopes**. At full strength it drags ten
clusters into one point, which is precisely what a hairball is. It stays visible and stops
steering.

**Groups come from the data, not from a heuristic.** `SCOPED_TO` already partitions content
into change scopes, so that is the grouping. Entities and facet values are deliberately
*unscoped* in the data model — they outlive the change that named them — so they would have
no group at all; they get one each of their own.

Two things had to be got right, and both were got wrong first:

- **Anchors, not centroids.** Pulling nodes toward their group's centroid holds a group
  together and does nothing to keep groups *apart* — they overlap and the picture stays one
  mesh. Groups are placed on a ring instead, sorted by id so the same graph arranges the
  same way every time.
- **Containment, not attraction.** A plain attractor pulls every member onto the anchor,
  and for a group with many members that beats the repulsion holding them apart: the nine
  facet values landed exactly on top of one another, nine labels stacked in one spot. A
  node inside its group's allowance is now left alone and charge spaces it out; only one
  that has wandered outside is pulled back. The allowance grows with the square root of
  the group's size, because that is how the area a group needs grows.

## 3. Editing

The panel edits what the GUI is allowed to edit — it is the privileged human surface, so
the operations the agent surface forbids live here and every one of them is journaled:

- **body** (`content_edited`), **tier** (`tier_change`, with the mandatory lifetime
  confirmation), **archive/reactivate**, **clear flag**;
- **add an edge** through `AgentSurface.link`, so goal-first and vocabulary rules apply
  exactly as they do for an agent;
- **remove an edge** — the one genuinely new operation.

`MemoryStore.delete_edge` is deliberately **absent from the agent surface**. An agent may
assert a relationship and may contradict a node, but removing an edge is *retraction*: it
deletes the only record that the relationship was ever claimed. It journals an
`edge_removed` event against the **source** node — an edge is an assertion made from it —
at weight 0, so it stays trust-neutral under the accumulation folds, the same choice
`sweep` makes for archived/reactivated. Without that, this would be the one destructive
operation in the system leaving no trace, in a store whose whole premise is that state
changes are recoverable from the journal.

Adding the event type meant widening the `events` CHECK and **moving the newest-token
guard** to `'edge_removed'` — the migration rule that, missed, makes live stores silently
fail to migrate and reject the new value at insert time.

## 4. Two things measurement changed

**Lazy loading.** `three.js` (736 kB) and `3d-force-graph` (812 kB) are several times the
rest of the app. The tab is code-split, so the main bundle went from 157.4 kB to
**159.8 kB** — someone who never opens the tab pays 2.4 kB.

**Framing does not trust one signal, in either dimension.** The force layout expands and
then contracts over a second or more, so a single fit at a fixed moment frames it
mid-flight — too early and the camera ends up inside the cluster, a moment later and the
graph is a speck in an empty canvas. Both were observed, in both renderers. So the fit is
**staggered** across a few delays, one of which lands after the layout settles, and
re-fitting an already-framed graph is a no-op. `onEngineStop` is still the signal that
matters when it arrives and cancels the rest; it just cannot be relied on, because a
throttled tab or an automation harness may never deliver it.

In 3D the library's `zoomToFit` was additionally unreliable, so that path computes the
centroid and bounding radius itself and places the camera at the distance the vertical
field of view requires. The 2D fit is a plain bounding-box calculation with no camera to
get wrong, and it behaves.

Framing stops as soon as the viewer takes over — pointer, wheel, or a node click — because
re-aiming the camera under someone who is navigating is worse than a bad first frame.

## 5. What is tested, and what cannot be

Three layers, and the split is deliberate.

**Python** (`pytest`): the graph payload and the fields the encoding reads, archived
filtering and the removal of edges dangling into hidden nodes, edge deletion and its
journaling, the 404 and 400 paths, and the CHECK migration against an old-schema database.

**JavaScript** (`vitest`, added with this change — the GUI had no harness before it):

- `graph-encoding.test.js` — the encoding's correctness properties. Every node type has a
  class and its *own* shape (a shape collision makes two types indistinguishable, and
  colour cannot rescue it because colour is class); size increases with tier and degree
  never lets a busy short-term note outrank a promoted foundation; parallel edges curve
  apart, including `a->b` against `b->a`, which occupy the same line on screen. The
  palette hexes are **pinned to the validated values**, so anyone tidying them re-opens a
  check that was already run.
- `Graph3D.test.js` — the half that can corrupt data, with `three` and `3d-force-graph`
  mocked by a chainable recorder that also captures the handlers the component registers,
  so a node click can be fired through the real code path. It covers which endpoint each
  control calls and with what payload, that archived nodes are excluded unless asked, that
  a refused `confirm()` is reported honestly rather than promoted anyway, and that a
  server error is surfaced rather than swallowed.

**Neither covers the rendering.** jsdom has no WebGL, so the geometry, the camera and the
bloom pass are out of reach of any of it. That layer is verified by rendering the page in
a real browser and looking at it — which is how the near-invisible control labels and both
framing bugs were found, and none of those would have failed a test.
