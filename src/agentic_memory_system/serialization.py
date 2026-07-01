from .schema import Node, Edge


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
