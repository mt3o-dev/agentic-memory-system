"""The agent-facing surface: four write operations and one read (Slice 9, MT3-21).

This layer is the entire write vocabulary an AI agent gets: open a scope
(``create_change``), add a node (``capture_artifact``), add an edge (``link``), log
that something happened (``append_event``/``append_events``) — plus the one read call
(``recall_context``). It is transport-independent; ``mcp_server.py`` wraps it in MCP.

⚠ Safety invariant (MT3-21 — protect this forever): nothing reachable from this class
can mutate trust, clear a review flag, promote a tier, or archive a node. Those are
either *derived* (trust folds from the journal, MT3-28) or live on *separate privileged
paths* (evaluator batch MT3-27, sweep/merge lifecycle). Recording a CONTRADICTS edge or
a CONTRADICTED event flags the target for review as a transparent side-effect — the
agent is recording that a contradiction exists, not deciding the target is wrong.
Never add a set_trust / clear_flag / promote / archive call here "for convenience".
"""

import re
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any

from .embedding import cosine
from .schema import Node, NodeType, Tier, Edge, EdgeType, Event, EventType
from .storage import MemoryStore

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
_LINKABLE_EDGE_TYPES = frozenset({EdgeType.depends_on, EdgeType.contradicts})

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

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slug(label: str) -> str:
    return _SLUG_RE.sub("-", label.lower()).strip("-")


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
            raise AgentSurfaceError(
                f"capture_artifact: type must be one of {allowed}, got {type!r}"
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
            if target.type in (NodeType.slice, NodeType.facet_value):
                raise AgentSurfaceError(
                    f"edges.target: {target.id!r} is a structural anchor, not content"
                )
            try:
                edge_type = EdgeType(str(spec.get("type", "")))
            except ValueError:
                edge_type = None
            if edge_type not in _LINKABLE_EDGE_TYPES:
                raise AgentSurfaceError(
                    f"edges.type must be DEPENDS_ON or CONTRADICTS, got {spec.get('type')!r}"
                )
            direction = str(spec.get("direction", "out"))
            if direction not in ("out", "in"):
                raise AgentSurfaceError("edges.direction must be 'out' or 'in'")
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
            raise AgentSurfaceError(
                f"link: type must be DEPENDS_ON or CONTRADICTS, got {type!r}"
            )
        source_node = self._require_node(source, "link.source")
        target_node = self._require_node(target, "link.target")
        for node in (source_node, target_node):
            if node.type in (NodeType.slice, NodeType.facet_value):
                raise AgentSurfaceError(
                    f"link: {node.id!r} is a structural anchor, not content"
                )
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
