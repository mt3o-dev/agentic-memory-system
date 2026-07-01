---
change_id: git-sync-round-trip
title: Git sync round-trip — dump DB to text, diff, merge on clone
status: implemented
created: 2026-06-25
updated: 2026-07-01
archived_at: null
---

## Notes

Slice 4 from docs/03_NEXT_STEPS.md. Implement the clean/smudge dump filter: DB dumps to text on commit, rebuilds on checkout. Change on one clone, merge on another, confirm legible line-wise diff and clean merge. De-risks storage/sync early while schema is still cheap to change. Linear: MT3-22.
