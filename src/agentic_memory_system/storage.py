import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .schema import Node, NodeType, Tier, Edge, EdgeType

_CREATE_NODES = """
CREATE TABLE IF NOT EXISTS nodes (
    id          TEXT    PRIMARY KEY,
    type        TEXT    NOT NULL CHECK(type IN ('decision','concept','constraint','issue','invariant')),
    tier        TEXT    NOT NULL CHECK(tier IN ('short-term','mid-term','long-term','lifetime')),
    path        TEXT    NOT NULL,
    body        TEXT    NOT NULL,
    created_at  TEXT    NOT NULL,
    needs_review     INTEGER NOT NULL DEFAULT 0,
    retrieval_weight REAL    NOT NULL DEFAULT 1.0,
    trust_weight     REAL    NOT NULL DEFAULT 1.0
)
"""


_CREATE_EDGES = """
CREATE TABLE IF NOT EXISTS edges (
    source_id   TEXT NOT NULL REFERENCES nodes(id),
    target_id   TEXT NOT NULL REFERENCES nodes(id),
    type        TEXT NOT NULL CHECK(type IN ('DEPENDS_ON')),
    created_at  TEXT NOT NULL,
    PRIMARY KEY (source_id, target_id, type)
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
)
SELECT r.node_id, r.source_id, r.target_id, r.etype, r.edge_created_at,
       n.id, n.type, n.tier, n.path, n.body, n.created_at, n.needs_review,
       n.retrieval_weight, n.trust_weight
FROM reachable r
JOIN nodes n ON n.id = r.node_id
"""


class MemoryStore:
    def __init__(self, db_path: str | Path = "context/memory-graph.db") -> None:
        self._conn = sqlite3.connect(str(db_path))
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        with self._conn:
            self._conn.execute(_CREATE_NODES)
            self._conn.execute(_CREATE_EDGES)
        for col in ("retrieval_weight", "trust_weight"):
            try:
                self._conn.execute(
                    f"ALTER TABLE nodes ADD COLUMN {col} REAL NOT NULL DEFAULT 1.0"
                )
            except sqlite3.OperationalError:
                pass  # column already exists

    def write_node(self, node: Node) -> Node:
        node_id = node.id if node.id is not None else str(uuid.uuid4())
        created_at = node.created_at if node.created_at is not None else datetime.now(timezone.utc)
        with self._conn:
            self._conn.execute(
                "INSERT INTO nodes (id, type, tier, path, body, created_at, needs_review, retrieval_weight, trust_weight) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
                ),
            )
        return node.model_copy(update={"id": node_id, "created_at": created_at})

    def read_node(self, node_id: str) -> Node | None:
        row = self._conn.execute(
            "SELECT id, type, tier, path, body, created_at, needs_review, retrieval_weight, trust_weight FROM nodes WHERE id = ?",
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
        )

    def write_edge(self, edge: Edge) -> Edge:
        created_at = edge.created_at if edge.created_at is not None else datetime.now(timezone.utc)
        with self._conn:
            self._conn.execute(
                "INSERT INTO edges (source_id, target_id, type, created_at) VALUES (?, ?, ?, ?)",
                (edge.source_id, edge.target_id, edge.type.value, created_at.isoformat()),
            )
        return edge.model_copy(update={"created_at": created_at})

    def traverse(self, node_id: str) -> list[tuple[Node, Edge | None]]:
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
            return hop_decay * (_ALPHA * node.retrieval_weight + _BETA * node.trust_weight + _GAMMA * recency)

        scored = [(node, _score(node, depths[node.id])) for node, _ in raw]
        return sorted(scored, key=lambda x: x[1], reverse=True)

    def dump_pairs(self) -> list[tuple[Node, list[Edge]]]:
        rows = self._conn.execute(
            "SELECT id FROM nodes ORDER BY created_at ASC, id ASC"
        ).fetchall()
        result: list[tuple[Node, list[Edge]]] = []
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
            result.append((node, edges))
        return result

    def close(self) -> None:
        self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        self._conn.close()
