"""Filter-free git sync: the store heals itself instead of needing local git config.

The old design tracked the `.db` through a clean/smudge filter, which cannot be made
transparent — git will never auto-register a repo-provided filter, because running
arbitrary commands from a clone is a security boundary. These tests pin the replacement:
the dump is the tracked source, the database is a build artifact, and both directions
happen without anyone remembering to ask.
"""

import os
from pathlib import Path

import pytest

from agentic_memory_system import sync
from agentic_memory_system.agent_surface import AgentSurface
from agentic_memory_system.storage import MemoryStore


def _seed(db_path, change="demo", body="SQLite over Kuzu: Kuzu was archived."):
    store = MemoryStore(db_path)
    surface = AgentSurface(store)
    goal = surface.create_change(change, "seed the store")["goal_node_id"]
    node_id = surface.capture_artifact(body, "decision", goal)["node_id"]
    store.close()
    return node_id


def _bodies(db_path):
    store = MemoryStore(db_path, auto_sync=False)
    try:
        rows = store._conn.execute("SELECT body FROM nodes ORDER BY body").fetchall()
        return [r[0] for r in rows]
    finally:
        store.close()


# --- the write side: the dump is always current before a commit ---


def test_a_write_session_refreshes_the_dump_by_itself(tmp_path):
    db = tmp_path / "graph.db"
    _seed(db)
    dump = sync.dump_path_for(db)
    assert dump.is_file()
    assert "SQLite over Kuzu" in dump.read_text(encoding="utf-8")


def test_a_read_only_session_leaves_the_dump_alone(tmp_path):
    db = tmp_path / "graph.db"
    _seed(db)
    dump = sync.dump_path_for(db)
    before = dump.read_bytes(), dump.stat().st_mtime_ns

    store = MemoryStore(db)
    AgentSurface(store).review_queue()
    store.close()

    # total_changes counts rows written, not reads — so a recall never dirties the dump
    # and never produces a spurious diff for someone to wonder about.
    assert (dump.read_bytes(), dump.stat().st_mtime_ns) == before


def test_the_dump_is_plain_text_git_can_diff(tmp_path):
    db = tmp_path / "graph.db"
    _seed(db)
    text = sync.dump_path_for(db).read_text(encoding="utf-8")
    assert text.startswith("# agentic-memory-system dump")
    assert "\x00" not in text


# --- the read side: a fresh clone just works ---


def test_a_fresh_clone_rebuilds_the_database_on_open(tmp_path):
    """The case the filter design broke: dump present, database never checked out."""
    db = tmp_path / "graph.db"
    node_id = _seed(db)
    db.unlink()  # gitignored — a clone has the dump and nothing else
    assert not db.exists()

    store = MemoryStore(db)
    try:
        assert store.read_node(node_id) is not None
        assert any("restored" in note for note in store.sync_notes)
    finally:
        store.close()


def test_a_pull_that_moves_the_dump_forward_is_picked_up(tmp_path):
    db = tmp_path / "graph.db"
    _seed(db)
    dump = sync.dump_path_for(db)

    # Simulate `git pull`: the dump gains a node and its mtime moves ahead.
    other = tmp_path / "other.db"
    _seed(other, change="upstream", body="Totals are derived, never stored.")
    dump.write_text(sync.dump_path_for(other).read_text(encoding="utf-8"), encoding="utf-8")
    os.utime(dump, (dump.stat().st_atime, db.stat().st_mtime + 10))

    store = MemoryStore(db)
    try:
        assert any("Totals are derived" in b for b in _bodies_of(store))
    finally:
        store.close()


def _bodies_of(store):
    return [r[0] for r in store._conn.execute("SELECT body FROM nodes").fetchall()]


def test_local_writes_are_not_clobbered_when_the_database_is_newer(tmp_path):
    """The one case where restoring would destroy work — so it must not restore."""
    db = tmp_path / "graph.db"
    _seed(db)
    dump = sync.dump_path_for(db)
    # Roll the dump back, but leave it OLDER than the database: the database holds
    # work the dump has not caught up with, which is what a crashed session looks like.
    dump.write_text("# agentic-memory-system dump\n# format_version: 1\n\n", encoding="utf-8")
    os.utime(dump, (dump.stat().st_atime, db.stat().st_mtime - 10))

    store = MemoryStore(db)
    try:
        assert any("SQLite over Kuzu" in b for b in _bodies_of(store))
        assert store.sync_notes == []
    finally:
        store.close()
    # ...and closing re-dumps, so the divergence self-heals rather than persisting.
    assert "SQLite over Kuzu" in dump.read_text(encoding="utf-8")


def test_an_existing_database_is_backed_up_before_being_replaced(tmp_path):
    db = tmp_path / "graph.db"
    _seed(db)
    dump = sync.dump_path_for(db)
    os.utime(dump, (dump.stat().st_atime, db.stat().st_mtime + 10))

    MemoryStore(db).close()
    assert Path(str(db) + ".bak").is_file()


# --- the legacy checkout: heal what the filter design left behind ---


def test_dump_text_sitting_at_the_db_path_is_healed_in_place(tmp_path):
    """Exactly what an unfiltered clone of the old layout produced."""
    db = tmp_path / "graph.db"
    node_id = _seed(db)
    text = sync.dump_path_for(db).read_text(encoding="utf-8")
    sync.dump_path_for(db).unlink()
    db.write_text(text, encoding="utf-8")  # the file named .db is really dump text
    assert not sync.is_database(db)

    store = MemoryStore(db)
    try:
        assert sync.is_database(db)
        assert store.read_node(node_id) is not None
        assert any("legacy checkout healed" in note for note in store.sync_notes)
    finally:
        store.close()


def test_a_file_that_is_neither_is_reported_not_guessed_at(tmp_path):
    db = tmp_path / "graph.db"
    db.write_text("this is not a dump and not a database\n", encoding="utf-8")
    note = sync.auto_restore(db)
    assert note is not None and "neither a database nor a dump" in note
    # untouched — the store never overwrites a file it does not understand
    assert db.read_text(encoding="utf-8").startswith("this is not")


# --- controls ---


def test_auto_sync_can_be_switched_off(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMORY_AUTO_SYNC", "0")
    db = tmp_path / "graph.db"
    _seed(db)
    assert not sync.dump_path_for(db).exists()


def test_in_memory_stores_never_touch_the_filesystem(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _seed(":memory:")
    assert list(tmp_path.iterdir()) == []


def test_a_broken_dump_never_breaks_the_open(tmp_path):
    db = tmp_path / "graph.db"
    _seed(db)
    dump = sync.dump_path_for(db)
    dump.write_text("# agentic-memory-system dump\n\n[node:x]\ntype: nonsense\n", encoding="utf-8")
    os.utime(dump, (dump.stat().st_atime, db.stat().st_mtime + 10))

    store = MemoryStore(db)  # must not raise
    try:
        assert any("could not restore" in note for note in store.sync_notes)
        assert any("SQLite over Kuzu" in b for b in _bodies_of(store))  # original intact
    finally:
        store.close()


def test_round_trip_preserves_the_graph(tmp_path):
    db = tmp_path / "graph.db"
    store = MemoryStore(db)
    surface = AgentSurface(store)
    goal = surface.create_change("round-trip", "check fidelity")["goal_node_id"]
    entity = surface.capture_entity("Invoice", "A request for payment.", goal)["node_id"]
    surface.capture_artifact(
        "Invoices are immutable after issue.", "constraint", goal, facets=["billing"],
        edges=[{"target": entity, "type": "ABOUT", "direction": "out"}],
    )
    before = _bodies_of(store)
    store.close()

    db.unlink()
    rebuilt = MemoryStore(db)
    try:
        assert sorted(_bodies_of(rebuilt)) == sorted(before)
        edges = rebuilt._conn.execute(
            "SELECT type FROM edges ORDER BY type"
        ).fetchall()
        assert ("ABOUT",) in edges and ("HAS_FACET",) in edges
        assert rebuilt.entity_status(entity) == "proposed"  # journal survived too
    finally:
        rebuilt.close()


def test_sync_never_shells_out(tmp_path):
    """The point of the whole change: no git invocation, so nothing to configure.

    Asserting on `subprocess` rather than on the string "git config" — which appears in
    these modules' prose explaining what was removed — pins the behaviour instead of the
    documentation.
    """
    import agentic_memory_system.cli as cli_mod
    import agentic_memory_system.storage as storage_mod

    for module in (sync, storage_mod, cli_mod):
        source = Path(module.__file__).read_text(encoding="utf-8")
        assert "subprocess" not in source, f"{module.__name__} must not shell out"
