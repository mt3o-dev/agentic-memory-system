# agentic-memory-system

A graph-based, agent-first memory system: typed knowledge nodes and edges in
project-local SQLite, an append-only decision journal that trust is *derived* from
(never mutated in place), flag-based staleness, mark-sweep liveness tied to change
lifecycles, and deterministic multi-seed Personalized PageRank retrieval with the
change Goal as the mandatory, dominant seed.

Design principle: retrieval is a pure function (same graph + same query → same
ranking, no LLM in the query path), and everything dangerous — trust, flag
resolution, tier promotion, archival — is derived or privileged, never part of the
agent's write vocabulary.

## Architecture at a glance

| Layer | Module | What it does |
|---|---|---|
| Schema | `schema.py` | Node types (decision, concept, constraint, issue, invariant, slice, facet_value, goal, **entity**), edge types (DEPENDS_ON, CONTRADICTS, SCOPED_TO, HAS_FACET, **ABOUT**, **CONSOLIDATES**), journal events |
| Storage | `storage.py` | SQLite store: CRUD, recursive-CTE traversal, event journal, flag-based staleness, slice lifecycle + mark-sweep archival, domain-entity lifecycle, consolidation, single- and multi-seed recall |
| Trust | `fold.py` | Trust folded from the journal (order-independent strategies) — never stored mutation |
| Staleness | `penalty.py`, `resolver.py`, `evaluator.py` | Query-time penalties for flagged nodes; rules → evaluator → human resolution ladder; LLM evaluator for guided review |
| Retrieval | `retrieval.py`, `embedding.py` | Goal-dominant multi-seed Personalized PageRank; edge policy as data; deterministic hashed-BoW embeddings behind a swappable port |
| Sync | `sync.py`, `serialization.py` | Git sync with no clean/smudge filter: the tracked `.dump` is the source, the `.db` is a gitignored build artifact the store rebuilds on open and refreshes on close |
| Recovery | `doctor.py` | Diagnose a store without writing to it (integrity, db↔dump agreement, leftover sidecars, abandoned staging), and apply only the repairs that cannot cost data unless forced |
| Concurrency | `locking.py` | Shared/exclusive file lock: every live connection holds the shared half, replacing the database needs the exclusive half. SQLite handles concurrent *connections*; nothing but this handles a whole-file swap |
| Agent surface | `agent_surface.py` | The one place agent operations and their rules live — 5 writes + 5 reads, safe by construction |
| Transports | `cli.py`, `mcp_server.py` | Two doors onto that surface: the CLI (default, always works) and MCP (optimization). Both pure delegation |
| Human surface | `gui_api.py`, `gui/` | Minimal web GUI (Svelte + Bootstrap) for inspection and the human-in-the-loop checkpoints |

Scoring: `effective_score = structure × (α·retrieval + β·trust + γ·recency)`, where
`structure` is hop decay (single-seed) or normalized PPR mass (multi-seed) — see
`context/changes/multi-seed-retrieval/ppr-composition.md`.

## For AI agents — two transports, one surface

The agent surface is the system; MCP and the CLI are doors into it, neither holding
judgment of its own. See [`docs/08_TRANSPORTS.md`](docs/08_TRANSPORTS.md) for the design.

**The CLI is the default**, because it is the one that always works — no registration,
no approval, no session restart, in a repo that was just cloned:

```sh
uv sync
uv run agentic-memory --help
uv run agentic-memory recall "VAT rounding" --goal <goal-id>
uv run agentic-memory domain-model --status proposed

# long prose never goes through shell quoting:
uv run agentic-memory capture - --type constraint --goal <goal-id> <<'EOF'
The payment webhook retries; handlers behind it must be idempotent.
EOF
```

**MCP is the optimization** — better ergonomics where it is available (structured
arguments, schemas, discovery), so register it too. A `.mcp.json` is committed at the
repo root; for other projects:

```sh
claude mcp add --scope project agentic-memory -- uv run --directory /path/to/agentic-memory-system agentic-memory-mcp
```

An MCP server binds at session start, which means it cannot serve a fresh session in an
unfamiliar checkout — the moment that needs recall most. That asymmetry is why the floor
is the CLI and the ceiling is MCP.

### The agent surface — 5 writes + 5 reads

| Tool | What it does |
|---|---|
| `create_change(change_id, goal, parent_refs?)` | Opens a unit of work: mints the change anchor + the mandatory **Goal** node and activates the change as a liveness root. Call first. |
| `capture_artifact(content, type, goal_ref, facets?, edges?, tier?)` | Captures a decision/concept/constraint/issue/invariant, atomically with its edges, anchored to the goal it serves and scoped to the goal's change. Facet labels are validated against the controlled vocabulary — near-synonyms come back as warnings, never silent duplicates. |
| `capture_entity(name, definition, goal_ref, facets?, evidence?, edges?)` | **Proposes** a domain entity — a named thing the project's language refers to. Keyed by name (capturing `Invoice` twice returns the first node and never rewrites its definition), always starts `proposed`, and carries its provenance into the journal. Only a human ratifies. |
| `link(source, target, type)` | Relates existing nodes (`DEPENDS_ON` \| `CONTRADICTS` \| `ABOUT` \| `CONSOLIDATES`). A CONTRADICTS edge flags the target for review as a transparent side-effect. |
| `append_event(event_type, node_ref, reason?)` / `append_events([...])` | The feedback loop: journal `USED` / `CONFIRMED` / `CONTRADICTED` / `REVIEWED` / `NOTED` against the stable ids the read call handed out. Append-only. |
| `recall_context(query, goal_ref)` | The read path: goal-dominant multi-seed PPR over the live graph, returned as ranked verbatim content blocks with stable ids, coarse type/tier/disputed tags (and entity status), and a compact list of contradictions among the results. Deterministic; no scores leak to the agent. |
| `impact_of(node_ref)` | Read the blast radius of a node — the artifacts that transitively `DEPENDS_ON` it, or for an entity, everything written `ABOUT` it — before proposing a change to it. Nearest-first, id-tagged, with hop depth. |
| `stale_nodes()` | Read the staleness queue: content nodes currently flagged for review. Read-only — it surfaces what a human should assess, and cannot clear a flag. |
| `domain_model(status?)` | Read the project's ubiquitous language as the graph holds it, filtered to `proposed` (the ratification backlog) or `confirmed` (the settled model). Read before naming anything in code, tests, or a plan. |
| `consolidation_candidates()` | Read clusters of artifacts that say variations of one thing across several changes. Read-only: the agent brings the candidate and a proposed wording; the human commits. |

**Safety invariant:** nothing in the agent surface can mutate trust, clear a review
flag, promote a tier, archive a node, confirm or retire a domain entity, or commit a
consolidation. Trust is folded from the journal by privileged callers; flag resolution
belongs to the evaluator/human ladder; archival is a consequence of change lifecycle
(`sweep`); entity ratification and consolidation are human judgment. Do not add such a
tool "for convenience".

### Domain entities and consolidation

Two design questions that were open through slice 10, now built — the full reasoning is
in [`docs/06_DOMAIN_ENTITIES.md`](docs/06_DOMAIN_ENTITIES.md) and
[`docs/07_CONSOLIDATION.md`](docs/07_CONSOLIDATION.md).

**Domain entities** are the 4th dynamics class. An entity *names* something the
project's language refers to; every other node type *asserts* something that could be
true or false. That difference is mechanical in five places: capture is keyed by name,
the lifecycle is an identity ladder (`proposed → confirmed → retired`) rather than a
validity one, the sweep roots entities **by class rather than by tier** so the domain
outlives the change that named it, recency decay is switched off (age is evidence about
claims, not identity), and `ABOUT` is the one edge whose *reverse* direction carries
weight — which is what makes an entity a hub that pulls in what the project knows about
it, including artifacts captured under other goals.

**Consolidation** triggers on cross-change recurrence — several live artifacts from
distinct change scopes saying variations of one thing — and runs strictly additively: it
mints an abstraction and wires `CONSOLIDATES` (provenance, never walked by the retrieval
walker, so an abstraction never resurrects the dormant detail it replaced) plus instance
`DEPENDS_ON` (the content channel). No instance is edited, archived, or re-tiered.

## For humans (GUI)

The human counterpart to the agent surface — where the human-in-the-loop checkpoints
(staleness review, contradiction reconciliation, tier promotion) actually happen:

```sh
uv run agentic-memory-gui                                    # http://127.0.0.1:8765
MEMORY_DB_PATH=/path/to/graph.db uv run agentic-memory-gui
```

Minimal Svelte + Bootstrap app (pre-built under `gui/dist`, no node needed to run):
browse and search the graph, walk edges from node to node, read each node's journal,
work the review queue (with the rules-resolver verdict as a hint), promote/demote
tiers (lifetime promotion requires explicit confirmation), manage change liveness and
run sweeps, ratify the domain model (**Domain** tab: confirm/retire proposed entities,
review consolidation candidates and commit the abstraction in your own words), and
preview retrieval with full scores — humans see the mechanism that
agents deliberately don't. Editing is supported and deliberately thin: create
artifacts and add edges through the same enforced write path agents use (goal-first,
facet governance, CONTRADICTS side-effects), edit node bodies, set weights directly,
and archive/unarchive. Every human write is journaled as an event, so manual
intervention never breaks the derived-state guarantees.

To hack on the GUI: `cd gui && npm install && npm run dev` (Vite dev server proxying
`/api` to the Python server), `npm run build` to refresh `gui/dist`.

### Guided review (LLM evaluator)

Each flagged item in the Review tab has a **review** button that opens a guided
wizard: the evaluator (`evaluator.py`, the MT3-27 tier of the resolution ladder)
explains the conflict, shows the flagged node next to its contradictors and blast
radius, poses one deciding question, and pre-selects a recommended resolution.
The human decides — *still valid* (clear), *superseded* (archive + optional lineage
edge to the replacement), *wrong* (archive), *needs correction* (edit, then clear),
or *defer* (keep flagged, journal the look) — plus an optional tier move (the
lifetime confirmation gate applies) and trust recompute. The decision is applied in
one transaction and journaled as a `manual_review` event recording both the AI's
recommendation and the human's choice, so followed-vs-overridden stays queryable.
The safety model is unchanged: the evaluator only *advises*, its verdicts are
journaled (`source="evaluator"`), and mutations happen exclusively on the human
surface (`source="gui-guided"`). The agent/MCP surface gains nothing.

Configuration (environment variables, read at GUI startup):

| Variable | Effect |
|---|---|
| `ANTHROPIC_API_KEY` | Enables the LLM path. Unset → deterministic **template guidance** derived from the rules verdict and journal evidence; the wizard works identically offline, just without model-written explanations. Keep the key in a chmod-600 env file, never in a service unit. |
| `MEMORY_EVALUATOR_MODEL` | Model id for guidance calls (default `claude-haiku-4-5`). |
| `ANTHROPIC_BASE_URL` | Point the SDK at any Anthropic-compatible endpoint (LiteLLM in Anthropic mode, a local proxy, a gateway). |

Guidance is cached per node until a new (non-evaluator) journal event appears, so
re-opening a node never re-bills; any API error degrades silently to template
guidance rather than breaking the wizard.

**Other LLM providers.** The evaluator is provider-agnostic at the code seam but
Anthropic-shaped at the wire: `LLMEvaluator.from_env()` builds a stock
`AsyncAnthropic` client, so anything speaking the Anthropic Messages API works via
`ANTHROPIC_BASE_URL` + `MEMORY_EVALUATOR_MODEL` with no code changes. For a
genuinely different API (OpenAI, Gemini, Ollama, vLLM), the client is duck-typed —
the evaluator only calls `await client.messages.create(...)` and reads text content
blocks — so a small adapter object passed as
`create_app(store, evaluator=LLMEvaluator(client=adapter))` is all it takes when
embedding; the `agentic-memory-gui` CLI entrypoint has no provider switch yet. Two
caveats behind compatibility proxies: the request uses Anthropic-specific
structured-output (`output_config.format`) and `system=` parameters, and a backend
that ignores the JSON-schema constraint falls back to template guidance (working,
but without the model's explanation) rather than erroring.

## Development

```sh
uv run pytest        # full suite
uv run agentic-memory sync status     # which side is ahead (db vs tracked dump)
uv run agentic-memory sync dump       # refresh the dump explicitly (long-running procs)
uv run python scripts/memory_lifecycle.py entities     # domain model + ratification backlog
uv run python scripts/memory_lifecycle.py candidates   # consolidation candidates
```

Both new CLI commands are **reads**. Entity ratification and committing a consolidation
are deliberately absent from the script for the same reason they are absent from the MCP
surface: unlike deactivate+sweep (a mechanical consequence of a merge that already
happened), they are judgment calls about what the project's language is and what deserves
to outlive a change.

Design history lives in `docs/` (start at `docs/00_README_START_HERE.md`) and
per-change plans under `context/changes/` / `context/archive/`. The authoritative
design record is the Linear project ("Agentic Memory System", MT3-17…MT3-30).

### Status (build-order slices, `docs/03_NEXT_STEPS.md`)

1–7 ✅ capture/recall, typed traversal, ranked scoring, git-sync, event-sourced
trust, flag-based staleness, liveness/archival · 8 ✅ multi-seed PPR retrieval ·
9 ✅ write-path MCP surface · GUI ✅ (v1.5 with editing) · 10 ✅ 10x lifecycle
binding (`.claude/skills/memory-*` + `CLAUDE.md` binding table) · evaluator agent ✅
(MT3-27 guided review in the GUI) · 11 ✅ domain entities (`06`) · 12 ✅ consolidation
(`07`)

Remaining design work: procedural lifecycle specifics (`MT3-29`) and the per-channel
edge-policy question (`MT3-20`/`MT3-30`).

## Building & installing

Releases carry three **install-only-what-you-need** assets. `uv build` produces the
Python distribution, `npm run build` produces the GUI, and `scripts/build-assets.py`
(stdlib only) packages the GUI and skills. `make dist` runs all of it; everything
lands in `dist/`:

```bash
make dist          # uv build + (cd gui && npm ci && npm run build) + package tarballs
```

Building the GUI needs Node (it is a build artifact — `gui/dist` is not tracked).

- **`agentic_memory_system-<ver>-py3-none-any.whl`** (+ sdist) — the engine: the
  `agentic-memory` CLI, the `agentic-memory-mcp` server, and the `agentic-memory-gui`
  API. Install with `uv tool install` (or pipx):

  ```bash
  uv tool install ./agentic_memory_system-<ver>-py3-none-any.whl
  ```

- **`memory-gui-<ver>.tar.gz`** — the Svelte GUI, built fresh (`gui/dist`) and
  packaged as a static bundle for serving behind the API.

- **`memory-skills-<ver>.tar.gz`** — the `memory-*` skills (the primitive bindings
  for the MCP surface) plus an installer:

  ```bash
  tar xzf memory-skills-*.tar.gz && cd memory-skills-*
  ./install.sh                           # → ~/.claude/skills
  ./install.sh --target ~/.agent/skills  # or .kiro / .opencode / a project dir
  ```

Each asset is standalone — grab only what you need.

### Releasing

The version lives in `pyproject.toml` (`[project] version`); both `uv build` and the
asset packager read it (CI prefers the tag).

1. Bump `version` in `pyproject.toml` and commit.
2. Tag and push:

   ```bash
   git tag v0.2.0
   git push origin v0.2.0
   ```

3. The tag triggers [`.github/workflows/release.yml`](.github/workflows/release.yml),
   which runs `uv build` and `scripts/build-assets.py` on a clean checkout and
   **attaches the wheel, sdist, GUI tarball, and skills tarball to the GitHub Release**
   for that tag.

`dist/` is gitignored and nothing is published from a developer's machine — the tag
is the only trigger. `make dist` dry-runs the same assets locally.
