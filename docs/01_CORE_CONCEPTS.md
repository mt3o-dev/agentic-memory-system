# Core Concepts

This explains the system from first principles so you can reason about it, not just follow instructions. Each section ends with the Linear ticket(s) where the full detail and reasoning live.

---

## 0. The premise

Wikis and Jira are built for humans to read and write. This system is built for an **LLM agent** to retrieve from deterministically and write to under structural constraints. Every advantage the system has over a wiki comes from that difference: relevance-ranked retrieval instead of keyword search, staleness/contradiction detection instead of silent rot, queryable typed relationships instead of decorative hyperlinks.

The system is also **normative**: it doesn't just store context, it shapes how the consuming agent works. Best practices (goal-first thinking, vertical slices, not over-engineering) are encoded as **schema validity rules**, not as system-prompt guidelines — because constraints in the data model can't be ignored under pressure, while prompt instructions can. (See `MT3-25`.)

---

## 1. The substrate: typed graph in SQLite, synced via git

**Nodes** are artifacts (decisions, concepts, constraints, procedures, etc.). **Edges** are typed, directed, weighted relationships between them. Together they form a property graph.

Storage is **SQLite-as-graph**: two core tables (`nodes`, `edges`), traversal via recursive CTEs. Not a dedicated graph DB — Kuzu was the original pick but was **archived in October 2025** (Apple acqui-hire), and separately, the git-sync requirement is only cleanly solvable on SQLite.

**Git sync** tracks a legible **text dump** and treats the database as a local build artifact, so the store produces real line-wise diffs and merges like source code rather than an opaque binary blob. The store rebuilds itself from the dump on open and refreshes the dump on close, which needs no setup at all. (It was originally a git clean/smudge filter — but filter config is local-only and git will never auto-register one, so that design could not be made transparent. See `09_GIT_SYNC.md`.)

→ `MT3-22` (storage + sync), `MT3-17` (schema)

---

## 2. Two orthogonal axes (the recurring insight)

The single most important conceptual move in the whole design: **several things that look like one axis are actually two**. Conflating them was the repeated early mistake; separating them resolved it each time.

- **Salience** (how generally relevant is this?) vs **Liveness** (is it currently in-scope?). A lifetime lesson is high-salience and always live; a slice's implementation detail is low-salience and only live while that slice is active. (See §4.) Domain entities are the case that proves the axes really are separate: they are permanently live without being high-salience, because liveness follows from *what kind of node it is*, not from how important it is.
- **Tier** (salience level) vs **Type** (what kind of memory it is). Type determines *dynamics* — see §5.
- **Content-retrieval** vs **provenance-query** vs **liveness-marking** — the same edge can be traversable in one channel and invisible in another. (See §7.)

If you find yourself building a single mechanism that's trying to serve two of these at once, stop — it's probably the category error again.

→ `MT3-18`, `MT3-29`, `MT3-30`

---

## 3. Retrieval: deterministic, multi-seed PageRank

Retrieval must be a **pure function** — same inputs, same output, no LLM deciding what's relevant. This is what makes it testable, cacheable, and trustworthy.

Three sub-problems, each researched separately (all under `MT3-20`):

- **Selection** — *what* to pull. Multi-seed **Personalized PageRank**: the active **Goal node is a mandatory, dominant-weighted seed** (preserves the "you can't retrieve without a goal" guarantee), plus supplementary seeds found via embedding similarity (fixes the "relevant evidence lives elsewhere" brittleness). PPR is pure linear algebra, so multi-seed costs nothing in determinism. The goal-vs-breadth tradeoff becomes a **config dial** (seed restart-weights), not a binary.
- **Scoring** — how to *rank*. `effective_score = hop_decay × (α·retrieval_weight + β·trust_weight + γ·recency)`. The hybrid additive/multiplicative shape matters: pure multiplication was a **bug** (any near-zero factor zeroed everything, wiping new-but-relevant nodes). Hop decay is **reciprocal** `h/(d+h)` (gentle — distant links fade but don't vanish). Decay rates are **tier- and type-keyed**. Velocity is a tie-breaking tilt, not a primary term.
- **Serialization** — what crosses the wire to the agent. **Content as text, relationships as explicit edge-lines.** (Measured finding: LLMs reason *better* from explicit triples than prose, and prose-ifying a graph *loses* information.) Stable opaque IDs are sent directly — they double as the write-back handle for the feedback loop.

→ `MT3-20` (all three passes are separate comments)

---

## 4. Liveness / archival: garbage-collection, not reference-counting

Scope-bound artifacts (a slice's implementation details) shouldn't sit on the salience ladder — their relevance is *conditional*, not faint. They should be **archived (dormant) when their scope is inactive, and reactivated wholesale when it reopens** — not slowly decayed.

The naive rule "archive when all `SCOPED_TO` targets are inactive" is **reference-counting**, and it breaks on foundations: a foundation underlies many slices, so it'd be archived the moment the last slice goes quiet — exactly backwards.

The fix is **mark-and-sweep reachability** (from garbage collectors): define a **root set** (long-term + lifetime tiers + currently-active slices), trace outward, archive only the unreachable. Foundations are roots, so they're never archived. This also cleanly separates two fates the salience model couldn't: *speculative-never-shipped* (unreachable + low weight → swept, dies) vs *shipped-but-inactive* (unreachable + root-derived → archived, waits).

Liveness is **derived, not stored** — computed by reachability, not a maintained flag.

→ `MT3-18`

---

## 5. Memory types determine dynamics

Borrowed from cognitive science (CoALA), but adopted **only where it changes behavior** — relabeling existing concepts for their own sake is a documented anti-pattern. The cache/salience framing isn't wrong, it was *over-scoped*. Corrected:

- **Semantic** (facts, concepts, constraints) — decays, trust-weighted, consolidates, supersedes. The cache machinery legitimately lives *here*.
- **Episodic** (the decision journal) — **immutable, append-only, never decays**. What happened stays permanently true; only its relevance changes. This *is* an event log natively (see §6).
- **Procedural** (skills, workflows) — designer/workflow-**authored**, **reinforced** on successful use, **does not disuse-decay**, high bar to auto-mutate. Almost the opposite of cache eviction.
- **Domain entities** (`Invoice`, `Customer`; also `Person`, `ExternalRef`) — the 4th class. An entity **names** something the project's language refers to; the other three **assert** things that could be true or false. `Invoice` is not true and `Customer` is not false — you rename, split, merge, or retire them, you cannot contradict them. So they get an *identity* ladder (proposed → confirmed → retired) instead of a validity one, they never decay, they are never consolidated, and they are roots of the live set **by class rather than by tier** — the domain outlives the change that named it. They are also the graph's **hubs**: `ABOUT` is the one edge type whose reverse direction carries weight, so landing on an entity pulls what the project knows about it, including artifacts captured under other goals.

  (This class was originally scoped as "reference entities" — Person/ExternalRef. That named a subcase; the general case is the domain model, of which those are two members. See `06_DOMAIN_ENTITIES.md`.)

"Promotion" was overloaded and is now split: **within-type strengthening** (a tentative fact becomes established) vs **between-type consolidation** (an episodic pattern abstracts into a semantic fact or procedure). Consolidation triggers on **cross-change recurrence**, runs **upward and additively** (it mints an abstraction; no instance is edited, archived, or re-tiered), and has a **split owner** — a deterministic read-only detector any agent may run, and a privileged commit, because a consolidated node exists to be promoted past the sweep. Episodic→procedural stays unbuilt while there is no procedural node type. See `07_CONSOLIDATION.md`.

→ `MT3-29`, `MT3-18`

---

## 6. Trust, staleness, and the journal (event-sourcing)

**Trust is event-sourced.** A node's `trust_weight` is *computed by folding its events* in the decision journal, not mutated in place. This is what makes it **order-independent and merge-safe** — critical because git-sync means two people's contradictions arrive out of order after a merge. (In-place decrement was order-sensitive — a real bug.)

**Staleness propagates by flagging, not decrementing.** When something is contradicted, downstream nodes get a `needs_review` **flag** (idempotent — flagging twice = flagging once; merge-safe) rather than a numeric trust subtraction. The flag applies a **scoring penalty at query time** without touching stored trust, so a false alarm is free to reverse (just clear the flag). The cascade is **self-bounding** (each hop multiplies by an edge weight < 1, shrinking geometrically). The review queue surfaces **origins, not consequences** — review the one contradicted foundation, not its 40 mechanically-affected dependents.

The **decision journal** records every flag/clear/commit *with its reason* — it's simultaneously the human-readable audit trail and the event store trust is recomputed from. One mechanism, two payoffs.

→ `MT3-23` (staleness), `MT3-28` (event-sourced trust)

---

## 7. Who decides what's true? The 3-tier resolver

In an automated workflow there's no human watching, so "a reviewer decides" needs unpacking. Validity assessment is a tiered ladder, cheapest first:

1. **Deterministic rules** — clear flags needing no judgment (contradiction's source was itself superseded; node re-confirmed by a new high-trust artifact). No LLM.
2. **Evaluator agent** — adjudicates genuinely ambiguous flags. A **distinct model**, **separate from the working agent** (so it has no task-completion incentive to clear flags for convenience). Runs in **batch**, writes verdicts **directly to the journal**, and can **defer** to humans when its own confidence is low.
3. **Human** — reserved for the highest stakes (anything touching **lifetime**-tier nodes).

The working agent **never** mutates trust — it can propose (emit a flag) but the commit goes through rules or the evaluator. Exactly one trust-mutation path, always audited.

→ `MT3-27`

---

## 8. Categorization: facets, not a tree

A single directory-tree forces every artifact into one "essence" and rots within months — a problem information science named in 1933. The answer is **faceted classification**: several small independent facets (subsystem, node-type, lifecycle, tier), each artifact carrying one value per relevant facet. Cross-cutting concerns dissolve (an artifact just holds multiple facet values). Facets are reorganization-resistant — there's no master tree to invalidate.

Facet values come from a **controlled vocabulary** (an LLM inventing labels freely *is* a folksonomy generator → synonym chaos). **Embeddings** detect synonym collisions at creation time — but as a *suggester* ("did you mean existing `UX`?"), never an auto-merger, because similarity ≠ equivalence ("authentication"/"authorization" are close but distinct). Facet count is **derived from graph size** (logarithmic), surfaced as a suggestion, not auto-created.

→ `MT3-19`

---

## 9. Provenance & metadata: the property-graph rule

No freeform `metadata: json` blob — it violates the property-graph model (properties must be atomic single-valued scalars) *and* hides data from traversal/scoring. The rule: **"will you query *from* it?" → node+edge; "only read it *off* the node?" → atomic scalar property.**

- `created_at` is a stored column. **Every other date** (`updated_at`, `archived_at`, ...) is a journal-event timestamp, **derived** not stored.
- People → `Person` entities; external docs → `ExternalRef` entities — both ordinary members of the domain-entity class (§5), connected by edges that carry role + timestamp **on the edge**.
- The same rule is why an entity's **provenance** (the `file:line` it was extracted from, or the user's words that named it) lives in its `entity_proposed` journal event rather than as a field on the node: you read it once, at the ratification gate, and it is a fact about an event.
- These provenance edges are **non-structural** — invisible to content retrieval, but first-class for provenance queries. (This is what motivates the per-channel edge-policy idea in §2.)

→ `MT3-30`

---

## 10. Cross-cutting principles (the "grain" of the system)

When in doubt, these patterns have resolved most questions consistently — follow them:

- **Derived, not stored.** Liveness, effective_score, trust, non-creation dates — all *computed* from an authoritative log, with any stored value being a cache/snapshot over it.
- **Orthogonal axes.** Keep salience/liveness, tier/type, and the query-channels separate. Conflation is the recurring bug.
- **Config dials over binaries.** Hard either/or choices have repeatedly turned out to be a tunable parameter with a sensible default (seed weights, penalty strength, facet count).
- **Schema constraints over prompts.** Guardrails belong in the data model's validity rules, where they can't be ignored.
- **Dogfooding.** The normative constraints meant to steer a consuming agent also apply to building *this* project — if you're over-engineering, the design is supposed to catch you.
