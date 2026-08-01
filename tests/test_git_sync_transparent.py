"""Filter-free git sync: the store heals itself instead of needing local git config.

The old design tracked the `.db` through a clean/smudge filter, which cannot be made
transparent — git will never auto-register a repo-provided filter, because running
arbitrary commands from a clone is a security boundary. These tests pin the replacement:
the dump is the tracked source, the database is a build artifact, and both directions
happen without anyone remembering to ask.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from agentic_memory_system import sync
from agentic_memory_system.serialization import parse_dump
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


# --- merges: the dump is a text file two branches will edit at once ---


def test_conflict_markers_are_located_by_line(tmp_path):
    text = "a\n<<<<<<< HEAD\nmine\n=======\ntheirs\n>>>>>>> other\nb\n"
    assert sync.conflict_markers_in(text) == [2, 4, 6]
    # A body that merely mentions the syntax is not a conflict: `=======` counts only as
    # a whole line, and the other two need the trailing space git writes.
    assert sync.conflict_markers_in("see the ======= rule\n<<<<<<<nope\n") == []


def test_a_conflicted_dump_refuses_to_open_rather_than_serve_an_empty_graph(tmp_path):
    """The defect this pins produced a *fluent wrong answer*, which is the worst kind.

    A conflicted dump parsed to nothing, the store opened with zero nodes, and the next
    question got "(no domain entities yet)" — a sentence that reads like knowledge about
    a graph that in fact exists and simply was not loaded.
    """
    db = tmp_path / "graph.db"
    _seed(db)
    dump = sync.dump_path_for(db)
    dump.write_text("<<<<<<< HEAD\n" + dump.read_text(encoding="utf-8"), encoding="utf-8")
    db.unlink()

    with pytest.raises(sync.DumpUnusableError) as exc:
        MemoryStore(db)
    assert "conflict" in str(exc.value) and str(dump) in str(exc.value)


def test_a_conflicted_dump_is_fatal_even_when_an_older_database_survives(tmp_path):
    """Unlike a merely malformed dump, an unresolved merge does not degrade — it stops.

    Serving the older database would let this session write into it, and that work would
    then exist only there; whichever side the eventual resolution keeps, the other is
    lost. Refusing keeps the graph in one place, so the fix stays a text edit.
    """
    db = tmp_path / "graph.db"
    _seed(db)
    dump = sync.dump_path_for(db)
    dump.write_text(dump.read_text(encoding="utf-8") + "\n>>>>>>> theirs\n", encoding="utf-8")

    with pytest.raises(sync.DumpUnusableError):
        MemoryStore(db)


def test_a_malformed_dump_with_no_surviving_database_is_fatal_too(tmp_path):
    db = tmp_path / "graph.db"
    _seed(db)
    sync.dump_path_for(db).write_text(
        "# agentic-memory-system dump\n\n[node:x]\ntype: nonsense\n", encoding="utf-8"
    )
    db.unlink()

    with pytest.raises(sync.DumpUnusableError):
        MemoryStore(db)


def test_a_missing_dump_is_never_an_error(tmp_path):
    """The control on all of the above: no dump at all is an ordinary fresh store."""
    store = MemoryStore(tmp_path / "brand-new.db")
    store.close()


def test_a_conflicted_dump_is_never_overwritten(tmp_path):
    """A long-lived store (the GUI) can meet a conflict that appeared after it opened.

    Dumping over it would replace both sides of the merge with this process's graph —
    deleting the incoming branch's nodes *and* the markers, so `git status` reads as a
    clean resolution and the loss gets committed.
    """
    db = tmp_path / "graph.db"
    node_id = _seed(db)
    dump = sync.dump_path_for(db)

    store = MemoryStore(db)  # opens against a healthy dump
    surface = AgentSurface(store)
    goal = surface.create_change("during-merge", "work while the tree is conflicted")
    surface.capture_artifact("Written during the merge.", "decision", goal["goal_node_id"])
    theirs = dump.read_text(encoding="utf-8") + "\n<<<<<<< HEAD\n=======\n>>>>>>> theirs\n"
    dump.write_text(theirs, encoding="utf-8")
    store.close()

    assert dump.read_text(encoding="utf-8") == theirs, "the conflicted dump was clobbered"
    assert any("did NOT refresh" in note for note in store.sync_notes)
    assert node_id  # the seeded node is still in the dump's surviving text


def test_the_dump_is_ordered_for_merging_not_for_reading(tmp_path):
    """Ordering by id, at both levels, is what makes independent work merge.

    Chronological order puts every new block at the end of the file and every new event
    at the end of its node, so two branches doing unrelated work both append to the same
    region and git calls it a conflict. Random uuids scatter the additions instead.
    """
    db = tmp_path / "graph.db"
    store = MemoryStore(db)
    surface = AgentSurface(store)
    goal = surface.create_change("ordering", "check the dump order")["goal_node_id"]
    for i in range(6):
        surface.capture_artifact(f"Claim number {i}.", "concept", goal)
    for i in range(6):
        surface.append_events([{"event_type": "USED", "node_ref": goal, "reason": f"round {i}"}])

    pairs = store.dump_pairs()
    try:
        ids = [node.id for node, _edges, _events in pairs]
        assert ids == sorted(ids)

        journal = next(events for node, _e, events in pairs if node.id == goal)
        assert len(journal) > 2
        assert [e.id for e in journal] == sorted(e.id for e in journal)
        # ...while every *reader* still gets the journal in the order it happened.
        chronological = store.read_events(goal)
        assert [e.created_at for e in chronological] == sorted(
            e.created_at for e in chronological
        )
    finally:
        store.close()


@pytest.fixture
def deterministic_ids(monkeypatch):
    """Hand out uuids from a fixed sequence, so a merge test cannot be flaky.

    Ordering-by-id buys a *probability*, not a guarantee: two additions merge when their
    ids sort into different gaps among the blocks already there, which is the usual case
    once a store has any history and never a certainty. Real uuid4 would make the test
    below pass most of the time and fail the rest, which is worse than not having it —
    so the randomness is replaced with a sequence that spreads, and what the test then
    pins is the *format's* merge behaviour rather than a dice roll.
    """
    import random
    import uuid as uuid_mod

    rng = random.Random(20260801)

    def spread() -> uuid_mod.UUID:
        # A seeded PRNG, not a counter: consecutive ids must land *far apart* in sort
        # order, which is the whole property being relied on. (A counter produces ids
        # that always sort last — which is chronological order wearing a uuid costume,
        # and defeats the mechanism instead of testing it.)
        return uuid_mod.UUID(int=rng.getrandbits(128), version=4)

    monkeypatch.setattr("agentic_memory_system.storage.uuid.uuid4", spread)
    monkeypatch.setattr("agentic_memory_system.agent_surface.uuid.uuid4", spread)
    return spread


def test_deleting_the_markers_by_hand_is_the_trap_the_resolver_exists_for(tmp_path):
    """Why `sync resolve` is a command and not a paragraph of instructions.

    Two ``used`` events differ only in id, timestamp, and reason, so git aligns them and
    reports *those three lines* rather than two whole blocks. Keeping "both sides" line by
    line then produces one block wearing parts of each — and it parses, silently.
    """
    conflicted = (
        "# agentic-memory-system dump\n\n"
        "[node:n1]\ntype: concept\ntier: short-term\npath: /n\ncreated_at: \n"
        "needs_review: false\n\nbody\n---\n"
        "<<<<<<< HEAD\n[event:e-alice]\n=======\n[event:e-bob]\n>>>>>>> bob\n"
        "node_id: n1\ntype: used\nweight: 1.0\npolarity: 1\nsource: agent\n"
        "created_at: 2026-08-01T00:00:00+00:00\n\nalice used it\n"
    )
    stripped = "\n".join(
        line
        for line in conflicted.split("\n")
        if not (line.startswith(("<<<<<<< ", ">>>>>>> ")) or line.rstrip() == "=======")
    )
    spliced = parse_dump(stripped)
    events = [e for _n, _e, evs in spliced for e in evs]
    assert len(events) == 1, "the hand resolution silently lost one of the two events"

    # The resolver keeps both, because it merges by id instead of by line.
    ours, theirs = sync.split_conflict(conflicted)
    merged = parse_dump(sync.merge_dumps(ours, theirs))
    assert {e.id for _n, _e, evs in merged for e in evs} == {"e-alice", "e-bob"}


def test_resolving_a_conflict_unions_both_sides(tmp_path):
    db = tmp_path / "graph.db"
    _seed(db)
    dump = sync.dump_path_for(db)
    base = dump.read_text(encoding="utf-8")

    other = tmp_path / "other.db"
    _seed(other, change="theirs", body="Their branch decided something else.")
    their_blocks = "\n".join(
        line
        for line in sync.dump_path_for(other).read_text(encoding="utf-8").split("\n")
        if not line.startswith("#")
    ).strip("\n")
    # Only their side gained blocks — the shape of "they added, we did not".
    dump.write_text(
        base.rstrip("\n") + "\n<<<<<<< HEAD\n=======\n---\n" + their_blocks
        + "\n>>>>>>> theirs\n",
        encoding="utf-8",
    )

    nodes, _events = sync.resolve_conflict(dump)
    assert nodes > 0
    assert not sync.conflict_markers_in(dump.read_text(encoding="utf-8"))

    db.unlink()
    store = MemoryStore(db)  # the resolved dump is loadable, and holds both sides
    try:
        bodies = _bodies_of(store)
        assert any("SQLite over Kuzu" in b for b in bodies)
        assert any("Their branch decided" in b for b in bodies)
    finally:
        store.close()


def test_resolving_a_dump_that_is_not_conflicted_is_refused(tmp_path):
    db = tmp_path / "graph.db"
    _seed(db)
    with pytest.raises(sync.DumpUnusableError):
        sync.resolve_conflict(sync.dump_path_for(db))


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
def test_parallel_branches_merge_into_a_graph_holding_both_sides(tmp_path, deterministic_ids):
    """The end-to-end claim, made against real git rather than against our own reasoning.

    Two people work in parallel: each captures an artifact and journals against the same
    pre-existing node. Nothing they did conflicts semantically.

    What is asserted is *not* "git merges this cleanly" — that is a probability, not a
    property (ordering by id lowers the odds of two insertions landing at the same offset;
    it cannot rule it out, and in a small store it happens often). What is asserted is the
    part users depend on: whether git merges it outright or hands back a conflict resolved
    the documented way, the resulting dump rebuilds into a graph holding *both* sides.
    """
    repo = tmp_path / "repo"
    (repo / "context").mkdir(parents=True)
    db = repo / "context" / "memory-graph.db"

    def git(*argv):
        return subprocess.run(
            ["git", *argv], cwd=repo, capture_output=True, text=True, check=False
        )

    git("init", "-q", "-b", "main")
    git("config", "user.email", "t@e.st")
    git("config", "user.name", "test")
    (repo / ".gitignore").write_text("context/*.db*\ncontext/*.bak\n", encoding="utf-8")

    store = MemoryStore(db)
    surface = AgentSurface(store)
    goal = surface.create_change("shared", "a goal both branches touch")["goal_node_id"]
    shared = surface.capture_artifact("A foundation both will use.", "concept", goal)["node_id"]
    for i in range(3):
        surface.append_events([{"event_type": "USED", "node_ref": shared, "reason": f"history {i}"}])
    store.close()
    git("add", "-A")
    git("commit", "-qm", "baseline")

    def work(branch, body, kind, reason):
        git("checkout", "-q", branch if branch == "main" else "-b", branch)
        store = MemoryStore(db)
        surface = AgentSurface(store)
        surface.capture_artifact(body, kind, goal)
        surface.append_events([{"event_type": "USED", "node_ref": shared, "reason": reason}])
        store.close()
        git("add", "-A")
        git("commit", "-qm", branch)

    work("alice", "Alice: retries must be idempotent.", "constraint", "alice used it")
    git("checkout", "-q", "main")
    work("bob", "Bob: timeouts default to thirty seconds.", "decision", "bob used it")

    git("checkout", "-q", "alice")
    git("merge", "bob")
    dump = sync.dump_path_for(db)
    if sync.conflict_markers_in(dump.read_text(encoding="utf-8")):
        sync.resolve_conflict(dump)
    assert not sync.conflict_markers_in(dump.read_text(encoding="utf-8"))

    # And the merged text is a real graph, not just a file that merged.
    for suffix in ("", "-wal", "-shm"):
        Path(str(db) + suffix).unlink(missing_ok=True)
    merged = MemoryStore(db)
    try:
        bodies = _bodies_of(merged)
        assert any("Alice: retries" in b for b in bodies)
        assert any("Bob: timeouts" in b for b in bodies)
        reasons = {e.reason for e in merged.read_events(shared)}
        assert {"alice used it", "bob used it"} <= reasons
    finally:
        merged.close()


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
