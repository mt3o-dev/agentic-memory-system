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
    goal = "goal"
    # The 4th dynamics class (MT3-29/30, formerly "reference entities"): a node that
    # NAMES something the project's language refers to — a domain-model entity
    # (Invoice, Customer), an actor (Person), or an external referent (ExternalRef) —
    # rather than ASSERTING a claim that could be true or false. Identity, not
    # validity: it does not decay, does not consolidate, and survives every sweep.
    # See docs/06_DOMAIN_ENTITIES.md.
    entity = "entity"


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
    # content → entity: "this artifact is about that domain entity". The attachment
    # edge that turns an entity into a retrieval hub — the one edge type whose
    # REVERSE direction carries deliberate weight (see retrieval.DEFAULT_EDGE_POLICY).
    about = "ABOUT"
    # abstraction → instance: "this semantic node was consolidated from that episode".
    # Provenance channel only (policy weight 0 both ways) — recalling an abstraction
    # must never drag its dormant instances back into the live set.
    consolidates = "CONSOLIDATES"


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
    used = "used"
    noted = "noted"
    content_edited = "content_edited"
    weight_set = "weight_set"
    # Domain-entity lifecycle. Status is DERIVED by folding these (latest wins), never
    # a stored column — same pattern as slice liveness. Agents may only propose;
    # confirm/retire are privileged human acts (GUI / lifecycle CLI).
    entity_proposed = "entity_proposed"
    entity_confirmed = "entity_confirmed"
    entity_retired = "entity_retired"
    # Journaled on the minted abstraction and on every instance it consolidates.
    consolidated = "consolidated"
    # An edge removed by a human on the privileged surface. Journaled against the SOURCE
    # node, because an edge is an assertion made from it; weight 0 keeps it trust-neutral
    # under the accumulation folds, exactly as archived/reactivated are.
    edge_removed = "edge_removed"


class Event(BaseModel):
    id: str | None = None
    node_id: str
    type: EventType
    weight: float
    polarity: Literal[-1, 1]
    source: str
    reason: str
    created_at: datetime | None = None
