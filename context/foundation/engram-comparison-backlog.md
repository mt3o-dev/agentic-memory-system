# Improvement backlog — comparison against Engram (techtheist/engram)

Candidate improvements surfaced by comparing this project against Engram, a
more mature/externally-facing memory-for-AI-agents product. Decisions below
are the human's calls from the comparison discussion — not all accepted
candidates are scheduled; several need further design work first.

## 1. Embedding model — ACCEPTED, worth pursuing

`embedding.py`'s port is already designed to be swappable; the current
default (deterministic hashed bag-of-words) is the actual weak point for
recall on paraphrased queries. A local ONNX embedding model (Engram uses
`fastembed`) plugged into the existing port is low-risk — doesn't touch PPR
structure, trust folding, or the safety wall, and stays within "no LLM in
the query path" (an embedding lookup isn't generative). Not yet scheduled.

## 2. Multi-project memory — REJECTED as a blanket feature; open as a scoped extension

Rejected as an "always-shared" cross-project graph (Engram's home-graph
model). The human's framing: within a larger ecosystem of related projects
it's worth sharing *some* subgraphs, but it is not true that every project
someone develops needs its graph shared with every other. If revisited,
this should be an opt-in extension layered on top of the existing
agentic-memory-system + graph-workflow — e.g. an explicit "promote this
subgraph to a shared ecosystem store" action (mirroring the privileged-only
posture of tier promotion/archival), not a default cross-project read path.
No design started; genuinely optional, not a rejected-forever idea.

## 3. IDE-native surfaces — ACCEPTED in principle, far future

Real UX gain for the human-in-the-loop review flow (staleness,
contradictions, tier promotion) this project already requires, over the
current Svelte/Bootstrap web GUI. Human's call: we are far from the point
where this is the right next investment. Parked, not scheduled, not
rejected.

## 4. Reshapeable ontology — REJECTED (both agree)

Engram's node/edge types are per-graph config; ours is fixed. Fixed types
are simpler to reason about and match graph-workflow skills' hardcoded
assumptions — reshaping would ripple into every skill that captures/queries
by type name. Closed.

## 5. Published retrieval benchmark harness — ACCEPTED, design complete

Technical deep-dive done:
`context/changes/retrieval-benchmark-harness/benchmark-harness-design.md`.
Key findings: `recall_multi` decomposes into two independently-measurable
stages (facet-vocabulary seed discovery, then PPR+quality-blend
composition) — metrics are designed per-stage plus end-to-end, not one
blended number, so a future embedding-model swap (#1) can be proven to
improve seed discovery specifically. Synthetic corpus, not real-history-
derived (leakage isn't the concern here, controllable vocabulary overlap
is). Results live in `eval/`, gated as a reviewed-artifact convention first,
promoted to a hard CI check only once the metric has a stable baseline.
Recommended sequencing: build this **before** #1, so the embedding swap's
effect is measured rather than asserted. Not yet implemented — no `plan.md`
written, this is the design pass only. Smaller/lower-risk than #1 or #7 and
makes both measurable once they do happen — reasonable candidate for "next
concrete piece of work" on this backlog.

## 6. Storage format (TepinDB) — REJECTED

Plain project-local SQLite + the existing legible-text dump/restore already
solves what TepinDB's single-file design targets (git portability), with
better inspectability (`sqlite3` works everywhere; a custom format needs
its own tooling even if `npx tepindb` is provided). Closed.

## 7. NLI-based conflict/duplicate detection — reframed, worth designing as detection-only

Elaboration requested on why this was flagged as "not a straight port,"
and what a version compatible with this project's invariants would look
like.

**What it actually is.** NLI (Natural Language Inference) is a small,
fast, *classifier* model — not generative — trained to predict whether one
text span entails, contradicts, or is neutral toward another. It's cheap
to run fully offline (Engram bundles one via ONNX/`fastembed`). Engram
runs it to (a) check a new claim against existing canon at capture time
and (b) sweep the graph for latent contradictions/near-duplicates, queuing
both for judgment in its Review drawer.

**The real gap it would close here.** Today, a `CONTRADICTS` edge only
exists if an agent or human explicitly calls `link()`. Two independently
captured decisions that actually contradict each other, but that nobody
noticed and linked, are **silently present with no flag at all** — worse
than a flagged contradiction, because the system has zero visibility into
it. An NLI sweep is a real answer to a real hole in the current design.

**Why it's not a straight port — precisely, not just "it's an LLM."** Two
separate things were being conflated in the prior discussion, worth
untangling:
1. *Running NLI inference itself* is not actually a "query path" or
   "LLM in retrieval" violation — `recall_context()` wouldn't call it at
   all. Contradiction detection is a write-time/background concern, not a
   read-time ranking concern. This part is fine.
2. *Engram's judgment model* — "models nominate; you (or your assistant)
   judge" — is where the actual conflict with this project's design lives.
   Letting the calling agent be the one who resolves a flagged conflict
   erodes the hard wall this project draws around privileged operations
   (trust mutation, flag resolution, tier promotion, archival — "never
   part of the agent's write vocabulary"). That wall, not NLI itself, is
   what should not be ported.

**What a compatible design looks like**, reusing existing architecture
rather than bolting on something foreign: a new privileged component
(parallel to `evaluator.py`'s LLM evaluator) that runs NLI inference —
periodically, on-demand via the GUI, or synchronously right after a
`capture_artifact` call, scoped for cost — over candidate node pairs, and
writes **candidate flags**, never `CONTRADICTS` edges directly and never a
resolution. Those candidates surface through the *same* rules → evaluator
→ human resolution ladder `resolver.py` already implements, visible via
`stale_nodes()`-style read or a new candidate-conflicts queue, resolved
only on the human/GUI surface exactly like every other privileged
operation today. The agent surface gains nothing — it still can't create,
clear, or act on a `CONTRADICTS` edge on its own.

**Practical constraints worth flagging for the eventual design pass**:
- Comparison scope must be bounded, not full-graph O(n²) — e.g. only
  compare a new capture against nodes sharing a facet, or within N hops of
  the same goal/change, not every node in the store.
- New ML runtime dependency (ONNX + a bundled/downloaded NLI model,
  tens-to-hundreds of MB) — this project currently has zero ML runtime
  deps beyond the *optional* Anthropic API client used by the guided-review
  evaluator. Worth weighing alongside #1's embedding model, since both
  would likely share the same ONNX runtime dependency if pursued together.

Not scheduled — but reframed from "rejected" to "candidate, detection-only,
needs a design pass," since the underlying gap (silent unflagged
contradictions) is real regardless of whether NLI specifically is the
answer.

## 8. Visualization — split into three, not one item

Checked the actual GUI code before speculating: `gui/package.json`'s only
dependency is `bootstrap`. No D3, Cytoscape, vis-network, or any rendering
library; `Browse.svelte` has zero canvas/SVG/force-simulation code. Today
there is **no graph visualization at all** — the entire GUI is Bootstrap
lists, tables, and detail pages (`Browse`, `Changes`, `NodeDetail`,
`Recall`, `ReviewQueue`). This is a distinct gap from #3 (IDE-native
surfaces) — #3 is about *where* the UI lives, this is about *what it shows*
— and unlike #3, it needs zero plugin/distribution work: it's addable to
the existing browser GUI directly.

Three separable ideas, deliberately not bundled into one "add
visualization" item, because they have very different cost/value for this
specific system:

**8a. Whole-graph node-link view** (Engram's live pane, 4 layouts) — the
generic, most-Engram-like option. Real tradeoff worth naming: force-directed
graphs get visually noisy fast at scale, and this system's retrieval model
isn't "browse the whole graph" in spirit — it's goal-scoped, query-triggered
recall (`recall_multi` is a per-query walk, not a standing view). Likely the
biggest build and the weakest fit to how this system is actually used.
Lowest priority of the three.

**8b. Timeline/feed view** — Engram's second screen: the same memory as a
chronological scrollable story, version/supersession markers, judgment
inline. Higher-value than 8a for this project specifically because **the
data already exists**: `NodeDetail.svelte` currently renders each node's
journal as a per-node table (`detail.events`), and the append-only,
event-sourced trust-folding design (`fold.py`) already produces the
timestamped event stream a project-wide timeline needs. This is a rendering
gap, not a data-model gap — no new capture/storage work, just a new GUI
view over data already being written.

**8c. Recall visualization — not present in Engram at all (it isn't
PPR-based), and the highest-priority of the three for this project
specifically.** Render one query's actual `recall_multi` walk: the seed
vector (goal + `discover_seeds`'s supplementary weights), the reached
subgraph with PPR mass shown as visual weight, and where the
`α·retrieval + β·trust + γ·recency` blend re-ordered the PPR-only ranking.
Directly serves backlog item #5 (the benchmark harness): turns "why did
this query rank X above Y" from a metrics table into an inspectable
picture, useful both for tuning (α/β/γ, damping) and for a human trusting
*why* `recall_context` returned what it did. Real synergy, not a separate
effort — it would reuse the same instrumentation (seed weights,
mass-per-node, gate score) the harness already needs to expose for its
per-stage metrics (§3 of `benchmark-harness-design.md`).

**Recommended order: 8c, then 8b, then 8a** (if 8a happens at all) — 8c is
small, novel, and pairs directly with already-scoped work (#5); 8b is cheap
because the data model already supports it; 8a is the biggest build with
the least differentiation from what Engram already ships, and the weakest
fit to this system's query-scoped (not browse-scoped) retrieval design.
Not scheduled — no design doc yet, unlike #5 and #7.

## 9. Code-pointing nodes + drift detection — candidate, worth designing

`Node.path` today is a synthetic slug generated from content
(`/artifact/{_slug(content[:40])}`, see `agent_surface.py`'s
`capture_artifact`), not a validated pointer to a real file/line — it's a
human-readable label, never checked against the filesystem. Engram's nodes
can reference actual source locations; refs that stop resolving badge their
node as drifted, under a repair-or-retire contract, with an optional hook
attaching a file's memory the moment it's read.

Given this is specifically a coding-agent memory system, decisions/
constraints/invariants that are *about* a piece of code being unable to
*point at* that code is a real gap, not a cosmetic one — a constraint like
"session cookies expire after 30 days" captured against `session.ts` has no
way to notice `session.ts` was deleted or the logic moved to a different
file, so the memory silently goes stale in a way no `CONTRADICTS` edge or
staleness flag would ever catch (those only fire from node-to-node
relationships, never from node-to-code drift).

A compatible design would add an optional `code_ref` (file path + line
range, or a symbol reference) alongside the existing synthetic `path`
label — not replacing it, since `path` currently also serves as a
human-readable identifier independent of any code location. Resolution
checking (does the ref still point at something real) is a privileged,
read-only sweep candidate — same shape as `stale_nodes()` — not something
the agent surface would run automatically. Not designed in detail yet;
flagged as a strong candidate given the project's own domain, worth a
technical deep-dive similar to #5's before scoping.

**Sharpened by `docs/recall-and-capture.md`**: validate `code_ref` at
**write time**, not only via a periodic drift sweep — "missing code refs:
paths that don't resolve in the repository, caught at write time instead of
at the next drift scan." Cheap (a filesystem check `capture_artifact` can
run inline) and should be in scope from the first version of this feature,
not bolted on after the fact — a `code_ref` that never resolved even once
is a different, easier-to-catch failure mode than one that drifted after
the fact, and there's no reason to wait for a sweep to catch the easy case.

## 10. Embedding caching — a latent prerequisite of #1, not standalone

`discover_seeds` (`storage.py`) calls `self._embedder.embed(body)` for
**every** live `facet_value` node's body, on **every single query** —
uncached, recomputed from scratch each time. Free with
`HashedBagOfWordsEmbedder` (near-zero cost, no model inference), but this
becomes a real latency and compute-cost problem the moment #1 (a real local
embedding model) ships — every `recall_context` call would re-embed the
entire facet vocabulary rather than reusing a stored vector.

This isn't an independent feature to schedule on its own — it's a
prerequisite that must ship *as part of* #1's implementation, not
discovered afterward as a performance regression. Whatever design #1
lands on needs a cache (embed each facet_value once at capture/edit time,
store the vector, invalidate only on `content_edited`) rather than
recomputing per query. Noting it now so #1's eventual plan accounts for it
from the start rather than treating it as a surprise follow-up bug.

## 11. Session-start digest — candidate, small, no new capability needed

Engram proactively briefs at session start: conflicts to judge, open work,
standing decisions — rather than relying entirely on the calling agent to
know to ask. This project already has every read primitive a digest would
need (`stale_nodes()`, `impact_of()`, `recall_context()`) — nothing new to
build at the storage/retrieval layer, just a new packaging: a thin
MCP read tool (or a documented skill-level convention in graph-workflow's
`gw-*` skills) that bundles "what's flagged for review + what's open +
what the standing goal-scoped decisions are" into one call at the start of
a session, instead of requiring the agent to remember to call three
separate tools.

Worth noting the natural home for this might be graph-workflow's skill
layer (e.g. `gw-new`/`gw-ask` already encode "recall before deciding" as a
discipline) rather than the MCP server itself — the server already exposes
the pieces; the packaging decision is about which layer should own
assembling them into "a digest."

## 12. Content-level near-duplicate detection — candidate, distinct from #7

Checked `agent_surface.py`'s `capture_artifact`: near-synonym checking
exists today, but only for controlled **facet labels** ("near-synonyms of
existing values come back as `facet_warnings` instead of silently minting
duplicates"). There is no equivalent for full artifact **content** — two
decisions that say almost the same thing in different words, with no
disagreement between them, both get captured today with no warning.

Distinct from #7 (NLI contradiction detection): duplicates aren't
conflicts. The detection signal is semantic similarity between two node
bodies, not entailment/contradiction classification — a simpler, cheaper
check than full NLI, and could plausibly reuse whatever embedding model
#1 lands on (cosine similarity above a threshold) rather than needing its
own model. Worth sequencing after #1 for that reason — the embedding
upgrade may make this nearly free to add.

**Sharpened by `docs/recall-and-capture.md`**: a **negated duplicate** is
the real danger, not plain duplicates — "the 'duplicate' actually says the
opposite ('use X' vs 'don't use X') — flagged distinctly, because blindly
merging it would corrupt the canon; the right move is a `CONTRADICTS` edge,"
not a merge. A similarity-only design (cosine distance above a threshold)
cannot tell these apart — "session timeouts should be long" and "session
timeouts should be short" are highly similar text with opposite meaning,
and naive merging would silently discard the disagreement. Any design for
this item needs a polarity/negation check alongside similarity, not
similarity alone — likely means this can't be *fully* independent of #7
(NLI) after all, even though the everyday case (genuine redundancy) doesn't
need NLI. Worth designing the two together rather than assuming #12 is the
purely-cheap half.

## 13. Adaptive threshold calibration from judgment history — candidate, bigger lift

Engram: "past 200 notes, the graph re-calibrates its conflict threshold
from your own judgment history." Checked `resolver.py`, `evaluator.py`,
`penalty.py` — zero threshold/calibration logic exists; `BASE_PENALTY` and
all severity math are fixed constants, independent of how any past flag
was actually resolved by a human.

Distinct from the numeric-config candidate noted earlier (exposing α/β/γ/
damping as static per-project settings a human sets once, see
`benchmark-harness-design.md`'s ablation matrix) — this is the system
*learning* from an accept/reject feedback loop (which flagged conflicts a
human actually confirmed vs. dismissed) and adjusting its own sensitivity
over time, no manual config action required. Real cold-start problem worth
designing around before this is actionable: what governs behavior before
enough judged history exists (Engram's own number, 200 notes, suggests
they hit this too). Bigger lift than most items on this list — needs its
own design pass, not scoped here.

## 14. CLI diagnostic ("doctor") command — candidate, small

Checked `gui_api.py`: a real `/api/health` route exists (counts nodes,
flagged, archived, edgeless) — but it only diagnoses DB state, and only
once the GUI server is already running against a reachable database. There
is nothing that diagnoses the layer underneath: whether
`uv run agentic-memory-mcp` is actually launchable, whether
`MEMORY_DB_PATH` resolves to the intended file, whether an MCP client can
actually reach the server at all. Today that's checked informally —
`gw-init`'s skill instructions literally tell the agent to "verify the
memory server... any cheap read" by hand, rather than there being one
command to run.

A `doctor`-style CLI checking the full chain (env resolution → process
launch → DB reachability → the existing health endpoint) would replace
that ad hoc skill-level check with something reliable, and would double as
the diagnostic step `gw-init` already wants to perform — this could
plausibly replace, not just supplement, that part of the skill.

## 15. Configurable capture intensity — candidate, but likely a skill-layer concern, not a server one

Engram: three capture intensities (relaxed/normal/aggressive) tune how
eagerly the assistant writes. Real tension worth naming before treating
this as a portable feature: in this project, *what* gets captured is
entirely the calling agent's decision, driven by graph-workflow's `gw-*`
skill instructions ("capture residue, not narration," etc.) — the MCP
server itself never decides to write anything unprompted, so there is no
server-side "intensity" dial to turn. A faithful port would be a
per-project setting the `gw-*` skills read and adjust their own capture
discipline against (e.g. how liberally `gw-research`/`gw-implement`
capture findings), not a memory-server feature at all — same shape as the
file-read-hook tension noted for the visualization/session-digest items.
Low priority until/unless graph-workflow's skill layer wants this kind of
per-project tuning.

## 16. `check_claim` — a read-only, zero-write advisory tool — ACCEPTED as the preferred framing of #7

Found in `docs/conflicts-and-checkup.md`: alongside the background NLI
sweep, Engram exposes `check_claim` as a synchronous MCP tool the
assistant calls proactively — *"does the canon contradict this plan?"* —
before acting, not after. It runs local NLI against nearby canon and
returns an advisory verdict (**supports** / **contradicts** / **silent**)
in the same turn, and critically: **it never mutates storage**. It's a
query, exactly like `recall_context`/`impact_of`.

This is a better starting point than the sweep-based design originally
proposed for #7. The sweep design still needed a new privileged surface
(a component parallel to `evaluator.py` writing candidate flags, resolved
only through the human/GUI ladder) specifically because it writes
*something* (even just a candidate flag) unprompted. `check_claim` sidesteps
that whole question — as a pure read tool, it needs no new wall around it
at all; it's already the same trust tier as every existing read in the
agent surface. An agent calls it before `capture_artifact`-ing a new
decision, or before proposing a plan that assumes something about existing
canon, and gets an answer with zero tokens spent and zero risk of a bad
model verdict ever touching stored state — the exact "models nominate,
people/agents judge" principle this project already holds, just applied to
a read instead of a write.

Recommend leading with this over the sweep design if/when #7's NLI
question is revisited: build `check_claim` first (smaller, no new
privileged surface, immediately useful on its own), and treat the
background sweep (proactively surfacing *unasked* latent contradictions)
as a separate, larger follow-on — not a package deal.

## 17. Session/subagent-scoped write attribution — ACCEPTED, motivated by how this project is actually used

Checked every `source=` call site across `storage.py`/`gui_api.py`/
`agent_surface.py`: the journal's `source` field is a generic string
("agent-surface", "gui", "gui-guided", "evaluator") — it identifies *which
surface* made a write, never *which session or subagent*. Engram's model:
"subagents share the MCP connection: they can search and read the brief,
but they start cold (no injected context) and their writes attribute to
the parent session."

This isn't a hypothetical gap — it's directly motivated by how
graph-workflow itself is used in practice: a single `gw-implement` or
`gw-review` run routinely fans out into several concurrent Claude Code
subagents (build agents, independent review agents), each potentially
calling `capture_artifact`/`append_event` against the same graph. Today,
every one of those writes is journaled as generically "agent-surface" —
there's no way to later ask "which of the three parallel review passes
flagged this contradiction" or "did the build agent or the review agent
capture this decision." graph-workflow's own dogfooding record
(`docs/DOGFOODING.md`, the per-slice build-then-review pattern) already
depends on knowing which pass found what — the memory layer arguably should
carry the same granularity its own calling workflow already tracks
elsewhere.

A compatible design: an optional `session_id` (or subagent id) parameter
threaded through the existing MCP write tools, defaulting to the current
generic behavior when absent (no breaking change), recorded alongside
`source` in the journal — not replacing it, since `source` (which
*surface*: agent vs. GUI vs. evaluator) and session/subagent id (*which
instance* of that surface) answer different questions. Not designed in
detail yet; smaller in scope than most items on this list since it's
additive to an existing field, not a new subsystem.
