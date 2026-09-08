# The 3D graph view — what it encodes, and which features earn their place

*The v2 the original GUI design deferred: "Graph viz: none in v1 — click-through edge
walking. Nothing fancy; force-directed view is v2."*

`three.js` + [`3d-force-graph`](https://github.com/vasturiano/3d-force-graph), lazy-loaded
into the existing Svelte GUI as a **Graph** tab, with node and edge editing in a side
panel. `gui/src/graph-encoding.js` holds the encoding, `Graph3D.svelte` the view.

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
| **size** | tier, short → lifetime | ordinal magnitude, and tier *is* the human curation axis. Degree nudges it slightly so hubs read as hubs, but never enough for a well-connected note to outrank a promoted foundation |
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
| **Bloom** | **Off by default, and selective when on** | As a global glow it is actively harmful here: bloom pushes every hue toward white, destroying the one channel the colour encoding depends on. It earns its place only as a *highlighter* — "glow disputed" makes flagged nodes emit, so the effect marks the review queue instead of washing out the graph |

Added because the data needed them: **filter by type**, **show/hide archived** (dormant
nodes are excluded by default — the first thing you see should not be mostly history), and
an always-present **legend**, which is also the relief for the three light-mode steps that
sit below 3:1 contrast.

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

**Framing is computed, and does not trust one signal.** `zoomToFit` mis-framed this graph
repeatedly and inconsistently: the same code put the camera *inside* the cluster in one
render and left the graph a speck in an empty canvas in the next, because the force layout
expands and then contracts and the heuristic is sensitive to when it runs. The view now
computes the centroid and bounding radius itself and places the camera at the distance the
vertical field of view requires — deterministic, same positions in, same frame out. It is
triggered by `onEngineStop` (the signal that means the layout settled) **with a timer
fallback**, because that event is not guaranteed to arrive in a throttled or automated
tab, and without the fallback the view is left on the library's default camera.

Framing stops as soon as the viewer takes over — pointer, wheel, or a node click — because
re-aiming the camera under someone who is navigating is worse than a bad first frame.

## 5. What is not verified

The Python side is covered by tests (graph payload, archived filtering, edge removal and
its journaling, the 404 and 400 paths). The view itself was checked by rendering it
headlessly and looking at it — which is how the contrast failure on the control labels,
and both framing bugs, were found. There is no JS test harness in this project, so the
Svelte component has no automated coverage; interactions were exercised against the live
API instead.
