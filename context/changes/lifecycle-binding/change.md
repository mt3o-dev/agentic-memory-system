---
change_id: lifecycle-binding
title: Bind memory skills to 10x workflow lifecycle events
status: new
created: 2026-06-25
updated: 2026-06-25
archived_at: null
---

## Notes

Slice 10 from docs/03_NEXT_STEPS.md. Wire the memory skills to 10x lifecycle events: recall-context at change start (after create_change), capture-artifact at plan/phase boundaries, review-staleness at PR, archive-on-merge at merge. Worktree checkout = slice activation = liveness root toggle. See docs/05_10X_INTEGRATION.md for the full skills↔calls↔lifecycle binding table. Linear: MT3-24, MT3-21.
