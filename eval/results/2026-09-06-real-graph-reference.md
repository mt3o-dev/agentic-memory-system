## 2026-09-06 — real-graph reference check

Not a benchmark run: a check on **two claims made about the benchmark**, using real graphs
instead of the synthetic corpus. Real graphs carry no gold labels, so nothing here measures
correctness — only *disagreement*, which is enough for both claims because both predict a
difference rather than a right answer.

**Method.** Both stores were **copied** before being opened. Pointing `MemoryStore` at
another project's live store would take its lock, possibly rebuild it from its dump, and
refresh that dump on close — mutating someone else's graph to run an experiment. Queries
are each graph's own facet labels: real vocabulary, and the kind of topic someone searches.

| store | nodes | scopes | largest scope |
|---|---|---|---|
| `kartka` | 90 | 3 | **48 artifacts** |
| `agentic-memory-system` | 137 | 19 | 11 artifacts |

### Claim 1 — "seed discovery matters more on dense scopes". **Refuted.**

Measured as: how often does adding supplementary seeds (`goal_weight` 1.0 → 0.7) change
the top-ranked node?

| scope | artifacts | top-1 changed by seeds |
|---|---|---|
| `kartka:/change/foundation` | **48** | **32%** |
| `memory-backfill` | 5 | 89% |
| `graph-gui` | 4 | 78% |
| `ci-test-workflow` | 4 | 67% |
| `kartka:/change/kartka-fsrs-param-fitting` | 4 | 5% |

Seeds change the answer *less* on the 48-artifact scope than on several 3–5 artifact ones.
Scope size does not predict how much seed discovery matters, so "small scopes already rank
the right answer first" was not the reason the embedder swap left top-1 unmoved.

### Claim 2 — "the swap doesn't change top-1". **Also wrong, and the benchmark hid it.**

| | hashed vs static |
|---|---|
| queries where the **seed sets** differ | **28/28 (100%)** |
| top-1 differs, scopes ≥10 artifacts | 7/28 (**25%**) |
| top-1 differs, scopes <10 artifacts | 33/163 (**20%**) |

The two embedders pick different seeds for *every* query, and the top answer changes for
about a fifth of them — at the same rate on dense and sparse scopes.

The synthetic benchmark reported end-to-end success 0.67 → 0.67 and that was read as "the
top answer did not change". It says no such thing: it says the same **number** of queries
succeeded, not the same ones. An aggregate over 17 queries cannot distinguish "nothing
moved" from "four things moved and cancelled out".

### What this does not say

Which embedder is *right* on those ~22%. Real graphs have no gold labels, which is why the
synthetic corpus exists — and why the fix is a bigger labelled corpus, not more real data.
