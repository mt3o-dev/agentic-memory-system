import re
from datetime import datetime, timezone

from .schema import Node, NodeType, Tier, Edge, EdgeType


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


def dump_node(node: Node, outgoing_edges: list[Edge] | None = None) -> str:
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
    lines.append(node.body)
    return "\n".join(lines)


def dump_all(pairs: list[tuple[Node, list[Edge]]], *, header: bool = True) -> str:
    blocks = [dump_node(node, edges) for node, edges in pairs]
    body = "\n---\n".join(blocks)
    if header:
        prefix = "# agentic-memory-system dump\n# format_version: 1\n\n"
        body = prefix + body
    return body + "\n"


_EDGE_RE = re.compile(r"^-> (\S+) \[node:([^\]]+)\](?: @ (.+))?$")
_NODE_HEADER_RE = re.compile(r"^\[node:([^\]]+)\]$")


def parse_dump(text: str) -> list[tuple[Node, list[Edge]]]:
    if not text.strip():
        return []

    # strip leading comment lines
    lines = text.splitlines()
    start = 0
    while start < len(lines) and lines[start].startswith("#"):
        start += 1
    body = "\n".join(lines[start:])

    raw_blocks = body.split("\n---\n")
    result: list[tuple[Node, list[Edge]]] = []

    for block in raw_blocks:
        block = block.strip()
        if not block:
            continue

        block_lines = block.splitlines()
        if not block_lines:
            continue

        m = _NODE_HEADER_RE.match(block_lines[0])
        if not m:
            continue
        node_id = m.group(1)

        headers: dict[str, str] = {}
        edges: list[Edge] = []
        body_lines: list[str] = []
        in_body = False

        for line in block_lines[1:]:
            if in_body:
                body_lines.append(line)
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
        result.append((node, edges))

    return result
