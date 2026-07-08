# Lifecycle Binding — Plan Brief

> Spec: `docs/05_10X_INTEGRATION.md` §Skills bind calls to lifecycle events, MT3-24
> (skills layer), MT3-21 (trigger points). Stage contract observed in
> mt3o-dev/punktomat (live 10x project).

## What & Why

Slice 10: the judgment layer. The MCP tools (slice 9) are deterministic operations;
this slice adds the *skills* that decide when to call them, bound to the 10x change
lifecycle so memory participation is a property of the workflow, not of agent
discipline.

## Key Decisions Made

| Decision | Choice | Why |
|---|---|---|
| Form | Six repo-local Claude Code skills (`.claude/skills/memory-*`) + CLAUDE.md binding table | Matches how 10x itself ships (skills invoked at stages); CLAUDE.md makes the wiring ambient without waiting for skill triggers |
| Naming | `memory-*` prefix, composing WITH the 10x plugin | The 10x skills stay untouched; memory skills fire at the same moments |
| Tools vs skills split | Tools never embed judgment; skills never bypass tools | The MT3-24 working hypothesis, kept strictly |
| Goal id plumbing | `memory_goal:` line appended to the change's change.md | The one piece of state the binding needs, stored where the 10x lifecycle already lives |
| Archive path | `scripts/memory_lifecycle.py deactivate <id> --sweep` (privileged, journaled, prints what changed) | Deactivation/sweep are merge-lifecycle consequences, deliberately absent from the agent surface — a repo-local script is the sanctioned bridge |
| Review at PR | Skill *surfaces* disputed nodes + promotion candidates into the PR text and points at the GUI | Flag resolution and promotion are human gates; the agent can never resolve them |
| Skill inventory vs MT3-24 | recall (absorbs surface-contradictions), capture, feedback (record-event), open-change, review-staleness, archive-on-merge | trace-impact and audit-graph deferred: `impact_of()` isn't in the read surface yet; audit counts already sit in the GUI health strip |
| Portability | Copy `.claude/skills/memory-*` + register MCP server with per-project `MEMORY_DB_PATH` | No packaging work until a second project actually adopts it |

## Scope

**In:** six SKILL.md files, `scripts/memory_lifecycle.py` (status/activate/
deactivate/sweep), CLAUDE.md (binding table + repo conventions), CLI tests, README
status update.

**Out (deferred):** trace-impact and audit-graph skills (need `impact_of()` /
`stale_nodes()` read calls), consolidation (undesigned, MT3-18/29), evaluator wiring
(MT3-27), packaging as an installable plugin.

## Success Criteria

- Full suite green (190) — CLI covered: deactivate+sweep archives the scope,
  activate+sweep restores it, unknown change exits cleanly
- Each skill states its 10x trigger moment in its description and its exact MCP call
  shape in the body
- The safety model is restated where it bites: no skill clears flags, changes trust,
  promotes, or archives through the agent surface
