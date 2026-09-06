# memory backlog — domain-entities-consolidation

> **REPLAYED 2026-09-06 — do not replay again.** Every operation below is now in the
> store under `memory_goal: 585f39c8-3810-40fb-8bb5-378819d2dc88`. Replaying a second
> time would mint duplicate nodes, since capture is append-only and has no idempotency
> key. Kept as the record of what was queued and why. Still open, and deliberately: the
> §7 promotion candidates and the six `proposed` entities await a human ruling in the GUI.

The `agentic-memory` MCP server was not reachable in the session that implemented this
change, so per the standing degraded-mode rule every would-be memory operation is queued
here for replay rather than skipped. A capture made only against a dead server never
happened; this file is what makes it recoverable.

Replay in order against the repo's own store (`context/memory-graph.db`).

## 1. Open the scope

```
create_change(change_id="domain-entities-consolidation",
              goal="Resolve the two remaining design questions — the 4th dynamics class and the consolidation operation — with running code plus a design note")
```

Record the returned `goal_node_id` as `memory_goal:` in `change.md`, then use it as
`goal_ref` for everything below.

## 2. Domain entities of this project's own domain

The memory system's domain, modelled with its own new mechanism:

```
capture_entity(name="Node", definition="One artifact in the memory graph: a typed, tiered, path-addressed body of text with a stable uuid.", facets=["schema"], evidence="src/agentic_memory_system/schema.py:26")
capture_entity(name="Change", definition="One unit of work. Anchored by a slice node, owns exactly one Goal, and acts as a liveness root while active.", facets=["lifecycle"], evidence="src/agentic_memory_system/agent_surface.py:create_change")
capture_entity(name="Goal", definition="The mandatory anchor every capture and recall serves; the dominant PPR seed. A change has exactly one.", facets=["retrieval"], evidence="docs/01_CORE_CONCEPTS.md §3")
capture_entity(name="Domain entity", definition="A node that names something the project's language refers to rather than asserting a claim; the 4th dynamics class.", facets=["schema"], evidence="docs/06_DOMAIN_ENTITIES.md")
capture_entity(name="Facet value", definition="One term of the controlled classification vocabulary; findability only, never walked by the retrieval walker.", facets=["schema"], evidence="src/agentic_memory_system/storage.py:discover_seeds")
capture_entity(name="Journal event", definition="One append-only record against a node. Trust, liveness, and entity status are all folded from these, never stored.", facets=["journal"], evidence="src/agentic_memory_system/schema.py:Event")
```

All land as `proposed`. Ratify in the GUI Domain tab — that is the point of the gate.

## 3. Decisions

```
capture_artifact(type="decision", facets=["schema"],
  content="The 4th dynamics class is DOMAIN ENTITIES, not 'reference entities'. Person/ExternalRef named a subcase; the class is defined by identity-bearing reference — the node names a referent rather than asserting a claim — which makes it the project's domain model, with actors and external refs as ordinary members.")

capture_artifact(type="decision", facets=["lifecycle"],
  content="Domain entities are roots of the live set BY CLASS, not by tier: the domain outlives the change that named it, so an entity must not need a promotion to survive that change's sweep. Retirement, not archival, is how an entity leaves — and retirement outranks a lifetime promotion.")

capture_artifact(type="decision", facets=["retrieval"],
  content="ABOUT reverse carries deliberate weight (0.35) — the only non-zero reverse direction in the edge policy. An entity is the one node class whose purpose is to be walked backwards from; PPR row-normalization keeps a popular hub broadening the bundle rather than swamping it.")

capture_artifact(type="decision", facets=["review"],
  content="Consolidation's owner is SPLIT like the trust ladder: a deterministic read-only detector any agent may run, and a privileged commit. A consolidated node exists to be promoted past the sweep, so an agent that both abstracts and nominates its own abstraction would be writing long-term memory unsupervised.")

capture_artifact(type="decision", facets=["review"],
  content="Consolidation runs upward and strictly additively — it mints an abstraction and never edits, archives, merges, or re-tiers an instance. CONSOLIDATES is a provenance channel at policy weight 0, because an abstraction's instances are dormant on purpose and walking it at query time would undo the sweep.")
```

## 4. Constraints and invariants discovered

```
capture_artifact(type="constraint", facets=["schema"],
  content="The _migrate_*_check guards are NEWEST-TOKEN checks, not membership checks: every CHECK widening must move its guard to the new newest token, or live stores silently fail to migrate and reject the new value at insert time.")

capture_artifact(type="constraint", facets=["storage"],
  content="`id NOT IN (NULL)` is NULL for every row in SQLite, so a NOT IN exclusion built from an empty list silently filters out everything it was meant to preserve. Omit the clause entirely when the list is empty; the positive `IN (NULL)` form is safe.")

capture_artifact(type="invariant", facets=["retrieval"],
  content="The sweep root set expands along SCOPED_TO only. A root that expanded along ABOUT would let one long-lived entity pin every artifact ever written about it, and nothing would go dormant again.")

capture_artifact(type="constraint", facets=["schema"],
  content="capture_entity never overwrites an existing entity's definition. Disagreement is captured as an artifact with a CONTRADICTS edge so it reaches review; silent redefinition would let any agent move the domain mid-change with no gate.")

capture_artifact(type="constraint", facets=["review"],
  content="Consolidation candidates require instances from at least two distinct change scopes. Three artifacts inside one change saying the same thing is repetition — a capture-quality finding for review, not knowledge to abstract.")
```

## 5. Concepts

```
capture_artifact(type="concept", facets=["schema"],
  content="Entity governance and facet governance are deliberately asymmetric: a warned facet label is SKIPPED, a warned entity name is MINTED with the warning carried into its proposal event. Entities have a mandatory human ratification gate that facets do not, so identity questions are ruled on there, by a person looking at both definitions.")
```

Attach the constraint/concept nodes to the entities they concern with `ABOUT` edges as
you replay them — that wiring is what makes the entity a hub rather than a label.

## 6. Journal

One batched `append_events` with `CONFIRMED` for the nodes this change actively
exercised (the PPR composition note, the liveness/mark-sweep design), `USED` for the rest.

## 7. Promotion candidates for the human

Every decision and constraint above, plus the six entities. The two SQLite/migration
constraints are the highest-value promotions — they encode bugs this change actually hit.
