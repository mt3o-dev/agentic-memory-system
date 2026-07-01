---
change_id: write-path-mcp-surface
title: MCP write surface — four agent-facing write calls and one read call
status: new
created: 2026-06-25
updated: 2026-06-25
archived_at: null
---

## Notes

Slice 9 from docs/03_NEXT_STEPS.md. Implement the five MCP calls: create_change, capture_artifact, link, append_event (writes) + recall-context (read). Enforce the safety invariant: none of the write calls can mutate trust, clear a flag, promote to lifetime, or archive. See docs/05_10X_INTEGRATION.md for the full parameter/return spec. Linear: MT3-21.
