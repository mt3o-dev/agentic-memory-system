"""The agent-facing surface: the write operations and reads an AI agent gets (MT3-21).

This layer is the entire write vocabulary: open a scope (``create_change``), add a
knowledge node (``capture_artifact``), name a domain entity (``capture_entity``), add an
edge (``link``), log that something happened (``append_event``/``append_events``) — plus
the reads (``recall_context``, ``trace_impact``, ``review_queue``, ``domain_model``,
``consolidation_candidates``). It is transport-independent; ``mcp_server.py`` wraps it
in MCP.

⚠ Safety invariant (MT3-21 — protect this forever): nothing reachable from this class
can mutate trust, clear a review flag, promote a tier, archive a node, confirm or retire
a domain entity, or commit a consolidation. Those are either *derived* (trust folds from
the journal, MT3-28; entity status folds from the journal too) or live on *separate
privileged paths* (evaluator batch MT3-27, sweep/merge lifecycle, the human GUI).
Recording a CONTRADICTS edge or a CONTRADICTED event flags the target for review as a
transparent side-effect — the agent is recording that a contradiction exists, not
deciding the target is wrong. Likewise ``capture_entity`` *proposes*; only a human
ratifies. Never add a set_trust / clear_flag / promote / archive / confirm_entity /
consolidate call here "for convenience".
"""

import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any

from .embedding import cosine
from .schema import Node, NodeType, Tier, Edge, EdgeType, Event, EventType
from .storage import MemoryStore, slug as _slug

# Node types an agent may capture. Anchors (slice, facet_value) and goals are minted
# by create_change / facet governance, never free-form.
_CONTENT_TYPES = frozenset(
    {
        NodeType.decision,
        NodeType.concept,
        NodeType.constraint,
        NodeType.issue,
        NodeType.invariant,
    }
)

# Tiers an agent may create at. long-term and lifetime are promotion outcomes
# (human/privileged checkpoints, MT3-18) — never a creation-time choice.
_CREATION_TIERS = frozenset({Tier.short_term, Tier.mid_term})

# Edge types an agent may create between existing content nodes. SCOPED_TO and
# HAS_FACET are structural axes managed by this surface itself.
#
# ABOUT and CONSOLIDATES are agent-writable because both are *recordings*, not verdicts:
# "this note concerns that entity" and "this summary was distilled from those episodes"
# are facts the working agent is the best-placed party to state. Neither can demote,
# archive, or re-tier anything, so neither widens the safety surface.
_LINKABLE_EDGE_TYPES = frozenset(
    {EdgeType.depends_on, EdgeType.contradicts, EdgeType.about, EdgeType.consolidates}
)

# Entity-name collision threshold. Lower bar than facets (0.35) *and* a softer response:
# a warned facet label is skipped, a warned entity is still minted. Entities have a
# mandatory human confirmation gate that facets do not, so the right place to rule
# "Client and Customer are the same thing" is that gate, with a person looking at both —
# not a silent refusal at capture time that leaves the agent unable to name a genuinely
# distinct concept.
_ENTITY_SUGGEST_THRESHOLD = 0.5

# Agent event vocabulary → (implemented EventType, weight, polarity). Everything except
# CONFIRMED/CONTRADICTED carries weight 0.0, so it is trust-neutral even when a
# privileged caller later folds the journal.
_EVENT_MAP: dict[str, tuple[EventType, float, int]] = {
    "USED": (EventType.used, 0.0, 1),
    "CONFIRMED": (EventType.confirmation_added, 1.0, 1),
    "REVIEWED": (EventType.manual_review, 0.0, 1),
    "NOTED": (EventType.noted, 0.0, 1),
    # CONTRADICTED is handled specially: it also sets the needs_review flag.
    "CONTRADICTED": (EventType.contradiction_raised, 1.0, -1),
}

# Facet-vocabulary governance thresholds (MT3-19: embeddings are a collision
# detector feeding the governance gate, never a silent merger).
_FACET_SUGGEST_THRESHOLD = 0.35

class AgentSurfaceError(ValueError):
    """A rejected agent-surface call (validation, goal-first, vocabulary)."""


class AgentSurface:
    def __init__(self, store: MemoryStore) -> None:
        self._store = store

    # --- helpers ---

    def _require_node(self, node_id: str, what: str) -> Node:
        node = self._store.read_node(node_id)
        if node is None:
            raise AgentSurfaceError(f"{what}: no node with id {node_id!r}")
        if node.archived:
            raise AgentSurfaceError(f"{what}: node {node_id!r} is archived")
        return node

    def _require_goal(self, goal_ref: str) -> Node:
        goal = self._require_node(goal_ref, "goal_ref")
        if goal.type is not NodeType.goal:
            raise AgentSurfaceError(
                f"goal_ref: node {goal_ref!r} is a {goal.type.value}, not a goal — "
                "every artifact must serve a goal (create one via create_change; "
                "exploration is a goal too)"
            )
        return goal

    def _scoping_slice_id(self, node_id: str) -> str | None:
        row = self._store._conn.execute(
            "SELECT e.source_id FROM edges e "
            "JOIN nodes s ON s.id = e.source_id AND s.type = 'slice' "
            "WHERE e.target_id = ? AND e.type = 'SCOPED_TO' "
            "ORDER BY e.created_at, e.source_id LIMIT 1",
            (node_id,),
        ).fetchone()
        return row[0] if row else None

    @staticmethod
    def _check_edge_endpoints(edge_type: EdgeType, source: Node, target: Node) -> None:
        """Typed-edge endpoint rules, shared by ``capture_artifact`` and ``link``.

        The graph is only as queryable as its edge types are honest: an ABOUT edge that
        does not point at an entity turns the entity hub into noise, and a CONSOLIDATES
        edge pointing at one is a category error (an entity is a referent, never an
        episode to abstract). Cheap to enforce here, impossible to repair later.
        """
        for node in (source, target):
            if node.type in (NodeType.slice, NodeType.facet_value):
                raise AgentSurfaceError(
                    f"{node.id!r} is a structural anchor, not content"
                )
        source_is_entity = source.type is NodeType.entity
        target_is_entity = target.type is NodeType.entity

        # Order matters: the two "an entity is not that kind of thing" rules run before
        # the general artifact→entity rule, so an agent that tries to contradict an
        # entity is told *why* it cannot rather than being redirected to ABOUT, which
        # would not have helped either.
        if (source_is_entity or target_is_entity) and edge_type is EdgeType.contradicts:
            raise AgentSurfaceError(
                "a domain entity names a referent, not a claim — it cannot be "
                "contradicted. Retire it (a human act), or capture an artifact that "
                "contradicts a claim ABOUT it."
            )
        if (source_is_entity or target_is_entity) and edge_type is EdgeType.consolidates:
            raise AgentSurfaceError(
                "CONSOLIDATES abstracts episodes into a semantic artifact; an entity is a "
                "referent, not an episode and not an abstraction over episodes"
            )

        if edge_type is EdgeType.about:
            if not target_is_entity:
                raise AgentSurfaceError(
                    f"ABOUT must point at an entity; {target.id!r} is a "
                    f"{target.type.value}. Use DEPENDS_ON between artifacts."
                )
            if source_is_entity:
                raise AgentSurfaceError(
                    "ABOUT attaches an artifact to the entity it concerns; an entity does "
                    "not hold an opinion about another entity. Relate entities with "
                    "DEPENDS_ON (part-of), or capture a concept ABOUT both."
                )
        elif target_is_entity and not source_is_entity:
            # Entity↔entity DEPENDS_ON survives this: it is the part-of spine of the
            # domain model (LineItem DEPENDS_ON Invoice).
            raise AgentSurfaceError(
                f"{target.id!r} is a domain entity — relate artifacts to it with ABOUT, "
                f"not {edge_type.value}"
            )

    def _facet_values(self) -> list[Node]:
        rows = self._store._conn.execute(
            "SELECT id FROM nodes WHERE type = 'facet_value' AND archived = 0 ORDER BY id"
        ).fetchall()
        return [self._store.read_node(r[0]) for r in rows]

    # --- 1. create_change — open a scope ---

    def create_change(
        self, change_id: str, goal: str, parent_refs: list[str] | None = None
    ) -> dict[str, Any]:
        """Mint the change anchor + mandatory Goal node and activate the liveness root.

        The change is a ``slice`` node (the implemented liveness anchor); the Goal is
        the entry point every later capture and recall anchors to (MT3-25 goal-first
        starts here). ``parent_refs`` become goal→parent DEPENDS_ON edges, which also
        makes prior knowledge reachable from the goal seed during PPR recall.
        """
        if not change_id.strip():
            raise AgentSurfaceError("create_change: change_id must be non-empty")
        if not goal.strip():
            raise AgentSurfaceError(
                "create_change: goal is mandatory — a change with no goal cannot be opened"
            )
        change_path = f"/change/{_slug(change_id)}"
        existing = self._store._conn.execute(
            "SELECT id FROM nodes WHERE type = 'slice' AND path = ?", (change_path,)
        ).fetchone()
        if existing:
            raise AgentSurfaceError(
                f"create_change: change {change_id!r} already exists ({existing[0]})"
            )
        parents = [self._require_node(p, "parent_refs") for p in (parent_refs or [])]

        now = datetime.now(timezone.utc)
        change_node = Node(
            id=str(uuid.uuid4()),
            type=NodeType.slice,
            tier=Tier.short_term,
            path=change_path,
            body=f"change scope: {change_id}",
            created_at=now,
        )
        goal_node = Node(
            id=str(uuid.uuid4()),
            type=NodeType.goal,
            tier=Tier.short_term,
            path=f"/goal/{_slug(change_id)}",
            body=goal,
            created_at=now,
        )
        edges = [
            Edge(source_id=change_node.id, target_id=goal_node.id, type=EdgeType.scoped_to, created_at=now)
        ]
        for parent in parents:
            edges.append(
                Edge(source_id=goal_node.id, target_id=parent.id, type=EdgeType.depends_on, created_at=now)
            )
        self._store.write_atomic([change_node, goal_node], edges)
        self._store.activate_slice(
            change_node.id, source="agent-surface", reason=f"create_change {change_id}"
        )
        return {
            "change_node_id": change_node.id,
            "goal_node_id": goal_node.id,
            "activated": True,
        }

    # --- 2. capture_artifact — the workhorse ---

    def capture_artifact(
        self,
        content: str,
        type: str,
        goal_ref: str,
        facets: list[str] | None = None,
        edges: list[dict[str, str]] | None = None,
        tier: str = "short-term",
        path: str = "",
    ) -> dict[str, Any]:
        """Capture one artifact node, atomically with its edges (MT3-21 LOCKED).

        ``goal_ref`` is mandatory and strict — a node that serves no goal is rejected.
        The goal anchors the artifact with a goal→artifact DEPENDS_ON edge (achieving
        the goal depends on it), which is also what makes the artifact reachable from
        the goal seed at recall time. The artifact inherits the goal's change scope via
        SCOPED_TO, so it lives and dies with the change. Facet labels are validated
        against the controlled vocabulary; near-synonyms come back as
        ``facet_warnings`` instead of silently minting duplicates.
        """
        if not content.strip():
            raise AgentSurfaceError("capture_artifact: content must be non-empty")
        try:
            node_type = NodeType(type)
        except ValueError:
            node_type = None
        if node_type not in _CONTENT_TYPES:
            allowed = sorted(t.value for t in _CONTENT_TYPES)
            hint = (
                " — domain entities are named things, not claims: use capture_entity"
                if node_type is NodeType.entity
                else ""
            )
            raise AgentSurfaceError(
                f"capture_artifact: type must be one of {allowed}, got {type!r}{hint}"
            )
        try:
            node_tier = Tier(tier)
        except ValueError:
            node_tier = None
        if node_tier not in _CREATION_TIERS:
            raise AgentSurfaceError(
                "capture_artifact: tier must be 'short-term' or 'mid-term' — "
                "long-term/lifetime are promotion outcomes, not a creation-time choice"
            )
        goal = self._require_goal(goal_ref)

        now = datetime.now(timezone.utc)
        node = Node(
            id=str(uuid.uuid4()),
            type=node_type,
            tier=node_tier,
            path=path or f"/artifact/{_slug(content[:40]) or 'untitled'}",
            body=content,
            created_at=now,
        )

        new_edges = [
            Edge(source_id=goal.id, target_id=node.id, type=EdgeType.depends_on, created_at=now)
        ]
        change_slice = self._scoping_slice_id(goal.id)
        if change_slice is not None:
            new_edges.append(
                Edge(source_id=change_slice, target_id=node.id, type=EdgeType.scoped_to, created_at=now)
            )

        events: list[Event] = []
        flag_ids: list[str] = []
        edge_results: list[str] = []
        for spec in edges or []:
            target = self._require_node(str(spec.get("target", "")), "edges.target")
            try:
                edge_type = EdgeType(str(spec.get("type", "")))
            except ValueError:
                edge_type = None
            if edge_type not in _LINKABLE_EDGE_TYPES:
                allowed = sorted(t.value for t in _LINKABLE_EDGE_TYPES)
                raise AgentSurfaceError(
                    f"edges.type must be one of {allowed}, got {spec.get('type')!r}"
                )
            direction = str(spec.get("direction", "out"))
            if direction not in ("out", "in"):
                raise AgentSurfaceError("edges.direction must be 'out' or 'in'")
            if direction == "out":
                self._check_edge_endpoints(edge_type, node, target)
            else:
                self._check_edge_endpoints(edge_type, target, node)
            source_id, target_id = (
                (node.id, target.id) if direction == "out" else (target.id, node.id)
            )
            new_edges.append(
                Edge(source_id=source_id, target_id=target_id, type=edge_type, created_at=now)
            )
            edge_results.append(f"{source_id} {edge_type.value} {target_id}")
            if edge_type is EdgeType.contradicts:
                flag_ids.append(target_id)
                events.append(
                    Event(
                        id=str(uuid.uuid4()),
                        node_id=target_id,
                        type=EventType.contradiction_raised,
                        weight=1.0,
                        polarity=-1,
                        source="agent-surface",
                        reason=f"contradicted at capture of {node.id}",
                        created_at=now,
                    )
                )

        facet_edges, facet_nodes, warnings = self._resolve_facets(facets or [], node.id, now)
        self._store.write_atomic(
            [node, *facet_nodes], new_edges + facet_edges, events, flag_ids
        )
        result: dict[str, Any] = {"node_id": node.id, "edge_results": edge_results}
        if flag_ids:
            result["side_effects"] = [f"{nid} flagged needs_review" for nid in flag_ids]
        if warnings:
            result["facet_warnings"] = warnings
        return result

    def _resolve_facets(
        self, labels: list[str], node_id: str, now: datetime
    ) -> tuple[list[Edge], list[Node], list[str]]:
        """Controlled-vocabulary gate: reuse exact matches, warn on near-synonyms.

        The slice-8 embedder does the *finding* of collision candidates; the agent (or
        a human) keeps the *equivalence* call — a warned label is skipped, never
        silently merged or duplicated (MT3-19).
        """
        existing = self._facet_values()
        embedder = self._store._embedder
        edges: list[Edge] = []
        minted: list[Node] = []
        warnings: list[str] = []
        for label in labels:
            if not label.strip():
                continue
            exact = next(
                (f for f in existing if f.body.strip().lower() == label.strip().lower()),
                None,
            )
            if exact is not None:
                edges.append(
                    Edge(source_id=node_id, target_id=exact.id, type=EdgeType.has_facet, created_at=now)
                )
                continue
            label_vec = embedder.embed(label)
            best, best_sim = None, 0.0
            for facet in existing:
                sim = cosine(label_vec, embedder.embed(facet.body))
                if sim > best_sim:
                    best, best_sim = facet, sim
            if best is not None and best_sim >= _FACET_SUGGEST_THRESHOLD:
                warnings.append(
                    f"facet {label!r} not added: did you mean existing "
                    f"{best.body!r} ({best.id})? Re-call with that value or a distinct label."
                )
                continue
            facet_node = Node(
                id=str(uuid.uuid4()),
                type=NodeType.facet_value,
                tier=Tier.lifetime,
                path=f"/facet/{_slug(label)}",
                body=label.strip(),
                created_at=now,
            )
            minted.append(facet_node)
            existing.append(facet_node)
            edges.append(
                Edge(source_id=node_id, target_id=facet_node.id, type=EdgeType.has_facet, created_at=now)
            )
        return edges, minted, warnings

    # --- 2b. capture_entity — the domain model (4th dynamics class, MT3-29/30) ---

    def capture_entity(
        self,
        name: str,
        definition: str,
        goal_ref: str,
        facets: list[str] | None = None,
        evidence: str = "",
        edges: list[dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        """Propose one domain entity — a named thing the project's language refers to.

        Separate from ``capture_artifact`` because entities obey different rules on every
        axis that matters:

        - **Identity, not content.** The canonical name is the key (``/entity/<slug>``).
          Capturing ``Invoice`` twice returns the first node instead of minting a second
          — an entity the graph names twice is two half-domains that never rank into each
          other's recalls. The definition of an existing entity is never overwritten here:
          correcting it is a human edit, and *disagreeing* with it is an ordinary
          ``capture_artifact`` + CONTRADICTS, so the disagreement reaches review.
        - **Always proposed, never self-confirmed.** Every entity starts ``proposed`` and
          becomes part of the ratified domain model only when a human confirms it (GUI /
          lifecycle CLI). This holds for both adoption paths — greenfield elicitation and
          brownfield extraction differ in who *authored* the list, not in who ratifies it.
          ``evidence`` records the provenance (a ``file:line`` for extraction, the user's
          words for elicitation) into the ``entity_proposed`` event, where it is derived
          journal data rather than a metadata blob on the node (MT3-30 §9).
        - **No change scope.** Unlike an artifact, an entity gets no SCOPED_TO edge: the
          domain outlives the change that first named it, and the sweep roots entities by
          class. The goal→entity DEPENDS_ON edge still lands, so the entity is reachable
          from this change's goal seed.

        Returns ``{node_id, existing, status, entity_warnings?, edge_results}``.
        """
        clean_name = name.strip()
        if not clean_name:
            raise AgentSurfaceError("capture_entity: name must be non-empty")
        if not definition.strip():
            raise AgentSurfaceError(
                "capture_entity: definition must be non-empty — an entity nobody can tell "
                "apart from its neighbours is a label, not a domain model"
            )
        goal = self._require_goal(goal_ref)
        entity_path = f"/entity/{_slug(clean_name)}"

        existing_row = self._store._conn.execute(
            "SELECT id FROM nodes WHERE type = 'entity' AND path = ?", (entity_path,)
        ).fetchone()
        if existing_row:
            node_id = existing_row[0]
            return {
                "node_id": node_id,
                "existing": True,
                "status": self._store.entity_status(node_id),
                "note": (
                    f"entity {clean_name!r} already exists — reusing it. Its definition was "
                    "not changed; capture a decision/concept with a CONTRADICTS edge if you "
                    "disagree with it."
                ),
            }

        now = datetime.now(timezone.utc)
        node = Node(
            id=str(uuid.uuid4()),
            type=NodeType.entity,
            tier=Tier.short_term,  # tier is irrelevant to an entity's survival — see sweep
            path=entity_path,
            # Name lives in the body as well as the path so the entity is readable cold in
            # a recall bundle and so the name participates in seed-discovery embeddings.
            body=f"{clean_name} — {definition.strip()}",
            created_at=now,
        )

        warnings = self._entity_collisions(clean_name)
        new_edges = [
            Edge(source_id=goal.id, target_id=node.id, type=EdgeType.depends_on, created_at=now)
        ]
        edge_results: list[str] = []
        for spec in edges or []:
            target = self._require_node(str(spec.get("target", "")), "edges.target")
            try:
                edge_type = EdgeType(str(spec.get("type", "")))
            except ValueError:
                edge_type = None
            if edge_type is not EdgeType.depends_on:
                raise AgentSurfaceError(
                    "capture_entity: edges.type must be DEPENDS_ON — the part-of spine of "
                    "the domain model (LineItem DEPENDS_ON Invoice). Other relationships "
                    "between entities are statements, so they belong in a concept ABOUT "
                    f"both. Got {spec.get('type')!r}"
                )
            self._check_edge_endpoints(edge_type, node, target)
            new_edges.append(
                Edge(source_id=node.id, target_id=target.id, type=edge_type, created_at=now)
            )
            edge_results.append(f"{node.id} {edge_type.value} {target.id}")

        reason = f"proposed via agent surface: {evidence.strip()}" if evidence.strip() else (
            "proposed via agent surface (no provenance recorded)"
        )
        if warnings:
            reason += f" | collision check: {'; '.join(warnings)}"
        events = [
            Event(
                id=str(uuid.uuid4()),
                node_id=node.id,
                type=EventType.entity_proposed,
                weight=0.0,  # identity is not a truth claim — trust-neutral
                polarity=1,
                source="agent-surface",
                reason=reason,
                created_at=now,
            )
        ]
        facet_edges, facet_nodes, facet_warnings = self._resolve_facets(
            facets or [], node.id, now
        )
        self._store.write_atomic([node, *facet_nodes], new_edges + facet_edges, events)
        result: dict[str, Any] = {
            "node_id": node.id,
            "existing": False,
            "status": "proposed",
            "edge_results": edge_results,
        }
        if warnings:
            result["entity_warnings"] = warnings
        if facet_warnings:
            result["facet_warnings"] = facet_warnings
        return result

    def _entity_collisions(self, name: str) -> list[str]:
        """Near-duplicate entity names, as warnings the confirmation gate will see.

        Compares against *names* (the pre-em-dash prefix of the body), not full
        definitions: two entities with similar prose descriptions are usually genuinely
        different things, while two similar names usually are not.
        """
        embedder = self._store._embedder
        name_vec = embedder.embed(name)
        hits = []
        for node, status in self._store.entities():
            if status == "retired":
                continue
            other_name = node.body.split(" — ", 1)[0]
            if cosine(name_vec, embedder.embed(other_name)) >= _ENTITY_SUGGEST_THRESHOLD:
                hits.append(
                    f"{name!r} is close to existing entity {other_name!r} ({node.id}) — "
                    "reuse it, or say in the definition how they differ"
                )
        return hits

    # --- 3. link — relate existing nodes ---

    def link(
        self, source: str, target: str, type: str, reason: str = ""
    ) -> dict[str, Any]:
        """Relate two existing content nodes (esp. a mid-work CONTRADICTS).

        A CONTRADICTS edge *triggers flagging* of the target as a transparent
        side-effect — recorded in the journal, reported in ``side_effects``, and never
        a trust mutation (the evaluator derives trust impact later, MT3-27/28).
        """
        try:
            edge_type = EdgeType(type)
        except ValueError:
            edge_type = None
        if edge_type not in _LINKABLE_EDGE_TYPES:
            allowed = sorted(t.value for t in _LINKABLE_EDGE_TYPES)
            raise AgentSurfaceError(f"link: type must be one of {allowed}, got {type!r}")
        source_node = self._require_node(source, "link.source")
        target_node = self._require_node(target, "link.target")
        try:
            self._check_edge_endpoints(edge_type, source_node, target_node)
        except AgentSurfaceError as exc:
            raise AgentSurfaceError(f"link: {exc}") from exc
        side_effects: list[str] = []
        if edge_type is EdgeType.contradicts:
            self._store.raise_contradiction(
                source,
                target,
                source="agent-surface",
                reason=reason or f"link CONTRADICTS from {source}",
            )
            side_effects.append(f"{target} flagged needs_review")
        else:
            try:
                self._store.write_edge(
                    Edge(source_id=source, target_id=target, type=edge_type)
                )
            except sqlite3.IntegrityError:
                raise AgentSurfaceError(
                    f"link: edge {source} {edge_type.value} {target} already exists"
                )
        return {
            "edge": {"source": source, "target": target, "type": edge_type.value},
            "side_effects": side_effects,
        }

    # --- 4. append_event(s) — the feedback loop ---

    def append_event(
        self, event_type: str, node_ref: str, reason: str = ""
    ) -> dict[str, Any]:
        """Journal that something happened to a node. Append-only; never touches trust.

        ``node_ref`` is the stable id handed out by ``recall_context``, round-tripping
        back — this closes the feedback loop (MT3-21). CONTRADICTED additionally sets
        the review flag; trust itself only ever changes when a privileged caller folds
        the journal.
        """
        kind = event_type.strip().upper()
        if kind not in _EVENT_MAP:
            raise AgentSurfaceError(
                f"append_event: event_type must be one of {sorted(_EVENT_MAP)}, got {event_type!r}"
            )
        node = self._require_node(node_ref, "node_ref")
        if kind == "CONTRADICTED":
            event = self._store.flag_contradicted(
                node.id, source="agent-surface", reason=reason
            )
            return {"event_id": event.id, "side_effects": [f"{node.id} flagged needs_review"]}
        etype, weight, polarity = _EVENT_MAP[kind]
        event = self._store.append_event(
            Event(
                node_id=node.id,
                type=etype,
                weight=weight,
                polarity=polarity,
                source="agent-surface",
                reason=reason,
            )
        )
        return {"event_id": event.id}

    def append_events(self, events: list[dict[str, str]]) -> list[dict[str, Any]]:
        """Batched ``append_event`` (LOCKED in MT3-21 for token economy)."""
        return [
            self.append_event(
                str(spec.get("event_type", "")),
                str(spec.get("node_ref", "")),
                str(spec.get("reason", "")),
            )
            for spec in events
        ]

    # --- read path: recall_context ---

    def recall_context(self, query: str, goal_ref: str) -> str:
        """The one read call: a scored subgraph serialized for an agent (MT3-20 Pass 3).

        Internally: liveness mask → multi-seed PPR (goal-dominant) → blended scoring →
        rank. The wire format is ranked content blocks (verbatim text, order = signal)
        with coarse type/tier tags and stable ids, plus a compact edge-list of surviving
        CONTRADICTS relationships among the returned nodes. No weights, no scores, no
        internal mechanism leaks; the ids double as write-back handles for
        ``append_event``.
        """
        goal = self._require_goal(goal_ref)
        ranked = self._store.recall_multi(query, goal.id)
        if not ranked:
            return "(no context found for this goal)"
        blocks = []
        ids = [node.id for node, _ in ranked]
        for node, _score in ranked:
            tags = f"type={node.type.value} tier={node.tier.value}"
            if node.type is NodeType.entity:
                # An unratified entity read as settled domain language is the entity-side
                # analogue of serving a disputed node as truth — tag it the same way.
                status = self._store.entity_status(node.id)
                tags += f" {status}" if status != "confirmed" else " confirmed"
            if node.needs_review:
                tags += " disputed"
            blocks.append(f"[node:{node.id}] {tags}\n{node.body}")
        placeholders = ",".join("?" for _ in ids)
        rows = self._store._conn.execute(
            f"SELECT source_id, target_id FROM edges WHERE type = 'CONTRADICTS' "
            f"AND source_id IN ({placeholders}) AND target_id IN ({placeholders}) "
            "ORDER BY source_id, target_id",
            ids + ids,
        ).fetchall()
        if rows:
            edge_lines = "\n".join(f"[{s}] CONTRADICTS [{t}]" for s, t in rows)
            blocks.append(f"contradictions:\n{edge_lines}")
        return "\n\n".join(blocks)

    # --- read path: trace_impact & review_queue (both strictly read-only) ---

    def trace_impact(self, node_ref: str) -> str:
        """Read-only: the artifacts that depend on ``node_ref`` — its blast radius.

        Wraps ``MemoryStore.impact_of`` for the ``trace-impact`` skill: call it before
        proposing a change to an artifact so the ripple is visible. Returns dependents
        as ranked blocks (nearest first) tagged with stable ids, type/tier, hop
        ``depth``, and a 'disputed' marker on flagged nodes. Fidelity is bounded by the
        explicit DEPENDS_ON edges in the graph — an undocumented dependency won't show.
        Never mutates anything.
        """
        node = self._require_node(node_ref, "node_ref")
        impacted = self._store.impact_of(node.id)
        if not impacted:
            return f"(nothing depends on [node:{node.id}])"
        blocks = []
        for dep, distance in impacted:
            tags = f"type={dep.type.value} tier={dep.tier.value} depth={distance}"
            if dep.needs_review:
                tags += " disputed"
            blocks.append(f"[node:{dep.id}] {tags}\n{dep.body}")
        return "\n\n".join(blocks)

    def domain_model(self, status: str = "all") -> str:
        """Read-only: the project's domain entities and their ratification status.

        The read half of the domain-model workflow — what the graph currently believes the
        project's ubiquitous language is. ``status`` filters to ``proposed`` (the review
        backlog a human owes a ruling on), ``confirmed`` (the ratified model), or ``all``
        (default; retired entities are excluded from every filter, and reachable only via
        ``include_retired`` on the store).

        Read-only by construction: proposing goes through ``capture_entity``, and
        confirming/retiring is a privileged human act, never reachable from here.
        """
        wanted = status.strip().lower() or "all"
        if wanted not in ("all", "proposed", "confirmed"):
            raise AgentSurfaceError(
                f"domain_model: status must be all | proposed | confirmed, got {status!r}"
            )
        rows = [
            (node, node_status)
            for node, node_status in self._store.entities()
            if wanted == "all" or node_status == wanted
        ]
        if not rows:
            if wanted == "all":
                return (
                    "(no domain entities yet — the project's ubiquitous language has not "
                    "been modelled; run the domain-modelling workflow)"
                )
            return f"(no {wanted} domain entities)"
        blocks = []
        for node, node_status in rows:
            about = self._store._conn.execute(
                "SELECT COUNT(*) FROM edges e JOIN nodes n ON n.id = e.source_id "
                "AND n.archived = 0 WHERE e.target_id = ? AND e.type = 'ABOUT'",
                (node.id,),
            ).fetchone()[0]
            tags = f"status={node_status} attached={about}"
            if node.needs_review:
                tags += " disputed"
            blocks.append(f"[node:{node.id}] {tags}\n{node.body}")
        return "\n\n".join(blocks)

    def consolidation_candidates(self) -> str:
        """Read-only: recurrence in the graph that is worth abstracting into one node.

        Surfaces clusters of live, un-promoted artifacts that say variations of the same
        thing across several changes (the MT3-18/29 recurrence trigger). Read-only on
        purpose: minting the abstraction is a privileged step, because a consolidated node
        exists to be promoted past the sweep and an agent that both abstracts and nominates
        its own abstraction is writing long-term memory unsupervised. The agent's job is to
        bring the candidate to the human with a proposed wording.
        """
        candidates = self._store.consolidation_candidates()
        if not candidates:
            return "(no consolidation candidates — no cross-change recurrence detected)"
        blocks = []
        for candidate in candidates:
            members = []
            for node_id in candidate["node_ids"]:
                node = self._store.read_node(node_id)
                if node is not None:
                    members.append(f"  [node:{node.id}] type={node.type.value}\n  {node.body}")
            blocks.append(
                f"candidate: facet={candidate['facet']!r} "
                f"instances={len(candidate['node_ids'])} "
                f"scopes={len(candidate['scopes'])} "
                f"suggested_type={candidate['suggested_type']}\n" + "\n".join(members)
            )
        return "\n\n".join(blocks)

    def review_queue(self) -> str:
        """Read-only: the staleness queue — content nodes flagged ``needs_review``.

        Wraps ``MemoryStore.flagged_nodes`` for the ``review-staleness`` skill (a human
        gate at PR/review). Returns flagged nodes newest-first as blocks tagged with
        stable ids and type/tier. This surface only *reads* the queue — clearing a flag
        is the evaluator's/human's privileged call, never the agent's (safety invariant).
        """
        flagged = self._store.flagged_nodes()
        if not flagged:
            return "(no nodes are flagged for review)"
        blocks = [
            f"[node:{node.id}] type={node.type.value} tier={node.tier.value} disputed\n{node.body}"
            for node in flagged
        ]
        return "\n\n".join(blocks)
