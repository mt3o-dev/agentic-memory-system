import sqlite3
import uuid
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

from .schema import Node, NodeType, Tier, Edge, EdgeType, Event, EventType
from .fold import FoldStrategy, SumAndClampFold
from .penalty import (
    PenaltyStrategy,
    TrustTermPenalty,
    ScoreComponents,
    compute_penalty,
)
from .embedding import Embedder, HashedBagOfWordsEmbedder, cosine
from .retrieval import (
    DEFAULT_DAMPING,
    DEFAULT_EDGE_POLICY,
    DEFAULT_GOAL_WEIGHT,
    Direction,
    build_seed_vector,
    build_weighted_graph,
    personalized_pagerank,
)

_CREATE_NODES = """
CREATE TABLE IF NOT EXISTS nodes (
    id          TEXT    PRIMARY KEY,
    type        TEXT    NOT NULL CHECK(type IN ('decision','concept','constraint','issue','invariant','slice','facet_value','goal')),
    tier        TEXT    NOT NULL CHECK(tier IN ('short-term','mid-term','long-term','lifetime')),
    path        TEXT    NOT NULL,
    body        TEXT    NOT NULL,
    created_at  TEXT    NOT NULL,
    needs_review     INTEGER NOT NULL DEFAULT 0,
    retrieval_weight REAL    NOT NULL DEFAULT 1.0,
    trust_weight     REAL    NOT NULL DEFAULT 1.0,
    archived         INTEGER NOT NULL DEFAULT 0
)
"""


_CREATE_EDGES = """
CREATE TABLE IF NOT EXISTS edges (
    source_id   TEXT NOT NULL REFERENCES nodes(id),
    target_id   TEXT NOT NULL REFERENCES nodes(id),
    type        TEXT NOT NULL CHECK(type IN ('DEPENDS_ON','CONTRADICTS','SCOPED_TO','HAS_FACET')),
    created_at  TEXT NOT NULL,
    PRIMARY KEY (source_id, target_id, type)
)
"""

_CREATE_EVENTS = """
CREATE TABLE IF NOT EXISTS events (
    id          TEXT PRIMARY KEY,
    node_id     TEXT NOT NULL REFERENCES nodes(id),
    type        TEXT NOT NULL CHECK(type IN ('contradiction_raised','contradiction_cleared','confirmation_added','manual_review','tier_change','slice_activated','slice_deactivated','archived','reactivated','used','noted','content_edited','weight_set')),
    weight      REAL NOT NULL,
    polarity    INTEGER NOT NULL CHECK(polarity IN (-1, 1)),
    source      TEXT NOT NULL,
    reason      TEXT NOT NULL,
    created_at  TEXT NOT NULL
)
"""

_ALPHA = 0.5
_BETA = 0.3
_GAMMA = 0.2
_HOP_HALFLIFE = 3.0
_RECENCY_HALFLIFE_DAYS = 7.0
_K_SEED_FACETS = 3

_TRAVERSE_CTE = """
WITH RECURSIVE reachable(node_id, source_id, target_id, etype, edge_created_at) AS (
    SELECT ?, NULL, NULL, NULL, NULL
    UNION
    SELECT e.target_id, e.source_id, e.target_id, e.type, e.created_at
    FROM edges e
    JOIN reachable r ON e.source_id = r.node_id
    JOIN nodes tn ON tn.id = e.target_id AND tn.archived = 0 AND tn.type NOT IN ('slice','facet_value')
    WHERE e.type IN ('DEPENDS_ON','CONTRADICTS')
)
SELECT r.node_id, r.source_id, r.target_id, r.etype, r.edge_created_at,
       n.id, n.type, n.tier, n.path, n.body, n.created_at, n.needs_review,
       n.retrieval_weight, n.trust_weight, n.archived
FROM reachable r
JOIN nodes n ON n.id = r.node_id AND n.archived = 0 AND n.type NOT IN ('slice','facet_value')
"""


class MemoryStore:
    def __init__(
        self,
        db_path: str | Path = "context/memory-graph.db",
        fold_strategy: FoldStrategy | None = None,
        penalty_strategy: PenaltyStrategy | None = None,
        embedder: Embedder | None = None,
        edge_policy: dict[tuple[EdgeType, Direction], float] | None = None,
    ) -> None:
        # check_same_thread=False: the GUI server's event loop may touch the
        # connection from a different thread than the one that opened it. Access is
        # still effectively serialized (single event loop / single test portal);
        # this is not a concurrent-writer guarantee.
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._fold_strategy = fold_strategy or SumAndClampFold()
        self._penalty_strategy = penalty_strategy or TrustTermPenalty()
        self._embedder = embedder or HashedBagOfWordsEmbedder()
        self._edge_policy = edge_policy or DEFAULT_EDGE_POLICY
        with self._conn:
            self._conn.execute(_CREATE_NODES)
            self._conn.execute(_CREATE_EDGES)
            self._conn.execute(_CREATE_EVENTS)
        for col in ("retrieval_weight", "trust_weight"):
            try:
                self._conn.execute(
                    f"ALTER TABLE nodes ADD COLUMN {col} REAL NOT NULL DEFAULT 1.0"
                )
            except sqlite3.OperationalError:
                pass  # column already exists
        try:
            self._conn.execute(
                "ALTER TABLE nodes ADD COLUMN archived INTEGER NOT NULL DEFAULT 0"
            )
        except sqlite3.OperationalError:
            pass  # column already exists
        self._migrate_nodes_check()
        self._migrate_edges_check()
        self._migrate_events_check()

    def _rebuild_table(self, name: str, create_sql: str, columns: str) -> None:
        """Atomically rebuild a table to pick up a widened CHECK: rename → recreate →
        copy → drop, inside an explicit transaction so a mid-rebuild failure rolls back
        to the original table. Python's sqlite3 does not auto-begin a transaction for
        DDL, so without the explicit BEGIN the RENAME/CREATE would commit before the copy
        and a crash could strand data in the temp table. ``legacy_alter_table=ON`` keeps
        the RENAME from rewriting foreign-key references in *other* tables (edges/events
        REFERENCE nodes(id)) to point at the temp table we then drop; both PRAGMAs are
        toggled outside the transaction because ``foreign_keys`` cannot change
        mid-transaction. ``name``/``columns`` are internal constants, never caller input.
        """
        prev_isolation = self._conn.isolation_level
        self._conn.execute("PRAGMA legacy_alter_table=ON")
        self._conn.execute("PRAGMA foreign_keys=OFF")
        self._conn.isolation_level = None  # take manual transaction control
        try:
            self._conn.execute("BEGIN")
            try:
                self._conn.execute(f"ALTER TABLE {name} RENAME TO _{name}_old")
                self._conn.execute(create_sql)
                self._conn.execute(
                    f"INSERT INTO {name} ({columns}) SELECT {columns} FROM _{name}_old"
                )
                self._conn.execute(f"DROP TABLE _{name}_old")
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise
        finally:
            self._conn.isolation_level = prev_isolation
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.execute("PRAGMA legacy_alter_table=OFF")

    def _migrate_nodes_check(self) -> None:
        """Widen the nodes ``type`` CHECK on DBs created before the newest node type.

        SQLite can't ``ALTER`` a CHECK in place, so a DB whose nodes CHECK predates
        ``facet_value`` would reject a facet-value node. Detect that case and rebuild the
        table (rename → recreate with the current schema → copy → drop) with foreign keys
        off for the duration. Runs after the ``archived`` ADD COLUMN so the copy includes
        it. Guarded on the newest allowed type ('facet_value'), which also covers the
        earlier widening to 'slice'. A no-op on fresh/already-migrated DBs.
        """
        row = self._conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='nodes'"
        ).fetchone()
        if row is None or row[0] is None or "'goal'" in row[0]:
            return
        self._rebuild_table(
            "nodes",
            _CREATE_NODES,
            "id, type, tier, path, body, created_at, needs_review, retrieval_weight, trust_weight, archived",
        )

    def _migrate_edges_check(self) -> None:
        """Widen the edges ``type`` CHECK on DBs created before HAS_FACET existed.

        SQLite can't ``ALTER`` a CHECK constraint in place and ``CREATE TABLE IF NOT
        EXISTS`` won't touch a table that already exists, so a DB created with an older
        CHECK would reject a HAS_FACET edge. Detect that case and rebuild the table
        (rename → recreate with the current schema → copy → drop), following SQLite's
        recommended table-alteration procedure with foreign keys disabled for the
        duration. Guarded on the newest allowed type, so it also covers the earlier
        CONTRADICTS and SCOPED_TO widenings. A no-op on fresh/already-migrated DBs.
        """
        row = self._conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='edges'"
        ).fetchone()
        if row is None or row[0] is None or "HAS_FACET" in row[0]:
            return
        self._rebuild_table(
            "edges", _CREATE_EDGES, "source_id, target_id, type, created_at"
        )

    def _migrate_events_check(self) -> None:
        """Widen the events ``type`` CHECK on DBs created before the lifecycle/archival
        event types existed.

        Same rebuild procedure as the nodes/edges migrations. Guarded on the presence of
        'slice_activated' (one of the newest allowed types). A no-op on fresh/already-
        migrated DBs.
        """
        row = self._conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='events'"
        ).fetchone()
        # Guard on the NEWEST allowed type (like the nodes/edges migrations): a DB whose
        # CHECK already lists an earlier new type but predates 'weight_set' must still
        # rebuild, or the GUI editing inserts would hit a CHECK failure.
        if row is None or row[0] is None or "'weight_set'" in row[0]:
            return
        self._rebuild_table(
            "events",
            _CREATE_EVENTS,
            "id, node_id, type, weight, polarity, source, reason, created_at",
        )

    def write_node(self, node: Node) -> Node:
        node_id = node.id if node.id is not None else str(uuid.uuid4())
        created_at = node.created_at if node.created_at is not None else datetime.now(timezone.utc)
        with self._conn:
            self._conn.execute(
                "INSERT INTO nodes (id, type, tier, path, body, created_at, needs_review, retrieval_weight, trust_weight, archived) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    node_id,
                    node.type.value,
                    node.tier.value,
                    node.path,
                    node.body,
                    created_at.isoformat(),
                    int(node.needs_review),
                    node.retrieval_weight,
                    node.trust_weight,
                    int(node.archived),
                ),
            )
        return node.model_copy(update={"id": node_id, "created_at": created_at})

    def read_node(self, node_id: str) -> Node | None:
        row = self._conn.execute(
            "SELECT id, type, tier, path, body, created_at, needs_review, retrieval_weight, trust_weight, archived FROM nodes WHERE id = ?",
            (node_id,),
        ).fetchone()
        if row is None:
            return None
        return Node(
            id=row[0],
            type=NodeType(row[1]),
            tier=Tier(row[2]),
            path=row[3],
            body=row[4],
            created_at=datetime.fromisoformat(row[5]),
            needs_review=bool(row[6]),
            retrieval_weight=row[7],
            trust_weight=row[8],
            archived=bool(row[9]),
        )

    def write_edge(self, edge: Edge) -> Edge:
        created_at = edge.created_at if edge.created_at is not None else datetime.now(timezone.utc)
        with self._conn:
            self._conn.execute(
                "INSERT INTO edges (source_id, target_id, type, created_at) VALUES (?, ?, ?, ?)",
                (edge.source_id, edge.target_id, edge.type.value, created_at.isoformat()),
            )
        return edge.model_copy(update={"created_at": created_at})

    def _latest_severity(self, node_id: str) -> float:
        """Weight of the most recent contradiction_raised event, or 1.0 if none.

        This is the severity term the query-time penalty reads. It is deliberately
        *not* folded into trust_weight — a contradiction flags, it does not decrement
        (MT3-23). trust_weight is only ever changed by an explicit recompute_trust call.
        """
        row = self._conn.execute(
            "SELECT weight FROM events WHERE node_id = ? AND type = ? "
            "ORDER BY created_at DESC, id DESC LIMIT 1",
            (node_id, EventType.contradiction_raised.value),
        ).fetchone()
        return row[0] if row is not None else 1.0

    def raise_contradiction(
        self,
        source_id: str,
        target_id: str,
        *,
        severity: float = 1.0,
        source: str,
        reason: str,
    ) -> Edge:
        """Flag ``target_id`` as needing review because ``source_id`` contradicts it.

        Atomically writes a CONTRADICTS edge, sets the target's needs_review flag, and
        appends a contradiction_raised event (carrying ``severity`` as its weight, for
        the query-time penalty). Does NOT touch trust_weight — the demotion is entirely
        flag-driven at recall time, so clear_contradiction can restore the score for free.
        """
        created_at = datetime.now(timezone.utc)
        edge = Edge(
            source_id=source_id,
            target_id=target_id,
            type=EdgeType.contradicts,
            created_at=created_at,
        )
        event_id = str(uuid.uuid4())
        with self._conn:
            # INSERT OR IGNORE: re-raising on an existing pair is idempotent for the edge
            # (it's a set-membership fact) while the flag update + event below still apply,
            # so the contradiction_raised event log carries the severity history.
            self._conn.execute(
                "INSERT OR IGNORE INTO edges (source_id, target_id, type, created_at) VALUES (?, ?, ?, ?)",
                (source_id, target_id, EdgeType.contradicts.value, created_at.isoformat()),
            )
            self._conn.execute(
                "UPDATE nodes SET needs_review = 1 WHERE id = ?", (target_id,)
            )
            self._conn.execute(
                "INSERT INTO events (id, node_id, type, weight, polarity, source, reason, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    event_id,
                    target_id,
                    EventType.contradiction_raised.value,
                    severity,
                    -1,
                    source,
                    reason,
                    created_at.isoformat(),
                ),
            )
        return edge

    def flag_contradicted(
        self, node_id: str, *, severity: float = 1.0, source: str, reason: str
    ) -> Event:
        """Record that a node was contradicted, without naming a contradicting node.

        The edge-less sibling of ``raise_contradiction`` for the agent feedback loop
        (MT3-21 ``CONTRADICTED`` events): sets ``needs_review`` and appends a
        ``contradiction_raised`` event atomically. Like ``raise_contradiction``, it
        never touches ``trust_weight`` — trust only changes when a privileged caller
        folds the journal via ``recompute_trust``.
        """
        created_at = datetime.now(timezone.utc)
        event_id = str(uuid.uuid4())
        with self._conn:
            cursor = self._conn.execute(
                "UPDATE nodes SET needs_review = 1 WHERE id = ?", (node_id,)
            )
            if cursor.rowcount == 0:
                raise ValueError(f"flag_contradicted: no node with id {node_id!r}")
            self._conn.execute(
                "INSERT INTO events (id, node_id, type, weight, polarity, source, reason, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    event_id,
                    node_id,
                    EventType.contradiction_raised.value,
                    severity,
                    -1,
                    source,
                    reason,
                    created_at.isoformat(),
                ),
            )
        return Event(
            id=event_id,
            node_id=node_id,
            type=EventType.contradiction_raised,
            weight=severity,
            polarity=-1,
            source=source,
            reason=reason,
            created_at=created_at,
        )

    def write_atomic(
        self,
        nodes: list[Node],
        edges: list[Edge],
        events: list[Event] | None = None,
        flag_node_ids: list[str] | None = None,
    ) -> None:
        """Write nodes, edges, events, and review flags in one transaction.

        The write-path atomicity primitive (MT3-21): ``capture_artifact`` commits a node
        together with its edges (and any CONTRADICTS side-effect flags/journal entries)
        or not at all, which structurally prevents orphan nodes. Callers supply fully
        populated models — ids and ``created_at`` must already be set.
        """
        with self._conn:
            for node in nodes:
                self._conn.execute(
                    "INSERT INTO nodes (id, type, tier, path, body, created_at, needs_review, retrieval_weight, trust_weight, archived) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        node.id,
                        node.type.value,
                        node.tier.value,
                        node.path,
                        node.body,
                        node.created_at.isoformat(),  # type: ignore[union-attr]
                        int(node.needs_review),
                        node.retrieval_weight,
                        node.trust_weight,
                        int(node.archived),
                    ),
                )
            for edge in edges:
                self._conn.execute(
                    "INSERT INTO edges (source_id, target_id, type, created_at) VALUES (?, ?, ?, ?)",
                    (
                        edge.source_id,
                        edge.target_id,
                        edge.type.value,
                        edge.created_at.isoformat(),  # type: ignore[union-attr]
                    ),
                )
            for event in events or []:
                self._conn.execute(
                    "INSERT INTO events (id, node_id, type, weight, polarity, source, reason, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        event.id,
                        event.node_id,
                        event.type.value,
                        event.weight,
                        event.polarity,
                        event.source,
                        event.reason,
                        event.created_at.isoformat(),  # type: ignore[union-attr]
                    ),
                )
            for node_id in flag_node_ids or []:
                self._conn.execute(
                    "UPDATE nodes SET needs_review = 1 WHERE id = ?", (node_id,)
                )

    def clear_contradiction(self, target_id: str, *, source: str, reason: str) -> None:
        """Confirm a false alarm: clear the review flag and log the clearance.

        Atomically unsets needs_review and appends a contradiction_cleared event whose
        weight mirrors the latest contradiction's severity (polarity +1), so that a
        later fold cancels the raise. Because the recall penalty is flag-driven and
        trust_weight was never decremented, clearing the flag restores the node's
        effective score exactly, at no cost.
        """
        created_at = datetime.now(timezone.utc)
        event_id = str(uuid.uuid4())
        severity = self._latest_severity(target_id)
        with self._conn:
            self._conn.execute(
                "UPDATE nodes SET needs_review = 0 WHERE id = ?", (target_id,)
            )
            self._conn.execute(
                "INSERT INTO events (id, node_id, type, weight, polarity, source, reason, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    event_id,
                    target_id,
                    EventType.contradiction_cleared.value,
                    severity,
                    1,
                    source,
                    reason,
                    created_at.isoformat(),
                ),
            )

    def resolve_review(
        self,
        node_id: str,
        *,
        action: str,
        source: str,
        reason: str,
        new_body: str | None = None,
        replacement_id: str | None = None,
        recompute: bool = False,
    ) -> dict:
        """Apply one guided-review decision atomically (human surface only).

        The composite behind the GUI's guided resolution wizard: each ``action`` maps
        to the same primitives the individual privileged endpoints use, but committed
        in ONE transaction with one journal story — ``edit + clear`` or
        ``archive + clear`` either fully applies or not at all. Statements are inlined
        rather than calling ``clear_contradiction``/``_journaled_update`` because those
        each open their own transaction (sqlite3 transactions don't nest).

        Actions: ``still_valid`` clears the flag; ``superseded`` archives + clears and
        optionally records lineage (replacement DEPENDS_ON node); ``wrong`` archives +
        clears; ``needs_correction`` rewrites the body then clears; ``defer`` changes
        no state. Every action appends a ``manual_review`` event (trust-neutral)
        recording the decision, so even "looked and deferred" is auditable.
        ``recompute=True`` folds the post-resolution journal into ``trust_weight``
        inside the same transaction.
        """
        if action not in ("still_valid", "superseded", "wrong", "needs_correction", "defer"):
            raise ValueError(f"resolve_review: unknown action {action!r}")
        if action == "needs_correction" and not (new_body or "").strip():
            raise ValueError("resolve_review: needs_correction requires new_body")
        node = self.read_node(node_id)
        if node is None:
            raise ValueError(f"resolve_review: no node with id {node_id!r}")
        created_at = datetime.now(timezone.utc)
        stamp = created_at.isoformat()
        severity = self._latest_severity(node_id)

        def _event(event_type: EventType, weight: float, polarity: int, why: str):
            self._conn.execute(
                "INSERT INTO events (id, node_id, type, weight, polarity, source, reason, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (str(uuid.uuid4()), node_id, event_type.value, weight, polarity, source, why, stamp),
            )

        trust_weight = node.trust_weight
        with self._conn:
            if action == "needs_correction":
                self._conn.execute(
                    "UPDATE nodes SET body = ? WHERE id = ?", (new_body, node_id)
                )
                _event(EventType.content_edited, 0.0, 1, "guided review correction")
            if action in ("superseded", "wrong"):
                self._conn.execute(
                    "UPDATE nodes SET archived = 1 WHERE id = ?", (node_id,)
                )
                _event(EventType.archived, 0.0, -1, f"guided review: {action}")
                if action == "superseded" and replacement_id:
                    self._conn.execute(
                        "INSERT OR IGNORE INTO edges (source_id, target_id, type, created_at) VALUES (?, ?, ?, ?)",
                        (replacement_id, node_id, EdgeType.depends_on.value, stamp),
                    )
            if action != "defer":
                self._conn.execute(
                    "UPDATE nodes SET needs_review = 0 WHERE id = ?", (node_id,)
                )
                _event(
                    EventType.contradiction_cleared, severity, 1,
                    f"guided review: {action}",
                )
            _event(EventType.manual_review, 0.0, 1, reason)
            if recompute:
                trust_weight = self._fold_strategy.fold(self.read_events(node_id))
                self._conn.execute(
                    "UPDATE nodes SET trust_weight = ? WHERE id = ?",
                    (trust_weight, node_id),
                )
        return {"action": action, "trust_weight": trust_weight}

    def append_event(self, event: Event) -> Event:
        event_id = event.id if event.id is not None else str(uuid.uuid4())
        created_at = event.created_at if event.created_at is not None else datetime.now(timezone.utc)
        with self._conn:
            self._conn.execute(
                "INSERT INTO events (id, node_id, type, weight, polarity, source, reason, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    event_id,
                    event.node_id,
                    event.type.value,
                    event.weight,
                    event.polarity,
                    event.source,
                    event.reason,
                    created_at.isoformat(),
                ),
            )
        return event.model_copy(update={"id": event_id, "created_at": created_at})

    def read_events(self, node_id: str) -> list[Event]:
        rows = self._conn.execute(
            "SELECT id, node_id, type, weight, polarity, source, reason, created_at "
            "FROM events WHERE node_id = ? ORDER BY created_at ASC, id ASC",
            (node_id,),
        ).fetchall()
        return [
            Event(
                id=r[0],
                node_id=r[1],
                type=EventType(r[2]),
                weight=r[3],
                polarity=r[4],
                source=r[5],
                reason=r[6],
                created_at=datetime.fromisoformat(r[7]),
            )
            for r in rows
        ]

    def flagged_nodes(self) -> list[Node]:
        """Live content nodes flagged ``needs_review`` — the staleness queue (read-only).

        The read primitive behind a durable, cross-session staleness queue (the
        ``stale_nodes()`` read call anticipated by the slice-10 plan-brief). Returns
        flagged, non-archived content nodes newest-first; anchors (slice/facet_value)
        are never flagged content and are excluded. Purely a read — clearing a flag is
        the evaluator's/human's privileged call (``clear_contradiction``), never this.
        """
        rows = self._conn.execute(
            "SELECT id FROM nodes WHERE needs_review = 1 AND archived = 0 "
            "AND type NOT IN ('slice','facet_value') "
            "ORDER BY created_at DESC, id DESC"
        ).fetchall()
        return [self.read_node(r[0]) for r in rows]

    def recompute_trust(self, node_id: str, strategy: FoldStrategy | None = None) -> float:
        events = self.read_events(node_id)
        trust_weight = (strategy or self._fold_strategy).fold(events)
        with self._conn:
            cursor = self._conn.execute(
                "UPDATE nodes SET trust_weight = ? WHERE id = ?", (trust_weight, node_id)
            )
            if cursor.rowcount == 0:
                raise ValueError(f"recompute_trust: no node with id {node_id!r}")
        return trust_weight

    def set_tier(self, node_id: str, tier: Tier, *, source: str, reason: str) -> None:
        """Privileged tier change (promotion/demotion) — human checkpoint, journaled.

        Not part of the agent surface (MT3-18/21: promotion is never the agent's
        call). The override is recorded as a trust-neutral ``tier_change`` event so
        manual intervention stays inside the audit trail and derived-state guarantees.
        """
        self._journaled_update(
            node_id, "tier = ?", (tier.value,), EventType.tier_change, 1, source,
            f"tier -> {tier.value}" + (f": {reason}" if reason else ""),
        )

    def _journaled_update(
        self, node_id: str, set_sql: str, params: tuple, event_type: EventType,
        polarity: int, source: str, reason: str,
    ) -> None:
        """UPDATE one node + append a trust-neutral journal event, atomically."""
        created_at = datetime.now(timezone.utc)
        with self._conn:
            cursor = self._conn.execute(
                f"UPDATE nodes SET {set_sql} WHERE id = ?", (*params, node_id)
            )
            if cursor.rowcount == 0:
                raise ValueError(f"no node with id {node_id!r}")
            self._conn.execute(
                "INSERT INTO events (id, node_id, type, weight, polarity, source, reason, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    str(uuid.uuid4()),
                    node_id,
                    event_type.value,
                    0.0,
                    polarity,
                    source,
                    reason,
                    created_at.isoformat(),
                ),
            )

    def edit_body(self, node_id: str, body: str, *, source: str, reason: str = "") -> None:
        """Privileged content edit, journaled as a ``content_edited`` event.

        The event is trust-neutral; whether prior confirmations still apply to the new
        text is the human's call (re-confirm or recompute after editing).
        """
        self._journaled_update(
            node_id, "body = ?", (body,), EventType.content_edited, 1, source,
            reason or "body edited",
        )

    def set_weights(
        self,
        node_id: str,
        *,
        trust_weight: float | None = None,
        retrieval_weight: float | None = None,
        source: str,
        reason: str = "",
    ) -> None:
        """Privileged direct weight override, journaled as a ``weight_set`` event.

        Note the interaction with derived state: a manually set ``trust_weight``
        persists only until the next ``recompute_trust`` folds the journal again.
        """
        sets, params, parts = [], [], []
        if trust_weight is not None:
            sets.append("trust_weight = ?")
            params.append(trust_weight)
            parts.append(f"trust={trust_weight}")
        if retrieval_weight is not None:
            sets.append("retrieval_weight = ?")
            params.append(retrieval_weight)
            parts.append(f"retrieval={retrieval_weight}")
        if not sets:
            raise ValueError("set_weights: nothing to set")
        self._journaled_update(
            node_id, ", ".join(sets), tuple(params), EventType.weight_set, 1, source,
            ("; ".join(parts)) + (f": {reason}" if reason else ""),
        )

    def set_archived(self, node_id: str, archived: bool, *, source: str, reason: str = "") -> None:
        """Privileged manual archive/unarchive, journaled like sweep transitions.

        A manual override persists only until the next ``sweep`` recomputes liveness
        from the root set — use change deactivation + sweep for durable archival.
        """
        self._journaled_update(
            node_id,
            "archived = ?",
            (int(archived),),
            EventType.archived if archived else EventType.reactivated,
            -1 if archived else 1,
            source,
            reason or ("archived manually" if archived else "reactivated manually"),
        )

    def compact_events(self, node_id: str) -> None:
        # Deliberate no-op stub: compaction strategy is an open design question
        # (MT3-28), deferred beyond this slice.
        pass

    # --- slice lifecycle (active-state derived by folding journal events) ---

    def activate_slice(self, slice_id: str, *, source: str = "lifecycle", reason: str = "") -> Event:
        """Mark a slice active by appending a trust-neutral ``slice_activated`` event."""
        return self.append_event(
            Event(
                node_id=slice_id,
                type=EventType.slice_activated,
                weight=0.0,
                polarity=1,
                source=source,
                reason=reason,
            )
        )

    def deactivate_slice(self, slice_id: str, *, source: str = "lifecycle", reason: str = "") -> Event:
        """Mark a slice inactive by appending a trust-neutral ``slice_deactivated`` event."""
        return self.append_event(
            Event(
                node_id=slice_id,
                type=EventType.slice_deactivated,
                weight=0.0,
                polarity=-1,
                source=source,
                reason=reason,
            )
        )

    def is_slice_active(self, slice_id: str) -> bool:
        """Fold a slice's lifecycle events: active iff the latest is ``slice_activated``.

        No lifecycle events → inactive (a slice must be explicitly activated).
        """
        row = self._conn.execute(
            "SELECT type FROM events WHERE node_id = ? "
            "AND type IN ('slice_activated','slice_deactivated') "
            "ORDER BY created_at DESC, id DESC LIMIT 1",
            (slice_id,),
        ).fetchone()
        return row is not None and row[0] == EventType.slice_activated.value

    # --- liveness / archival (mark-sweep reachability from the root set) ---

    def _active_slice_ids(self) -> list[str]:
        # Single grouped query (vs one is_slice_active call per slice): take each slice's
        # latest lifecycle event (same created_at DESC, id DESC tiebreak as
        # is_slice_active) and keep those whose latest is slice_activated.
        rows = self._conn.execute(
            "SELECT node_id FROM ("
            "  SELECT e.node_id AS node_id, e.type AS type,"
            "         ROW_NUMBER() OVER ("
            "           PARTITION BY e.node_id ORDER BY e.created_at DESC, e.id DESC"
            "         ) AS rn"
            "  FROM events e"
            "  JOIN nodes n ON n.id = e.node_id AND n.type = 'slice'"
            "  WHERE e.type IN ('slice_activated','slice_deactivated')"
            ") WHERE rn = 1 AND type = 'slice_activated'"
        ).fetchall()
        return [r[0] for r in rows]

    def sweep(self, *, source: str = "sweep", reason: str = "mark-sweep liveness") -> dict[str, bool]:
        """Recompute liveness and materialize each content node's ``archived`` state.

        Root set = long-term/lifetime content nodes ∪ currently-active slices. The live
        set is the root set plus everything reachable from an active-slice root by
        following ``SCOPED_TO`` edges (slice → detail) transitively. Content nodes not in
        the live set are archived; nodes not scoped to any active slice therefore go
        dormant, while foundations (root tiers) stay live regardless. Slice and
        facet-value nodes are never archived (both are structural anchors, not content). Each archived/reactivated transition is journaled with a
        weight-0 event, which is trust-neutral under the accumulation folds
        (``SumAndClampFold`` default, ``WeightedAverageFold``) since it contributes 0;
        note it is NOT neutral under ``LastNWindowFold``, where it still consumes a
        window slot. Returns the ``{node_id: archived}`` map of nodes whose state changed.
        """
        active = self._active_slice_ids()
        placeholders = ",".join("?" for _ in active) if active else "NULL"
        mark_query = (
            "WITH RECURSIVE live(node_id) AS ("
            "  SELECT id FROM nodes WHERE tier IN ('long-term','lifetime') AND type != 'slice'"
            f"  UNION SELECT id FROM nodes WHERE id IN ({placeholders})"
            "  UNION SELECT e.target_id FROM edges e JOIN live l ON e.source_id = l.node_id"
            "        WHERE e.type = 'SCOPED_TO'"
            ") SELECT node_id FROM live"
        )
        live = {r[0] for r in self._conn.execute(mark_query, active).fetchall()}

        rows = self._conn.execute(
            "SELECT id, archived FROM nodes WHERE type NOT IN ('slice','facet_value')"
        ).fetchall()
        changed: dict[str, bool] = {}
        now = datetime.now(timezone.utc).isoformat()
        with self._conn:
            for node_id, archived_old in rows:
                archived_new = node_id not in live
                if bool(archived_old) == archived_new:
                    continue
                self._conn.execute(
                    "UPDATE nodes SET archived = ? WHERE id = ?",
                    (int(archived_new), node_id),
                )
                self._conn.execute(
                    "INSERT INTO events (id, node_id, type, weight, polarity, source, reason, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        str(uuid.uuid4()),
                        node_id,
                        (EventType.archived if archived_new else EventType.reactivated).value,
                        0.0,
                        -1 if archived_new else 1,
                        source,
                        reason,
                        now,
                    ),
                )
                changed[node_id] = archived_new
        return changed

    def traverse(self, node_id: str) -> list[tuple[Node, Edge | None]]:
        # Content channel: an archived (dormant) or anchor (slice/facet-value) seed
        # yields nothing, so a dormant node's live neighbours don't leak back in via
        # seeding. The CTE itself excludes SCOPED_TO/HAS_FACET edges and
        # archived/anchor nodes from the results.
        seed = self.read_node(node_id)
        if seed is None or seed.archived or seed.type in (NodeType.slice, NodeType.facet_value):
            return []
        rows = self._conn.execute(_TRAVERSE_CTE, (node_id,)).fetchall()
        result: list[tuple[Node, Edge | None]] = []
        for row in rows:
            node = Node(
                id=row[5],
                type=NodeType(row[6]),
                tier=Tier(row[7]),
                path=row[8],
                body=row[9],
                created_at=datetime.fromisoformat(row[10]),
                needs_review=bool(row[11]),
                retrieval_weight=row[12],
                trust_weight=row[13],
                archived=bool(row[14]),
            )
            incoming: Edge | None = None
            if row[1] is not None:
                incoming = Edge(
                    source_id=row[1],
                    target_id=row[2],
                    type=EdgeType(row[3]),
                    created_at=datetime.fromisoformat(row[4]),
                )
            result.append((node, incoming))
        return result

    def impact_of(self, node_id: str) -> list[tuple[Node, int]]:
        """Content nodes that (transitively) DEPENDS_ON ``node_id`` — its impact set.

        The read primitive behind the ``trace-impact`` skill (deferred by the slice-10
        plan-brief until ``impact_of()`` landed in the read surface — this is it): who is
        affected if this artifact changes. ``source DEPENDS_ON target`` means source
        depends on target, so the dependents of ``node_id`` are the *sources* reached by
        walking DEPENDS_ON edges backwards (target → source). Returns ``(Node, distance)``
        pairs ordered by distance then id — a breadth-first walk over live content edges
        only (archived nodes and structural anchors are excluded, mirroring ``traverse``).
        CONTRADICTS is not a dependency and is not followed. Read-only.

        Fidelity is bounded by the explicit DEPENDS_ON edges in the graph; an
        undocumented dependency does not appear here. Deterministic: edges are read in
        sorted order and the result is sorted, so the same graph yields the same list.
        """
        seed = self.read_node(node_id)
        if seed is None or seed.archived or seed.type in (NodeType.slice, NodeType.facet_value):
            return []
        rows = self._conn.execute(
            "SELECT e.source_id, e.target_id FROM edges e "
            "JOIN nodes s ON s.id = e.source_id AND s.archived = 0 "
            "     AND s.type NOT IN ('slice','facet_value') "
            "JOIN nodes t ON t.id = e.target_id AND t.archived = 0 "
            "     AND t.type NOT IN ('slice','facet_value') "
            "WHERE e.type = 'DEPENDS_ON' ORDER BY e.source_id, e.target_id"
        ).fetchall()
        reverse: dict[str, list[str]] = {}
        for source_id, target_id in rows:
            reverse.setdefault(target_id, []).append(source_id)
        # BFS outward from the seed following reverse (dependent) edges. Tracking the
        # visited set as `dist` makes this terminate on cycles — a node keeps its first
        # (shortest) distance and is never re-enqueued.
        dist: dict[str, int] = {node_id: 0}
        queue: deque[str] = deque([node_id])
        while queue:
            current = queue.popleft()
            for dependent in reverse.get(current, []):
                if dependent not in dist:
                    dist[dependent] = dist[current] + 1
                    queue.append(dependent)
        result = [(self.read_node(nid), d) for nid, d in dist.items() if d > 0]
        return sorted(result, key=lambda x: (x[1], x[0].id))

    def _score_node(self, node: Node, structure: float, now: datetime) -> float:
        """Compose the effective score: structural gate × additive quality blend.

        ``structure`` occupies the multiplicative-gate seat of the MT3-20 Pass-2
        formula — single-seed recall passes reciprocal hop decay, multi-seed recall
        passes normalized PPR mass. Trust/retrieval/recency stay an additive blend so a
        single low factor dampens rather than annihilates. Penalty is flag-driven and
        applied only at query time. Unflagged nodes get penalty 0, so every strategy
        reduces to the unpenalized formula (no regression); severity is looked up only
        for the few flagged nodes.
        """
        age_days = (now - node.created_at).total_seconds() / 86400 if node.created_at else 0.0
        recency = _RECENCY_HALFLIFE_DAYS / (age_days + _RECENCY_HALFLIFE_DAYS)
        penalty = (
            compute_penalty(node, self._latest_severity(node.id))
            if node.needs_review
            else 0.0
        )
        components = ScoreComponents(
            hop_decay=structure,
            alpha=_ALPHA,
            retrieval=node.retrieval_weight,
            beta=_BETA,
            trust=node.trust_weight,
            gamma=_GAMMA,
            recency=recency,
        )
        return self._penalty_strategy.apply(components, penalty)

    def recall(self, seed_id: str) -> list[tuple[Node, float]]:
        raw = self.traverse(seed_id)
        if not raw:
            return []
        now = datetime.now(timezone.utc)
        depths: dict[str, int] = {raw[0][0].id: 0}
        for node, edge in raw[1:]:
            depths[node.id] = depths.get(edge.source_id, 0) + 1  # type: ignore[union-attr]
        scored = [
            (node, self._score_node(node, _HOP_HALFLIFE / (depths[node.id] + _HOP_HALFLIFE), now))
            for node, _ in raw
        ]
        return sorted(scored, key=lambda x: x[1], reverse=True)

    # --- multi-seed retrieval (Slice 8: PPR with goal-dominant seed weights) ---

    def _live_content_edges(self) -> list[tuple[str, str, EdgeType]]:
        """Typed edges whose both endpoints are live content nodes, in stable order.

        Archived nodes and structural anchors (slice, facet_value) are excluded before
        the walk exists, so PPR mass can neither enter nor pass through them — the
        multi-seed analogue of the exclusions in ``_TRAVERSE_CTE``.
        """
        rows = self._conn.execute(
            "SELECT e.source_id, e.target_id, e.type FROM edges e "
            "JOIN nodes s ON s.id = e.source_id AND s.archived = 0 "
            "     AND s.type NOT IN ('slice','facet_value') "
            "JOIN nodes t ON t.id = e.target_id AND t.archived = 0 "
            "     AND t.type NOT IN ('slice','facet_value') "
            "ORDER BY e.source_id, e.target_id, e.type"
        ).fetchall()
        return [(r[0], r[1], EdgeType(r[2])) for r in rows]

    def discover_seeds(self, query: str, *, k: int = _K_SEED_FACETS) -> dict[str, float]:
        """Resolve query text to supplementary seed nodes via facet-value embeddings.

        Embeds the query, ranks live facet-value nodes by cosine similarity (ties broken
        by id — deterministic), keeps the top ``k`` with positive similarity, and expands
        each to the live content nodes that carry it via HAS_FACET. A node reached
        through several matched facets takes the strongest similarity. HAS_FACET is used
        only here, for findability — it is never walked during PPR (policy weight 0).
        """
        query_vec = self._embedder.embed(query)
        rows = self._conn.execute(
            "SELECT id, body FROM nodes WHERE type = 'facet_value' AND archived = 0 ORDER BY id"
        ).fetchall()
        similarities = [
            (facet_id, cosine(query_vec, self._embedder.embed(body)))
            for facet_id, body in rows
        ]
        top = sorted(
            [(fid, sim) for fid, sim in similarities if sim > 0],
            key=lambda x: (-x[1], x[0]),
        )[:k]
        seeds: dict[str, float] = {}
        for facet_id, similarity in top:
            members = self._conn.execute(
                "SELECT e.source_id FROM edges e "
                "JOIN nodes n ON n.id = e.source_id AND n.archived = 0 "
                "     AND n.type NOT IN ('slice','facet_value') "
                "WHERE e.target_id = ? AND e.type = 'HAS_FACET' ORDER BY e.source_id",
                (facet_id,),
            ).fetchall()
            for (node_id,) in members:
                seeds[node_id] = max(seeds.get(node_id, 0.0), similarity)
        return seeds

    def recall_multi(
        self,
        query: str,
        goal_id: str,
        *,
        goal_weight: float = DEFAULT_GOAL_WEIGHT,
        k_seeds: int = _K_SEED_FACETS,
        damping: float = DEFAULT_DAMPING,
    ) -> list[tuple[Node, float]]:
        """Multi-seed PPR retrieval with the Goal as mandatory, dominant seed (MT3-20).

        Selection and structural relevance are one number: a node's PPR mass from the
        seed set. Nodes with zero mass are structurally unreachable and excluded — no
        amount of trust or recency can resurrect them (goal-first gating, MT3-25). Mass
        is normalized by the maximum so the structural gate lands in (0, 1] like
        single-seed hop decay, then composed with the additive quality blend by
        ``_score_node``. ``goal_weight=1.0`` collapses to pure goal-first selection —
        the same reachable set as ``recall(goal_id)``.

        Pure function of the stored graph and arguments: deterministic seed discovery
        (hash embeddings, ordered ties), deterministic PPR (power iteration in sorted
        node order), deterministic ranking (score desc, then node id).
        """
        goal = self.read_node(goal_id)
        if goal is None or goal.archived or goal.type in (NodeType.slice, NodeType.facet_value):
            return []
        supplementary = self.discover_seeds(query, k=k_seeds)
        seeds = build_seed_vector(goal_id, supplementary, goal_weight=goal_weight)
        graph = build_weighted_graph(self._live_content_edges(), self._edge_policy)
        mass = personalized_pagerank(graph, seeds, damping=damping)
        reached = {node_id: m for node_id, m in mass.items() if m > 0}
        if not reached:
            return []
        max_mass = max(reached.values())
        now = datetime.now(timezone.utc)
        scored = []
        for node_id in sorted(reached):
            node = self.read_node(node_id)
            if node is None:
                continue
            scored.append((node, self._score_node(node, reached[node_id] / max_mass, now)))
        return sorted(scored, key=lambda x: (-x[1], x[0].id))

    def dump_pairs(self) -> list[tuple[Node, list[Edge], list[Event]]]:
        rows = self._conn.execute(
            "SELECT id FROM nodes ORDER BY created_at ASC, id ASC"
        ).fetchall()
        result: list[tuple[Node, list[Edge], list[Event]]] = []
        for (node_id,) in rows:
            node = self.read_node(node_id)
            edge_rows = self._conn.execute(
                "SELECT source_id, target_id, type, created_at FROM edges WHERE source_id = ?",
                (node_id,),
            ).fetchall()
            edges = [
                Edge(
                    source_id=r[0],
                    target_id=r[1],
                    type=EdgeType(r[2]),
                    created_at=datetime.fromisoformat(r[3]),
                )
                for r in edge_rows
            ]
            events = self.read_events(node_id)
            result.append((node, edges, events))
        return result

    def close(self) -> None:
        self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        self._conn.close()
