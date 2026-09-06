---
change_id: domain-entities-consolidation
title: Domain entities (4th dynamics class) + the consolidation operation
status: implemented
created: 2026-08-01
updated: 2026-08-01
archived_at: null
---

## Notes

Slices 11 and 12 from `docs/03_NEXT_STEPS.md` — the two items listed there as "real
design work, not yet started". Both are discharged with running code plus a design note,
which is this project's definition of done.

**Domain entities** (`MT3-29`, `MT3-30`). The open question was framed as "4th dynamics
class — reference entities: Person/ExternalRef neither decay, nor are immutable-events,
nor reinforce; confirm they need their own class and define it." The confirmation is yes,
but the framing was wrong: `Person`/`ExternalRef` name a *subcase*. The class is defined
by **identity-bearing reference** — an entity NAMES something the project's language
refers to, rather than ASSERTING a claim that could be true or false — which makes it the
**domain model**, of which actors and external refs are ordinary members. Refactored
accordingly. Design record: `docs/06_DOMAIN_ENTITIES.md`.

**Consolidation** (`MT3-18`, `MT3-29`). Trigger, direction, and owner were all open.
Answered as: cross-change recurrence; upward and strictly additive; split owner (open
detector, privileged commit). Design record: `docs/07_CONSOLIDATION.md`.

The two land together because they intersect at the review gate — consolidation's
always-on trigger is the change summary, and an entity is precisely the thing
consolidation must never produce (an entity is a referent, not an abstraction over
episodes; the surface rejects `CONSOLIDATES` from one).

## Memory scope

`memory_goal: 585f39c8-3810-40fb-8bb5-378819d2dc88`

Not recorded at the time — the `agentic-memory` MCP server was not reachable in the
session that implemented this change. Per the workflow's degraded-mode rule the memory
operations were queued rather than skipped (`memory-backlog.md`), and **replayed in full
on 2026-09-06**: 6 domain entities, 5 decisions, 4 constraints, 1 invariant, 1 concept,
their ABOUT wiring, and the journal batch. The scope is left ACTIVE on purpose — the
promotion candidates in `memory-backlog.md` §7 and the six `proposed` entities are a
human gate (GUI Domain / Review tabs), and a sweep now would make them dormant before
anyone had ruled on them.
