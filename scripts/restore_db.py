"""Git smudge filter: reads text dump from stdin, writes SQLite DB bytes to stdout."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from agentic_memory_system.serialization import parse_dump
from agentic_memory_system.storage import MemoryStore


def main() -> None:
    text = sys.stdin.read()

    tmp_path = tempfile.mktemp(suffix=".db")
    try:
        pairs = parse_dump(text)
        store = MemoryStore(tmp_path)

        # nodes first (FK constraint requires all nodes before any edges)
        for node, _edges in pairs:
            store.write_node(node)

        # edges second
        for _node, edges in pairs:
            for edge in edges:
                store.write_edge(edge)

        store.close()

        with open(tmp_path, "rb") as f:
            sys.stdout.buffer.write(f.read())
    except Exception as exc:
        print(f"restore_db error: {exc}", file=sys.stderr)
        sys.exit(1)
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


if __name__ == "__main__":
    main()
