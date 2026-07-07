# Graph GUI — Plan Brief

> Design doc: `context/changes/graph-gui/gui-design.md` (IA, wireframe, decisions)

## What & Why

MT3-26 minimal v1: the surface where the human-in-the-loop checkpoints actually
happen — staleness review, contradiction reconciliation, tier promotion, change
liveness. Svelte + Bootstrap, minimalistic by explicit owner constraint.

## Key Decisions Made

| Decision | Choice | Why |
|---|---|---|
| Shape | Standalone local web app: Starlette API + static Svelte SPA | Cheapest checkpoint surface; no auth/multi-user complexity |
| Privilege model | GUI gets the privileged ops the agent surface forbids; every one journaled | Human overrides stay inside the audit trail — determinism preserved |
| Transparency | Humans see scores/weights/journal (agents don't) | The GUI is also the retrieval debugging tool |
| Lifetime gate | Client confirm + server-side `confirmed: true` requirement | The MT3-18 mandatory checkpoint can't be bypassed |
| Verdict hint | RulesResolver verdict shown in the review queue | Ladder tier-1 informs, human decides |
| Graph viz | None in v1 — click-through edge walking | "Nothing fancy"; force-directed view is v2 |
| Distribution | `gui/dist` committed; `uv run agentic-memory-gui` needs no node | Python-only users get the GUI for free |
| Threading | `check_same_thread=False` on the SQLite connection | Server event loop / test portal cross-thread access; single-writer usage unchanged |

## Scope

**In:** `gui_api.py` (Starlette JSON API + static host, `agentic-memory-gui` entry
point), privileged `MemoryStore.set_tier` (journaled), `gui/` Svelte app (Browse +
NodeDetail + Review + Changes + Recall, health strip), API test suite, committed
`dist` build, README section.

**Out:** graph visualization, content/edge editing, velocity trends, drag-drop tree
reorg, facet management, auth, real-time sync (see design doc §Deferred).

## Success Criteria

- Full pytest suite green (API tests cover every endpoint incl. the lifetime gate,
  journaled overrides, sweep lifecycle, recall scores)
- `npm run build` clean; `uv run agentic-memory-gui` serves the built app
- Live browser walkthrough: browse → detail → review queue → clear flag → changes →
  sweep → recall ranking with visible penalty on the disputed node (screenshots in
  Linear MT3-26 comment)
