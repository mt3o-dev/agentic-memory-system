from enum import Enum
from datetime import datetime

from pydantic import BaseModel


class NodeType(str, Enum):
    decision = "decision"
    concept = "concept"
    constraint = "constraint"
    issue = "issue"
    invariant = "invariant"


class Tier(str, Enum):
    short_term = "short-term"
    mid_term = "mid-term"
    long_term = "long-term"
    lifetime = "lifetime"


class Node(BaseModel):
    id: str | None = None
    type: NodeType
    tier: Tier
    path: str
    body: str
    created_at: datetime | None = None
    needs_review: bool = False
    retrieval_weight: float = 1.0
    trust_weight: float = 1.0


class EdgeType(str, Enum):
    depends_on = "DEPENDS_ON"


class Edge(BaseModel):
    source_id: str
    target_id: str
    type: EdgeType
    created_at: datetime | None = None
