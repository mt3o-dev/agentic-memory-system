"""Git clean filter: reads SQLite DB bytes from stdin, writes text dump to stdout."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from agentic_memory_system.schema import Edge, EdgeType
from agentic_memory_system.serialization import dump_all
from agentic_memory_system.storage import MemoryStore


def main() -> None:
    db_bytes = sys.stdin.buffer.read()

    tmp_fd, tmp_path = tempfile.mkstemp(suffix=".db")
    try:
        os.write(tmp_fd, db_bytes)
        os.close(tmp_fd)

        store = MemoryStore(tmp_path)
        rows = store._conn.execute(
            "SELECT id FROM nodes ORDER BY created_at ASC, id ASC"
        ).fetchall()
        pairs = []
        for (node_id,) in rows:
            node = store.read_node(node_id)
            edge_rows = store._conn.execute(
                "SELECT source_id, target_id, type, created_at FROM edges WHERE source_id = ?",
                (node_id,),
            ).fetchall()
            from datetime import datetime
            edges = [
                Edge(
                    source_id=r[0],
                    target_id=r[1],
                    type=EdgeType(r[2]),
                    created_at=datetime.fromisoformat(r[3]),
                )
                for r in edge_rows
            ]
            pairs.append((node, edges))
        store.close()
        sys.stdout.write(dump_all(pairs))
    except Exception as exc:
        print(f"dump_db error: {exc}", file=sys.stderr)
        sys.exit(1)
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


if __name__ == "__main__":
    main()
