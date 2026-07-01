# Next Steps & Open Questions

How to go from a settled design to running code, what to build first, and the questions that still need answers.

---

## Guiding principle: tracer bullet first

The single biggest risk for this project — named repeatedly during design — is **the memory system becoming the product**: disappearing into schema elegance and weight-tuning before anything runs. Defend against it structurally by building a **thin vertical slice through every layer first**, then enriching.

Do **not** build the schema to completion, then storage, then retrieval. Build the thinnest thing that touches all of them and works end-to-end. (This is also exactly what the 10x workflow enforces — one bounded change at a time. The project should be built the way the system itself prescribes; see `05`.)

---

## Recommended build order (vertical slices)

Each slice is a capability the system can demonstrably *do*, not a layer it's *made of*. Each ships with a runnable demo + a short note on what was learned (which often answers an open design question against a real implementation).

1. **Capture + recall one node.** Create a single semantic node, persist to SQLite, retrieve by id, serialize to text. Touches schema (one node type, no edges), storage, simplest retrieval, serialization. Proves the pipeline is alive. *(MT3-17, MT3-22, MT3-20.)*

2. **Relate two nodes; recall both via the edge.** Add one edge type (`DEPENDS_ON`), traverse with a recursive CTE, serialize content + edge-line. Proves typed traversal and the content-plus-relationships wire format. *(MT3-17, MT3-20.)*

3. **Rank by relevance.** Introduce `retrieval_weight`/`trust_weight` and `effective_score`; recall returns ordered nodes. Weights enter only now. *(MT3-20.)*

4. **Git-sync round-trip.** Dump-to-text filter; change on one clone, merge on another, confirm legible diff + clean merge. De-risks storage/sync early while the schema is still cheap to change. *(MT3-22.)*

5. **Journal + event-sourced trust.** Append-only event log; compute `trust_weight` by folding events; confirm order-independence by applying two events in both orders. *(MT3-28, MT3-23.)*

6. **Flag-based staleness.** A `CONTRADICTS` edge sets `needs_review`; flagged nodes get the scoring penalty at query time; confirm a false-alarm clear restores state for free. *(MT3-23.)*

7. **Liveness/archival.** Add `SCOPED_TO` + a Slice/change node; implement mark-sweep reachability; confirm a foundation survives when its slices go inactive, and slice-detail archives/reactivates with scope. *(MT3-18.)*

8. **Multi-seed retrieval.** Add facet-value embeddings + Personalized PageRank with goal-dominant seed weights; confirm determinism (same query → same ranking). *(MT3-20, MT3-19.)*

9. **The write-path MCP surface.** Implement the four agent-facing write calls (`create_change`, `capture_artifact`, `link`, `append_event`) + the one read call. Enforce the safety invariant (no trust/flag/promote/archive in the agent surface). *(MT3-21.)*

10. **10x lifecycle binding.** Wire the skills to 10x events: `recall-context` at change start, `capture-artifact` at plan/phase boundaries, `review-staleness` at PR, `archive-on-merge` at merge. *(MT3-24, MT3-21, `05`.)*

After slice 10 the system is a working participant in the 10x development loop. The evaluator agent, consolidation, and GUI come after.

---

## No longer blocked

The "10x framework" that previously blocked the write path **has arrived and is integrated** (`05_10X_INTEGRATION.md`). The write-path cluster (MT3-21 workflow, MT3-24 skills) is now concrete: memory operations bind to 10x lifecycle events rather than being a separate workflow. MT3-21 is rescoped from "design triggers from scratch" to "bind to existing 10x events."

---

## Open questions, by urgency

### Will block implementation soon — resolve early

- **PPR × effective_score composition.** PPR gives *structural* relevance; `effective_score` gives *trust/recency/tier*. Two ranking signals that must combine into one order. How? (Weighted blend? PPR as a multiplier? Filter-then-rank?) Needed for slice 8. *(MT3-20.)*
- **Per-channel edge policy.** An edge's traverse/pull verdict may depend on the query channel (content-retrieval vs provenance-query vs liveness-marking) rather than one global value. Decide whether the edge-policy table needs a channel dimension. Affects slices 2, 7, and all provenance work. *(MT3-20, MT3-30.)*

### Real design work, not yet started

- **The consolidation operation.** Between-type abstraction (episodic→semantic, episodic→procedural) is identified but undesigned: triggers (how many episodic instances?), direction, and owner (likely the evaluator/reflection pass). **Blocks the `consolidate` skill.** *(MT3-18, MT3-29.)*
- **4th dynamics class — reference entities.** Person/ExternalRef neither decay, nor are immutable-events, nor reinforce. Confirm they need their own dynamics class and define it. *(MT3-29, MT3-30.)*
- **Procedural lifecycle specifics.** Reinforcement curve, the "high bar to auto-mutate" gate, reconciling with the slice-archival mechanism it resembles. *(MT3-29, MT3-17.)*

### Genuine forks awaiting a call (have a default, not locked)

- **Rule-based vs learned promotion** — leaning rule-first (a learned policy needs training data the system won't have on day one). *(MT3-18.)*

### Config values to tune in practice (ship a default, don't over-think)

- Flag-penalty coefficients (`base_penalty`, `severity`, `age_factor`) behind the `PenaltyStrategy` interface.
- Evaluator confidence/defer threshold; batch cadence (time vs queue-depth).
- Facet-count params (`base`, `k`, `min`, `max`).
- PPR seed restart-weights (goal-dominant default).
- Per-edge-type halftime `h`; per-tier and per-type decay rates.

---

## Definition of done (for a research/design project)

For each slice or open question, "done" means a runnable demo **plus** a short note (an ADR or Linear comment) recording what was decided and why — and **updating the relevant Linear ticket** so the authoritative record stays current. The design issues are a backlog of *knowledge*; implementing a slice is how you discharge the open questions attached to it.
