from enum import Enum
from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class NodeType(str, Enum):
    decision = "decision"
    concept = "concept"
    constraint = "constraint"
    issue = "issue"
    invariant = "invariant"
    slice = "slice"
    facet_value = "facet_value"


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
    archived: bool = False


class EdgeType(str, Enum):
    depends_on = "DEPENDS_ON"
    contradicts = "CONTRADICTS"
    scoped_to = "SCOPED_TO"
    has_facet = "HAS_FACET"


class Edge(BaseModel):
    source_id: str
    target_id: str
    type: EdgeType
    created_at: datetime | None = None


class EventType(str, Enum):
    contradiction_raised = "contradiction_raised"
    contradiction_cleared = "contradiction_cleared"
    confirmation_added = "confirmation_added"
    manual_review = "manual_review"
    tier_change = "tier_change"
    slice_activated = "slice_activated"
    slice_deactivated = "slice_deactivated"
    archived = "archived"
    reactivated = "reactivated"


class Event(BaseModel):
    id: str | None = None
    node_id: str
    type: EventType
    weight: float
    polarity: Literal[-1, 1]
    source: str
    reason: str
    created_at: datetime | None = None
