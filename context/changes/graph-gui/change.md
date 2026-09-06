---
change_id: graph-gui
title: Minimal human GUI for inspecting and curating the memory graph
status: implemented
created: 2026-07-07
updated: 2026-07-07
archived_at: null
memory_goal: c80edccd-c3a3-4488-a48d-508d0a4f0f3f
---

## Notes

MT3-26 (minimal v1). The human counterpart to the agent MCP surface: a Svelte +
Bootstrap single-page app served by a Starlette API (`gui_api.py`,
`uv run agentic-memory-gui`). Surfaces the human-in-the-loop checkpoints — staleness
review queue (MT3-23), tier promotion with the mandatory lifetime gate (MT3-18),
change liveness + sweep — plus browse/search, edge-walking, per-node journal, and a
recall playground that shows the scores agents never see. Every human override is
journaled as an event. UI/UX decisions: `gui-design.md`. Linear: MT3-26.