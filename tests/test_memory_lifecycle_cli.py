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


# --- unreplayed degraded-mode backlogs: a queue nobody reads is a queue that loses work ---


def _changes(tmp_path, **backlogs):
    root = tmp_path / "changes"
    for change_id, text in backlogs.items():
        folder = root / change_id
        folder.mkdir(parents=True)
        (folder / "memory-backlog.md").write_text(text, encoding="utf-8")
    root.mkdir(parents=True, exist_ok=True)
    return root


def test_a_backlog_with_no_marker_is_reported(tmp_path, capsys):
    root = _changes(tmp_path, some_change="# memory backlog\n\ncapture_artifact(...)\n")
    memory_lifecycle.main(["backlogs", "--changes-dir", str(root)])
    out = capsys.readouterr().out
    assert "memory-backlog.md" in out
    assert "never replayed" in out
    assert "1 unreplayed backlog(s)" in out


def test_a_replayed_backlog_is_silent(tmp_path, capsys):
    root = _changes(
        tmp_path,
        done="# memory backlog\n\n> **REPLAYED 2026-09-06 — do not replay again.**\n\ncapture(...)\n",
    )
    memory_lifecycle.main(["backlogs", "--changes-dir", str(root)])
    assert capsys.readouterr().out == ""


def test_the_marker_must_lead_a_line_not_merely_appear(tmp_path, capsys):
    """`not yet REPLAYED` is a backlog, not a discharged one."""
    root = _changes(tmp_path, sneaky="# memory backlog\n\nThese are not yet REPLAYED.\n")
    memory_lifecycle.main(["backlogs", "--changes-dir", str(root)])
    assert "sneaky" in capsys.readouterr().out


def test_it_reports_every_outstanding_backlog(tmp_path, capsys):
    root = _changes(
        tmp_path,
        a="queued\n",
        b="> **REPLAYED 2026-01-01**\nqueued\n",
        c="queued\n",
    )
    memory_lifecycle.main(["backlogs", "--changes-dir", str(root)])
    out = capsys.readouterr().out
    assert "2 unreplayed backlog(s)" in out
    assert "/b/" not in out


def test_it_never_opens_the_store(tmp_path, capsys):
    """A backlog exists BECAUSE the store was unreachable — the detector must not need it."""
    root = _changes(tmp_path, stuck="queued\n")
    memory_lifecycle.main(
        ["backlogs", "--changes-dir", str(root), "--db", str(tmp_path / "does-not-exist.db")]
    )
    assert "stuck" in capsys.readouterr().out
    assert not (tmp_path / "does-not-exist.db").exists()


def test_a_missing_changes_dir_is_not_an_error(tmp_path, capsys):
    memory_lifecycle.main(["backlogs", "--changes-dir", str(tmp_path / "nope")])
    assert capsys.readouterr().out == ""
