# Flag-based Staleness — Implementation Plan

> Slice 6 of `docs/03_NEXT_STEPS.md`. Linear: MT3-23.
> Brief: `context/changes/flag-based-staleness/plan-brief.md`
> Penalty-strategy comparison ADR: `context/changes/flag-based-staleness/penalty-strategies.md` (written in Phase 5)

## Problem & Goal

A `CONTRADICTS` edge should mark its target node `needs_review` and demote that node's
retrieval ranking **at query time** — not by decrementing its stored `trust_weight`. A
"false-alarm clear" flips the flag back off and the node's effective score is restored
**for free**, because the penalty is derived from the flag at read time and never written
into the node's weights.

This builds directly on Slice 5 (event-sourced trust): the append-only `events` log and
the `FoldStrategy` port already exist. Slice 6 adds the mirror-image read-time concept — a
`PenaltyStrategy` port — plus the contradiction write path and a resolver ladder.

## Current State (verified)

- `Node.needs_review: bool = False` exists and round-trips through storage + serialization
  (`schema.py:30`, `storage.py:96/114/205`, `serialization.py:13/30/144`).
- `EventType.contradiction_raised` / `contradiction_cleared` exist (`schema.py:47-48`) and
  serialize via the `[event:<id>]` block; `Event.weight` is dumped/parsed (`serialization.py:45`).
- `EdgeType` has **only** `depends_on = "DEPENDS_ON"` (`schema.py:36`); the edges SQL `CHECK`
  allows only `'DEPENDS_ON'` (`storage.py:28`). **`CONTRADICTS` does not exist yet.**
- `recall()` scoring is `hop_decay × (α·retrieval + β·trust + γ·recency)` with
  `α=0.5, β=0.3, γ=0.2` (`storage.py:229-233`). **No penalty term.**
- `write_edge()` is a plain insert with no side effects (`storage.py:131`).
- `FoldStrategy` port precedent: `fold.py` + `MemoryStore(fold_strategy=...)`.
- `scripts/restore_db.py` is generic (uses `write_edge`, no hardcoded edge types) — a fresh
  restore inherits any schema change automatically.

## Decisions (from questioning)

| # | Decision | Choice | Source |
|---|----------|--------|--------|
| 1 | Resolver ladder scope | **Full ladder as ports**: real `RulesResolver`, evaluator + human tiers as stub adapters behind the interface (evaluator = MT3-27, deferred) | Round 1 Q1 + Round 2 Q1 (default) |
| 2 | Penalty application site | **Three switchable strategies** behind a `PenaltyStrategy` port, config-selected like `FoldStrategy`, with a comparison doc | Round 1 Q2 |
| 3 | Flag-set path | **Explicit `raise_contradiction()`** that writes the edge, sets the flag, and appends a `contradiction_raised` event atomically | Round 1 Q3 |
| 4 | Severity source | **Carried on the `contradiction_raised` event's `weight`** (no schema change; audit trail) | Round 2 Q2 (default) |
| 5 | Docs home | **ADR co-located** in the change folder; short docstrings in `penalty.py` point to it | Round 2 Q3 (default) |
| 6 | Testing | **Hypothesis property tests per strategy + one integration/demo test** | Round 2 Q4 (default) |
| 7 | `age_factor` | Hook present, **default OFF** (`age_factor = 1.0`); config flag to enable later | Consensus (MT3-23), self-determined |

> Decisions 1, 4, 5, 6 were taken as the recommended defaults because the user stepped away
> mid-Round-2. All are cheap to revise — flagged here for review.

## The three penalty strategies

Penalty scalar (shared by all strategies):
`penalty = clamp(base_penalty × severity × age_factor, 0, 1)`, and `penalty = 0` when
`node.needs_review` is False. Defaults: `base_penalty = 0.5`, `severity` = latest
`contradiction_raised` event weight (default `1.0`), `age_factor = 1.0` (hook off).

Given per-node score components `hop_decay`, and weighted terms `α·retrieval`, `β·trust`,
`γ·recency`, the three adapters differ only in **where** `(1 − penalty)` is applied:

| Strategy | Formula | Best when |
|----------|---------|-----------|
| `TrustTermPenalty` **(default)** | `hop × (α·retrieval + β·trust·(1−p) + γ·recency)` | You want a contradiction to erode *trustworthiness only*, leaving findability/recency intact — the locked MT3-23 formula. A still-relevant but disputed node stays reachable. |
| `WholeScorePenalty` | `hop × (α·retrieval + β·trust + γ·recency) × (1−p)` | You want a flagged node aggressively demoted regardless of why it ranked — e.g. a review queue that should bury contested items hard. |
| `TrustRetrievalPenalty` | `hop × (α·retrieval·(1−p) + β·trust·(1−p) + γ·recency)` | You want both trust and findability penalized but recency preserved, so a *recently* contradicted node still surfaces briefly for triage before fading. |

The comparison doc (Phase 5) carries worked numeric examples for each.

## Phases

### Phase 1 — Schema + edge type
- `schema.py`: add `EdgeType.contradicts = "CONTRADICTS"`.
- `storage.py`: widen `_CREATE_EDGES` `CHECK(type IN (...))` to include `'CONTRADICTS'`.
- **Migration note**: SQLite can't `ALTER` a `CHECK`. Pre-existing `context/memory-graph.db`
  keeps the old constraint and would reject `CONTRADICTS`. The supported path is a rebuild
  via `scripts/restore_db.py` from the git-synced dump (fresh `CREATE` picks up the new
  CHECK). Document this in the plan-brief's risks; no runtime migration code.
- **Serialization**: no change — edges serialize generically (`serialization.py:36/124`),
  so `CONTRADICTS` round-trips as soon as the enum has it.
- **Acceptance**: a `CONTRADICTS` edge can be written, read back, and round-tripped through
  `dump_all`/`parse_dump`.

### Phase 2 — Contradiction write path
- `storage.py`, new methods (single transaction each):
  - `raise_contradiction(source_id, target_id, *, severity=1.0, source, reason) -> Edge`:
    writes the `CONTRADICTS` edge, sets `target.needs_review = True`, appends a
    `contradiction_raised` event on the **target** (`weight=severity`, `polarity=-1`).
  - `clear_contradiction(target_id, *, source, reason) -> None` (confirm false-alarm):
    sets `needs_review = False`, appends a `contradiction_cleared` event
    (`polarity=+1`). Leaves the edge in place for audit; the flag drives the penalty, so
    clearing it restores the score for free.
  - `_latest_severity(node_id) -> float`: latest `contradiction_raised` event weight, else `1.0`.
- **Acceptance**: raise sets flag + event; clear unsets flag + event; both atomic.

### Phase 3 — `PenaltyStrategy` port + `recall()` integration
- New module `penalty.py`:
  - `BASE_PENALTY = 0.5`.
  - `compute_penalty(node, severity, *, base=BASE_PENALTY, age_factor=1.0) -> float`
    (returns 0 when not `needs_review`; clamps to `[0,1]`).
  - `ScoreComponents` frozen dataclass: `hop_decay, alpha, retrieval, beta, trust, gamma, recency`.
  - `PenaltyStrategy` Protocol: `apply(components, penalty) -> float`.
  - Adapters: `TrustTermPenalty` (default), `WholeScorePenalty`, `TrustRetrievalPenalty`.
- `storage.py`:
  - `MemoryStore(__init__)` gains `penalty_strategy: PenaltyStrategy | None = None`
    (defaults to `TrustTermPenalty()`), mirroring `fold_strategy`.
  - `recall()` `_score`: build `ScoreComponents`, compute `penalty` (severity via
    `_latest_severity` only for flagged nodes; unflagged → penalty 0, zero extra queries),
    delegate final composition to `self._penalty_strategy.apply(...)`.
  - `age_factor` stays `1.0` (hook; optional `age_factor_enabled` flag wired but off).
- **Acceptance**: unflagged nodes score identically to today (no regression); a flagged node
  is demoted per the selected strategy; clearing the flag restores the exact prior score.

### Phase 4 — Resolver ladder (ports; rules real, rest stubbed)
- New module `resolver.py`:
  - `ResolverVerdict` enum: `auto_clear`, `defer`, `needs_human`.
  - `Resolver` Protocol: `resolve(node, events) -> ResolverVerdict`.
  - `RulesResolver` (real): deterministic rules, e.g. if the node's folded confirmation
    evidence outweighs the latest contradiction → `auto_clear`; else `defer`.
  - `EvaluatorResolver` (stub, MT3-27): always `defer` — placeholder behind the interface.
  - `HumanResolver` (stub): always `needs_human` — surfaces to a (future) review queue.
  - `LadderResolver`: chains rules → evaluator → human; per MT3-23 only escalates past rules
    for high-tier (e.g. `lifetime`) nodes, else stops at the rules verdict.
- Not wired into an automatic loop this slice — exposed as callable API + tested. Auto-run
  binding is Slice 10 / MT3-27 territory.
- **Acceptance**: `RulesResolver` returns correct verdicts on constructed event histories;
  stubs return their fixed verdicts; `LadderResolver` escalates only for lifetime tier.

### Phase 5 — Penalty-strategies ADR
- Write `context/changes/flag-based-staleness/penalty-strategies.md`: the comparison table
  above + a worked numeric example per strategy (same node, same severity, showing the three
  resulting scores) + a "when to pick which" section. Add short docstrings on each adapter
  pointing to this file.
- **Acceptance**: doc exists, examples are numerically correct against the implemented formulas.

### Phase 6 — Tests
- `tests/test_penalty_strategies.py` (Hypothesis, mirrors `test_fold_strategies.py`):
  per strategy — unflagged ⇒ no penalty (score == baseline); penalty monotonic non-increasing
  in severity; result finite and ordered sanely; penalty ∈ [0,1].
- `tests/test_contradiction_flow.py` (integration/demo): build a small graph, `recall`
  baseline, `raise_contradiction`, assert target demoted, `clear_contradiction`, assert score
  **exactly** restored to baseline — the "restores for free" invariant. Round-trip the graph
  (incl. CONTRADICTS edge + events) through `dump_all`/`parse_dump`.
- `tests/test_resolver.py`: `RulesResolver` verdicts; stub verdicts; `LadderResolver` tier gating.
- Run full suite; fix any fallout (none expected — `recall` is behavior-preserving for
  unflagged nodes and no existing tests create flagged nodes).

## Out of scope (deferred)

- Real evaluator agent (MT3-27) — stub only here.
- Automatic resolver-run binding / review-queue UI surfacing (Slice 9/10).
- `age_factor` decay curve (hook present, off).
- Propagation of contradictions to downstream nodes (`downstream_trust_delta`) — MT3-23's
  propagation half; this slice is the single-node flag→penalty→clear tracer bullet.
- Any change to `trust_weight` storage / `FoldStrategy` (Slice 5, unchanged).

## Success criteria

- `CONTRADICTS` edge type exists and round-trips through git-sync.
- `raise_contradiction` sets `needs_review` + emits a `contradiction_raised` event atomically.
- `recall()` demotes flagged nodes via a **config-swappable** `PenaltyStrategy` (3 adapters),
  default `TrustTermPenalty` = the locked MT3-23 formula.
- `clear_contradiction` restores a node's effective score **exactly** to its pre-flag value
  (proven by property/integration test, not asserted by hope).
- Resolver ladder exists as ports; `RulesResolver` is real; evaluator/human are stubs.
- Penalty-strategy comparison ADR with worked examples exists.
- Full existing test suite still passes.

## Progress

### Phase 1: Schema + edge type
#### Automated
- [x] 1.1 Add `EdgeType.contradicts = "CONTRADICTS"` to schema.py
- [x] 1.2 Widen `_CREATE_EDGES` SQL CHECK to include `'CONTRADICTS'`
- [x] 1.3 CONTRADICTS edge writes, reads, and round-trips through dump/parse

### Phase 2: Contradiction write path
#### Automated
- [x] 2.1 `raise_contradiction()` — edge + flag + contradiction_raised event, atomic
- [x] 2.2 `clear_contradiction()` — unset flag + contradiction_cleared event
- [x] 2.3 `_latest_severity()` helper

### Phase 3: PenaltyStrategy port + recall integration
#### Automated
- [x] 3.1 penalty.py: `compute_penalty`, `ScoreComponents`, `PenaltyStrategy` Protocol
- [x] 3.2 Adapters: TrustTermPenalty (default), WholeScorePenalty, TrustRetrievalPenalty
- [x] 3.3 `MemoryStore(penalty_strategy=...)` wiring, default TrustTermPenalty
- [x] 3.4 recall() builds ScoreComponents + penalty, delegates composition; unflagged unchanged

### Phase 4: Resolver ladder (ports; rules real, rest stubbed)
#### Automated
- [x] 4.1 resolver.py: `ResolverVerdict`, `Resolver` Protocol
- [x] 4.2 `RulesResolver` (real deterministic rules)
- [x] 4.3 `EvaluatorResolver` + `HumanResolver` stubs
- [x] 4.4 `LadderResolver` chaining with lifetime-tier gating

### Phase 5: Penalty-strategies ADR
#### Automated
- [x] 5.1 Write penalty-strategies.md (comparison + worked numeric examples)
- [x] 5.2 Docstrings on each adapter pointing to the ADR

### Phase 6: Tests
#### Automated
- [x] 6.1 test_penalty_strategies.py (Hypothesis property tests per strategy)
- [x] 6.2 test_contradiction_flow.py (integration: exact-restore invariant + round-trip)
- [x] 6.3 test_resolver.py (rules verdicts, stub verdicts, ladder tier gating)
- [x] 6.4 Full existing suite still green
#### Manual
- [ ] 6.5 Eyeball a recall() ordering before/after raise+clear on a small graph
