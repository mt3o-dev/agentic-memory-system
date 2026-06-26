from agentic_memory_system.schema import Node, NodeType, Tier
from agentic_memory_system.serialization import serialize_node


def _decision(**kwargs) -> Node:
    defaults = dict(type=NodeType.decision, tier=Tier.short_term, path="/arch/storage", body="SQLite chosen.")
    return Node(**{**defaults, **kwargs})


def test_write_assigns_id(store):
    written = store.write_node(_decision())
    assert written.id is not None
    assert len(written.id) == 36  # UUID v4


def test_write_read_roundtrip(store):
    n = _decision(body="We chose SQLite.")
    written = store.write_node(n)
    read = store.read_node(written.id)
    assert read is not None
    assert read.type == NodeType.decision
    assert read.tier == Tier.short_term
    assert read.path == "/arch/storage"
    assert read.body == "We chose SQLite."
    assert read.needs_review is False


def test_read_missing_returns_none(store):
    assert store.read_node("does-not-exist") is None


def test_needs_review_defaults_false(store):
    written = store.write_node(_decision())
    read = store.read_node(written.id)
    assert read.needs_review is False


def test_created_at_set_on_write(store):
    written = store.write_node(_decision())
    assert written.created_at is not None


def test_write_preserves_caller_id(store):
    n = _decision(id="my-fixed-id")
    written = store.write_node(n)
    assert written.id == "my-fixed-id"
    read = store.read_node("my-fixed-id")
    assert read is not None
    assert read.id == "my-fixed-id"


def test_serialize_header_format(store):
    n = _decision(path="/arch/storage", body="SQLite chosen.")
    written = store.write_node(n)
    out = serialize_node(written)
    lines = out.split("\n")
    assert lines[0] == f"[node:{written.id}]"
    assert "type: decision" in lines
    assert "tier: short-term" in lines
    assert "path: /arch/storage" in lines
    assert "needs_review: false" in lines
    blank_idx = lines.index("")
    assert lines[blank_idx + 1] == "SQLite chosen."
