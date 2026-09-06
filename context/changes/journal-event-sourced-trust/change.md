---
change_id: journal-event-sourced-trust
title: Append-only event journal with trust computed by folding events
status: impl_reviewed
created: 2026-06-25
updated: 2026-07-03
archived_at: null
memory_goal: f7640c47-b6f9-45fd-99a5-809e3d4cb86d
---

## Notes

Slice 5 from docs/03_NEXT_STEPS.md. Implement the decision journal as an append-only event log. Compute trust_weight by folding events (not stored in-place). Confirm order-independence by applying two events in both orders — result must be identical. Linear: MT3-28, MT3-23.

## Follow-up scope reconciliation

The plan's "What We're NOT Doing" defers `WeightedAverageFold` and `LastNWindowFold` to follow-up tasks; only the `FoldStrategy` Protocol + `SumAndClampFold` shipped in the slice itself. Those two strategies subsequently landed post-slice in commit `08512a1` (`feat(fold)`) with their own tests — expected follow-up work, not scope creep. Noted here so the NOT-Doing list and the current `fold.py` reconcile for a future reader.
