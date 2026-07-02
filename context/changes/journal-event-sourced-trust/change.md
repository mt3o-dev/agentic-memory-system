---
change_id: journal-event-sourced-trust
title: Append-only event journal with trust computed by folding events
status: planned
created: 2026-06-25
updated: 2026-07-02
archived_at: null
---

## Notes

Slice 5 from docs/03_NEXT_STEPS.md. Implement the decision journal as an append-only event log. Compute trust_weight by folding events (not stored in-place). Confirm order-independence by applying two events in both orders — result must be identical. Linear: MT3-28, MT3-23.
