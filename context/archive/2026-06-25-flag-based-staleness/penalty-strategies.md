# Penalty Strategies — Decision Record

> Slice 6 / MT3-23. Companion to `plan.md`. Implements the `PenaltyStrategy` port in
> `src/agentic_memory_system/penalty.py`.

## Context

A node flagged `needs_review` (by a `CONTRADICTS` edge) must be demoted in `recall()`
ranking **at query time** — never by writing down its stored `trust_weight`, so that a
false-alarm clear restores its score for free. *Where* within the score the demotion is
applied is a genuine design fork with no single right answer, so it is a swappable
`PenaltyStrategy` (config-selected via `MemoryStore(penalty_strategy=...)`, mirroring
`FoldStrategy`). This document records the three shipped strategies, their tradeoffs, and
worked numbers so a future reader can pick the right one for a given deployment.

## The shared penalty scalar

Every strategy consumes the same scalar:

```
penalty = clamp(base_penalty × severity × age_factor, 0, 1)      (0 when the node is unflagged)
```

- `base_penalty = 0.5` — the default strength of a single contradiction.
- `severity` — the weight of the node's latest `contradiction_raised` event (default `1.0`).
  This is the audit-carried "how bad is this contradiction" signal; it is **not** folded into
  `trust_weight` (flag, don't decrement).
- `age_factor` — a hook, **off by default (`1.0`)**. A later slice can make a long-standing
  flag hurt more; the shape of that curve is deliberately undecided here.

The recall score, before any penalty, is:

```
score = hop_decay × (α·retrieval + β·trust + γ·recency)      α=0.5, β=0.3, γ=0.2
```

The strategies differ **only** in where `(1 − penalty)` is inserted.

## The three strategies

| Strategy | Formula | One-line intent |
|----------|---------|-----------------|
| `TrustTermPenalty` **(default)** | `hop × (α·retrieval + β·trust·(1−p) + γ·recency)` | Erode trustworthiness only; keep findability + recency. |
| `WholeScorePenalty` | `hop × (α·retrieval + β·trust + γ·recency) × (1−p)` | Bury the node uniformly, whatever made it rank. |
| `TrustRetrievalPenalty` | `hop × (α·retrieval·(1−p) + β·trust·(1−p) + γ·recency)` | Penalize trust + findability, spare recency. |

Because every strategy multiplies by `(1 − penalty)` and an unflagged node has `penalty = 0`,
**all three are identical to the original formula for unflagged nodes** — selecting a strategy
can never regress ordinary retrieval, only change how flagged nodes are demoted.

## Worked example

One node reached at `depth = 1` (so `hop_decay = 3/(1+3) = 0.75`), freshly created
(`recency = 1.0`), `retrieval_weight = 1.0`, `trust_weight = 1.0`.

Unflagged score = `0.75 × (0.5 + 0.3 + 0.2) = 0.75`.

**Severity 1.0 → `penalty = 0.5 × 1.0 = 0.5`:**

| Strategy | Computation | Score | vs. unflagged |
|----------|-------------|-------|---------------|
| `TrustTermPenalty` | `0.75 × (0.5 + 0.3×0.5 + 0.2)` = `0.75 × 0.85` | **0.6375** | −15% |
| `WholeScorePenalty` | `0.75 × 1.0 × 0.5` | **0.375** | −50% |
| `TrustRetrievalPenalty` | `0.75 × (0.5×0.5 + 0.3×0.5 + 0.2)` = `0.75 × 0.60` | **0.45** | −40% |

**Severity 0.6 → `penalty = 0.5 × 0.6 = 0.3`** (a softer contradiction):

| Strategy | Score | vs. unflagged |
|----------|-------|---------------|
| `TrustTermPenalty` | **0.6825** | −9% |
| `WholeScorePenalty` | **0.525** | −30% |
| `TrustRetrievalPenalty` | **0.57** | −24% |

Two things to read off: the penalty scales smoothly with severity, and at equal severity the
three strategies span a wide demotion range (−15% to −50% at severity 1.0). That spread is the
whole point of making it swappable.

## When to pick which

- **`TrustTermPenalty` (default).** A contradiction means "someone disputes this," not "this is
  irrelevant." Keeping retrieval + recency intact means a disputed-but-central node still
  surfaces so it can actually be reviewed and resolved, just ranked a notch lower. This is the
  locked MT3-23 formula and the right default for a memory whose flagged nodes still need to be
  *found* to be fixed.
- **`WholeScorePenalty`.** When a flag should mean "get this out of my results," e.g. a
  high-trust automated pipeline where contested items must not influence downstream reasoning
  until cleared. Strongest, bluntest demotion; risks burying a node so far it never gets reviewed.
- **`TrustRetrievalPenalty`.** A middle path that still lets recency float a *recently* flagged
  node up briefly — useful when you want fresh contradictions to get a short triage window at
  the top before fading, but don't want old contested nodes lingering on findability alone.

## Consequences

- Adding a fourth strategy is a new class implementing `PenaltyStrategy.apply` — no changes to
  `recall()` or storage.
- `age_factor` is wired but inert; enabling it is a config/parameter decision for a later slice
  and does not require touching the strategies.
- The strategies are pure functions of `ScoreComponents` + `penalty`, so they are unit- and
  property-testable in isolation (see `tests/test_penalty_strategies.py`).
