# Transports — the CLI is the default, MCP is the optimization

*How the agent surface is reached. `MT3-21`.*

---

## 1. The surface is the system; a transport is a door

`agent_surface.py` holds every operation an agent gets and every rule that governs
them. `mcp_server.py` is 214 lines in which each tool is a single delegation:

```python
return _call(_get_surface().recall_context, query, goal_ref)
```

`cli.py` is its sibling — the same eleven operations, over `argparse` instead of MCP.
Neither holds judgment. That was always the design (`agent_surface.py`'s own docstring:
*"It is transport-independent"*); for a while only one transport shipped.

## 2. Why the CLI is the default

An MCP server binds **at session start**. Its configuration has to be committed,
discovered, and approved before the session exists. That makes it structurally unable to
serve the case that needs the graph most: **a fresh agent session in an unfamiliar
checkout**, with no context loaded and everything to recall.

Concretely, on a cloud session or any fresh clone, all of the following must line up
before a single `recall_context` can happen:

| Requirement | Fails when |
|---|---|
| `.mcp.json` committed at the repo root | it was added with `claude mcp add` at local/user scope, which writes `~/.claude.json` on one machine |
| paths in it resolve inside the container | it hardcodes a developer's absolute path |
| the server's dependencies resolve | `uv` cannot reach the index |
| the project's servers are approved | a cloned repo **cannot approve its own servers**; committed `enableAllProjectMcpServers` is ignored in an untrusted folder |
| the session has restarted since | always, if the config landed during the session |

The CLI needs none of them. It is a command in a repo that was just cloned:

```sh
uv run agentic-memory recall "VAT rounding" --goal <goal-id>
```

The rule that follows: **a workflow whose discipline evaporates because a config file is
missing is not a workflow.** The floor has to hold on day one, in an environment nobody
prepared. That is what "default" means here — not "preferred when you have a choice".

## 3. Why MCP is still worth having

Where it is available it is better, and the skills should prefer it:

- structured arguments instead of shell words, and no quoting between the agent and a
  300-word constraint;
- native tool-use ergonomics — schemas, argument validation, and discovery, so the agent
  does not need the vocabulary spelled out in `CLAUDE.md`;
- cheaper per call in tokens.

So: **CLI as the floor, MCP as the ceiling.** Both doors, one room. A skill names the
operation; whichever door is open that session carries it.

## 4. The safety invariant is inherited, not re-implemented

The CLI reaches `AgentSurface` and nothing else. Trust mutation, flag clearing, tier
promotion, archival, entity ratification, and consolidation commits are unreachable from
it for exactly the same reason they are unreachable over MCP: **the surface does not have
those methods.** One enforcement point, every transport at once. `test_cli.py` pins this
by asserting no privileged store method name appears in the module.

Adding a third transport (HTTP, a channel, a plugin) inherits the same guarantee for
free, provided it obeys one rule: **a transport delegates and never decides.** A
transport that reaches past the surface into `MemoryStore` has stopped being a transport.

### What the invariant is not

It is a **discipline boundary, not a sandbox**. Any agent that can run Bash can
`python -c "from agentic_memory_system.storage import MemoryStore; ..."` and call
privileged methods directly. That was true before the CLI existed and is equally true
over MCP — the CLI opens no new hole.

The guarantee is precise, and worth stating precisely: *the workflow's sanctioned
vocabulary does not contain those verbs.* An agent following the workflow cannot promote
a tier, because nothing it is told to call can. An agent deliberately working around the
workflow is a different threat model, and one no in-process API can address. Real
containment would need a process boundary — which `gui_api.py` already half-demonstrates,
by putting the privileged operations behind HTTP on a surface the agent is not pointed at.

## 5. The store has to be a database (solved, see `09_GIT_SYNC.md`)

This used to be the sharpest edge in the whole system. `context/memory-graph.db` was
committed *through* a `memory-db` clean/smudge filter, but **`git config filter.*` is
local repo config and is never cloned** — and git will never auto-register a
repo-provided filter, because running arbitrary commands from a clone is a security
boundary. A fresh clone therefore checked out the *text dump* into a file named `.db`,
and every transport reported `DatabaseError: file is not a database`.

It is gone. The dump is now the tracked source, the database is a gitignored build
artifact, and `MemoryStore` rebuilds it on open. No filter, no registration, no setup
script — see `09_GIT_SYNC.md`.

The lesson generalizes to transports: **a design that needs out-of-band local setup is
not transparent, and cannot be made transparent by documenting the setup better.** That
is the same reason the CLI, not MCP, is the default door.

## 6. Degraded mode, narrowed

The workflow's degraded-mode rule — queue every would-be operation in
`context/changes/<id>/memory-backlog.md` and replay later — was written for "the server is
down". With a CLI transport, *"the server was never registered"* stops being a
server-down case. The backlog goes back to meaning what it should: the store is genuinely
unreachable. That is a much rarer event, and a much more honest signal when it appears.
