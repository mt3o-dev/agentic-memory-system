"""HTTP API + static host for the human GUI (MT3-26).

The human counterpart to ``agent_surface.py``: where the human-in-the-loop
checkpoints happen — staleness/contradiction review (MT3-23), tier promotion with the
mandatory lifetime gate (MT3-18), change liveness and sweeps. Two deliberate contrasts
with the agent surface:

- Humans get the *privileged* operations (clear flag, set tier, recompute trust,
  activate/deactivate, sweep) — every one journaled as an event, so a manual override
  never breaks the derived-state guarantees (MT3-26 open question, resolved).
- Humans see the *mechanism*: scores, weights, and the journal are shown, where the
  agent read path deliberately hides them (MT3-20 Pass 3).

Run with ``uv run agentic-memory-gui`` (serves ``gui/dist`` + JSON API on
127.0.0.1:8765; store from ``MEMORY_DB_PATH``). Local, single-user, no auth —
inspect-after-the-fact rather than real-time sync.
"""

import os
import platform
import re
import sys
from contextlib import asynccontextmanager
from importlib import metadata as importlib_metadata
from pathlib import Path

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from . import locking, sync
from .agent_surface import AgentSurface, AgentSurfaceError
from .evaluator import LLMEvaluator
from .resolver import RulesResolver
from .schema import Event, EventType, Node, NodeType, Tier
from .storage import MemoryStore

_DIST = Path(__file__).resolve().parents[2] / "gui" / "dist"

_ANCHOR_TYPES = ("slice", "facet_value")


# Body is Markdown; previews are short plaintext, so strip the common markers rather
# than rendering. A lightweight regex is enough here — the detail view does the real
# rendering client-side with `marked`.
_MD_LINK_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")  # [text](url) -> text
_MD_STRIP_RE = re.compile(
    r"^#{1,6}\s+|"  # headings
    r"[*_`~]{1,3}|"  # emphasis/code markers
    r"^\s*[-*+]\s+|"  # bullet markers
    r"^\s*\d+\.\s+|"  # ordered list markers
    r"^\s*>\s?",  # blockquote markers
    re.MULTILINE,
)


def _preview(body: str, limit: int = 160) -> str:
    stripped = _MD_LINK_RE.sub(r"\1", body)
    stripped = _MD_STRIP_RE.sub("", stripped)
    flat = " ".join(stripped.split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


def _node_summary(node: Node) -> dict:
    return {
        "id": node.id,
        "type": node.type.value,
        "tier": node.tier.value,
        "path": node.path,
        "preview": _preview(node.body),
        "needs_review": node.needs_review,
        "archived": node.archived,
        "trust_weight": node.trust_weight,
        "retrieval_weight": node.retrieval_weight,
        "created_at": node.created_at.isoformat() if node.created_at else None,
    }


def _close_store_on_shutdown(store: MemoryStore):
    """A lifespan that closes the store when the server stops, and only then."""

    @asynccontextmanager
    async def lifespan(_app):
        yield
        store.close()

    return lifespan


class RefreshDumpAfterWrite(BaseHTTPMiddleware):
    """Publish the tracked dump after any request that wrote something.

    The GUI holds one store for the life of the server and never calls ``close()``, which
    is where ``auto_dump`` normally refreshes ``context/memory-graph.dump``. So every
    human ruling made here — a cleared flag, a tier promotion, a ratified entity — sat in
    the gitignored database and NOT in the tracked file until some other command happened
    to open and close the store. Those rulings are the one kind of knowledge in the graph
    that cannot be re-derived from anything, so leaving them un-published until a
    coincidence is the wrong default.

    It hangs off middleware rather than the fifteen mutating endpoints because that is a
    place a sixteenth cannot forget to call. The dirty check is the same
    ``total_changes`` watermark ``close()`` uses: rows written on this connection,
    ignoring reads, so a GET or a failed write publishes nothing.
    """

    def __init__(self, app, store: MemoryStore) -> None:
        super().__init__(app)
        self._store = store
        self._written = store._conn.total_changes

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        if request.method == "GET":
            return response
        total = self._store._conn.total_changes
        if total > self._written and response.status_code < 400:
            self._written = total
            # A failed dump must never fail the request that succeeded: the write is
            # already committed, and the next open re-publishes it either way.
            note = sync.auto_dump(self._store, self._store._db_path, changed=True)
            if note:
                self._store.sync_notes.append(note)
        return response


def create_app(store: MemoryStore, evaluator: LLMEvaluator | None = None) -> Starlette:
    resolver = RulesResolver()
    # The MT3-27 evaluator: LLM-backed when ANTHROPIC_API_KEY is configured,
    # deterministic template guidance otherwise. Injectable so tests stay offline.
    evaluator = evaluator if evaluator is not None else LLMEvaluator.from_env()
    # Creation and edge-adding reuse the agent surface deliberately: the GUI gets the
    # same goal-first, atomicity, and facet-governance enforcement — it is a human
    # front-end on the one write path, not a second unguarded one.
    surface = AgentSurface(store)

    def _get_node_or_404(node_id: str) -> Node | JSONResponse:
        node = store.read_node(node_id)
        if node is None:
            return JSONResponse({"error": f"no node {node_id!r}"}, status_code=404)
        return node

    async def health(request: Request) -> JSONResponse:
        c = store._conn
        counts = {
            "nodes": c.execute(
                "SELECT COUNT(*) FROM nodes WHERE type NOT IN (?, ?)", _ANCHOR_TYPES
            ).fetchone()[0],
            "flagged": c.execute(
                "SELECT COUNT(*) FROM nodes WHERE needs_review = 1 AND archived = 0"
            ).fetchone()[0],
            "archived": c.execute(
                "SELECT COUNT(*) FROM nodes WHERE archived = 1"
            ).fetchone()[0],
            "edgeless": c.execute(
                "SELECT COUNT(*) FROM nodes n WHERE n.type NOT IN (?, ?) AND n.archived = 0 "
                "AND NOT EXISTS (SELECT 1 FROM edges e WHERE e.source_id = n.id OR e.target_id = n.id)",
                _ANCHOR_TYPES,
            ).fetchone()[0],
            "facets": c.execute(
                "SELECT COUNT(*) FROM nodes WHERE type = 'facet_value'"
            ).fetchone()[0],
            "events": c.execute("SELECT COUNT(*) FROM events").fetchone()[0],
            "active_changes": len(store._active_slice_ids()),
        }
        statuses = store._entity_statuses().values()
        counts["entities"] = sum(1 for s in statuses if s != "retired")
        # The domain-review backlog: entities an agent proposed that nobody has ruled on.
        # Surfaced next to the flag count because it is the same kind of debt.
        counts["entities_proposed"] = sum(1 for s in statuses if s == "proposed")
        return JSONResponse(counts)

    async def info(request: Request) -> JSONResponse:
        try:
            version = importlib_metadata.version("agentic-memory-system")
        except importlib_metadata.PackageNotFoundError:
            version = None
        db_path = None
        for _, name, file in store._conn.execute("PRAGMA database_list"):
            if name == "main" and file:
                db_path = file
                break
        return JSONResponse(
            {
                "project": "agentic-memory-system",
                "version": version,
                "cwd": os.getcwd(),
                "db_path": db_path,
                "python_version": platform.python_version(),
                "pid": os.getpid(),
                "argv": sys.argv,
            }
        )

    async def list_nodes(request: Request) -> JSONResponse:
        q = request.query_params
        sql = "SELECT id FROM nodes WHERE 1=1"
        args: list = []
        if q.get("type"):
            sql += " AND type = ?"
            args.append(q["type"])
        else:
            sql += " AND type NOT IN (?, ?)"
            args.extend(_ANCHOR_TYPES)
        if q.get("tier"):
            sql += " AND tier = ?"
            args.append(q["tier"])
        if q.get("flagged") == "1":
            sql += " AND needs_review = 1"
        if q.get("archived") != "1":
            sql += " AND archived = 0"
        if q.get("q"):
            sql += " AND (body LIKE ? OR path LIKE ? OR id LIKE ?)"
            needle = f"%{q['q']}%"
            args.extend([needle, needle, needle])
        sql += " ORDER BY path, id LIMIT 500"
        rows = store._conn.execute(sql, args).fetchall()
        return JSONResponse([_node_summary(store.read_node(r[0])) for r in rows])

    async def node_detail(request: Request) -> JSONResponse:
        node = _get_node_or_404(request.path_params["node_id"])
        if isinstance(node, JSONResponse):
            return node

        def _edge_rows(sql: str) -> list[dict]:
            out = []
            for other_id, etype, created in store._conn.execute(sql, (node.id,)):
                other = store.read_node(other_id)
                if other is None:
                    continue
                out.append(
                    {"edge_type": etype, "created_at": created, "node": _node_summary(other)}
                )
            return out

        outgoing = _edge_rows(
            "SELECT target_id, type, created_at FROM edges WHERE source_id = ? ORDER BY type, target_id"
        )
        incoming = _edge_rows(
            "SELECT source_id, type, created_at FROM edges WHERE target_id = ? ORDER BY type, source_id"
        )
        events = [
            {
                "id": e.id,
                "type": e.type.value,
                "weight": e.weight,
                "polarity": e.polarity,
                "source": e.source,
                "reason": e.reason,
                "created_at": e.created_at.isoformat() if e.created_at else None,
            }
            for e in store.read_events(node.id)
        ]
        verdict = (
            resolver.resolve(node, store.read_events(node.id)).value
            if node.needs_review
            else None
        )
        return JSONResponse(
            {
                "node": {**_node_summary(node), "body": node.body},
                "outgoing": outgoing,
                "incoming": incoming,
                "events": events,
                "resolver_verdict": verdict,
            }
        )

    async def review_queue(request: Request) -> JSONResponse:
        rows = store._conn.execute(
            "SELECT id FROM nodes WHERE needs_review = 1 AND archived = 0 ORDER BY path, id"
        ).fetchall()
        queue = []
        for (node_id,) in rows:
            node = store.read_node(node_id)
            events = store.read_events(node_id)
            queue.append(
                {
                    **_node_summary(node),
                    "severity": store._latest_severity(node_id),
                    "resolver_verdict": resolver.resolve(node, events).value,
                }
            )
        return JSONResponse(queue)

    def _review_context(node: Node) -> dict:
        """Everything the evaluator (and the wizard) needs to judge one flagged node."""
        events = store.read_events(node.id)
        contradictors = []
        for (other_id,) in store._conn.execute(
            "SELECT CASE WHEN source_id = ? THEN target_id ELSE source_id END "
            "FROM edges WHERE type = 'CONTRADICTS' AND (source_id = ? OR target_id = ?)",
            (node.id, node.id, node.id),
        ).fetchall():
            other = store.read_node(other_id)
            if other is not None:
                contradictors.append({**_node_summary(other), "body": other.body})
        dependents = []
        for (dep_id,) in store._conn.execute(
            "SELECT source_id FROM edges WHERE type = 'DEPENDS_ON' AND target_id = ? LIMIT 10",
            (node.id,),
        ).fetchall():
            dep = store.read_node(dep_id)
            if dep is not None:
                dependents.append(_node_summary(dep))
        return {
            "node": {**_node_summary(node), "body": node.body},
            "contradictors": contradictors,
            "dependents": dependents,
            "severity": store._latest_severity(node.id),
            "rules_verdict": resolver.resolve(node, events).value,
            "events": [
                {
                    "type": e.type.value,
                    "weight": e.weight,
                    "polarity": e.polarity,
                    "source": e.source,
                    "reason": e.reason,
                    "created_at": e.created_at.isoformat() if e.created_at else None,
                }
                for e in events[-20:]
            ],
        }

    async def review_guidance(request: Request) -> JSONResponse:
        node = _get_node_or_404(request.path_params["node_id"])
        if isinstance(node, JSONResponse):
            return node
        if not node.needs_review:
            return JSONResponse({"error": "node is not flagged"}, status_code=400)
        context = _review_context(node)
        events = store.read_events(node.id)
        guidance, fresh = await evaluator.guidance(node, events, context)
        if fresh:
            # The design intent (MT3-27): the evaluator writes its verdicts to the
            # journal. Cache-gated by the evaluator, so a page refresh can't spam.
            store.append_event(
                Event(
                    node_id=node.id,
                    type=EventType.manual_review,
                    weight=0.0,
                    polarity=1,
                    source="evaluator",
                    reason=(
                        f"evaluator verdict: {guidance.recommended_action} — "
                        f"{guidance.recommended_reason}"
                    ),
                )
            )
        return JSONResponse({**context, "guidance": guidance.model_dump()})

    async def review_resolve(request: Request) -> JSONResponse:
        node = _get_node_or_404(request.path_params["node_id"])
        if isinstance(node, JSONResponse):
            return node
        if not node.needs_review:
            return JSONResponse({"error": "node is not flagged"}, status_code=400)
        p = await request.json()
        action = str(p.get("action", ""))
        if action not in ("still_valid", "superseded", "wrong", "needs_correction", "defer"):
            return JSONResponse({"error": f"unknown action {action!r}"}, status_code=400)
        new_body = p.get("new_body")
        if action == "needs_correction" and not str(new_body or "").strip():
            return JSONResponse(
                {"error": "needs_correction requires non-empty new_body"}, status_code=400
            )
        replacement_id = p.get("replacement_id") or None
        if replacement_id and store.read_node(replacement_id) is None:
            return JSONResponse(
                {"error": f"replacement node {replacement_id!r} not found"}, status_code=400
            )
        # Validate the optional tier add-on BEFORE mutating anything, so a rejected
        # lifetime promotion leaves the resolution unapplied too.
        tier = None
        if p.get("tier"):
            try:
                tier = Tier(str(p["tier"]))
            except ValueError:
                return JSONResponse({"error": "invalid tier"}, status_code=400)
            if tier is Tier.lifetime and not p.get("tier_confirmed"):
                return JSONResponse(
                    {"error": "lifetime promotion requires explicit confirmation"},
                    status_code=400,
                )
        recommended = str(p.get("recommended_action", "") or "(none)")
        reason = (
            f"guided resolve: recommended={recommended}, chose={action}"
            + (f": {p['reason']}" if p.get("reason") else "")
        )
        result = store.resolve_review(
            node.id,
            action=action,
            source="gui-guided",
            reason=reason,
            new_body=new_body,
            replacement_id=replacement_id,
            recompute=bool(p.get("recompute_trust")),
        )
        if tier is not None:
            store.set_tier(node.id, tier, source="gui-guided", reason="guided review tier move")
        next_row = store._conn.execute(
            "SELECT id FROM nodes WHERE needs_review = 1 AND archived = 0 "
            "AND type NOT IN ('slice','facet_value') AND id != ? ORDER BY path, id LIMIT 1",
            (node.id,),
        ).fetchone()
        return JSONResponse(
            {
                "ok": True,
                "action": result["action"],
                "trust_weight": result["trust_weight"],
                "next_id": next_row[0] if next_row else None,
            }
        )

    async def list_changes(request: Request) -> JSONResponse:
        active = set(store._active_slice_ids())
        rows = store._conn.execute(
            "SELECT id FROM nodes WHERE type = 'slice' ORDER BY path"
        ).fetchall()
        changes = []
        for (slice_id,) in rows:
            node = store.read_node(slice_id)
            scoped = store._conn.execute(
                "SELECT COUNT(*) FROM edges WHERE source_id = ? AND type = 'SCOPED_TO'",
                (slice_id,),
            ).fetchone()[0]
            changes.append(
                {**_node_summary(node), "active": slice_id in active, "scoped_nodes": scoped}
            )
        return JSONResponse(changes)

    async def list_goals(request: Request) -> JSONResponse:
        rows = store._conn.execute(
            "SELECT id FROM nodes WHERE type = 'goal' AND archived = 0 ORDER BY path"
        ).fetchall()
        return JSONResponse([_node_summary(store.read_node(r[0])) for r in rows])

    async def recall_preview(request: Request) -> JSONResponse:
        goal_id = request.query_params.get("goal", "")
        query = request.query_params.get("query", "")
        goal = store.read_node(goal_id)
        if goal is None or goal.type is not NodeType.goal:
            return JSONResponse({"error": "pick a goal node"}, status_code=400)
        ranked = store.recall_multi(query, goal_id)
        # Humans see the scores — the mechanism agents deliberately don't get.
        return JSONResponse(
            [{**_node_summary(node), "score": score} for node, score in ranked]
        )

    # --- editing (v1.5): thin wrappers, every write journaled ---

    async def create_artifact(request: Request) -> JSONResponse:
        p = await request.json()
        try:
            result = surface.capture_artifact(
                content=str(p.get("content", "")),
                type=str(p.get("type", "")),
                goal_ref=str(p.get("goal_ref", "")),
                facets=[f.strip() for f in p.get("facets", []) if f.strip()],
                edges=p.get("edges") or [],
                tier=str(p.get("tier", "short-term")),
            )
        except AgentSurfaceError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return JSONResponse(result)

    async def create_edge(request: Request) -> JSONResponse:
        p = await request.json()
        try:
            result = surface.link(
                str(p.get("source", "")),
                str(p.get("target", "")),
                str(p.get("type", "")),
                reason=str(p.get("reason", "linked via GUI")),
            )
        except AgentSurfaceError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return JSONResponse(result)

    async def edit_body(request: Request) -> JSONResponse:
        node = _get_node_or_404(request.path_params["node_id"])
        if isinstance(node, JSONResponse):
            return node
        p = await request.json()
        body = str(p.get("body", ""))
        if not body.strip():
            return JSONResponse({"error": "body must be non-empty"}, status_code=400)
        store.edit_body(node.id, body, source="gui", reason=str(p.get("reason", "")))
        return JSONResponse({"ok": True})

    async def set_weights(request: Request) -> JSONResponse:
        node = _get_node_or_404(request.path_params["node_id"])
        if isinstance(node, JSONResponse):
            return node
        p = await request.json()
        try:
            trust = None if p.get("trust_weight") is None else float(p["trust_weight"])
            retrieval = (
                None if p.get("retrieval_weight") is None else float(p["retrieval_weight"])
            )
            store.set_weights(
                node.id,
                trust_weight=trust,
                retrieval_weight=retrieval,
                source="gui",
                reason=str(p.get("reason", "")),
            )
        except (ValueError, TypeError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return JSONResponse({"ok": True})

    async def set_archived(request: Request) -> JSONResponse:
        node = _get_node_or_404(request.path_params["node_id"])
        if isinstance(node, JSONResponse):
            return node
        p = await request.json()
        store.set_archived(
            node.id,
            bool(p.get("archived")),
            source="gui",
            reason=str(p.get("reason", "")),
        )
        return JSONResponse({"ok": True})

    # --- privileged writes: every one journals an event ---

    async def clear_flag(request: Request) -> JSONResponse:
        node = _get_node_or_404(request.path_params["node_id"])
        if isinstance(node, JSONResponse):
            return node
        payload = await request.json()
        store.clear_contradiction(
            node.id, source="gui", reason=str(payload.get("reason", "cleared via GUI"))
        )
        return JSONResponse({"ok": True})

    async def set_tier(request: Request) -> JSONResponse:
        node = _get_node_or_404(request.path_params["node_id"])
        if isinstance(node, JSONResponse):
            return node
        payload = await request.json()
        try:
            tier = Tier(str(payload.get("tier", "")))
        except ValueError:
            return JSONResponse({"error": "invalid tier"}, status_code=400)
        # The mandatory lifetime-promotion checkpoint (MT3-18): the client asks the
        # human to confirm and must say so explicitly.
        if tier is Tier.lifetime and not payload.get("confirmed"):
            return JSONResponse(
                {"error": "lifetime promotion requires explicit confirmation"},
                status_code=400,
            )
        store.set_tier(
            node.id, tier, source="gui", reason=str(payload.get("reason", ""))
        )
        return JSONResponse({"ok": True})

    async def recompute_trust(request: Request) -> JSONResponse:
        node = _get_node_or_404(request.path_params["node_id"])
        if isinstance(node, JSONResponse):
            return node
        trust = store.recompute_trust(node.id)
        return JSONResponse({"ok": True, "trust_weight": trust})

    async def set_change_active(request: Request) -> JSONResponse:
        node = _get_node_or_404(request.path_params["node_id"])
        if isinstance(node, JSONResponse):
            return node
        if node.type is not NodeType.slice:
            return JSONResponse({"error": "not a change node"}, status_code=400)
        if request.url.path.endswith("/activate"):
            store.activate_slice(node.id, source="gui", reason="activated via GUI")
        else:
            store.deactivate_slice(node.id, source="gui", reason="deactivated via GUI")
        return JSONResponse({"ok": True})

    async def run_sweep(request: Request) -> JSONResponse:
        changed = store.sweep(source="gui", reason="sweep via GUI")
        return JSONResponse({"ok": True, "changed": changed})

    # --- domain model: the human ratification gate for entities (MT3-29/30) ---

    async def list_entities(request: Request) -> JSONResponse:
        include_retired = request.query_params.get("retired") == "1"
        out = []
        for node, status in store.entities(include_retired=include_retired):
            attached = store._conn.execute(
                "SELECT COUNT(*) FROM edges e JOIN nodes n ON n.id = e.source_id "
                "AND n.archived = 0 WHERE e.target_id = ? AND e.type = 'ABOUT'",
                (node.id,),
            ).fetchone()[0]
            proposal = store._conn.execute(
                "SELECT reason, created_at FROM events WHERE node_id = ? AND type = ? "
                "ORDER BY created_at ASC, id ASC LIMIT 1",
                (node.id, EventType.entity_proposed.value),
            ).fetchone()
            out.append(
                {
                    **_node_summary(node),
                    "body": node.body,
                    "status": status,
                    "attached": attached,
                    # The provenance the agent recorded at proposal time — a file:line for
                    # brownfield extraction, the user's words for greenfield elicitation.
                    # This is what the human is ruling on, so it must reach the wire.
                    "provenance": proposal[0] if proposal else None,
                    "proposed_at": proposal[1] if proposal else None,
                }
            )
        return JSONResponse(out)

    async def entity_lifecycle(request: Request) -> JSONResponse:
        node = _get_node_or_404(request.path_params["node_id"])
        if isinstance(node, JSONResponse):
            return node
        payload = await request.json()
        confirming = request.url.path.endswith("/confirm")
        try:
            if confirming:
                store.confirm_entity(
                    node.id, source="gui", reason=str(payload.get("reason", "")) or
                    "confirmed as part of the domain model",
                )
            else:
                store.retire_entity(
                    node.id, source="gui", reason=str(payload.get("reason", "")) or
                    "retired from the domain model",
                )
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return JSONResponse({"ok": True, "status": store.entity_status(node.id)})

    # --- consolidation: detector is open, the commit is human-only ---

    async def consolidation_candidates(request: Request) -> JSONResponse:
        candidates = []
        for candidate in store.consolidation_candidates():
            instances = []
            for node_id in candidate["node_ids"]:
                instance = store.read_node(node_id)
                if instance is not None:
                    instances.append({**_node_summary(instance), "body": instance.body})
            candidates.append({**candidate, "instances": instances})
        return JSONResponse(candidates)

    async def run_consolidate(request: Request) -> JSONResponse:
        p = await request.json()
        instance_ids = [str(i) for i in (p.get("instance_ids") or [])]
        try:
            node_type = NodeType(str(p.get("type", "concept")))
        except ValueError:
            return JSONResponse({"error": "invalid type"}, status_code=400)
        try:
            tier = Tier(str(p.get("tier", "mid-term")))
        except ValueError:
            return JSONResponse({"error": "invalid tier"}, status_code=400)
        # Same gate as every other lifetime write on this surface (MT3-18): consolidation
        # straight to lifetime would let one click mint permanent memory.
        if tier is Tier.lifetime and not p.get("tier_confirmed"):
            return JSONResponse(
                {"error": "lifetime tier requires explicit confirmation"}, status_code=400
            )
        try:
            result = store.consolidate(
                instance_ids,
                str(p.get("content", "")),
                type=node_type,
                goal_id=p.get("goal_ref") or None,
                tier=tier,
                source="gui",
                reason=str(p.get("reason", "")),
            )
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return JSONResponse({"ok": True, **result})

    async def index(request: Request):
        index_file = _DIST / "index.html"
        if index_file.exists():
            return FileResponse(index_file)
        return JSONResponse(
            {"error": "GUI not built — run `npm install && npm run build` in gui/"},
            status_code=503,
        )

    routes = [
        Route("/api/health", health),
        Route("/api/info", info),
        Route("/api/nodes", list_nodes, methods=["GET"]),
        Route("/api/nodes", create_artifact, methods=["POST"]),
        Route("/api/edges", create_edge, methods=["POST"]),
        Route("/api/nodes/{node_id}", node_detail),
        Route("/api/nodes/{node_id}/body", edit_body, methods=["POST"]),
        Route("/api/nodes/{node_id}/weights", set_weights, methods=["POST"]),
        Route("/api/nodes/{node_id}/archived", set_archived, methods=["POST"]),
        Route("/api/nodes/{node_id}/clear-flag", clear_flag, methods=["POST"]),
        Route("/api/nodes/{node_id}/tier", set_tier, methods=["POST"]),
        Route("/api/nodes/{node_id}/recompute-trust", recompute_trust, methods=["POST"]),
        Route("/api/review", review_queue),
        Route("/api/review/{node_id}/guidance", review_guidance),
        Route("/api/review/{node_id}/resolve", review_resolve, methods=["POST"]),
        Route("/api/changes", list_changes),
        Route("/api/changes/{node_id}/activate", set_change_active, methods=["POST"]),
        Route("/api/changes/{node_id}/deactivate", set_change_active, methods=["POST"]),
        Route("/api/goals", list_goals),
        Route("/api/recall", recall_preview),
        Route("/api/sweep", run_sweep, methods=["POST"]),
        Route("/api/entities", list_entities),
        Route("/api/entities/{node_id}/confirm", entity_lifecycle, methods=["POST"]),
        Route("/api/entities/{node_id}/retire", entity_lifecycle, methods=["POST"]),
        Route("/api/consolidation/candidates", consolidation_candidates),
        Route("/api/consolidate", run_consolidate, methods=["POST"]),
        Route("/", index),
    ]
    if _DIST.exists():
        routes.append(Mount("/", app=StaticFiles(directory=_DIST), name="static"))
    return Starlette(
        routes=routes,
        middleware=[Middleware(RefreshDumpAfterWrite, store=store)],
        # Ctrl-C on the server is the ordinary way this process ends, and it is
        # the moment the shared file lock should be released and the dump made
        # final. A SIGKILL still skips it, which is what `dump_is_stale` heals
        # on the next open. (`lifespan`, not the removed `on_shutdown` kwarg.)
        lifespan=_close_store_on_shutdown(store),
    )


def main() -> None:
    import uvicorn

    db_path = os.environ.get("MEMORY_DB_PATH", "context/memory-graph.db")
    try:
        store = MemoryStore(db_path)
    except locking.StoreBusy as exc:
        raise SystemExit(f"error: {exc} — retry once it finishes")
    # The server holds the store open for its whole life, which is the shared half of
    # the file lock (locking.py): while the GUI runs, no process may rebuild the
    # database from the dump underneath it. A `git pull` that moves the dump ahead is
    # therefore picked up after the GUI is stopped, and every open in between says so.
    app = create_app(store)
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("MEMORY_GUI_PORT", "8765")))


if __name__ == "__main__":
    main()
