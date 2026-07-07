# GUI Design — human interaction with the memory graph (MT3-26, v1)

**Constraint set by the owner:** plain Bootstrap, Svelte, minimalistic — nothing fancy.

## Design stance

The GUI is the *mirror image* of the agent surface. The agent gets a safe, mechanism-
hiding interface (no scores, no privileged writes); the human gets the opposite — the
mechanism made visible (scores, weights, the journal) and the privileged levers
(clear flag, set tier, recompute trust, activate/deactivate, sweep). One rule binds
both sides: **every write is journaled as an event**, so a human override never breaks
the derived-state guarantees. That resolves MT3-26's open question about manual
editing vs determinism.

Answers to the other MT3-26 open questions, for v1:

- **Standalone vs embedded** → standalone local web app (`uv run agentic-memory-gui`),
  single user, no auth. Cheapest thing that provides the checkpoint surface.
- **Read-mostly vs full editor** → read-mostly + checkpoint actions. Content and edge
  *editing* stay workflow-enforced (agents write through the MCP surface); the human
  curates state, not content.
- **Graph viz library** → none in v1. Edge-walking (click a neighbor, the detail pane
  moves there) covers "traverse interactively" without a layout engine. Force-directed
  belief-network view is the natural v2 if it earns its complexity.
- **Real-time sync** → inspect-after-the-fact. Refresh is manual/navigation-driven.

## Information architecture — four tabs, one health strip

```
┌──────────────────────────────────────────────────────────────────────┐
│ agentic memory   [Browse] [Review ●n] [Changes] [Recall]   n nodes · │
│                                                    n events · n act… │
├───────────────────────────┬──────────────────────────────────────────┤
│ Browse: filters + list    │ Node detail: body, badges, weights       │
│  search / type / tier /   │  actions: clear-flag (w/ rules verdict), │
│  flagged / incl.archived  │  recompute trust, set tier (lifetime ⇒   │
│  path + preview + badges  │  confirm dialog)                         │
│                           │  outgoing / incoming edges (click=walk)  │
│                           │  journal (event log, newest first)       │
└───────────────────────────┴──────────────────────────────────────────┘
```

- **Browse** — the inspector. List (max 500, path-sorted) with substring search and
  type/tier/flag/archived filters; two-pane master–detail. The detail pane is where
  all node-level curation lives.
- **Review** — the MT3-23 staleness queue: flagged nodes with contradiction severity
  and the deterministic `RulesResolver` verdict as a *hint* column (the ladder's tier-1
  opinion; the human stays the decider). One action: clear (false alarm) — restores
  the score for free because demotion was flag-driven.
- **Changes** — liveness control: each change slice with active/dormant state and
  scoped-node count; activate/deactivate + a sweep button that reports exactly which
  nodes changed state.
- **Recall** — the playground: pick a goal, type a query, see the exact ranked set an
  agent's `recall_context` would get, with normalized score bars. Transparency tool
  and debugging aid for retrieval tuning.

## Visual language (deliberately boring)

Stock Bootstrap 5.3, no theme, no custom components, one `<style>` block for a
max-width. Meaning is carried by badges only: type = light outline; tier =
secondary/info/primary/dark (short→lifetime); `disputed` = red; `archived` = yellow.
Paths render as `<code>`. The review count sits on the tab as a red badge — the one
"come look at this" signal. No modals (native `confirm()` for the lifetime gate), no
router (tab state), no client state library (Svelte 5 runes + fetch).

## The checkpoint interactions

- **Lifetime promotion (MT3-18 mandatory gate):** double gate — client `confirm()`
  dialog *and* the API rejects `tier=lifetime` without `confirmed: true`, so the gate
  can't be bypassed by a sloppy client.
- **Flag clearing:** journaled `contradiction_cleared` with `source="gui"`; the rules
  verdict shown next to the button so the human sees when the machine already agrees.
- **Trust recompute:** explicit button (never automatic) — folds the journal via the
  store's fold strategy; the new value is displayed immediately.

## Deferred (v2 candidates, in rough order of value)

Belief-network visualization (supporters vs contradictors, weight as intensity) ·
weight/velocity trends per node · orphan/edgeless warnings surfaced as a queue (the
count already sits in the health strip) · content/edge editing (needs a
workflow-safety story first) · taxonomy/facet management · drag-and-drop
reorganization · concurrent-agent live refresh.

## Build shape

`gui/` Vite + Svelte 5 app (5 components, ~450 lines total), Bootstrap from npm,
built to `gui/dist` which is **committed** so `uv run agentic-memory-gui` works with
no node toolchain. `gui_api.py` = Starlette JSON API + static host; dev mode proxies
`/api` from `npm run dev` to the Python server.
