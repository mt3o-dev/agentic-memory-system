from .schema import Node


def serialize_node(node: Node) -> str:
    lines = [
        f"[node:{node.id}]",
        f"type: {node.type.value}",
        f"tier: {node.tier.value}",
        f"path: {node.path}",
        f"needs_review: {'true' if node.needs_review else 'false'}",
        "",
        node.body,
    ]
    return "\n".join(lines)
