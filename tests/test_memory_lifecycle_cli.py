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


# --- coverage: the failure `backlogs` cannot see, because nobody knew it happened ---


def _change_folder(root, change_id, status, goal=None, filename="change.md"):
    folder = root / change_id
    folder.mkdir(parents=True, exist_ok=True)
    lines = ["---", f"change_id: {change_id}", f"status: {status}"]
    if goal:
        lines.append(f"memory_goal: {goal}")
    lines += ["---", "", "## Notes", "", "Something happened."]
    (folder / filename).write_text("\n".join(lines), encoding="utf-8")
    return folder


def _scope(db_path, change_id, artifacts=1):
    """Open a real change scope and capture into it, returning the goal id."""
    store = MemoryStore(db_path)
    surface = AgentSurface(store)
    try:
        goal = surface.create_change(change_id, f"do {change_id}")["goal_node_id"]
        for i in range(artifacts):
            surface.capture_artifact(f"A decision number {i}.", "decision", goal)
        return goal
    finally:
        store.close()


def test_a_finished_change_with_no_memory_goal_is_a_gap(db, tmp_path, capsys):
    path, _ = db
    root = tmp_path / "changes"
    _change_folder(root, "forgot", "implemented")
    with pytest.raises(SystemExit) as exit_info:
        memory_lifecycle.main(["coverage", "--db", str(path), "--changes-dir", str(root)])
    assert exit_info.value.code == 1
    out = capsys.readouterr().out
    assert "GAP" in out and "NO memory_goal" in out


def test_a_goal_that_is_not_in_the_store_is_a_gap(db, tmp_path, capsys):
    path, _ = db
    root = tmp_path / "changes"
    _change_folder(root, "dangling", "implemented", goal="00000000-0000-0000-0000-000000000000")
    with pytest.raises(SystemExit):
        memory_lifecycle.main(["coverage", "--db", str(path), "--changes-dir", str(root)])
    assert "goal not in the store" in capsys.readouterr().out


def test_a_scope_with_no_artifacts_is_a_gap(db, tmp_path, capsys):
    """An opened scope is not captured knowledge — create_change alone must not pass."""
    path, _ = db
    goal = _scope(path, "opened-only", artifacts=0)
    root = tmp_path / "changes"
    _change_folder(root, "opened-only", "implemented", goal=goal)
    with pytest.raises(SystemExit):
        memory_lifecycle.main(["coverage", "--db", str(path), "--changes-dir", str(root)])
    assert "0 artifact(s)" in capsys.readouterr().out


def test_a_captured_change_passes(db, tmp_path, capsys):
    path, _ = db
    goal = _scope(path, "captured", artifacts=2)
    root = tmp_path / "changes"
    _change_folder(root, "captured", "implemented", goal=goal)
    memory_lifecycle.main(["coverage", "--db", str(path), "--changes-dir", str(root)])
    out = capsys.readouterr().out
    assert "2 artifact(s)" in out and "GAP" not in out


def test_a_change_still_in_flight_is_not_a_gap(db, tmp_path, capsys):
    """Capturing nothing yet is normal before a change is finished — only 'done' is gated."""
    path, _ = db
    root = tmp_path / "changes"
    _change_folder(root, "in-progress", "researched")
    memory_lifecycle.main(["coverage", "--db", str(path), "--changes-dir", str(root)])
    assert "GAP" not in capsys.readouterr().out


def test_the_goal_is_found_outside_change_md(db, tmp_path, capsys):
    """bootstrap-verification has no change.md and records its goal in prose instead."""
    path, _ = db
    goal = _scope(path, "odd-one", artifacts=1)
    folder = tmp_path / "changes" / "odd-one"
    folder.mkdir(parents=True)
    (folder / "verification.md").write_text(
        f"## Memory scope\n\n`memory_goal: {goal}`\n", encoding="utf-8"
    )
    memory_lifecycle.main(["coverage", "--db", str(path), "--changes-dir", str(tmp_path / "changes")])
    assert "1 artifact(s)" in capsys.readouterr().out


def test_strict_backlogs_exits_non_zero_for_ci(tmp_path, capsys):
    root = _changes(tmp_path, stuck="queued\n")
    with pytest.raises(SystemExit) as exit_info:
        memory_lifecycle.main(["backlogs", "--changes-dir", str(root), "--strict"])
    assert exit_info.value.code == 1


def test_strict_backlogs_is_quiet_when_there_is_nothing(tmp_path, capsys):
    root = _changes(tmp_path, done="> **REPLAYED 2026-09-06**\n")
    memory_lifecycle.main(["backlogs", "--changes-dir", str(root), "--strict"])
    assert capsys.readouterr().out == ""
