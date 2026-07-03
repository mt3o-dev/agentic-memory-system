import sqlite3
import uuid
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

_CREATE_NODES = """
CREATE TABLE IF NOT EXISTS nodes (
    id          TEXT    PRIMARY KEY,
    type        TEXT    NOT NULL CHECK(type IN ('decision','concept','constraint','issue','invariant','slice')),
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
    type        TEXT NOT NULL CHECK(type IN ('DEPENDS_ON','CONTRADICTS','SCOPED_TO')),
    created_at  TEXT NOT NULL,
    PRIMARY KEY (source_id, target_id, type)
)
"""

_CREATE_EVENTS = """
CREATE TABLE IF NOT EXISTS events (
    id          TEXT PRIMARY KEY,
    node_id     TEXT NOT NULL REFERENCES nodes(id),
    type        TEXT NOT NULL CHECK(type IN ('contradiction_raised','contradiction_cleared','confirmation_added','manual_review','tier_change','slice_activated','slice_deactivated','archived','reactivated')),
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

_TRAVERSE_CTE = """
WITH RECURSIVE reachable(node_id, source_id, target_id, etype, edge_created_at) AS (
    SELECT ?, NULL, NULL, NULL, NULL
    UNION
    SELECT e.target_id, e.source_id, e.target_id, e.type, e.created_at
    FROM edges e
    JOIN reachable r ON e.source_id = r.node_id
    WHERE e.type IN ('DEPENDS_ON','CONTRADICTS')
)
SELECT r.node_id, r.source_id, r.target_id, r.etype, r.edge_created_at,
       n.id, n.type, n.tier, n.path, n.body, n.created_at, n.needs_review,
       n.retrieval_weight, n.trust_weight, n.archived
FROM reachable r
JOIN nodes n ON n.id = r.node_id AND n.archived = 0 AND n.type != 'slice'
"""


class MemoryStore:
    def __init__(
        self,
        db_path: str | Path = "context/memory-graph.db",
        fold_strategy: FoldStrategy | None = None,
        penalty_strategy: PenaltyStrategy | None = None,
    ) -> None:
        self._conn = sqlite3.connect(str(db_path))
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._fold_strategy = fold_strategy or SumAndClampFold()
        self._penalty_strategy = penalty_strategy or TrustTermPenalty()
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

    def _migrate_nodes_check(self) -> None:
        """Widen the nodes ``type`` CHECK on DBs created before the ``slice`` type existed.

        SQLite can't ``ALTER`` a CHECK in place, so a DB whose nodes CHECK predates
        ``slice`` would reject a slice node. Detect that case and rebuild the table
        (rename → recreate with the current schema → copy → drop) with foreign keys off
        for the duration. Runs after the ``archived`` ADD COLUMN so the copy includes it.
        A no-op on fresh/already-migrated DBs (whose CHECK already lists 'slice').
        """
        row = self._conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='nodes'"
        ).fetchone()
        if row is None or row[0] is None or "'slice'" in row[0]:
            return
        # PRAGMAs must be toggled outside any open transaction. legacy_alter_table=ON
        # stops the RENAME below from rewriting foreign-key references in *other* tables
        # (edges/events both REFERENCE nodes(id)) to point at the temp table we then drop.
        self._conn.execute("PRAGMA legacy_alter_table=ON")
        self._conn.execute("PRAGMA foreign_keys=OFF")
        try:
            with self._conn:
                self._conn.execute("ALTER TABLE nodes RENAME TO _nodes_old")
                self._conn.execute(_CREATE_NODES)
                self._conn.execute(
                    "INSERT INTO nodes (id, type, tier, path, body, created_at, "
                    "needs_review, retrieval_weight, trust_weight, archived) "
                    "SELECT id, type, tier, path, body, created_at, "
                    "needs_review, retrieval_weight, trust_weight, archived FROM _nodes_old"
                )
                self._conn.execute("DROP TABLE _nodes_old")
        finally:
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.execute("PRAGMA legacy_alter_table=OFF")

    def _migrate_edges_check(self) -> None:
        """Widen the edges ``type`` CHECK on DBs created before SCOPED_TO existed.

        SQLite can't ``ALTER`` a CHECK constraint in place and ``CREATE TABLE IF NOT
        EXISTS`` won't touch a table that already exists, so a DB created with an older
        CHECK would reject a SCOPED_TO edge. Detect that case and rebuild the table
        (rename → recreate with the current schema → copy → drop), following SQLite's
        recommended table-alteration procedure with foreign keys disabled for the
        duration. Guarded on the newest allowed type, so it also covers the earlier
        DEPENDS_ON→CONTRADICTS widening. A no-op on fresh/already-migrated DBs.
        """
        row = self._conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='edges'"
        ).fetchone()
        if row is None or row[0] is None or "SCOPED_TO" in row[0]:
            return
        # PRAGMAs must be toggled outside any open transaction. legacy_alter_table=ON
        # stops the RENAME below from rewriting foreign-key references in *other* tables
        # (edges/events both REFERENCE nodes(id)) to point at the temp table we then drop.
        self._conn.execute("PRAGMA legacy_alter_table=ON")
        self._conn.execute("PRAGMA foreign_keys=OFF")
        try:
            with self._conn:
                self._conn.execute("ALTER TABLE edges RENAME TO _edges_old")
                self._conn.execute(_CREATE_EDGES)
                self._conn.execute(
                    "INSERT INTO edges (source_id, target_id, type, created_at) "
                    "SELECT source_id, target_id, type, created_at FROM _edges_old"
                )
                self._conn.execute("DROP TABLE _edges_old")
        finally:
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.execute("PRAGMA legacy_alter_table=OFF")

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
        if row is None or row[0] is None or "slice_activated" in row[0]:
            return
        # PRAGMAs must be toggled outside any open transaction. legacy_alter_table=ON
        # stops the RENAME below from rewriting foreign-key references in *other* tables
        # (edges/events both REFERENCE nodes(id)) to point at the temp table we then drop.
        self._conn.execute("PRAGMA legacy_alter_table=ON")
        self._conn.execute("PRAGMA foreign_keys=OFF")
        try:
            with self._conn:
                self._conn.execute("ALTER TABLE events RENAME TO _events_old")
                self._conn.execute(_CREATE_EVENTS)
                self._conn.execute(
                    "INSERT INTO events (id, node_id, type, weight, polarity, source, reason, created_at) "
                    "SELECT id, node_id, type, weight, polarity, source, reason, created_at FROM _events_old"
                )
                self._conn.execute("DROP TABLE _events_old")
        finally:
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.execute("PRAGMA legacy_alter_table=OFF")

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
        slice_ids = [
            r[0]
            for r in self._conn.execute(
                "SELECT id FROM nodes WHERE type = 'slice'"
            ).fetchall()
        ]
        return [sid for sid in slice_ids if self.is_slice_active(sid)]

    def sweep(self, *, source: str = "sweep", reason: str = "mark-sweep liveness") -> dict[str, bool]:
        """Recompute liveness and materialize each content node's ``archived`` state.

        Root set = long-term/lifetime content nodes ∪ currently-active slices. The live
        set is the root set plus everything reachable from an active-slice root by
        following ``SCOPED_TO`` edges (slice → detail) transitively. Content nodes not in
        the live set are archived; nodes not scoped to any active slice therefore go
        dormant, while foundations (root tiers) stay live regardless. Slice nodes are
        never archived. Each archived/reactivated transition is journaled with a
        trust-neutral (weight-0) event. Returns the ``{node_id: archived}`` map of nodes
        whose state changed.
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
            "SELECT id, archived FROM nodes WHERE type != 'slice'"
        ).fetchall()
        changed: dict[str, bool] = {}
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
                        datetime.now(timezone.utc).isoformat(),
                    ),
                )
                changed[node_id] = archived_new
        return changed

    def traverse(self, node_id: str) -> list[tuple[Node, Edge | None]]:
        # Content channel: an archived (dormant) or slice (anchor) seed yields nothing,
        # so a dormant node's live neighbours don't leak back in via seeding. The CTE
        # itself excludes SCOPED_TO edges and archived/slice nodes from the results.
        seed = self.read_node(node_id)
        if seed is None or seed.archived or seed.type == NodeType.slice:
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

    def recall(self, seed_id: str) -> list[tuple[Node, float]]:
        raw = self.traverse(seed_id)
        if not raw:
            return []
        now = datetime.now(timezone.utc)
        depths: dict[str, int] = {raw[0][0].id: 0}
        for node, edge in raw[1:]:
            depths[node.id] = depths.get(edge.source_id, 0) + 1  # type: ignore[union-attr]

        def _score(node: Node, depth: int) -> float:
            hop_decay = _HOP_HALFLIFE / (depth + _HOP_HALFLIFE)
            age_days = (now - node.created_at).total_seconds() / 86400 if node.created_at else 0.0
            recency = _RECENCY_HALFLIFE_DAYS / (age_days + _RECENCY_HALFLIFE_DAYS)
            # Penalty is flag-driven and applied only at query time. Unflagged nodes get
            # penalty 0, so every strategy reduces to the original formula (no regression);
            # severity is looked up only for the few flagged nodes.
            penalty = (
                compute_penalty(node, self._latest_severity(node.id))
                if node.needs_review
                else 0.0
            )
            components = ScoreComponents(
                hop_decay=hop_decay,
                alpha=_ALPHA,
                retrieval=node.retrieval_weight,
                beta=_BETA,
                trust=node.trust_weight,
                gamma=_GAMMA,
                recency=recency,
            )
            return self._penalty_strategy.apply(components, penalty)

        scored = [(node, _score(node, depths[node.id])) for node, _ in raw]
        return sorted(scored, key=lambda x: x[1], reverse=True)

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
