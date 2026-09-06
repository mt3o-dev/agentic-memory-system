"""`agentic-memory doctor`: the recovery from issue #5, written down as code.

That report ended by saying the hand-written recovery "worked, but it's a manual recovery
procedure, not a fix". `locking.py` removed the way the corruption happened; these pin the
other half — that the procedure is now a command, that running it on a suspect store
cannot make things worse, and that it refuses the one repair which would destroy data.
"""

import random
import sqlite3
from pathlib import Path

import pytest

from agentic_memory_system import doctor, sync
from agentic_memory_system.agent_surface import AgentSurface
from agentic_memory_system.cli import main as cli_main
from agentic_memory_system.storage import MemoryStore


def _seed(db, body="SQLite over Kuzu: Kuzu was archived."):
    store = MemoryStore(db)
    surface = AgentSurface(store)
    goal = surface.create_change("demo", "seed the store")["goal_node_id"]
    surface.capture_artifact(body, "decision", goal)
    store.close()


def _levels(db):
    return {f.check: f.level for f in doctor.diagnose(db)}


def _corrupt(db):
    """Scramble pages past the header — the reported `disk image is malformed`."""
    raw = bytearray(Path(db).read_bytes())
    random.seed(7)
    for offset in range(4096, min(len(raw), 20000), 97):
        raw[offset] = random.randrange(256)
    Path(db).write_bytes(bytes(raw))


# --- the healthy case, and the promise that diagnosis is safe to run ---


def test_a_healthy_store_is_reported_healthy(tmp_path):
    db = tmp_path / "graph.db"
    _seed(db)
    findings = doctor.diagnose(db)
    assert all(f.level == doctor.OK for f in findings), doctor.render(findings)
    assert doctor.exit_code(findings) == 0
    assert "healthy" in doctor.render(findings)


def test_diagnosis_never_writes(tmp_path):
    """The reason to run doctor is that you suspect the store — it must not touch it."""
    db = tmp_path / "graph.db"
    _seed(db)
    dump = sync.dump_path_for(db)
    before = {p.name: p.stat().st_mtime_ns for p in tmp_path.iterdir()}
    _corrupt(db)  # even a damaged store must not be modified by looking at it
    after_corrupt = db.stat().st_mtime_ns
    doctor.diagnose(db)
    assert db.stat().st_mtime_ns == after_corrupt
    assert dump.stat().st_mtime_ns == before[dump.name]


# --- what it finds ---


def test_a_missing_database_is_a_failure_with_a_repair(tmp_path):
    db = tmp_path / "graph.db"
    _seed(db)
    db.unlink()
    findings = {f.check: f for f in doctor.diagnose(db)}
    assert findings["database"].level == doctor.FAIL
    assert "rebuild" in findings["database"].repair


def test_dump_text_at_the_db_path_is_recognised(tmp_path):
    """What an unfiltered clone of the pre-2026-08 layout produced."""
    db = tmp_path / "graph.db"
    _seed(db)
    db.write_text(sync.dump_path_for(db).read_text(encoding="utf-8"), encoding="utf-8")
    findings = {f.check: f for f in doctor.diagnose(db)}
    assert findings["database"].level == doctor.FAIL
    assert "not a SQLite database" in findings["database"].detail


def test_a_corrupt_btree_is_reported(tmp_path):
    db = tmp_path / "graph.db"
    _seed(db)
    _corrupt(db)
    findings = {f.check: f for f in doctor.diagnose(db)}
    assert findings["integrity"].level == doctor.FAIL
    assert doctor.exit_code(list(findings.values())) == 1


def test_a_missing_dump_is_a_failure(tmp_path):
    db = tmp_path / "graph.db"
    _seed(db)
    sync.dump_path_for(db).unlink()
    assert _levels(db)["dump"] == doctor.FAIL


def test_an_unparseable_dump_is_a_failure(tmp_path):
    db = tmp_path / "graph.db"
    _seed(db)
    text = sync.dump_path_for(db).read_text(encoding="utf-8")
    sync.dump_path_for(db).write_text(text[: len(text) // 2] + "\ntype=nonsense\n", encoding="utf-8")
    assert _levels(db)["dump"] == doctor.FAIL


def test_a_leftover_wal_is_flagged_only_when_nobody_holds_the_store(tmp_path):
    db = tmp_path / "graph.db"
    _seed(db)
    leaked = sqlite3.connect(str(db))
    leaked.execute("PRAGMA journal_mode=WAL")
    leaked.execute("INSERT INTO nodes (id, type, tier, path, body, created_at) VALUES "
                   "('x','decision','short-term','/x','x','2026-07-01T12:00:00+00:00')")
    leaked.commit()
    wal = Path(str(db) + "-wal")
    frames = wal.read_bytes()
    leaked.close()

    live = MemoryStore(db)          # a GUI, say: the WAL is not "leftover" at all
    try:
        wal.write_bytes(frames)
        assert _levels(db)["sidecars"] == doctor.OK
    finally:
        live.close()

    wal.write_bytes(frames)         # ...and now nobody holds it
    findings = {f.check: f for f in doctor.diagnose(db)}
    assert findings["sidecars"].level == doctor.WARN
    assert "died before closing" in findings["sidecars"].detail


def test_abandoned_staging_files_are_found_and_deleted(tmp_path):
    db = tmp_path / "graph.db"
    _seed(db)
    orphan = tmp_path / f"{db.name}.restoring.99999"
    orphan.write_bytes(b"half a database")
    assert _levels(db)["staging"] == doctor.WARN
    assert any("staging" in line for line in doctor.repair(db))
    assert not orphan.exists()


# --- what it refuses to do ---


def test_it_will_not_discard_undumped_work(tmp_path):
    """A database newer than its dump is a crashed session's work, not damage."""
    db = tmp_path / "graph.db"
    _seed(db)
    store = MemoryStore(db, auto_sync=False)          # write without refreshing the dump
    AgentSurface(store).capture_artifact(
        "Only in the database.", "decision",
        [r[0] for r in store._conn.execute("SELECT id FROM nodes WHERE type='goal'")][0],
    )
    store.close()

    findings = {f.check: f for f in doctor.diagnose(db)}
    assert findings["agreement"].level == doctor.WARN
    assert findings["agreement"].forced, "rebuilding here would destroy the work"

    actions = doctor.repair(db)
    assert any("left the database alone" in a for a in actions)
    assert _has(db, "Only in the database.")


def test_force_rebuilds_even_then(tmp_path):
    db = tmp_path / "graph.db"
    _seed(db)
    store = MemoryStore(db, auto_sync=False)
    AgentSurface(store).capture_artifact(
        "Only in the database.", "decision",
        [r[0] for r in store._conn.execute("SELECT id FROM nodes WHERE type='goal'")][0],
    )
    store.close()
    doctor.repair(db, force=True)
    assert not _has(db, "Only in the database.")


def _has(db, body):
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        return conn.execute("SELECT count(*) FROM nodes WHERE body = ?", (body,)).fetchone()[0] > 0
    finally:
        conn.close()


# --- repairing ---


def test_repair_rebuilds_a_corrupt_store_and_keeps_a_backup(tmp_path):
    db = tmp_path / "graph.db"
    _seed(db, body="the original decision")
    _corrupt(db)
    actions = doctor.repair(db)
    assert any("rebuilt" in a for a in actions)
    assert Path(str(db) + ".bak").is_file()
    assert _has(db, "the original decision")
    assert doctor.exit_code(doctor.diagnose(db)) == 0


def test_repair_cannot_rebuild_without_a_dump(tmp_path):
    db = tmp_path / "graph.db"
    _seed(db)
    sync.dump_path_for(db).unlink()
    _corrupt(db)
    assert any("CANNOT rebuild" in a for a in doctor.repair(db))


# --- the transport ---


def test_the_cli_exits_non_zero_on_a_damaged_store(tmp_path, capsys):
    db = tmp_path / "graph.db"
    _seed(db)
    _corrupt(db)
    with pytest.raises(SystemExit) as exit_info:
        cli_main(["--db", str(db), "doctor"])
    assert exit_info.value.code == 1
    assert "FAIL" in capsys.readouterr().out


def test_the_cli_repairs_and_then_reports_healthy(tmp_path, capsys):
    db = tmp_path / "graph.db"
    _seed(db)
    _corrupt(db)
    cli_main(["--db", str(db), "doctor", "--repair"])
    out = capsys.readouterr().out
    assert "rebuilt" in out and "healthy" in out
