# plan — domain-entities-consolidation

Two slices, one change, because they share a schema migration and meet at the review
gate. Each phase ends in a state `uv run pytest` can verify.

## Non-goals

- Episodic → **procedural** consolidation. There is no procedural node type; procedures
  live in skill files. Recorded as a boundary in `docs/07_CONSOLIDATION.md`, not built.
- Aliases as a first-class entity mechanism, and typed entity↔entity relation edges.
  Both are argued down in `docs/06_DOMAIN_ENTITIES.md` §4 rather than deferred silently.
- Automatic promotion of consolidated nodes, or any agent-reachable ratification. Those
  would move the safety line, which is the one thing this change must not do.

## Phases

### P1 — schema + storage (verify: `uv run pytest`)

`NodeType.entity`; `ABOUT` / `CONSOLIDATES` edge types; `entity_proposed` /
`entity_confirmed` / `entity_retired` / `consolidated` events. All three CHECK
migrations move their guard to the new newest token (`'entity'`, `CONSOLIDATES`,
`'consolidated'`) — the guard is a newest-token check, so a widening that does not move
it silently fails to migrate live stores.

Storage: entity status folded from the journal (`entity_status`, `_entity_statuses`,
`entities`); privileged `confirm_entity` / `retire_entity`; sweep root set gains every
non-retired entity, with retirement outranking the tier clause; `_score_node` pins
recency for entities; `impact_of` follows `ABOUT` backwards; `discover_seeds` gains the
entity axis; `consolidation_candidates` + privileged `consolidate`.

*Risk:* the sweep's tier-root clause needed a `NOT IN` exclusion for retired entities, and
`id NOT IN (NULL)` is NULL for every row — it would silently un-root every foundation.
The clause is omitted entirely when nothing is retired, and
`test_root_set_membership` covers it.

### P2 — retrieval policy (verify: `uv run pytest`)

`ABOUT` forward 1.0 / reverse `DEFAULT_ENTITY_HUB_WEIGHT` (0.35) — the one deliberate
reverse weight in the table; `CONSOLIDATES` 0.0 both ways (provenance channel).

*Risk:* a non-zero reverse weight is the first in the policy table, so it is the first
chance for a popular hub to swamp a bundle. Mitigated by PPR's row normalization (a
30-artifact entity contributes ~0.035 per neighbour) and kept as a config dial with a
test proving 0.0 turns entities back into pure sinks.

### P3 — agent surface + MCP (verify: `uv run pytest`)

`capture_entity` (name-keyed, never overwrites, always `proposed`, provenance into the
journal); shared `_check_edge_endpoints` so `ABOUT` must target an entity and nothing but
`ABOUT` may; `domain_model` and `consolidation_candidates` reads; entity status tagged in
recall bundles. Three new MCP tools.

### P4 — human surface (verify: `uv run pytest`, `npm run build`)

`/api/entities` (+ confirm/retire), `/api/consolidation/candidates`, `/api/consolidate`
with the lifetime gate; health counts; a `Domain` GUI tab; read-only `entities` /
`candidates` lifecycle CLI commands.

### P5 — design records + docs

`docs/06_DOMAIN_ENTITIES.md`, `docs/07_CONSOLIDATION.md`, and the updates to `00`, `01`,
`03`, `README.md`, `CLAUDE.md`.

## Verification

`uv run pytest` — 275 passing (65 new across `test_domain_entities.py`,
`test_consolidation.py`, and the GUI API additions), including the upgrade path from the
previous release's schema and determinism replays for seed discovery and candidate
detection.
