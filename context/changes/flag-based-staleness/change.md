---
change_id: flag-based-staleness
title: Flag-based staleness — CONTRADICTS edge sets needs_review with scoring penalty
status: impl_reviewed
created: 2026-06-25
updated: 2026-07-02
archived_at: null
---

## Notes

Slice 6 from docs/03_NEXT_STEPS.md. A CONTRADICTS edge sets needs_review flag on the target node. Flagged nodes get a scoring penalty at query time (not a trust decrement). Confirm a false-alarm clear (removing the flag) fully restores the node's effective score. Linear: MT3-23.
