# Flag-based Staleness — Plan Brief

> Full plan: context/changes/flag-based-staleness/plan.md

## What & Why

A `CONTRADICTS` edge should flag its target `needs_review` and demote that node's ranking
**at query time**, never by writing down its `trust_weight`. Because the penalty is derived
from the flag at read time, a "false-alarm clear" flips the flag off and the node's effective
score is restored **for free**. This is the read-time mirror of Slice 5's write-time
event-sourced trust, and reuses that slice's port/adapter style.

## Starting Point

`needs_review` and the `contradiction_raised`/`contradiction_cleared` event types already
exist and round-trip. What's missing: the `CONTRADICTS` edge type (`EdgeType` has only
`DEPENDS_ON`; the edges SQL `CHECK` allows only `DEPENDS_ON`), any flag-setting write path
(`write_edge` is a plain insert), and any penalty term in `recall()`'s score
(`hop × (α·retrieval + β·trust + γ·recency)`, `storage.py:229`).

## Desired End State

`raise_contradiction()` writes the edge, sets the flag, and appends a `contradiction_raised`
event atomically. `recall()` applies a **config-swappable** `PenaltyStrategy` (three adapters,
default = the locked MT3-23 formula). `clear_contradiction()` flips the flag off and the node's
score returns **exactly** to its pre-flag value. A resolver ladder exists as ports (real rules
tier, stubbed evaluator/human). All of it round-trips through git-sync unchanged.

## Key Decisions

| Decision | Choice | Why | Source |
|---|---|---|---|
| Resolver ladder scope | Full ladder as **ports**; real `RulesResolver`, evaluator + human **stubbed** | Delivers the switchable structure now without absorbing MT3-27's LLM evaluator; stubs swap in later with no call-site change | R1Q1 + R2Q1 (default) |
| Penalty site | **Three** switchable strategies behind a `PenaltyStrategy` port + comparison ADR | User wants the tradeoff surfaced and config-selectable, matching the `FoldStrategy` pattern | R1Q2 |
| Flag-set path | Explicit `raise_contradiction()` = edge + flag + event, atomic | Keeps `write_edge` generic; makes flag+event atomic; event gives audit trail + severity home | R1Q3 |
| Severity source | Carried on the `contradiction_raised` event `weight` | No schema change; reuses an existing field; records how severe each contradiction was | R2Q2 (default) |
| Docs home | ADR co-located in the change folder; docstrings point to it | Matches repo's "runnable demo + a note recording the decision" DoD; versioned next to the change | R2Q3 (default) |
| Testing | Hypothesis property tests per strategy + one integration/demo test | Mirrors Slice 5; makes "restores for free" a proven invariant | R2Q4 (default) |
| `age_factor` | Hook present, **default off** (`= 1.0`) | Matches MT3-23 consensus; avoids designing a decay curve in this slice | consensus |

> R2 decisions (and R1Q1's ladder scope) were taken as recommended defaults because the user
> stepped away mid-Round-2. All are cheap to revise.

## The three penalty strategies (default: `TrustTermPenalty`)

Shared scalar: `penalty = clamp(base_penalty(0.5) × severity × age_factor(1.0), 0, 1)`,
`0` when not flagged. Adapters differ only in where `(1 − penalty)` lands:

- **`TrustTermPenalty`** — `β·trust·(1−p)` only. Erodes trustworthiness, keeps findability. *(locked MT3-23 formula)*
- **`WholeScorePenalty`** — whole score `× (1−p)`. Aggressive, buries contested items.
- **`TrustRetrievalPenalty`** — trust + retrieval penalized, recency preserved. Contradicted-but-recent items surface briefly for triage.

## Phases at a Glance

| Phase | Delivers | Key risk |
|---|---|---|
| 1 — Schema + edge | `EdgeType.contradicts`, widened SQL CHECK | Pre-existing DB keeps old CHECK → rebuild via `restore_db` (documented, no migration code) |
| 2 — Write path | `raise_contradiction` / `clear_contradiction` + events | Atomicity of edge+flag+event in one txn |
| 3 — Penalty port | `penalty.py` (3 adapters) + `recall()` integration | Must be behavior-preserving for **un**flagged nodes (no regression) |
| 4 — Resolver ladder | `resolver.py` ports; rules real, rest stubbed | Keeping the evaluator tier a genuine stub, not creeping into MT3-27 |
| 5 — ADR | penalty-strategies comparison + worked examples | Examples must be numerically correct vs. code |
| 6 — Tests | property + integration + resolver | Exact-restore invariant assertion |

**Prerequisites:** none — Slices 1–5 complete; this builds on them without changing their behavior.
**Estimated effort:** Medium — two new modules (`penalty.py`, `resolver.py`), two new store
methods, one enum value + one SQL CHECK widening, one ADR, three test files. No serialization
change (edges + event weight already round-trip).

## Out of Scope

Real evaluator agent (MT3-27), automatic resolver-run binding / review-queue UI (Slice 9/10),
`age_factor` decay curve, downstream contradiction propagation (MT3-23's propagation half),
any change to `trust_weight`/`FoldStrategy`.

## Open Risks & Assumptions

- **Default-taken decisions** (ladder scope, severity source, docs home, testing) may not match
  intent since the user was AFK — first review point.
- **CHECK migration**: pre-existing `context/memory-graph.db` needs a `restore_db` rebuild to
  accept `CONTRADICTS`; disposable/git-rebuilt DBs are unaffected.
- **Severity lookup at query time**: penalty reads the latest `contradiction_raised` event per
  *flagged* node — bounded cost (few flagged nodes), zero extra queries for unflagged nodes.

## Success Criteria (summary)

- `CONTRADICTS` round-trips through git-sync.
- Flag set atomically with a `contradiction_raised` event.
- `recall()` demotes flagged nodes via a swappable `PenaltyStrategy` (3 adapters; default = MT3-23 formula).
- `clear_contradiction` restores effective score **exactly** (proven, not hoped).
- Resolver ladder as ports; rules real, evaluator/human stubbed.
- Comparison ADR with worked examples; full existing suite passes.
