"""Measure retrieval per stage, because one blended number cannot attribute a regression.

``recall_multi`` is two independent stages, and a benchmark that treats it as one black
box will misattribute every change made to either:

    stage 1  seed discovery   query -> embedding -> cosine vs facet-value bodies ->
                              HAS_FACET expansion to member nodes
    stage 2  composition      goal-dominant PPR over the content graph, then
                              normalized mass x (a*retrieval + b*trust + c*recency)

Swapping the embedder can only move stage 1; retuning the weights or the edge policy can
only move stage 2. A single "recall went up 8%" cannot tell you which happened, so each
is measured alone and the end-to-end number is reported as the headline separately.

Stage 2 is isolated by holding stage 1 to the right answer: the seeds are the members of
the query's gold facet — what a perfect seed discovery would return — not the gold node
itself, which would make the measurement trivial. For the multi-hop queries that means
PPR still has three hops to travel, and for the cross-goal queries it means the seed set
deliberately spans two goals and goal dominance has to pick.

    uv run python eval/harness.py                 # the tables
    uv run python eval/harness.py --write <id>    # ...and append them to eval/results/

Nothing here needs production API that did not already exist: ``discover_seeds`` and
``recall_multi`` are both public, which was the point of the design.
"""

import argparse
import contextlib
import os
import sys
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.dirname(__file__))

import corpus as corpus_module  # noqa: E402
from agentic_memory_system import storage as storage_module  # noqa: E402

K = 5  # the top-k every @k metric uses


@contextlib.contextmanager
def _weights(alpha: float, beta: float, gamma: float):
    """Temporarily retune the quality blend, for the ablation matrix."""
    saved = (storage_module._ALPHA, storage_module._BETA, storage_module._GAMMA)
    storage_module._ALPHA, storage_module._BETA, storage_module._GAMMA = alpha, beta, gamma
    try:
        yield
    finally:
        storage_module._ALPHA, storage_module._BETA, storage_module._GAMMA = saved


@contextlib.contextmanager
def _perfect_stage_one(store, seeds: dict[str, float]):
    """Hold seed discovery to the right answer, so stage 2 is measured alone."""
    original = store.discover_seeds
    store.discover_seeds = lambda *args, **kwargs: dict(seeds)
    try:
        yield
    finally:
        store.discover_seeds = original


def _facets_of(store, node_id: str) -> set[str]:
    return {
        row[0]
        for row in store._conn.execute(
            "SELECT target_id FROM edges WHERE source_id = ? AND type = 'HAS_FACET'",
            (node_id,),
        )
    }


def _members_of(store, facet_id: str) -> list[str]:
    return [
        row[0]
        for row in store._conn.execute(
            "SELECT source_id FROM edges WHERE target_id = ? AND type = 'HAS_FACET'",
            (facet_id,),
        )
    ]


def _ranked(results, goal_id: str):
    """The ranking a caller actually reads: everything but the goal they asked from.

    ``recall_multi`` seeds the goal at weight 0.7, so the goal node itself comes back at
    rank 1 of every single query. Leaving it in caps MRR at 0.5 no matter how good the
    ranker is, and reports a perfect answer as mediocre — the goal is the question, not
    an answer to it.
    """
    return [(node, score) for node, score in results if node.id != goal_id]


def _rank_of(results, node_id: str) -> int | None:
    for position, (node, _score) in enumerate(results, start=1):
        if node.id == node_id:
            return position
    return None


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def stage_one(corpus) -> dict[str, dict[str, float]]:
    """facet_recall@k: does seed discovery reach the facet the answer is filed under?

    Measured through the public surface — ``discover_seeds`` returns member nodes, so the
    matched facet set is reconstructed from their HAS_FACET edges rather than by reaching
    into the ranking.
    """
    per_category: dict[str, list[float]] = {}
    for query in corpus.queries:
        if not query.gold_facet:
            continue
        gold_facet_id = corpus.facets[query.gold_facet]
        seeds = corpus.store.discover_seeds(query.text)
        reached = set()
        for seed_id in seeds:
            reached |= _facets_of(corpus.store, seed_id)
        per_category.setdefault(query.category, []).append(1.0 if gold_facet_id in reached else 0.0)
    return {
        category: {"facet_recall": _mean(hits), "n": len(hits)}
        for category, hits in per_category.items()
    }


def _score_queries(corpus, isolate_stage_two: bool) -> dict[str, dict[str, float]]:
    per_category: dict[str, dict[str, list[float]]] = {}
    for query in corpus.queries:
        goal = corpus.goals[query.scope]
        gold_id = corpus.nodes[query.gold]
        if isolate_stage_two:
            facet_id = corpus.facets[query.gold_facet]
            seeds = {member: 1.0 for member in _members_of(corpus.store, facet_id)}
            with _perfect_stage_one(corpus.store, seeds):
                results = _ranked(corpus.store.recall_multi(query.text, goal), goal)
        else:
            results = _ranked(corpus.store.recall_multi(query.text, goal), goal)

        rank = _rank_of(results, gold_id)
        bucket = per_category.setdefault(
            query.category, {"recall": [], "mrr": [], "focus": [], "noise": [], "success": []}
        )
        bucket["recall"].append(1.0 if rank is not None and rank <= K else 0.0)
        # `success` is the correctness column, and it is not always "rank 1". For the
        # contradiction category the right answer is present-but-demoted, so MRR scores
        # the failure as perfect — a weighting that disables the staleness penalty would
        # look like an improvement. That is the mistake the design warned about by name:
        # the penalty doing its job "is not a bug the harness should report as a miss".
        if query.expect == "demoted":
            bucket["success"].append(1.0 if rank is not None and rank > 1 else 0.0)
        else:
            bucket["success"].append(1.0 if rank == 1 else 0.0)
            bucket["mrr"].append(1.0 / rank if rank else 0.0)
        # focus: how little of the ranked list a caller reads past before the answer.
        # 1.0 when it is first, falling toward 0 as it sinks. Defined here rather than
        # borrowed: there is no token budget in a graph read, so Engram's version of the
        # idea has no clean analogue.
        bucket["focus"].append(
            1.0 - (rank - 1) / len(results) if rank and results else 0.0
        )
        if query.noise:
            noise_ids = {corpus.nodes[key] for key in query.noise}
            top = [node.id for node, _ in results[:K]]
            bucket["noise"].append(
                sum(1 for node_id in top if node_id in noise_ids) / max(1, len(top))
            )
    return {
        category: {
            "success": _mean(values["success"]),
            "recall@k": _mean(values["recall"]),
            "MRR": _mean(values["mrr"]) if values["mrr"] else float("nan"),
            "focus": _mean(values["focus"]),
            "noise": _mean(values["noise"]) if values["noise"] else float("nan"),
            "n": len(values["recall"]),
        }
        for category, values in per_category.items()
    }


def _table(title: str, rows: dict[str, dict[str, float]], columns: list[str]) -> str:
    lines = [f"### {title}", "", "| category | " + " | ".join(columns) + " | n |",
             "|---|" + "---|" * (len(columns) + 1)]
    for category in corpus_module.CATEGORIES:
        if category not in rows:
            continue
        values = rows[category]
        cells = []
        for column in columns:
            value = values.get(column, float("nan"))
            cells.append("—" if value != value else f"{value:.2f}")  # NaN check
        lines.append(f"| {category} | " + " | ".join(cells) + f" | {int(values['n'])} |")
    overall = {
        column: _mean([v[column] for v in rows.values() if v.get(column) == v.get(column)])
        for column in columns
    }
    lines.append("| **all** | " + " | ".join(f"**{overall[c]:.2f}**" for c in columns)
                 + f" | {sum(int(v['n']) for v in rows.values())} |")
    return "\n".join(lines) + "\n"


def report(corpus) -> str:
    parts = [
        _table("Stage 1 — seed discovery, isolated", stage_one(corpus), ["facet_recall"]),
        _table("Stage 2 — composition, with seed discovery held correct",
               _score_queries(corpus, isolate_stage_two=True),
               ["success", "recall@k", "MRR", "focus", "noise"]),
        _table("End to end — both stages as shipped (the headline)",
               _score_queries(corpus, isolate_stage_two=False),
               ["success", "recall@k", "MRR", "focus", "noise"]),
    ]
    ablations = [
        ("shipped defaults (a=0.5, b=0.3, c=0.2)", 0.5, 0.3, 0.2),
        ("structure only (a=1, b=0, c=0)", 1.0, 0.0, 0.0),
        ("trust only (a=0, b=1, c=0)", 0.0, 1.0, 0.0),
        ("recency only (a=0, b=0, c=1)", 0.0, 0.0, 1.0),
    ]
    lines = ["### Ablation — end-to-end, per category", "",
             "`success` is the correctness column; MRR aggregates only the categories "
             "whose right answer is rank 1. **β is not just the trust weight** — "
             "`TrustTermPenalty` computes `β·trust·(1−p)`, so β=0 disables the staleness "
             "penalty outright, which is why the structure-only column can 'win' the "
             "contradiction category by failing to demote a disputed node.", "",
             "| weighting | " + " | ".join(corpus_module.CATEGORIES) + " | MRR (rank-1 categories) |",
             "|---|" + "---|" * (len(corpus_module.CATEGORIES) + 1)]
    for label, alpha, beta, gamma in ablations:
        with _weights(alpha, beta, gamma):
            rows = _score_queries(corpus, isolate_stage_two=False)
        cells = [f"{rows[c]['success']:.2f}" if c in rows else "—" for c in corpus_module.CATEGORIES]
        mrr = _mean([r["MRR"] for r in rows.values() if r["MRR"] == r["MRR"]])
        lines.append(f"| {label} | " + " | ".join(cells) + f" | {mrr:.3f} |")
    parts.append("\n".join(lines) + "\n")
    return "\n".join(parts)


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", metavar="CHANGE_ID",
                        help="append the tables to eval/results/<date>-<change-id>.md")
    parser.add_argument("--filler", type=int, default=18,
                        help="filler scopes added around the labelled core (default 18)")
    args = parser.parse_args(argv)

    import tempfile

    db = os.path.join(tempfile.mkdtemp(prefix="agentic-memory-eval-"), "bench.db")
    built = corpus_module.build(db, filler_scopes=args.filler)
    try:
        nodes = built.store._conn.execute("SELECT count(*) FROM nodes").fetchone()[0]
        header = (
            f"Corpus: {len(built.goals)} goals, {nodes} nodes, {len(built.facets)} facets, "
            f"{len(built.queries)} labelled queries, k={K}\n"
        )
        body = header + "\n" + report(built)
    finally:
        built.store.close()

    print(body)
    if args.write:
        target = os.path.join(
            os.path.dirname(__file__), "results", f"{date.today().isoformat()}-{args.write}.md"
        )
        # Append, never overwrite: a benchmark history you can diff and blame is worth
        # more than one number that silently drifted, and it is the same reason the
        # journal is append-only.
        with open(target, "a", encoding="utf-8") as handle:
            handle.write(f"\n## {date.today().isoformat()} — {args.write}\n\n{body}")
        print(f"appended to {target}")


if __name__ == "__main__":
    main()
