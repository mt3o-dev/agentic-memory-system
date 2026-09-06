"""Concurrent processes on one store: many connections are fine, one file swap is not.

The bug these pin (issue #5) is not a lock-contention bug. `sync.restore_from_text`
replaced `memory-graph.db` wholesale while other processes — a GUI write server, a second
agent session, an ad-hoc CLI — had it open, and left the previous database's
`-wal`/`-shm` beside the new file. Sidecars are addressed by *filename*, not by inode, so
SQLite adopts the old WAL for the new database and replays frames describing pages that
were never in it. That silently undoes the restore in the mild case and leaves a
malformed B-tree (`database disk image is malformed`) in the reported one.

The fix is two rules, and each test below holds exactly one of them up:

    every live connection holds a SHARED lock (`locking.py`)
    replacing the file requires an EXCLUSIVE one — and deletes the sidecars as it swaps

Concurrency *inside* the file stays SQLite's job: shared locks do not serialize writers,
and `busy_timeout` makes a second writer wait rather than fail.
"""

import os
import sqlite3
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from agentic_memory_system import locking, sync
from agentic_memory_system.agent_surface import AgentSurface
from agentic_memory_system.schema import Event, EventType
from agentic_memory_system.storage import MemoryStore


def _seed(db_path, change="demo", body="SQLite over Kuzu: Kuzu was archived."):
    store = MemoryStore(db_path)
    surface = AgentSurface(store)
    goal = surface.create_change(change, "seed the store")["goal_node_id"]
    surface.capture_artifact(body, "decision", goal)
    store.close()


def _bodies(db_path):
    store = MemoryStore(db_path, auto_sync=False)
    try:
        return [r[0] for r in store._conn.execute("SELECT body FROM nodes").fetchall()]
    finally:
        store.close()


def _dump_of(tmp_path, body):
    """A dump holding `body`, built in a throwaway store beside the real one."""
    other = tmp_path / "upstream.db"
    _seed(other, change="upstream", body=body)
    return sync.dump_path_for(other).read_text(encoding="utf-8")


def _publish(dump_path, text, db_path):
    """Land new dump text and move its mtime ahead — what a `git pull` looks like."""
    dump_path.write_text(text, encoding="utf-8")
    os.utime(dump_path, (dump_path.stat().st_atime, Path(db_path).stat().st_mtime + 10))


def _crash_leaving_a_wal(db_path, sql):
    """Leave behind what a session that died before closing leaves: a committed WAL.

    Closing a connection checkpoints and deletes the sidecars, so a `kill -9` cannot be
    imitated by closing one. Snapshot the main file *before* the write and the WAL
    *after* it, then put both back: the row is now reachable only through the WAL, which
    is exactly the on-disk state a crash produces — and exactly the state that made the
    reported restore silently resurrect the old database.
    """
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    before = Path(db_path).read_bytes()
    conn.execute(sql)
    conn.commit()
    frames = Path(str(db_path) + "-wal").read_bytes()
    conn.close()
    Path(db_path).write_bytes(before)
    Path(str(db_path) + "-wal").write_bytes(frames)


_LEAKED_ROW = (
    "INSERT INTO nodes (id, type, tier, path, body, created_at) VALUES "
    "('leak', 'decision', 'short-term', '/leak', 'only in the wal', '2026-07-01T12:00:00+00:00')"
)


# --- the swap itself: sidecars belong to the file being replaced, not to the new one ---


def test_a_restore_deletes_the_replaced_databases_wal_sidecars(tmp_path):
    db = tmp_path / "graph.db"
    _seed(db)
    for sidecar in ("-wal", "-shm"):
        Path(str(db) + sidecar).write_bytes(b"stale")

    sync.restore_from_text(sync.dump_path_for(db).read_text(encoding="utf-8"), db)

    assert not Path(str(db) + "-wal").exists()
    assert not Path(str(db) + "-shm").exists()


def test_a_restore_takes_effect_even_when_the_old_database_left_a_wal(tmp_path):
    """The regression proper: without the sidecar cleanup the restore silently no-ops.

    SQLite replays the inherited WAL over the rebuilt file, resurrecting the *old* rows
    and discarding everything the restore just wrote — a restore that reports success
    and changed nothing.
    """
    db = tmp_path / "graph.db"
    _seed(db, body="Kuzu is the graph engine.")
    _crash_leaving_a_wal(db, _LEAKED_ROW)
    assert Path(str(db) + "-wal").exists(), "precondition: a real WAL is sitting beside the db"

    sync.restore_from_text(_dump_of(tmp_path, "SQLite is the graph engine."), db)

    bodies = _bodies(db)
    assert "SQLite is the graph engine." in bodies
    assert "Kuzu is the graph engine." not in bodies


def test_a_restore_that_fails_leaves_no_staging_file_behind(tmp_path):
    db = tmp_path / "graph.db"
    _seed(db)
    text = sync.dump_path_for(db).read_text(encoding="utf-8")
    with pytest.raises(Exception):
        # Truncating mid-node is what an interrupted `git pull` or a full disk produces.
        sync.restore_from_text(text[: len(text) // 2] + "\ntype=nonsense\n", db)
    assert not list(tmp_path.glob("*.restoring*"))
    assert _bodies(db), "the database that was there is still the database that is there"


def test_staging_files_are_private_to_the_process(tmp_path):
    """Two restores racing must not write one another's half-built database."""
    db = tmp_path / "graph.db"
    _seed(db)
    staging = sync.restore_staging_path(db)
    assert str(os.getpid()) in staging.name
    assert staging != sync.dump_path_for(db)


# --- the lock: a live connection is what makes a replace unsafe, so it forbids one ---


def test_the_database_is_not_replaced_while_a_connection_is_open(tmp_path):
    db = tmp_path / "graph.db"
    _seed(db, body="Kuzu is the graph engine.")
    dump = sync.dump_path_for(db)
    upstream = _dump_of(tmp_path, "SQLite is the graph engine.")

    live = MemoryStore(db)  # a GUI server, an MCP server, another agent session
    try:
        _publish(dump, upstream, db)  # `git pull` lands while it is open
        inode = db.stat().st_ino
        note = sync.auto_restore(db)
        assert note is not None and "skipped the rebuild" in note
        assert db.stat().st_ino == inode, "the file under the open connection was replaced"
        assert "Kuzu is the graph engine." in [
            r[0] for r in live._conn.execute("SELECT body FROM nodes").fetchall()
        ]
    finally:
        live.close()

    # ...and once it closes, the very next open picks the dump up as usual.
    second = MemoryStore(db)
    try:
        assert "SQLite is the graph engine." in [
            r[0] for r in second._conn.execute("SELECT body FROM nodes").fetchall()
        ]
    finally:
        second.close()


def test_a_skipped_rebuild_is_reported_not_silent(tmp_path):
    db = tmp_path / "graph.db"
    _seed(db)
    _publish(sync.dump_path_for(db), _dump_of(tmp_path, "Totals are derived."), db)

    live = MemoryStore(db)
    try:
        second = MemoryStore(db)
        try:
            assert any("skipped the rebuild" in note for note in second.sync_notes)
        finally:
            second.close()
    finally:
        live.close()


def test_the_lock_is_a_sidecar_not_the_database(tmp_path):
    """A lock on the file being replaced is a lock on an inode about to be discarded."""
    db = tmp_path / "graph.db"
    assert locking.lock_path_for(db) == tmp_path / "graph.db.lock"


def test_the_lock_survives_the_replace_it_guards(tmp_path):
    db = tmp_path / "graph.db"
    _seed(db)
    lock_inode = None
    with locking.store_lock(db, exclusive=True):
        lock_inode = locking.lock_path_for(db).stat().st_ino
        sync.restore_from_text(sync.dump_path_for(db).read_text(encoding="utf-8"), db, lock=False)
    assert locking.lock_path_for(db).stat().st_ino == lock_inode


def test_the_lock_holds_across_processes(tmp_path):
    """flock is per file description, so the in-process tests above prove less than this."""
    db = tmp_path / "graph.db"
    _seed(db)
    child = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import sys, time; sys.path.insert(0, %r);"
            "from agentic_memory_system import locking;"
            "held = locking.acquire(%r);"
            "print('held', flush=True); time.sleep(5)"
            % (str(Path(__file__).resolve().parents[1] / "src"), str(db)),
        ],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert child.stdout.readline().strip() == "held"
        with pytest.raises(locking.StoreBusy):
            locking.acquire(db, exclusive=True, timeout=0.5)
    finally:
        child.kill()
        child.wait()
    # The kernel drops the lock when the process dies — nothing stale is left behind.
    locking.release(locking.acquire(db, exclusive=True, timeout=5.0))


def test_shared_locks_do_not_serialise_each_other(tmp_path):
    db = tmp_path / "graph.db"
    _seed(db)
    a, b = MemoryStore(db), MemoryStore(db)
    try:
        assert a._conn.execute("SELECT count(*) FROM nodes").fetchone()[0] > 0
        assert b._conn.execute("SELECT count(*) FROM nodes").fetchone()[0] > 0
    finally:
        a.close()
        b.close()


def test_in_memory_stores_take_no_lock(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    store = MemoryStore(":memory:")
    try:
        assert store._lock is None
    finally:
        store.close()
    assert not list(tmp_path.iterdir())


def test_locking_can_be_switched_off(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMORY_LOCK", "0")
    db = tmp_path / "graph.db"
    _seed(db)
    assert not locking.lock_path_for(db).exists()
    assert locking.acquire(db, exclusive=True) is None


# --- inside the file, concurrency stays SQLite's job ---


def test_a_second_writer_waits_instead_of_failing(tmp_path):
    """Without busy_timeout the default is zero patience: an instant 'database is locked'."""
    db = tmp_path / "graph.db"
    _seed(db)
    store = MemoryStore(db)
    holding = threading.Event()

    def hold_the_write_lock():
        blocker = sqlite3.connect(str(db))  # sqlite3 connections are thread-bound
        blocker.execute("BEGIN IMMEDIATE")
        blocker.execute("UPDATE nodes SET path = path")
        holding.set()
        time.sleep(0.3)  # long enough that zero patience is the difference
        blocker.commit()
        blocker.close()

    thread = threading.Thread(target=hold_the_write_lock)
    thread.start()
    assert holding.wait(2.0), "precondition: the other writer holds the write lock"
    try:
        store._conn.execute("UPDATE nodes SET path = path")  # blocks, then succeeds
        store._conn.commit()
    finally:
        thread.join()
        store.close()


def test_the_busy_timeout_is_configured(tmp_path):
    db = tmp_path / "graph.db"
    store = MemoryStore(db)
    try:
        assert store._conn.execute("PRAGMA busy_timeout").fetchone()[0] > 0
    finally:
        store.close()


# --- the backup taken before a replace has to be worth having ---


def test_the_backup_before_a_replace_includes_uncheckpointed_work(tmp_path):
    """A `.bak` copied without checkpointing is missing every transaction still in the WAL."""
    db = tmp_path / "graph.db"
    _seed(db, body="Kuzu is the graph engine.")
    _crash_leaving_a_wal(db, _LEAKED_ROW)
    _publish(sync.dump_path_for(db), _dump_of(tmp_path, "SQLite is the graph engine."), db)

    MemoryStore(db).close()

    backup = sqlite3.connect(str(db) + ".bak")
    try:
        bodies = [r[0] for r in backup.execute("SELECT body FROM nodes").fetchall()]
    finally:
        backup.close()
    assert "only in the wal" in bodies


# --- the backup is a file to read, not a store to sync ---


def test_opening_a_backup_neither_rebuilds_it_nor_publishes_a_dump(tmp_path):
    """`with_suffix` maps `graph.db.bak` to `graph.db.dump` — one character from the real one.

    Reading a backup is what anyone does first after a corruption. Before this guard it
    wrote a phantom dump beside the tracked one (the unexplained `memory-graph.db.dump`
    in the report), and a newer such dump would have rebuilt the backup *from* it,
    destroying the copy being rescued.
    """
    db = tmp_path / "graph.db"
    _seed(db, body="the rescued rows")
    backup = tmp_path / "graph.db.bak"
    backup.write_bytes(db.read_bytes())
    # A dump at the name `with_suffix` picks for the backup, newer than the backup.
    phantom = tmp_path / "graph.db.dump"
    _publish(phantom, _dump_of(tmp_path, "something else entirely"), backup)

    store = MemoryStore(backup)
    try:
        store.append_event(
            Event(
                id="e1",
                node_id=[r[0] for r in store._conn.execute("SELECT id FROM nodes")][0],
                type=EventType.used,
                weight=0.1,
                polarity=1,
                source="test",
                reason="a write, so a dump would be refreshed if one were owed",
                created_at=datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc),
            )
        )
        assert "the rescued rows" in [
            r[0] for r in store._conn.execute("SELECT body FROM nodes").fetchall()
        ]
        assert store.sync_notes == []
    finally:
        store.close()

    # The phantom dump is left exactly as it was: not refreshed from the backup...
    assert "the rescued rows" not in phantom.read_text(encoding="utf-8")
    # ...and not applied to it either.
    assert "the rescued rows" in [
        r[0] for r in sqlite3.connect(str(backup)).execute("SELECT body FROM nodes")
    ]


# --- degrading silently is the failure mode the git-filter design died of ---


def test_a_store_without_the_lock_says_so(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMORY_LOCK", "0")
    db = tmp_path / "graph.db"
    store = MemoryStore(db)
    try:
        assert any("locking is switched off" in note for note in store.sync_notes)
    finally:
        store.close()


def test_a_store_with_the_lock_says_nothing_about_it(tmp_path):
    db = tmp_path / "graph.db"
    store = MemoryStore(db)
    try:
        assert not any("locking" in note for note in store.sync_notes)
    finally:
        store.close()


def test_in_memory_stores_are_not_warned_about_a_lock_they_never_wanted(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMORY_LOCK", "0")
    store = MemoryStore(":memory:")
    try:
        assert store.sync_notes == []
    finally:
        store.close()


def test_a_db_path_that_is_not_a_path_is_refused(tmp_path):
    """`str()` accepts anything, so the wrong variable used to become a filename.

    A test passing a `(path, change)` fixture tuple straight in created databases named
    after the tuple's repr, in the working directory, and they were committed before
    anyone noticed. The stringification is the bug; refusing it is the fix.
    """
    with pytest.raises(TypeError, match="must be a str or Path"):
        MemoryStore((tmp_path / "graph.db", {"goal_node_id": "abc"}))
    assert not list(tmp_path.iterdir()), "nothing should have been created"
