import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .schema import Node, NodeType, Tier

_CREATE_NODES = """
CREATE TABLE IF NOT EXISTS nodes (
    id          TEXT    PRIMARY KEY,
    type        TEXT    NOT NULL CHECK(type IN ('decision','concept','constraint','issue','invariant')),
    tier        TEXT    NOT NULL CHECK(tier IN ('short-term','mid-term','long-term','lifetime')),
    path        TEXT    NOT NULL,
    body        TEXT    NOT NULL,
    created_at  TEXT    NOT NULL,
    needs_review INTEGER NOT NULL DEFAULT 0
)
"""


class MemoryStore:
    def __init__(self, db_path: str | Path = "context/memory-graph.db") -> None:
        self._conn = sqlite3.connect(str(db_path))
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        with self._conn:
            self._conn.execute(_CREATE_NODES)

    def write_node(self, node: Node) -> Node:
        node_id = node.id if node.id is not None else str(uuid.uuid4())
        created_at = node.created_at if node.created_at is not None else datetime.now(timezone.utc)
        with self._conn:
            self._conn.execute(
                "INSERT INTO nodes (id, type, tier, path, body, created_at, needs_review) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    node_id,
                    node.type.value,
                    node.tier.value,
                    node.path,
                    node.body,
                    created_at.isoformat(),
                    int(node.needs_review),
                ),
            )
        return node.model_copy(update={"id": node_id, "created_at": created_at})

    def read_node(self, node_id: str) -> Node | None:
        row = self._conn.execute(
            "SELECT id, type, tier, path, body, created_at, needs_review FROM nodes WHERE id = ?",
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
        )

    def close(self) -> None:
        self._conn.close()
