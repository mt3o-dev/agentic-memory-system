---
change_id: graph-3d-view
title: A force-directed view of the graph (2D by default), with node and edge editing
status: implemented
created: 2026-09-08
updated: 2026-09-08
archived_at: null
memory_goal: b13dfc27-aa63-4b91-a32d-b697f5dabf67
---

## Notes

The v2 the original GUI design deferred: *"Graph viz: none in v1 — click-through edge
walking. Nothing fancy; force-directed view is v2."* three.js + `3d-force-graph`,
lazy-loaded as a **Graph** tab in the existing GUI, with editing in a side panel.

Design, the encoding, and the feature-by-feature argument: `docs/11_GRAPH_VIEW.md`.

## 2D by default, after building 3D first

3D was built, rendered, looked at — and demoted. **Perspective destroys the size channel**:
size carries tier, and apparent size under perspective is size x distance, so a `lifetime`
node at the back is indistinguishable from a `short-term` node at the front. Occlusion
hides content outright, and labels cannot be shown at rest without z-fighting. 3D buys room
to untangle a dense graph, at a scale this one is nowhere near. It stays one click away and
its ~2 MB of renderer is fetched only if asked for; the 2D renderer is 90 kB.

The 2D path is also the only one that can be tested — a canvas context is a plain object,
so `graph-draw.test.js` asserts what was drawn.

## The part that decided the design

A force-directed graph is an **all-pairs** colour form, so it is capped at three
categorical hues. One hue per node type fails the validator outright — green vs orange is
dE 3.2 for a protanope, magenta vs orange 12.9 for everyone. So colour carries the
three-way class split this project already makes (asserts a claim / names a referent /
structure), shape carries type, size carries tier, and a red ring carries `needs_review`.
That encoding is a consequence of running the check, not a preference.

## What was pushed back on

Fly controls and auto-orbit were requested and are not the defaults: this is a curation
surface where you click a node and edit it, and both make that harder. Both ship as
toggles. Bloom is off by default and selective when on — as a global effect it destroys
the colour channel the encoding depends on.

## New backend surface

- `GET /api/graph` — every node and edge in one payload, archived excluded by default.
- `POST /api/edges/delete` → `MemoryStore.delete_edge`, privileged and journaled as a new
  `edge_removed` event (weight 0, against the source node). Adding the event type meant
  widening the `events` CHECK and moving the newest-token guard.

## Verified

396 tests. The view was rendered headlessly and looked at, which is how the control-label
contrast failure and both framing bugs were found. Every edit path was exercised against
a live server on a copy of the store — body edit, edge add (the CONTRADICTS correctly
flagged its target), the lifetime gate refusing without confirmation, and edge removal
journaling `edge_removed` at weight 0.
