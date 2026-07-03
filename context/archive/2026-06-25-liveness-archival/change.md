---
change_id: liveness-archival
title: Liveness and archival via mark-sweep reachability from the root set
status: archived
created: 2026-06-25
updated: 2026-07-03
archived_at: 2026-07-03T19:30:51Z
---

## Notes

Slice 7 from docs/03_NEXT_STEPS.md. Add SCOPED_TO edge type + a Slice/change node. Implement mark-sweep reachability from the root set (long-term + lifetime tiers + active slices). Confirm: a foundation node survives when all its slices go inactive; a slice-detail node archives and reactivates with its scope. Linear: MT3-18.
