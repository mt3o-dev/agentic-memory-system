import re
from datetime import datetime, timezone

from .schema import Node, NodeType, Tier, Edge, EdgeType, Event, EventType


_BODY_SENTINEL_RE = re.compile(r"^\\*---$")


def _escape_body(text: str) -> str:
    # Escape any line that is only backslashes followed by '---' by prepending
    # one more backslash. Injective (unlike escaping only the bare '---' case),
    # so a literal '\---' line round-trips instead of collapsing to '---'.
    return "\n".join(
        "\\" + line if _BODY_SENTINEL_RE.match(line) else line
        for line in text.split("\n")
    )


def _unescape_body_line(line: str) -> str:
    return line[1:] if line.startswith("\\") and _BODY_SENTINEL_RE.match(line) else line


def serialize_node(node: Node, outgoing_edges: list[Edge] | None = None) -> str:
    lines = [
        f"[node:{node.id}]",
        f"type: {node.type.value}",
        f"tier: {node.tier.value}",
        f"path: {node.path}",
        f"needs_review: {'true' if node.needs_review else 'false'}",
    ]
    for edge in (outgoing_edges or []):
        lines.append(f"-> {edge.type.value} [node:{edge.target_id}]")
    lines.append("")
    lines.append(node.body)
    return "\n".join(lines)


def _dump_event_block(event: Event) -> str:
    ts = event.created_at.isoformat() if event.created_at else ""
    lines = [
        f"[event:{event.id}]",
        f"node_id: {event.node_id}",
        f"type: {event.type.value}",
        f"weight: {event.weight}",
        f"polarity: {event.polarity}",
        f"source: {event.source}",
        f"created_at: {ts}",
        "",
    ]
    lines.append(_escape_body(event.reason))
    return "\n".join(lines)


def dump_node(
    node: Node,
    outgoing_edges: list[Edge] | None = None,
    events: list[Event] | None = None,
) -> str:
    ts = node.created_at.isoformat() if node.created_at else ""
    lines = [
        f"[node:{node.id}]",
        f"type: {node.type.value}",
        f"tier: {node.tier.value}",
        f"path: {node.path}",
        f"created_at: {ts}",
        f"needs_review: {'true' if node.needs_review else 'false'}",
        f"retrieval_weight: {node.retrieval_weight}",
        f"trust_weight: {node.trust_weight}",
    ]
    for edge in (outgoing_edges or []):
        edge_ts = edge.created_at.isoformat() if edge.created_at else ""
        lines.append(f"-> {edge.type.value} [node:{edge.target_id}] @ {edge_ts}")
    lines.append("")
    lines.append(_escape_body(node.body))
    node_block = "\n".join(lines)
    event_blocks = [_dump_event_block(event) for event in (events or [])]
    return "\n---\n".join([node_block, *event_blocks])


def dump_all(pairs: list[tuple[Node, list[Edge], list[Event]]], *, header: bool = True) -> str:
    blocks = [dump_node(node, edges, events) for node, edges, events in pairs]
    body = "\n---\n".join(blocks)
    if header:
        prefix = "# agentic-memory-system dump\n# format_version: 1\n\n"
        body = prefix + body
    return body + "\n"


_EDGE_RE = re.compile(r"^-> (\S+) \[node:([^\]]+)\](?: @ (.+))?$")
_NODE_HEADER_RE = re.compile(r"^\[node:([^\]]+)\]$")
_EVENT_HEADER_RE = re.compile(r"^\[event:([^\]]+)\]$")


def parse_dump(text: str) -> list[tuple[Node, list[Edge], list[Event]]]:
    if not text.strip():
        return []

    # Strip leading comment lines and blank separators, splitting on "\n" only.
    # splitlines() would also break on \r and unicode line separators, silently
    # corrupting any body/reason that contains them.
    lines = text.split("\n")
    start = 0
    while start < len(lines) and lines[start].startswith("#"):
        start += 1
    while start < len(lines) and lines[start] == "":
        start += 1
    body = "\n".join(lines[start:])
    # dump_all appends exactly one trailing newline; drop that one artifact so a
    # body/reason that genuinely ends in a newline round-trips intact.
    if body.endswith("\n"):
        body = body[:-1]

    raw_blocks = body.split("\n---\n")
    node_pairs: list[tuple[Node, list[Edge]]] = []
    events_by_node: dict[str, list[Event]] = {}

    for block in raw_blocks:
        if not block:
            continue

        block_lines = block.split("\n")
        if not block_lines[0]:
            continue

        node_m = _NODE_HEADER_RE.match(block_lines[0])
        event_m = _EVENT_HEADER_RE.match(block_lines[0])

        if node_m:
            node_id = node_m.group(1)

            headers: dict[str, str] = {}
            edges: list[Edge] = []
            body_lines: list[str] = []
            in_body = False

            for line in block_lines[1:]:
                if in_body:
                    body_lines.append(_unescape_body_line(line))
                elif line == "":
                    in_body = True
                else:
                    em = _EDGE_RE.match(line)
                    if em:
                        etype, target_id, edge_ts = em.group(1), em.group(2), em.group(3)
                        edge_created = datetime.fromisoformat(edge_ts) if edge_ts else None
                        edges.append(Edge(
                            source_id=node_id,
                            target_id=target_id,
                            type=EdgeType(etype),
                            created_at=edge_created,
                        ))
                    else:
                        if ": " in line:
                            k, _, v = line.partition(": ")
                            headers[k.strip()] = v.strip()

            node = Node(
                id=node_id,
                type=NodeType(headers["type"]),
                tier=Tier(headers["tier"]),
                path=headers["path"],
                body="\n".join(body_lines),
                created_at=datetime.fromisoformat(headers["created_at"]) if headers.get("created_at") else None,
                needs_review=headers.get("needs_review", "false") == "true",
                retrieval_weight=float(headers.get("retrieval_weight", "1.0")),
                trust_weight=float(headers.get("trust_weight", "1.0")),
            )
            node_pairs.append((node, edges))

        elif event_m:
            event_id = event_m.group(1)

            headers = {}
            body_lines = []
            in_body = False

            for line in block_lines[1:]:
                if in_body:
                    body_lines.append(_unescape_body_line(line))
                elif line == "":
                    in_body = True
                else:
                    if ": " in line:
                        k, _, v = line.partition(": ")
                        headers[k.strip()] = v.strip()

            event = Event(
                id=event_id,
                node_id=headers["node_id"],
                type=EventType(headers["type"]),
                weight=float(headers["weight"]),
                polarity=int(headers["polarity"]),
                source=headers["source"],
                reason="\n".join(body_lines),
                created_at=datetime.fromisoformat(headers["created_at"]) if headers.get("created_at") else None,
            )
            events_by_node.setdefault(event.node_id, []).append(event)

    return [
        (node, edges, events_by_node.get(node.id, []))
        for node, edges in node_pairs
    ]
