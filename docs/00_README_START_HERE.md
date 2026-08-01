# Agentic Memory System — Handoff

**Read this first.** This document set hands off a research-and-design effort to Claude Code and a developer so they can start building. Nothing here is implemented yet — the work so far is a thoroughly pressure-tested design, with every decision and its reasoning captured in Linear.

## What this project is

A graph-oriented **memory/context store for agentic LLM workflows** — the thing an agent reads from before a task and writes to after. It is *not* a wiki and not a human knowledge base. The primary reader and writer is an LLM agent; humans only inspect and intervene. Every design choice flows from that single fact.

One-paragraph architecture: typed **nodes + typed edges** stored in **SQLite-as-graph**, synced across machines via **git** (database dumped to text so diffs and merges work). Retrieval is **deterministic** — no LLM in the query path — using **multi-seed Personalized PageRank**. Memory behaves differently by **type** (semantic knowledge decays; an episodic journal is immutable; procedures reinforce and don't decay). Best practices are enforced **structurally through schema constraints**, not through prompts — the store is *normative*: it shapes how its consuming agent works.

## The document set

| Doc | Purpose |
|-----|---------|
| `00_README_START_HERE.md` | This file — orientation + how the docs and Linear fit together |
| `01_CORE_CONCEPTS.md` | The architecture and the key ideas, explained from first principles |
| `02_LINEAR_MAP.md` | Where everything lives — every ticket, what's settled vs open, how to navigate |
| `03_NEXT_STEPS.md` | What to build first, in what order, and the open questions blocking each |
| `04_RESEARCH_APPROACH.md` | *How* this design was produced — the method to continue with, so the work stays consistent |
| `05_10X_INTEGRATION.md` | How the memory system merges with the 10x development workflow + the MCP read/write API |
| `06_DOMAIN_ENTITIES.md` | The 4th dynamics class — why "reference entities" became **domain entities**, the five behaviors that differ, and the greenfield/brownfield modelling approaches |
| `07_CONSOLIDATION.md` | The consolidation operation — trigger, direction, owner |

Read `00`–`05` in order: `01` gives you the mental model, `02` tells you where the detail lives, `03` tells you what to do, `04` tells you how to keep doing it well, `05` is the concrete integration + API surface to build against. `06` and `07` are the design records for the two questions `03` listed as "real design work, not yet started" — read them when you touch entities, retrieval policy, or the review gate.

## Where the real detail lives: Linear

This doc set is a **map, not the territory**. The authoritative, exhaustive record is the Linear project:

- **Project**: "Agentic Memory System" (team `Mt3o`, project id `211ecdaf-94b5-4d9c-b49d-a55e0f5c80be`)
- **14 issues**, `MT3-17` through `MT3-30`
- Each issue body holds its design; each issue's **comments** hold the research findings, proposed solutions, and locked decisions. *The comments are where the depth is* — read them, not just the issue bodies.
- The **project description** + a pinned **"Project State Snapshot"** comment give the same orientation as these docs, kept in sync.

When a doc here says "see MT3-20", open that ticket and read its comments top to bottom.

## The single most important framing

If you take one thing from this handoff: **this is machine-facing infrastructure with a thin human inspection layer — not a human tool with machine features bolted on.** The moment the design drifts toward "let humans browse and edit comfortably," it starts reinventing Confluence and loses its reason to exist. Keep it agent-first.

## Status at handoff

- Core data model and retrieval design: **substantially settled** (storage, scoring, liveness, trust, staleness, categorization, memory-type dynamics, provenance).
- Write-path cluster: **now unblocked.** The "10x framework" (the external input that was blocking it) has arrived and is integrated — the memory system merges with the 10x development workflow rather than being a separate system. The MCP read/write API is specified. See `05_10X_INTEGRATION.md`.
- Nothing is code yet. The first job is a tracer-bullet vertical slice (see `03_NEXT_STEPS.md`).
