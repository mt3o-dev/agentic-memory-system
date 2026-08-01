# The Consolidation Operation

*Resolves the `03_NEXT_STEPS.md` open question "The consolidation operation". `MT3-18`,
`MT3-29`.*

---

## 1. What was open

`01_CORE_CONCEPTS.md` §5 split an overloaded word into two:

- **within-type strengthening** — a tentative fact becomes established. Built: it is the
  tier ladder plus journal-folded trust.
- **between-type consolidation** — an episodic pattern abstracts into a semantic fact or
  procedure. *"A type-crossing operation still to be designed."*

Three sub-questions were named and none answered: **triggers** (how many episodic
instances?), **direction**, and **owner**.

## 2. Where the episodic layer actually is

The system has no `episodic` node type, which made "episodic → semantic" hard to place.
It has two episodic *layers*:

1. the **journal** (append-only events — immutable, never decays), and
2. the **change-scoped short/mid-term artifacts**, which are episodic in the sense that
   matters here: they are what one episode of work learned, and the sweep sends them
   dormant when that episode ends.

Layer 2 is what consolidation operates on. Layer 1 is evidence, not raw material — you
do not abstract a `USED` event into a concept.

This also bounds the operation honestly: **episodic → procedural is out of scope**. The
system has no procedural node type; procedures live in skill files, authored by
designers. Consolidation here produces semantic nodes, and says so.

---

## 3. The answers

### 3.1 Trigger — cross-change recurrence

A candidate is a cluster of:

- **≥ 3 live content artifacts** (`_CONSOLIDATE_MIN_INSTANCES`)
- from **≥ 2 distinct change scopes** (`_CONSOLIDATE_MIN_SCOPES`)
- sharing a **facet**
- mutually similar (cosine ≥ `_CONSOLIDATE_SIMILARITY`, 0.45)
- **not already promoted** past mid-term, and **not already consolidated**

The cross-scope requirement is the load-bearing one. Three artifacts inside one change
saying the same thing is *repetition* — a capture-quality problem for review to
flag, not knowledge to abstract. The same statement arrived at independently in two
changes is a pattern that outlived its episode, which is exactly what "abstract this"
should mean.

The exclusions matter as much as the inclusions. Promoted nodes are out because a human
promotion is a stronger statement than any clustering, and re-abstracting them would
just duplicate settled knowledge. Already-consolidated nodes are out so a worked
candidate stops reappearing at every gate — a review queue that re-serves resolved items
is a review queue people stop reading.

There is a **second, always-on trigger** with no threshold at all: the per-change
episode summary at the review gate. Every change consolidates into one change-summary
node before the sweep sends its detail dormant. That path was already prose in the
workflow; it now has a real edge type and a real provenance trail.

Clustering is **greedy single-link over sorted ids**: take the lowest-id unclustered
node as a seed, absorb everything within the similarity radius, repeat. Not k-means, not
hierarchical — the read path must be a pure function of the stored graph (`MT3-20`), and
greedy-from-a-fixed-order is the strongest thing that stays trivially deterministic.

### 3.2 Direction — upward, and strictly additive

Consolidation **mints**. It never edits, merges, archives, or re-tiers an instance.

```
                 ┌──────────────────┐
                 │   abstraction    │  (new semantic node)
                 └──────────────────┘
        CONSOLIDATES ↓ ↓ ↓      ↑ ↑ ↑ DEPENDS_ON
                 ┌──────────────────┐
                 │  instance nodes  │  (unchanged)
                 └──────────────────┘
```

Two edge directions, doing two different jobs:

- **instance `DEPENDS_ON` abstraction** — the content channel. Recall from an instance
  reaches the abstraction, and `impact_of(abstraction)` returns its instances, so
  changing the abstraction shows its blast radius like any other node.
- **abstraction `CONSOLIDATES` instance** — the provenance channel, policy weight **0 in
  both directions**. This is the important one: the abstraction's instances are usually
  dormant *on purpose* — that dormancy is what consolidation bought. Walking
  `CONSOLIDATES` at query time would undo the sweep. It stays queryable in the GUI and in
  provenance reads; the PPR walker never crosses it.

**Type-crossing rule.** The abstraction's type is derived, not freely chosen: a
homogeneous cluster keeps its type (three invariants abstract to an invariant), a mixed
cluster becomes a `concept` — the only type that can hold "these are instances of one
idea" without overclaiming normative force.

### 3.3 Owner — split, exactly like the trust ladder

| Half | Who | Why |
|---|---|---|
| **Detector** (`consolidation_candidates`) | Deterministic, read-only, open to agents and the evaluator | It cannot do damage, and the agent is well placed to spot the pattern and draft the wording |
| **Commit** (`consolidate`) | Privileged: GUI / an agent acting as the human's scribe | A consolidated node exists to be **promoted past the sweep** — an agent that both abstracts and nominates its own abstraction is writing the project's long-term memory unsupervised |

This is the same shape as `raise_contradiction` vs `clear_contradiction`, and for the
same reason. Surfacing is safe; committing is judgment.

The one thing an agent *can* write is the ordinary change summary: a normal
`capture_artifact` plus honest `CONSOLIDATES` edges. That is a recording of what was
distilled from what — not a nomination for permanent memory, which still goes through
the human promotion gate like everything else.

---

## 4. What this does not solve

- **Journal compaction** (`MT3-28`, `compact_events` is still a no-op stub). Different
  problem, different layer.
- **Automatic promotion of consolidated nodes.** They are minted at mid-term like
  anything else and promoted by a human. Automating that would smuggle the whole safety
  model out through the back door.
- **De-consolidation.** If an abstraction turns out wrong, it is contradicted, reviewed,
  and archived like any other node; the instances were never touched, so nothing needs
  unwinding.

---

## 5. Dials

| Dial | Default | When to move it |
|---|---|---|
| `_CONSOLIDATE_MIN_INSTANCES` | 3 | Up on a large graph where 3 is noise; never below 2 |
| `_CONSOLIDATE_MIN_SCOPES` | 2 | Never below 2 — it is what separates a pattern from repetition |
| `_CONSOLIDATE_SIMILARITY` | 0.45 | Up if candidates are grab-bags; down if obvious recurrence is missed |
