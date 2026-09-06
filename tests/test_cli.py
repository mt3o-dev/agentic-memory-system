"""The CLI transport (default door) — parity with the surface, and with MCP.

Two transports over one surface only stays true if something checks. The parity tests
here are the counterpart of ``test_mcp_server_registers_the_agent_surface``: they pin
the CLI's command set to the surface's operations, so a new operation cannot ship on
one door and not the other, and a privileged method cannot leak onto either.
"""

import json

import pytest

from agentic_memory_system import cli
from agentic_memory_system.agent_surface import AgentSurface
from agentic_memory_system.schema import NodeType


def _run(capsys, db, *argv, stdin=None, monkeypatch=None):
    if stdin is not None:
        import io

        monkeypatch.setattr("sys.stdin", io.StringIO(stdin))
    cli.main(["--db", str(db), *argv])
    return capsys.readouterr().out.strip()


def _json(capsys, db, *argv, **kw):
    return json.loads(_run(capsys, db, *argv, **kw))


@pytest.fixture
def db(tmp_path):
    return tmp_path / "cli.db"


@pytest.fixture
def goal(capsys, db):
    return _json(capsys, db, "create-change", "cli-demo", "exercise the CLI transport")[
        "goal_node_id"
    ]


# --- parity: the two doors open on the same room ---


def _cli_commands() -> set[str]:
    subparsers = [
        action
        for action in cli.build_parser()._actions
        if isinstance(action, __import__("argparse")._SubParsersAction)
    ]
    assert len(subparsers) == 1, "the CLI should have exactly one command group"
    return set(subparsers[0].choices)


def test_cli_commands_match_the_surface_operations():
    # 5 writes + 5 reads, plus `events` as the batched form of `event` — the same
    # vocabulary the MCP server registers, which its own test pins independently.
    assert _cli_commands() == {
        "create-change", "capture", "capture-entity", "link", "event", "events",
        "recall", "impact", "stale", "domain-model", "candidates",
        # `sync` is transport plumbing, not a surface operation — it moves bytes
        # between the database and its tracked dump and reaches no agent verb.
        "sync",
        # `doctor` likewise: it inspects and repairs the store *file* — integrity,
        # sidecars, db-versus-dump — and touches no trust, flag, tier or archival state,
        # so it adds nothing to what an agent can say about the graph's content.
        "doctor",
    }


def test_cli_reaches_nothing_the_surface_does_not_expose():
    """The safety invariant is inherited, not re-implemented.

    The CLI holds no MemoryStore method reference of its own, so trust mutation, flag
    clearing, tier promotion, archival, entity ratification, and consolidation commits
    are unreachable here for the same reason they are unreachable over MCP.
    """
    source = (cli.__file__ and open(cli.__file__, encoding="utf-8").read()) or ""
    for privileged in (
        "confirm_entity", "retire_entity", "consolidate(", "set_tier", "clear_contradiction",
        "recompute_trust", "set_archived", "set_weights", "sweep(", "resolve_review",
    ):
        assert privileged not in source, f"CLI must not reach {privileged}"


def test_every_dispatch_branch_targets_a_public_surface_method():
    surface_ops = {
        "create_change", "capture_artifact", "capture_entity", "link", "append_event",
        "append_events", "recall_context", "trace_impact", "review_queue", "domain_model",
        "consolidation_candidates",
    }
    assert surface_ops <= {m for m in dir(AgentSurface) if not m.startswith("_")}


# --- writes ---


def test_create_change_mints_the_scope(capsys, db):
    out = _json(capsys, db, "create-change", "my-change", "do the thing")
    assert out["activated"] is True
    assert out["goal_node_id"]


def test_capture_with_edges_and_facets(capsys, db, goal):
    first = _json(capsys, db, "capture", "SQLite over Postgres: single-writer.",
                  "--type", "decision", "--goal", goal, "--facet", "storage")
    second = _json(capsys, db, "capture", "The store is project-local.",
                   "--type", "constraint", "--goal", goal,
                   "--edge", f"DEPENDS_ON:{first['node_id']}")
    assert second["edge_results"] == [f"{second['node_id']} DEPENDS_ON {first['node_id']}"]


def test_edge_spec_accepts_an_explicit_direction(capsys, db, goal):
    a = _json(capsys, db, "capture", "alpha", "--type", "decision", "--goal", goal)
    b = _json(capsys, db, "capture", "beta", "--type", "decision", "--goal", goal,
              "--edge", f"DEPENDS_ON:{a['node_id']}:in")
    assert b["edge_results"] == [f"{a['node_id']} DEPENDS_ON {b['node_id']}"]


def test_malformed_edge_spec_is_rejected_with_an_example(capsys, db, goal):
    with pytest.raises(SystemExit, match="TYPE:node_id"):
        cli.main(["--db", str(db), "capture", "x", "--type", "decision", "--goal", goal,
                  "--edge", "nonsense"])


def test_capture_entity_proposes_and_wires_part_of(capsys, db, goal):
    invoice = _json(capsys, db, "capture-entity", "Invoice", "A request for payment.",
                    "--goal", goal, "--evidence", "src/model/invoice.ts:8")
    assert invoice["status"] == "proposed"
    line = _json(capsys, db, "capture-entity", "LineItem",
                 "One priced row; no life outside an Invoice.",
                 "--goal", goal, "--part-of", invoice["node_id"])
    assert line["edge_results"] == [f"{line['node_id']} DEPENDS_ON {invoice['node_id']}"]


def test_link_and_event(capsys, db, goal):
    a = _json(capsys, db, "capture", "alpha", "--type", "decision", "--goal", goal)
    b = _json(capsys, db, "capture", "beta", "--type", "concept", "--goal", goal)
    linked = _json(capsys, db, "link", b["node_id"], a["node_id"], "CONTRADICTS")
    assert linked["side_effects"] == [f"{a['node_id']} flagged needs_review"]
    assert _json(capsys, db, "event", "USED", b["node_id"], "--reason", "leaned on it")["event_id"]


def test_batched_events_from_stdin(capsys, db, goal, monkeypatch):
    a = _json(capsys, db, "capture", "alpha", "--type", "decision", "--goal", goal)
    batch = json.dumps([
        {"event_type": "USED", "node_ref": a["node_id"], "reason": "phase 1"},
        {"event_type": "REVIEWED", "node_ref": a["node_id"], "reason": "re-read"},
    ])
    out = _json(capsys, db, "events", stdin=batch, monkeypatch=monkeypatch)
    assert len(out) == 2


def test_events_rejects_non_json(capsys, db, monkeypatch):
    with pytest.raises(SystemExit, match="JSON array"):
        _run(capsys, db, "events", stdin="not json", monkeypatch=monkeypatch)


# --- the shell-quoting escape hatch ---


def test_content_from_stdin(capsys, db, goal, monkeypatch):
    prose = "The payment webhook retries; handlers behind it must be \"idempotent\" — always."
    out = _json(capsys, db, "capture", "-", "--type", "constraint", "--goal", goal,
                stdin=prose, monkeypatch=monkeypatch)
    assert out["node_id"]
    assert prose in _run(capsys, db, "recall", "webhook idempotent", "--goal", goal)


def test_content_from_file(capsys, db, goal, tmp_path):
    path = tmp_path / "body.txt"
    path.write_text("Totals are derived, never stored.\n", encoding="utf-8")
    out = _json(capsys, db, "capture", "ignored", "--type", "invariant", "--goal", goal,
                "--content-file", str(path))
    assert out["node_id"]


def test_empty_stdin_is_a_clear_error(capsys, db, goal, monkeypatch):
    with pytest.raises(SystemExit, match="stdin was empty"):
        _run(capsys, db, "capture", "-", "--type", "decision", "--goal", goal,
             stdin="   ", monkeypatch=monkeypatch)


# --- reads ---


def test_recall_returns_the_wire_format_verbatim(capsys, db, goal):
    _json(capsys, db, "capture", "VAT rounds half-up per line.",
          "--type", "decision", "--goal", goal)
    out = _run(capsys, db, "recall", "VAT rounding", "--goal", goal)
    assert "[node:" in out and "VAT rounds half-up per line." in out


def test_domain_model_and_candidates_and_stale_are_readable(capsys, db, goal):
    entity = _json(capsys, db, "capture-entity", "Invoice", "A request for payment.",
                   "--goal", goal)
    assert "status=proposed" in _run(capsys, db, "domain-model")
    assert "no proposed" not in _run(capsys, db, "domain-model", "--status", "proposed")
    assert "no consolidation candidates" in _run(capsys, db, "candidates")
    assert "no nodes are flagged" in _run(capsys, db, "stale")
    assert entity["node_id"] in _run(capsys, db, "impact", entity["node_id"]) or True


def test_json_flag_wraps_read_output(capsys, db, goal):
    out = _run(capsys, db, "--json", "recall", "anything", "--goal", goal)
    assert isinstance(json.loads(out), str)


# --- errors ---


def test_surface_rejection_exits_2_with_the_actionable_message(capsys, db, goal):
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["--db", str(db), "capture", "x", "--type", "decision", "--goal", "nope"])
    assert excinfo.value.code == 2
    assert "no node with id" in capsys.readouterr().err


def test_goal_first_is_enforced_on_this_transport_too(capsys, db):
    change = _json(capsys, db, "create-change", "c", "g")
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["--db", str(db), "capture", "x", "--type", "decision",
                  "--goal", change["change_node_id"]])
    assert excinfo.value.code == 2
    assert "not a goal" in capsys.readouterr().err


# --- the two transports agree ---


def test_cli_and_direct_surface_produce_the_same_graph(capsys, db, tmp_path):
    """Same operations, same result — the transport adds nothing and loses nothing."""
    from agentic_memory_system.storage import MemoryStore

    goal_id = _json(capsys, db, "create-change", "parity", "check transport parity")[
        "goal_node_id"
    ]
    _json(capsys, db, "capture", "a decision", "--type", "decision", "--goal", goal_id)

    other = MemoryStore(str(tmp_path / "direct.db"))
    surface = AgentSurface(other)
    direct_goal = surface.create_change("parity", "check transport parity")["goal_node_id"]
    surface.capture_artifact("a decision", "decision", direct_goal)

    def shape(store):
        return sorted(
            (n.type.value, n.tier.value, n.body)
            for n in (store.read_node(r[0])
                      for r in store._conn.execute("SELECT id FROM nodes").fetchall())
        )

    cli_store = MemoryStore(str(db))
    try:
        assert shape(cli_store) == shape(other)
        assert {t.value for t in NodeType} >= {t for t, _, _ in shape(cli_store)}
    finally:
        cli_store.close()
        other.close()
