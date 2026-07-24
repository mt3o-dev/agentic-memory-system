import pytest
from starlette.testclient import TestClient

from agentic_memory_system.agent_surface import AgentSurface
from agentic_memory_system.evaluator import LLMEvaluator
from agentic_memory_system.gui_api import create_app
from agentic_memory_system.schema import EventType
from agentic_memory_system.storage import MemoryStore


@pytest.fixture
def seeded():
    """A store with one change, three artifacts, one contradiction — plus its client."""
    store = MemoryStore(":memory:")
    surface = AgentSurface(store)
    change = surface.create_change("gui-demo", "exercise the GUI API")
    goal = change["goal_node_id"]
    a = surface.capture_artifact("alpha decision", "decision", goal, facets=["invoicing"])
    b = surface.capture_artifact("beta concept", "concept", goal)
    c = surface.capture_artifact("gamma constraint", "constraint", goal)
    surface.link(a["node_id"], b["node_id"], "CONTRADICTS", reason="conflict")
    # Explicit template-mode evaluator: tests never touch the network regardless of
    # whether ANTHROPIC_API_KEY happens to be set in the environment.
    client = TestClient(create_app(store, evaluator=LLMEvaluator()))
    yield client, store, change, a, b, c
    store.close()


def test_health_counts(seeded):
    client, *_ = seeded
    body = client.get("/api/health").json()
    assert body["nodes"] == 4  # goal + 3 artifacts (anchors excluded)
    assert body["flagged"] == 1
    assert body["active_changes"] == 1


def test_list_nodes_filters(seeded):
    client, _, change, a, b, c = seeded
    everything = client.get("/api/nodes").json()
    assert {n["id"] for n in everything} >= {a["node_id"], b["node_id"], c["node_id"]}
    assert all(n["type"] not in ("slice", "facet_value") for n in everything)
    flagged = client.get("/api/nodes?flagged=1").json()
    assert [n["id"] for n in flagged] == [b["node_id"]]
    searched = client.get("/api/nodes?q=gamma").json()
    assert [n["id"] for n in searched] == [c["node_id"]]


def test_node_detail_edges_events_verdict(seeded):
    client, _, change, a, b, _ = seeded
    detail = client.get(f"/api/nodes/{b['node_id']}").json()
    assert detail["node"]["body"] == "beta concept"
    incoming_types = {e["edge_type"] for e in detail["incoming"]}
    assert "CONTRADICTS" in incoming_types
    assert any(e["type"] == "contradiction_raised" for e in detail["events"])
    assert detail["resolver_verdict"] == "defer"
    assert client.get("/api/nodes/nope").status_code == 404


def test_review_queue_and_clear_flag(seeded):
    client, store, _, _, b, _ = seeded
    queue = client.get("/api/review").json()
    assert [n["id"] for n in queue] == [b["node_id"]]
    assert queue[0]["severity"] == 1.0
    resp = client.post(
        f"/api/nodes/{b['node_id']}/clear-flag", json={"reason": "false alarm"}
    )
    assert resp.json()["ok"] is True
    assert store.read_node(b["node_id"]).needs_review is False
    assert client.get("/api/review").json() == []
    # the override is journaled
    assert any(
        e.type == EventType.contradiction_cleared and e.source == "gui"
        for e in store.read_events(b["node_id"])
    )


def test_tier_change_with_lifetime_gate(seeded):
    client, store, _, a, *_ = seeded
    node_id = a["node_id"]
    ok = client.post(f"/api/nodes/{node_id}/tier", json={"tier": "long-term"})
    assert ok.json()["ok"] is True
    assert store.read_node(node_id).tier.value == "long-term"
    # lifetime requires explicit confirmation
    denied = client.post(f"/api/nodes/{node_id}/tier", json={"tier": "lifetime"})
    assert denied.status_code == 400
    confirmed = client.post(
        f"/api/nodes/{node_id}/tier", json={"tier": "lifetime", "confirmed": True}
    )
    assert confirmed.json()["ok"] is True
    assert any(e.type == EventType.tier_change for e in store.read_events(node_id))


def test_recompute_trust_endpoint(seeded):
    client, store, _, _, b, _ = seeded
    body = client.post(f"/api/nodes/{b['node_id']}/recompute-trust").json()
    assert body["ok"] is True
    assert body["trust_weight"] == 0.0  # one contradiction folded from the journal
    assert store.read_node(b["node_id"]).trust_weight == 0.0


def test_changes_lifecycle_and_sweep(seeded):
    client, store, change, a, *_ = seeded
    changes = client.get("/api/changes").json()
    assert changes[0]["id"] == change["change_node_id"]
    assert changes[0]["active"] is True
    client.post(f"/api/changes/{change['change_node_id']}/deactivate")
    swept = client.post("/api/sweep").json()
    assert swept["changed"].get(a["node_id"]) is True
    client.post(f"/api/changes/{change['change_node_id']}/activate")
    swept = client.post("/api/sweep").json()
    assert swept["changed"].get(a["node_id"]) is False


# --- editing (v1.5): create, link, edit body, weights, archive ---


def test_create_artifact_via_gui_enforces_goal_first(seeded):
    client, store, change, *_ = seeded
    created = client.post(
        "/api/nodes",
        json={
            "content": "delta invariant",
            "type": "invariant",
            "goal_ref": change["goal_node_id"],
            "facets": ["invoicing"],
        },
    ).json()
    assert "node_id" in created
    assert store.read_node(created["node_id"]).body == "delta invariant"
    # same enforcement as the agent surface: no goal, no node
    rejected = client.post(
        "/api/nodes", json={"content": "x", "type": "decision", "goal_ref": "nope"}
    )
    assert rejected.status_code == 400
    assert "goal" in rejected.json()["error"]


def test_create_artifact_surfaces_facet_warnings(seeded):
    client, _, change, *_ = seeded
    result = client.post(
        "/api/nodes",
        json={
            "content": "near-synonym probe",
            "type": "concept",
            "goal_ref": change["goal_node_id"],
            "facets": ["invoicing rules"],
        },
    ).json()
    assert any("did you mean" in w for w in result.get("facet_warnings", []))


def test_create_edge_via_gui(seeded):
    client, store, _, a, _, c = seeded
    result = client.post(
        "/api/edges",
        json={"source": c["node_id"], "target": a["node_id"], "type": "DEPENDS_ON"},
    ).json()
    assert result["edge"]["type"] == "DEPENDS_ON"
    dup = client.post(
        "/api/edges",
        json={"source": c["node_id"], "target": a["node_id"], "type": "DEPENDS_ON"},
    )
    assert dup.status_code == 400
    contra = client.post(
        "/api/edges",
        json={"source": a["node_id"], "target": c["node_id"], "type": "CONTRADICTS"},
    ).json()
    assert contra["side_effects"] == [f"{c['node_id']} flagged needs_review"]
    assert store.read_node(c["node_id"]).needs_review is True


def test_edit_body_updates_and_journals(seeded):
    client, store, _, a, *_ = seeded
    resp = client.post(
        f"/api/nodes/{a['node_id']}/body",
        json={"body": "alpha decision, revised", "reason": "clarified wording"},
    )
    assert resp.json()["ok"] is True
    assert store.read_node(a["node_id"]).body == "alpha decision, revised"
    events = store.read_events(a["node_id"])
    assert any(e.type == EventType.content_edited and e.source == "gui" for e in events)
    empty = client.post(f"/api/nodes/{a['node_id']}/body", json={"body": "  "})
    assert empty.status_code == 400


def test_set_weights_updates_and_journals(seeded):
    client, store, _, a, *_ = seeded
    resp = client.post(
        f"/api/nodes/{a['node_id']}/weights",
        json={"trust_weight": 0.4, "retrieval_weight": 2.5, "reason": "manual calibration"},
    )
    assert resp.json()["ok"] is True
    node = store.read_node(a["node_id"])
    assert node.trust_weight == 0.4 and node.retrieval_weight == 2.5
    assert any(e.type == EventType.weight_set for e in store.read_events(a["node_id"]))
    nothing = client.post(f"/api/nodes/{a['node_id']}/weights", json={})
    assert nothing.status_code == 400


def test_manual_archive_roundtrip_journaled(seeded):
    client, store, _, a, *_ = seeded
    client.post(f"/api/nodes/{a['node_id']}/archived", json={"archived": True})
    assert store.read_node(a["node_id"]).archived is True
    client.post(f"/api/nodes/{a['node_id']}/archived", json={"archived": False})
    assert store.read_node(a["node_id"]).archived is False
    types = [e.type for e in store.read_events(a["node_id"])]
    assert EventType.archived in types and EventType.reactivated in types


def test_goals_and_recall_preview_shows_scores(seeded):
    client, _, change, a, *_ = seeded
    goals = client.get("/api/goals").json()
    assert [g["id"] for g in goals] == [change["goal_node_id"]]
    ranked = client.get(
        f"/api/recall?goal={change['goal_node_id']}&query=invoicing"
    ).json()
    ids = [n["id"] for n in ranked]
    assert change["goal_node_id"] in ids and a["node_id"] in ids
    assert all(isinstance(n["score"], float) for n in ranked)
    assert client.get(f"/api/recall?goal={a['node_id']}&query=x").status_code == 400


# --- guided review (MT3-27): guidance + composite resolve ---


def _guided_client(store, evaluator):
    return TestClient(create_app(store, evaluator=evaluator))


def test_guidance_endpoint_template_mode(seeded):
    client, store, _, a, b, _ = seeded
    body = client.get(f"/api/review/{b['node_id']}/guidance").json()
    assert body["guidance"]["source"] == "template"
    assert body["guidance"]["recommended_action"] in (
        "still_valid", "superseded", "wrong", "needs_correction", "defer"
    )
    assert [c["id"] for c in body["contradictors"]] == [a["node_id"]]
    assert body["severity"] == 1.0 and body["rules_verdict"] == "defer"
    # template guidance is not an evaluator verdict — nothing journaled
    assert not any(e.source == "evaluator" for e in store.read_events(b["node_id"]))
    # 404 unknown / 400 unflagged
    assert client.get("/api/review/nope/guidance").status_code == 404
    assert client.get(f"/api/review/{a['node_id']}/guidance").status_code == 400


def test_guidance_llm_journals_evaluator_verdict_once(seeded):
    _, store, _, a, b, _ = seeded
    from test_evaluator import FakeClient

    client = _guided_client(store, LLMEvaluator(client=FakeClient()))
    first = client.get(f"/api/review/{b['node_id']}/guidance").json()
    assert first["guidance"]["source"] == "llm"
    evaluator_events = [
        e for e in store.read_events(b["node_id"]) if e.source == "evaluator"
    ]
    assert len(evaluator_events) == 1
    assert evaluator_events[0].type == EventType.manual_review
    assert "superseded" in evaluator_events[0].reason
    # second GET is served from cache: no duplicate journal entry
    client.get(f"/api/review/{b['node_id']}/guidance")
    assert (
        len([e for e in store.read_events(b["node_id"]) if e.source == "evaluator"]) == 1
    )


def test_resolve_still_valid_clears_and_journals(seeded):
    client, store, _, _, b, _ = seeded
    resp = client.post(
        f"/api/review/{b['node_id']}/resolve",
        json={
            "action": "still_valid",
            "reason": "checked the docs, still true",
            "recommended_action": "defer",
            "recompute_trust": True,
        },
    ).json()
    assert resp["ok"] is True and resp["next_id"] is None
    node = store.read_node(b["node_id"])
    assert node.needs_review is False and node.archived is False
    assert resp["trust_weight"] == node.trust_weight == 1.0  # clear cancels the raise
    events = store.read_events(b["node_id"])
    assert any(e.type == EventType.contradiction_cleared and e.source == "gui-guided" for e in events)
    decision = next(e for e in events if e.type == EventType.manual_review)
    assert "recommended=defer" in decision.reason and "chose=still_valid" in decision.reason


def test_resolve_superseded_archives_and_records_lineage(seeded):
    client, store, _, a, b, _ = seeded
    resp = client.post(
        f"/api/review/{b['node_id']}/resolve",
        json={"action": "superseded", "replacement_id": a["node_id"]},
    ).json()
    assert resp["ok"] is True
    node = store.read_node(b["node_id"])
    assert node.archived is True and node.needs_review is False
    edge = store._conn.execute(
        "SELECT 1 FROM edges WHERE source_id = ? AND target_id = ? AND type = 'DEPENDS_ON'",
        (a["node_id"], b["node_id"]),
    ).fetchone()
    assert edge is not None
    assert any(e.type == EventType.archived for e in store.read_events(b["node_id"]))


def test_resolve_wrong_archives(seeded):
    client, store, _, _, b, _ = seeded
    client.post(f"/api/review/{b['node_id']}/resolve", json={"action": "wrong"})
    node = store.read_node(b["node_id"])
    assert node.archived is True and node.needs_review is False


def test_resolve_needs_correction_edits_then_clears(seeded):
    client, store, _, _, b, _ = seeded
    resp = client.post(
        f"/api/review/{b['node_id']}/resolve",
        json={"action": "needs_correction", "new_body": "beta concept, corrected"},
    )
    assert resp.json()["ok"] is True
    node = store.read_node(b["node_id"])
    assert node.body == "beta concept, corrected" and node.needs_review is False
    types = [e.type for e in store.read_events(b["node_id"])]
    assert EventType.content_edited in types and EventType.contradiction_cleared in types


def test_resolve_defer_keeps_flag_but_journals_the_look(seeded):
    client, store, _, _, b, _ = seeded
    resp = client.post(
        f"/api/review/{b['node_id']}/resolve", json={"action": "defer", "reason": "not sure"}
    )
    assert resp.json()["ok"] is True
    node = store.read_node(b["node_id"])
    assert node.needs_review is True and node.archived is False
    assert any(
        e.type == EventType.manual_review and e.source == "gui-guided"
        for e in store.read_events(b["node_id"])
    )


def test_resolve_validation_is_atomic(seeded):
    client, store, _, _, b, _ = seeded
    before = len(store.read_events(b["node_id"]))
    # bad action / empty correction / unknown replacement / unconfirmed lifetime tier:
    # all 400, and none of them touch state or the journal
    for payload in (
        {"action": "nuke"},
        {"action": "needs_correction", "new_body": "  "},
        {"action": "wrong", "replacement_id": "ghost"},
        {"action": "still_valid", "tier": "lifetime"},
        {"action": "still_valid", "tier": "galactic"},
    ):
        resp = client.post(f"/api/review/{b['node_id']}/resolve", json=payload)
        assert resp.status_code == 400, payload
    node = store.read_node(b["node_id"])
    assert node.needs_review is True and node.body == "beta concept"
    assert len(store.read_events(b["node_id"])) == before
    # unflagged node → 400
    client.post(f"/api/review/{b['node_id']}/resolve", json={"action": "still_valid"})
    assert (
        client.post(f"/api/review/{b['node_id']}/resolve", json={"action": "wrong"}).status_code
        == 400
    )


def test_resolve_tier_addon_and_next_id(seeded):
    client, store, _, a, b, c = seeded
    # flag a second node so next_id has something to point at
    store.flag_contradicted(c["node_id"], source="test", reason="also disputed")
    resp = client.post(
        f"/api/review/{b['node_id']}/resolve",
        json={"action": "still_valid", "tier": "long-term"},
    ).json()
    assert resp["next_id"] == c["node_id"]
    assert store.read_node(b["node_id"]).tier.value == "long-term"
    # lifetime with confirmation passes the MT3-18 gate
    resp = client.post(
        f"/api/review/{c['node_id']}/resolve",
        json={"action": "still_valid", "tier": "lifetime", "tier_confirmed": True},
    ).json()
    assert resp["ok"] is True and resp["next_id"] is None
    assert store.read_node(c["node_id"]).tier.value == "lifetime"
