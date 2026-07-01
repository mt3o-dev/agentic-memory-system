"""Git clean filter: reads SQLite DB bytes from stdin, writes text dump to stdout."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from agentic_memory_system.serialization import dump_all
from agentic_memory_system.storage import MemoryStore


def main() -> None:
    db_bytes = sys.stdin.buffer.read()

    tmp_fd, tmp_path = tempfile.mkstemp(suffix=".db")
    try:
        os.write(tmp_fd, db_bytes)
        os.close(tmp_fd)

        store = MemoryStore(tmp_path)
        pairs = store.dump_pairs()
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
