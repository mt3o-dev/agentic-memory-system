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
from pathlib import Path

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from .resolver import RulesResolver
from .schema import Node, NodeType, Tier
from .storage import MemoryStore

_DIST = Path(__file__).resolve().parents[2] / "gui" / "dist"

_ANCHOR_TYPES = ("slice", "facet_value")


def _preview(body: str, limit: int = 160) -> str:
    flat = " ".join(body.split())
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


def create_app(store: MemoryStore) -> Starlette:
    resolver = RulesResolver()

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
        return JSONResponse(counts)

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
            sql += " AND (body LIKE ? OR path LIKE ?)"
            needle = f"%{q['q']}%"
            args.extend([needle, needle])
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
        Route("/api/nodes", list_nodes),
        Route("/api/nodes/{node_id}", node_detail),
        Route("/api/nodes/{node_id}/clear-flag", clear_flag, methods=["POST"]),
        Route("/api/nodes/{node_id}/tier", set_tier, methods=["POST"]),
        Route("/api/nodes/{node_id}/recompute-trust", recompute_trust, methods=["POST"]),
        Route("/api/review", review_queue),
        Route("/api/changes", list_changes),
        Route("/api/changes/{node_id}/activate", set_change_active, methods=["POST"]),
        Route("/api/changes/{node_id}/deactivate", set_change_active, methods=["POST"]),
        Route("/api/goals", list_goals),
        Route("/api/recall", recall_preview),
        Route("/api/sweep", run_sweep, methods=["POST"]),
        Route("/", index),
    ]
    if _DIST.exists():
        routes.append(Mount("/", app=StaticFiles(directory=_DIST), name="static"))
    return Starlette(routes=routes)


def main() -> None:
    import uvicorn

    db_path = os.environ.get("MEMORY_DB_PATH", "context/memory-graph.db")
    app = create_app(MemoryStore(db_path))
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("MEMORY_GUI_PORT", "8765")))


if __name__ == "__main__":
    main()
