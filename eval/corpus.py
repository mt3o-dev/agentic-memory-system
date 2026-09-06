"""A deterministic synthetic corpus for measuring retrieval, built through the real write path.

**Why synthetic rather than mined from real history.** Nothing here asks an LLM to answer
from delivered context — the question is only whether a deterministic algorithm ranks the
right node first — so pretraining leakage, the usual reason to invent a subject, is
irrelevant. The reason that does apply is *controllable vocabulary overlap*: a synthetic
corpus lets you write a query and its gold node together and construct the hard cases
(paraphrase, cross-goal facet collision, N-hop chains) deliberately, instead of hoping
real history happens to contain them in useful proportions.

Two halves, and the split is the point:

- a **labelled core**, hand-written, where every query's correct answer is known by
  construction and each of the five query categories is represented on purpose;
- **seeded filler**, generated, which contributes volume and competing structure so the
  core's numbers are not measured against an empty graph.

Everything is built with ``AgentSurface``, not raw store writes, so the corpus exercises
goal-first anchoring, atomic capture and facet governance exactly as production does. It
is fully deterministic: fixed text, fixed ordering, a seeded RNG for the filler only.
"""

import contextlib
import random
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from agentic_memory_system.agent_surface import AgentSurface
from agentic_memory_system.schema import Event, EventType
from agentic_memory_system.storage import MemoryStore

# Query categories, each measuring something different about the pipeline.
EXACT = "exact-facet-match"      # lexically close to the facet label: baseline sanity
PARAPHRASE = "paraphrase"        # same meaning, different words: hashed-BoW's weak spot
MULTI_HOP = "multi-hop"          # answer sits N hops from the goal via DEPENDS_ON
CROSS_GOAL = "cross-goal-noise"  # a facet term is shared with an unrelated goal
CONTRADICTION = "contradiction"  # answer is flagged: should rank faintly, not vanish
QUALITY = "quality-over-structure"  # three structurally identical answers; only trust and
                                    # age separate them, so it is the one category the
                                    # quality blend has to earn its weights on

CATEGORIES = (EXACT, PARAPHRASE, MULTI_HOP, CROSS_GOAL, CONTRADICTION, QUALITY)


@dataclass
class Query:
    """One labelled question. ``gold`` is correct by construction, not by judgement."""

    text: str
    category: str
    scope: str          # the change whose goal it is asked from
    gold: str           # key of the node that should rank first
    gold_facet: str = ""  # the facet stage 1 must find, where the category has one
    noise: tuple[str, ...] = ()  # nodes whose presence in the top-k is measurable noise
    expect: str = "first"  # "first", or "demoted" where ranking it first is the failure


@dataclass
class Corpus:
    """A built store plus the labels needed to score it."""

    store: MemoryStore
    goals: dict[str, str] = field(default_factory=dict)   # scope key -> goal node id
    nodes: dict[str, str] = field(default_factory=dict)   # node key  -> node id
    facets: dict[str, str] = field(default_factory=dict)  # facet label -> facet node id
    queries: list[Query] = field(default_factory=list)


# The labelled core. Facet labels are deliberately lexically distinct from one another so
# facet governance mints each one rather than warning-and-skipping a near-match — a
# skipped facet would silently break a gold label rather than fail loudly.
_CORE: list[dict] = [
    {
        "scope": "billing",
        "goal": "Get invoice totals right for partial refunds",
        "nodes": [
            ("vat-rounding", "constraint", ["vat-rounding"],
             "VAT is rounded per line item and never on the invoice total, because summing "
             "rounded lines and rounding a summed total disagree by a cent often enough to "
             "fail reconciliation."),
            ("refund-window", "decision", ["refund-window"],
             "A refund is accepted for thirty days after delivery, not after purchase, "
             "because the delivery date is the one both sides can evidence."),
            ("credit-note", "decision", ["credit-note"],
             "A partial refund issues a credit note rather than editing the original "
             "invoice, because an issued invoice is an immutable legal document."),
        ],
        "edges": [("credit-note", "vat-rounding")],
    },
    {
        "scope": "auth",
        "goal": "Keep sessions safe without making people log in constantly",
        "nodes": [
            ("session-expiry", "decision", ["session-expiry"],
             "An idle session expires after two hours and an absolute session after "
             "sixteen, so a stolen cookie has a bounded life even on a machine in use."),
            ("token-rotation", "constraint", ["token-rotation"],
             "Refresh tokens rotate on every use and a reused token invalidates the whole "
             "family, which turns theft into a detectable event rather than a silent one."),
            ("password-reset", "issue", ["password-reset"],
             "The password reset link does not invalidate existing sessions, so recovering "
             "an account does not evict whoever is already in it."),
        ],
        "edges": [("token-rotation", "session-expiry")],
    },
    {
        # Multi-hop: the answer is deliberately three DEPENDS_ON hops from the goal, and
        # carries no facet of its own, so only structure can reach it.
        "scope": "shipping",
        "goal": "Quote delivery dates customers can rely on",
        "nodes": [
            ("carrier-choice", "decision", ["carrier-selection"],
             "Carrier is chosen per parcel by predicted arrival, not by cost, because a "
             "missed promised date costs more than the postage difference."),
            ("transit-model", "concept", [],
             "Predicted arrival comes from a per-lane transit model rather than the "
             "carrier's published service level, which is a target and not a measurement."),
            ("holiday-calendar", "constraint", [],
             "The transit model excludes destination public holidays, which is the single "
             "largest source of over-promised dates in the historical data."),
            ("promise-buffer", "decision", [],
             "One working day of buffer is added to every quoted date, absorbing the "
             "long tail the model cannot predict without becoming useless."),
        ],
        "edges": [
            ("carrier-choice", "transit-model"),
            ("transit-model", "holiday-calendar"),
            ("holiday-calendar", "promise-buffer"),
        ],
    },
    {
        # Cross-goal noise: 'retention' means something completely different here than in
        # `analytics` below, and both carry the same facet label on purpose.
        "scope": "storage",
        "goal": "Stop the event log growing without bound",
        "nodes": [
            ("log-retention", "decision", ["retention-policy"],
             "Raw request logs are kept for ninety days and then deleted, because nothing "
             "downstream reads them past a quarter and the storage bill is linear."),
            ("cold-archive", "decision", ["retention-policy"],
             "Anything kept past ninety days moves to cold object storage, where retrieval "
             "is slow and cheap rather than fast and expensive."),
        ],
        "edges": [],
    },
    {
        "scope": "analytics",
        "goal": "Understand why customers stop buying",
        "nodes": [
            ("customer-retention", "concept", ["retention-policy"],
             "Customer retention is measured by repeat purchase within ninety days, which "
             "is the interval where the cohort curve actually bends."),
            ("churn-signal", "decision", ["churn-signal"],
             "A support ticket followed by no purchase in thirty days is the earliest "
             "reliable churn signal we have found."),
        ],
        "edges": [],
    },
    {
        # Contradiction: the flagged node must still surface, faintly, per TrustTermPenalty.
        "scope": "caching",
        "goal": "Serve repeat reads without serving stale data",
        "nodes": [
            ("cache-ttl", "decision", ["cache-invalidation"],
             "Product pages are cached for five minutes, which is the longest staleness the "
             "merchandising team accepted for price changes."),
            ("cache-purge", "decision", ["cache-invalidation"],
             "A price change purges the product page immediately instead of waiting for the "
             "time-to-live, because a wrong price is not a staleness problem."),
        ],
        "edges": [],
        # cache-purge CONTRADICTS cache-ttl: flags cache-ttl for review.
        "contradicts": [("cache-purge", "cache-ttl")],
    },
]

_CORE.append({
    # Three answers to one question, deliberately indistinguishable by structure: same
    # facet, same type, each anchored to the goal by capture in the same way. Only the
    # quality terms can separate them, which makes this the category that says whether
    # beta and gamma earn their weights or merely dilute alpha.
    "scope": "pricing",
    "goal": "Apply the discount rules the finance team actually signed off",
    "nodes": [
        ("discount-current", "decision", ["volume-discount"],
         "A volume discount applies from the eleventh unit and is calculated on the whole "
         "order, not on the units past the tenth, which is what the signed-off pricing "
         "sheet says and what finance reconciles against."),
        ("discount-stale", "decision", ["volume-discount"],
         "A volume discount applies from the eleventh unit and is calculated only on the "
         "units past the tenth, which is how the old spreadsheet did it."),
        ("discount-disputed", "decision", ["volume-discount"],
         "A volume discount applies from the sixth unit, at the rate agreed verbally with "
         "the enterprise team."),
    ],
    "edges": [],
    # discount-stale is OLD but uncontested; discount-disputed is fresh but contradicted.
    "age_days": {"discount-stale": 45},
    "erode_trust": {"discount-disputed": 0.6},
})

_QUERIES = [
    Query("vat rounding", EXACT, "billing", "vat-rounding", "vat-rounding"),
    Query("credit note", EXACT, "billing", "credit-note", "credit-note"),
    Query("session expiry", EXACT, "auth", "session-expiry", "session-expiry"),
    Query("token rotation", EXACT, "auth", "token-rotation", "token-rotation"),
    Query("cache invalidation", EXACT, "caching", "cache-purge", "cache-invalidation"),

    Query("how long does a login last", PARAPHRASE, "auth", "session-expiry", "session-expiry"),
    Query("giving money back to a customer", PARAPHRASE, "billing", "refund-window", "refund-window"),
    Query("do we round tax per line or per bill", PARAPHRASE, "billing", "vat-rounding", "vat-rounding"),
    Query("stopping someone reusing a stolen credential", PARAPHRASE, "auth", "token-rotation", "token-rotation"),
    Query("how long do we keep old logs around", PARAPHRASE, "storage", "log-retention", "retention-policy"),

    Query("carrier selection", MULTI_HOP, "shipping", "promise-buffer", "carrier-selection"),
    Query("carrier selection", MULTI_HOP, "shipping", "holiday-calendar", "carrier-selection"),

    # The gold node is in `storage`; `analytics` shares the facet and must not win.
    Query("retention policy", CROSS_GOAL, "storage", "log-retention", "retention-policy",
          noise=("customer-retention",)),
    Query("retention policy", CROSS_GOAL, "analytics", "customer-retention", "retention-policy",
          noise=("log-retention", "cold-archive")),

    # The gold node here is the FLAGGED one, and ranking it first is the bug, not the
    # goal: TrustTermPenalty's documented job is to keep a disputed node reachable and
    # demoted. Scored as "present but not first" — MRR would reward exactly the failure.
    Query("how long is a product page cached", CONTRADICTION, "caching", "cache-ttl",
          "cache-invalidation", expect="demoted"),

    Query("discount policy", QUALITY, "pricing", "discount-current", "volume-discount",
          noise=("discount-stale", "discount-disputed")),
    Query("how is a volume discount worked out", QUALITY, "pricing", "discount-current",
          "volume-discount", noise=("discount-stale", "discount-disputed")),
]

_FILLER_FACETS = [
    "queue-backpressure", "schema-migration", "rate-limiting", "feature-flagging",
    "audit-logging", "image-resizing", "webhook-delivery", "search-indexing",
]
_FILLER_TYPES = ["decision", "constraint", "concept", "issue", "invariant"]


def _age(store: MemoryStore, node_id: str, days: int) -> None:
    """Back-date a node, so the recency term has something to measure.

    A fixture concern, done with SQL rather than through the surface: nothing in the
    agent vocabulary can change when a node was created, and nothing should be able to.
    Without it every node in a generated corpus is the same age and gamma cannot possibly
    discriminate — which is not evidence that recency is worthless, only that the corpus
    never asked it a question.
    """
    when = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    with store._conn:
        store._conn.execute("UPDATE nodes SET created_at = ? WHERE id = ?", (when, node_id))


def _erode_trust(store: MemoryStore, node_id: str, amount: float) -> None:
    """Lower a node's trust by journalling a contradiction and folding it.

    Two things this makes visible. Trust is clamped to [0, 1] and starts at 1.0, so it can
    only ever move DOWN — a CONFIRMED event on an uncontested node is a no-op. And the
    fold is lazy: ``flag_contradicted`` journals without recomputing, so a corpus that
    only used the agent surface would leave every node at exactly 1.0 and the beta term
    a constant. The explicit recompute here is the same call the GUI's per-node button
    makes; it is what the (unbuilt) evaluator batch would do in bulk.
    """
    store.append_event(Event(
        id=str(uuid.uuid4()),
        node_id=node_id,
        type=EventType.contradiction_raised,
        weight=amount,
        polarity=-1,
        source="corpus",
        reason="corpus fixture: a node the project has argued with",
        created_at=datetime.now(timezone.utc),
    ))
    store.recompute_trust(node_id)


@contextlib.contextmanager
def _deterministic_ids(seed: int):
    """Mint node ids from a seeded generator for the duration of the build.

    Retrieval breaks score ties by id, so a corpus built with fresh uuid4s ranks tied
    nodes differently on every build and the benchmark's numbers jitter for reasons that
    have nothing to do with the code under test. A benchmark that cannot be rebuilt
    byte-identically is measuring its own randomness.
    """
    rng = random.Random(seed)
    original = uuid.uuid4
    uuid.uuid4 = lambda: uuid.UUID(int=rng.getrandbits(128), version=4)
    try:
        yield
    finally:
        uuid.uuid4 = original


def build(db_path, filler_scopes: int = 18, seed: int = 20260906) -> Corpus:
    """Build the corpus at ``db_path`` and return it with its labels.

    ``filler_scopes`` adds volume and competing structure; the labelled core is fixed, so
    growing the filler changes how hard the questions are without changing what is asked.
    """
    store = MemoryStore(db_path, auto_sync=False)
    surface = AgentSurface(store)
    corpus = Corpus(store=store)

    with _deterministic_ids(seed):
        _populate(surface, store, corpus, filler_scopes, seed)
    return corpus


def _populate(surface, store, corpus, filler_scopes: int, seed: int) -> None:
    for block in _CORE:
        goal = surface.create_change(block["scope"], block["goal"])["goal_node_id"]
        corpus.goals[block["scope"]] = goal
        for key, type_, facets, body in block["nodes"]:
            result = surface.capture_artifact(body, type_, goal, facets=list(facets))
            assert not result.get("facet_warnings"), (
                f"facet governance skipped a label for {key!r}: {result['facet_warnings']} — "
                "a skipped facet silently breaks a gold label, so the corpus must not have "
                "near-duplicate facet names"
            )
            corpus.nodes[key] = result["node_id"]
        for source, target in block.get("edges", []):
            surface.link(corpus.nodes[source], corpus.nodes[target], "DEPENDS_ON")
        for source, target in block.get("contradicts", []):
            surface.link(corpus.nodes[source], corpus.nodes[target], "CONTRADICTS")

    for block in _CORE:
        for key, days in block.get("age_days", {}).items():
            _age(store, corpus.nodes[key], days)
        for key, amount in block.get("erode_trust", {}).items():
            _erode_trust(store, corpus.nodes[key], amount)

    # Cross-scope dependencies. Without them every scope is an island, PPR reaches only
    # the handful of nodes under one goal, and recall@k is 1.00 by construction rather
    # than by merit — the harness's first run said exactly that about itself. Real graphs
    # have cross-change edges; so does this one now.
    for source, target in [
        ("credit-note", "session-expiry"),      # billing depends on who is logged in
        ("refund-window", "carrier-choice"),    # refund clock starts at delivery
        ("cache-ttl", "log-retention"),         # caching and storage share a lifecycle
        ("churn-signal", "refund-window"),      # churn is read off refund behaviour
        ("password-reset", "cache-purge"),      # a reset must purge cached pages
        ("promise-buffer", "customer-retention"),
        ("cold-archive", "transit-model"),
        ("token-rotation", "credit-note"),
    ]:
        surface.link(corpus.nodes[source], corpus.nodes[target], "DEPENDS_ON")

    rng = random.Random(seed)
    for index in range(filler_scopes):
        scope = f"filler-{index:02d}"
        goal = surface.create_change(scope, f"Filler scope {index} for corpus volume")["goal_node_id"]
        corpus.goals[scope] = goal
        previous = None
        for position in range(rng.randint(3, 6)):
            facet = _FILLER_FACETS[(index + position) % len(_FILLER_FACETS)]
            body = (
                f"Filler decision {index}.{position} about {facet.replace('-', ' ')}: "
                f"the {facet.split('-')[0]} path is handled by the {facet.split('-')[-1]} "
                f"component, which is configured per environment."
            )
            node_id = surface.capture_artifact(
                body, _FILLER_TYPES[(index + position) % len(_FILLER_TYPES)], goal,
                facets=[facet],
            )["node_id"]
            key = f"{scope}-{position}"
            corpus.nodes[key] = node_id
            _age(store, node_id, rng.randint(0, 60))
            # A third of the filler has been argued with at some point, so the trust term
            # has a gradient across the corpus rather than a single outlier.
            if rng.random() < 0.33:
                _erode_trust(store, node_id, round(rng.uniform(0.2, 0.7), 2))
            if previous is not None and rng.random() < 0.6:
                surface.link(node_id, previous, "DEPENDS_ON")
            previous = node_id

    core_keys = [key for key in corpus.nodes if not key.startswith("filler-")]
    for index in range(filler_scopes):
        # Every third filler scope hangs off a core node, so the labelled queries compete
        # with material they did not author.
        if index % 3 == 0:
            surface.link(
                corpus.nodes[f"filler-{index:02d}-0"],
                corpus.nodes[core_keys[index % len(core_keys)]],
                "DEPENDS_ON",
            )

    for facet_id, path in store._conn.execute(
        "SELECT id, path FROM nodes WHERE type = 'facet_value'"
    ).fetchall():
        corpus.facets[path.rsplit("/", 1)[-1]] = facet_id

    corpus.queries = list(_QUERIES)
