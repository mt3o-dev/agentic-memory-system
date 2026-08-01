# Domain Entities — the 4th dynamics class

*Resolves the `03_NEXT_STEPS.md` open question "4th dynamics class — reference
entities". `MT3-29`, `MT3-30`.*

---

## 1. The refactor: reference entities → domain entities

The design originally named the class **reference entities**, with `Person` and
`ExternalRef` as its members, and asked: *do these need their own dynamics class?*

That framing named a **subcase** and missed the general one. `Person` and
`ExternalRef` are not interesting because they point *outside* the system — plenty of
`concept` nodes describe external things. They are interesting because of what kind of
node they are:

> An entity **names a thing the project's language refers to**.
> A claim **asserts something that could be true or false**.

`Invoice` is not true. `Customer` is not false. They are *referents* — you can rename
them, split them, merge them, or stop having them, but you cannot contradict them.
Under that reframing the class is exactly the **domain model** in the DDD sense: the
ubiquitous-language entities of the problem domain, of which actors (`Person`) and
external referents (`ExternalRef`) are two ordinary members.

So the class is renamed **domain entities**, and `Person`/`ExternalRef` become
instances of it rather than its definition. `NodeType.entity`.

**The test this had to pass.** `01_CORE_CONCEPTS.md` §5 records a standing rule:
cognitive-science structure is adopted **only where it changes behavior** — relabeling
for its own sake is a documented anti-pattern. A class that only added a tag would have
been rejected. What follows is the behavior that actually differs.

---

## 2. Five behaviors that differ

| # | Behavior | The four content types | Domain entities |
|---|---|---|---|
| 1 | **Identity** | Body is the content; two similar nodes are two facts | The **name is the key** (`/entity/<slug>`); capturing a name twice returns the first node |
| 2 | **Truth model** | Validity ladder: flag → review → clear/supersede | Identity ladder: **proposed → confirmed → retired** |
| 3 | **Liveness** | Scope-bound: swept when the change goes dormant, unless promoted | **Root set by class**, regardless of tier |
| 4 | **Decay** | Recency term fades with age | **No decay** — recency pinned to 1.0 |
| 5 | **Retrieval role** | Reached forward from the goal | **Hub**: the one edge with a deliberate reverse weight, plus a direct PPR seed |

### 2.1 Identity, not content

An entity the graph names twice is two half-domains that never rank into each other's
recalls — the facet-drift failure, one level up and much more damaging. So
`capture_entity("Invoice", …)` on an existing `Invoice` returns the existing node and
**never overwrites its definition**.

That refusal is deliberate. Silently rewriting a ratified definition would let any
agent redefine the domain mid-change with no gate. The paths that remain are the honest
ones: a human edits the body, or an agent captures a `decision`/`concept` with a
`CONTRADICTS` edge — which flags, and reaches review, like every other disagreement.

**Near-duplicate names warn but still mint** — the opposite of facet governance, where
a warned label is *skipped*. The asymmetry is principled: entities have a mandatory
human ratification gate that facets do not. "Is `Client` the same as `Customer`?" is an
identity question a person should answer while looking at both definitions, not
something to refuse at capture time in a way that leaves the agent unable to name a
genuinely distinct concept. The collision warning is carried into the proposal event, so
the human sees it at the gate.

### 2.2 The identity ladder, and who may climb it

```
capture_entity  ──►  proposed  ──[human]──►  confirmed  ──[human]──►  retired
                        ▲                                                │
                        └──────────────── [human] ───────────────────────┘
```

Status is **derived by folding journal events** (latest of `entity_proposed` /
`entity_confirmed` / `entity_retired` wins) — never a stored column. Same mechanism as
slice liveness, and for the same reason: order-independence after a git-sync merge. An
entity with no lifecycle events at all reads as `proposed`, so a hand-inserted row is
not more trusted than one that went through the surface.

Only `entity_proposed` is reachable from the agent surface. Confirmation and retirement
are privileged human acts (GUI, or an agent acting as the human's per-item scribe
through the GUI API). This is the same split as trust and tier, for the same reason: an
agent that proposes and then confirms its own proposal is not a gate.

**Retirement is supersession, not deletion.** The node stays, its `ABOUT` edges stay
traceable, it just leaves the root set — so the next sweep sends it dormant. Retirement
also **outranks a lifetime promotion**; without that, a promoted entity would be
permanently unretirable.

### 2.3 Liveness by class

The sweep's root set gains *every non-retired entity*, independent of tier. The domain
outlives the change that happened to name it first, so an entity must not need a
promotion to survive the sweep of that change.

The root set deliberately does **not** expand along `ABOUT`. A surviving `Invoice` does
not keep every note ever written about invoices live — otherwise one long-lived hub
would pin the whole graph and nothing would ever go dormant again. Entities are
long-lived *points*, not long-lived *neighbourhoods*.

### 2.4 No decay

Recency is pinned to `1.0` for entities. Age is evidence of staleness for a *claim*; for
*identity* it is evidence of nothing.

No-decay is not immunity: flag penalties still apply in full, because a renamed or
misdefined entity should still be demotable. Exactly one term changes.

### 2.5 The hub

`ABOUT` (content → entity) is the attachment edge, and it is the **only** edge type
whose reverse direction carries weight (`DEFAULT_ENTITY_HUB_WEIGHT = 0.35`).

That is the payoff. Landing on `Invoice` pulls what the project knows about invoices —
including artifacts captured under *other* goals, in *other* changes, that no
goal-forward path would ever reach. It is the "relevant evidence lives elsewhere" case
that multi-seed retrieval exists to fix, now with a structural handle instead of relying
on embedding luck. Damping keeps it proportionate: PPR row-normalizes out-weights, so a
3-artifact entity contributes ~0.35 to each and a 30-artifact one ~0.035 — a popular
entity broadens the bundle rather than swamping it. The weight is a config dial; 0.0
turns entities into pure sinks.

Entities also seed PPR **directly** from the query (alongside facet values), rather than
being expanded like a facet's membership. The difference matters: a facet is a label
whose members *are* the content, while an entity is a node with a walkable
neighbourhood, so seeding it lets PPR decide how far into "everything about Invoice" to
go. Retired entities are never seeded.

Finally, `impact_of` follows `ABOUT` backwards: renaming or redefining `Invoice` ripples
to everything written about it, and the amendment flow must see that blast radius first.

---

## 3. Modelling the domain: two approaches

Establishing the entity set is a different job on a greenfield project than on a
brownfield one, and the difference is **who authors the list** — never who ratifies it.
Both paths end at the same human confirmation gate, and the store cannot tell them
apart except by the provenance recorded in the proposal event.

### 3.1 Greenfield — the user drives

The domain does not exist in any artifact yet; it exists in people's heads. The agent's
job is **elicitation and transcription**, not invention: an agent that invents entities
on a greenfield project is writing fiction that the codebase will then be built to
match.

Shape: work outward from the product's nouns, one bounded area at a time. For each
candidate, get from the user (a) the canonical singular name, (b) one or two sentences
that tell it apart from its neighbours, (c) whether it has identity of its own or is a
part of something else. Capture with the user's own words as `evidence`. Stop at the
edge of what the user can state confidently — a speculative entity captured "to be
thorough" outranks nothing and pollutes every future recall.

### 3.2 Brownfield — the agent proposes, the user reviews

The domain is already implicit in the code, and usually inconsistent: two names for one
thing, one name for two things, and terms in the schema that nobody says out loud. The
agent's job is **extraction with evidence** — every proposal carries a `file:line` so
the reviewer can check it in seconds rather than adjudicating from memory.

Shape: mine the persistence schema, the core/domain modules, and any existing PRD or
glossary; propose in batches small enough to review in one sitting; surface the *drift*
findings explicitly (synonyms, collisions, code-only terms), because those are the
findings only this pass produces and they are worth more than the uncontroversial
entities.

The extracted set is a hypothesis about the domain, not the domain. Nothing is settled
until confirmed.

### 3.3 What the store does differently

Nothing, by design. `evidence` — a `file:line` for extraction, the user's words for
elicitation — goes into the `entity_proposed` event's reason, where it is derived
journal data rather than a metadata blob on the node (the `MT3-30` §9 property-graph
rule). One mechanism, two workflows.

---

## 4. Deliberately not built

- **Aliases as a first-class mechanism.** Alternative names go in the definition prose,
  where seed-discovery embeddings already pick them up. A separate alias structure would
  be a second vocabulary to govern, and the collision detector plus the human gate cover
  the case that motivated it. Revisit if synonym misses show up in practice.
- **Typed entity↔entity relations** (`OWNS`, `PLACES`, …). Edges carry no properties, so
  the verb would have to become an edge type per relation — a vocabulary explosion. The
  existing pattern already covers it: the relationship *statement* is a `concept` node
  `ABOUT` both entities ("an invoice aggregate owns its line items; totals are derived").
  `DEPENDS_ON` between entities carries part-of structure where it is needed.
- **Entity-typed subclasses** (`Person`, `ExternalRef` as distinct node types). They are
  members of this class, not classes of their own; a facet distinguishes them if a
  project needs the distinction.

---

## 5. Open dials

| Dial | Default | When to move it |
|---|---|---|
| `DEFAULT_ENTITY_HUB_WEIGHT` | 0.35 | Down if recall bundles drown in loosely-related entity neighbours; 0.0 makes entities pure sinks |
| `_K_SEED_ENTITIES` | 3 | Up on a large, well-modelled domain where queries name several entities |
| `_ENTITY_SUGGEST_THRESHOLD` | 0.5 | Down if near-duplicate entities are slipping through the gate |
