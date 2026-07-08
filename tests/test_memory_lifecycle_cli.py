import importlib.util
import sys
from pathlib import Path

import pytest

from agentic_memory_system.agent_surface import AgentSurface
from agentic_memory_system.storage import MemoryStore

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "memory_lifecycle.py"
spec = importlib.util.spec_from_file_location("memory_lifecycle", _SCRIPT)
memory_lifecycle = importlib.util.module_from_spec(spec)
spec.loader.exec_module(memory_lifecycle)


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "graph.db"
    store = MemoryStore(path)
    surface = AgentSurface(store)
    change = surface.create_change("demo-change", "prove the lifecycle CLI")
    surface.capture_artifact("short-lived detail", "decision", change["goal_node_id"])
    store.close()
    return path, change


def test_deactivate_with_sweep_archives_scope(db, capsys):
    path, change = db
    memory_lifecycle.main(["deactivate", "demo-change", "--sweep", "--db", str(path)])
    out = capsys.readouterr().out
    assert "deactivated demo-change" in out
    assert "archived" in out
    store = MemoryStore(path)
    assert store.read_node(change["goal_node_id"]).archived is True
    assert store.is_slice_active(change["change_node_id"]) is False
    store.close()


def test_activate_reverses_and_status_reports(db, capsys):
    path, change = db
    memory_lifecycle.main(["deactivate", "demo-change", "--sweep", "--db", str(path)])
    memory_lifecycle.main(["activate", "demo-change", "--sweep", "--db", str(path)])
    memory_lifecycle.main(["status", "--db", str(path)])
    out = capsys.readouterr().out
    assert "reactivated" in out
    status_line = next(line for line in out.splitlines() if "/change/demo-change" in line)
    assert status_line.startswith("ACTIVE")
    store = MemoryStore(path)
    assert store.read_node(change["goal_node_id"]).archived is False
    store.close()


def test_unknown_change_exits_cleanly(db):
    path, _ = db
    with pytest.raises(SystemExit, match="no change"):
        memory_lifecycle.main(["deactivate", "nope", "--db", str(path)])
